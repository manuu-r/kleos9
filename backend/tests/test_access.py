"""Account/session authority using disposable databases and no external services."""
from dataclasses import replace

import pytest
from sqlalchemy import select

from kleos.access import AccessService, AccessSettings
from kleos.access.service import Account, Invite, Session
from kleos.contracts.models import Actor, DomainError
from kleos.projects.store import ProjectStore

PASSWORD = 'correct fixture password'


@pytest.fixture
def access(tmp_path):
    store = ProjectStore('sqlite:///' + str(tmp_path / 'access.db'))
    service = AccessService(store, AccessSettings(secret_key='s' * 32, environment='test', setup_token='setup-secret'))
    store.initialize_for_tests()
    yield service
    store.engine.dispose()


def owner(access, native=True):
    result = access.setup({'setupToken': 'setup-secret', 'organization': 'Hardware team',
        'email': 'OWNER@example.invalid', 'name': 'Owner', 'password': PASSWORD, 'native': native})
    headers = {'Authorization': 'Bearer ' + result.body['token']} if native else {}
    cookies = {} if native else {result.cookies[0].name: result.cookies[0].value}
    actor = access.authenticate(headers, cookies, 'GET')
    headers['X-Organization-Id'] = actor.tenant_id
    return actor, headers, cookies


def joined(access, administrator, role='member', email='member@example.invalid'):
    invitation = access.action(administrator, 'create_invite', {}, {'role': role, 'email': email})
    result = access.signup({'inviteCode': invitation['code'], 'email': email, 'password': PASSWORD, 'native': True})
    headers = {'Authorization': 'Bearer ' + result.body['token'], 'X-Organization-Id': administrator.tenant_id}
    return access.authenticate(headers, {}, 'GET'), headers, invitation


def test_no_accounts_are_created_without_explicit_setup_or_development_flag(access):
    with pytest.raises(DomainError) as error:
        access.authenticate({}, {}, 'GET')
    assert error.value.status == 401
    with access.store.transaction() as session:
        assert list(session.scalars(select(Account))) == []
    assert access.auth('config', {}, {}, {}).body['setupRequired']
    with pytest.raises(DomainError):
        access.setup({'setupToken': 'wrong'})


def test_setup_is_one_time_and_tokens_passwords_are_not_persisted_plaintext(access):
    actor, headers, _ = owner(access)
    token = headers['Authorization'].split()[1]
    with access.store.transaction() as session:
        account = session.get(Account, actor.user_id)
        assert account.email == 'owner@example.invalid'
        assert account.password_hash.startswith('scrypt:') and PASSWORD not in account.password_hash
        stored = list(session.scalars(select(Session)))
        assert len(stored) == 1 and stored[0].token_hash != token and len(stored[0].token_hash) == 64
    with pytest.raises(DomainError) as error:
        access.setup({'setupToken': 'setup-secret', 'organization': 'Other', 'email': 'other@example.invalid',
                      'password': PASSWORD})
    assert error.value.code == 'setup_complete'


def test_cookie_session_requires_csrf_and_cannot_be_used_as_bearer(access):
    actor, headers, cookies = owner(access, native=False)
    view = access.auth('session', {}, headers, cookies).body
    assert view['subject'] == actor.user_id and view['csrfToken']
    with pytest.raises(DomainError) as error:
        access.authenticate(headers, cookies, 'POST')
    assert error.value.code == 'csrf_required'
    assert access.authenticate({**headers, 'X-CSRF-Token': view['csrfToken']}, cookies, 'POST') == actor
    with pytest.raises(DomainError):
        access.authenticate({'Authorization': 'Bearer ' + next(iter(cookies.values()))}, {}, 'GET')
    with pytest.raises(DomainError):
        access.logout(headers, cookies)
    access.logout({**headers, 'X-CSRF-Token': view['csrfToken']}, cookies)
    with pytest.raises(DomainError):
        access.authenticate(headers, cookies, 'GET')


def test_invites_are_email_bound_hashed_single_use_and_not_listed_as_secrets(access):
    administrator, _, _ = owner(access)
    invite = access.action(administrator, 'create_invite', {}, {'email': 'member@example.invalid', 'role': 'member'})
    with pytest.raises(DomainError):
        access.signup({'inviteCode': invite['code'], 'email': 'wrong@example.invalid', 'password': PASSWORD})
    access.signup({'inviteCode': invite['code'], 'email': 'member@example.invalid', 'password': PASSWORD})
    with pytest.raises(DomainError):
        access.signup({'inviteCode': invite['code'], 'email': 'member@example.invalid', 'password': PASSWORD})
    with access.store.transaction() as session:
        stored = session.scalar(select(Invite))
        assert stored.used and stored.token_hash != invite['code']
    listing = access.query(administrator, 'invites')
    assert invite['code'] not in str(listing) and listing['invites'][0]['status'] == 'used'


def test_login_lockout_persists_across_service_restart(access):
    administrator, _, _ = owner(access)
    for _ in range(10):
        with pytest.raises(DomainError) as error:
            access.login({'email': 'owner@example.invalid', 'password': 'wrong'})
        assert error.value.status == 401
    restarted = AccessService(access.store, access.settings)
    with pytest.raises(DomainError) as error:
        restarted.login({'email': 'owner@example.invalid', 'password': PASSWORD})
    assert error.value.status == 429
    assert restarted.query(administrator, 'members')['members'][0]['locked']


def test_valid_login_creates_new_sessions_and_logout_revokes_only_selected_session(access):
    _, initial, _ = owner(access)
    second = access.login({'email': 'owner@example.invalid', 'password': PASSWORD, 'native': True})
    headers = {'Authorization': 'Bearer ' + second.body['token']}
    assert headers['Authorization'] != initial['Authorization']
    access.logout(headers, {})
    with pytest.raises(DomainError):
        access.authenticate(headers, {}, 'GET')
    assert access.authenticate(initial, {}, 'GET')


def test_tenant_selection_and_removed_membership_are_rechecked(access):
    administrator, _, _ = owner(access)
    member, headers, _ = joined(access, administrator)
    with pytest.raises(DomainError):
        access.authenticate({**headers, 'X-Organization-Id': 'another-tenant'}, {}, 'GET')
    with pytest.raises(DomainError):
        access.query(Actor(user_id=member.user_id, tenant_id='another-tenant'), 'members')
    access.action(administrator, 'remove_member', {'subject': member.user_id}, {})
    with pytest.raises(DomainError):
        access.authenticate(headers, {}, 'GET')


def test_role_limits_and_last_owner_protection(access):
    administrator, _, _ = owner(access)
    admin, _, _ = joined(access, administrator, role='admin')
    viewer, headers, _ = joined(access, administrator, role='viewer', email='viewer@example.invalid')
    with pytest.raises(DomainError):
        access.authenticate(headers, {}, 'POST')
    with pytest.raises(DomainError):
        access.query(viewer, 'members')
    with pytest.raises(DomainError):
        access.action(admin, 'create_invite', {}, {'role': 'owner'})
    with pytest.raises(DomainError):
        access.action(admin, 'reset_password', {'subject': administrator.user_id}, {})
    with pytest.raises(DomainError) as error:
        access.action(administrator, 'remove_member', {'subject': administrator.user_id}, {})
    assert error.value.code == 'last_owner'
    with pytest.raises(DomainError):
        access.action(administrator, 'change_role', {'subject': administrator.user_id}, {'role': 'member'})


def test_password_reset_revokes_sessions_and_unlocks_account(access):
    administrator, _, _ = owner(access)
    member, headers, _ = joined(access, administrator)
    result = access.action(administrator, 'reset_password', {'subject': member.user_id}, {})
    with pytest.raises(DomainError):
        access.authenticate(headers, {}, 'GET')
    with pytest.raises(DomainError):
        access.login({'email': 'member@example.invalid', 'password': PASSWORD})
    assert access.login({'email': 'member@example.invalid', 'password': result['temporaryPassword'], 'native': True}).body['token']


def test_expired_sessions_cannot_authenticate(access):
    _, headers, _ = owner(access)
    with access.store.transaction() as session:
        session.scalar(select(Session)).expires = 0
    with pytest.raises(DomainError) as error:
        access.authenticate(headers, {}, 'GET')
    assert error.value.status == 401


def test_development_bypass_is_explicit_local_and_still_checks_tenant(access):
    with pytest.raises(ValueError):
        replace(access.settings, environment='production', development_no_login=True)
    local = AccessService(access.store, replace(access.settings, development_no_login=True))
    actor = local.authenticate({}, {}, 'GET')
    assert actor == Actor(tenant_id='development', user_id='development')
    assert not local.auth('config', {}, {}, {}).body['loginRequired']
    with pytest.raises(DomainError):
        local.authenticate({'X-Organization-Id': 'other'}, {}, 'GET')
    with pytest.raises(DomainError):
        local.authenticate({'Authorization': 'Bearer invalid'}, {}, 'GET')
