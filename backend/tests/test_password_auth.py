from datetime import timedelta

import pytest
from pydantic import ValidationError

from kleos.auth import reset_password
from kleos.config import Settings
from kleos.db import Invite, Membership, now

PASSWORD = "correct horse battery"


def signup(auth, code, email="ada@example.com", **extra):
    return auth.client.post("/api/v1/auth/signup", json={
        "inviteCode": code, "email": email, "password": PASSWORD, **extra})


def test_signup_creates_membership_and_web_session(auth):
    response = signup(auth, auth.invite("admin"), "Ada@Example.com", name="Ada")
    assert response.status_code == 200
    assert response.json()["token"] is None
    assert "kleos_session" in auth.client.cookies
    session = auth.client.get("/api/v1/auth/session").json()
    assert (session["tenantId"], session["role"]) == ("org-a", "admin")
    # Unsafe methods need the CSRF token that matches the session.
    assert auth.client.post("/api/v1/auth/logout").status_code == 403
    csrf = {"X-CSRF-Token": auth.client.cookies["kleos_csrf"]}
    assert auth.client.post("/api/v1/auth/logout", headers=csrf).status_code == 200
    auth.client.cookies.clear()
    assert auth.client.get("/api/v1/auth/session").status_code == 401


def test_invite_code_is_single_use_and_stored_hashed(auth):
    code = auth.invite()
    with auth.db.transaction() as session:
        stored = session.query(Invite).one()
        assert code not in (stored.code_hash, stored.id) and len(stored.code_hash) == 64
    assert signup(auth, code).status_code == 200
    again = signup(auth, code, "grace@example.com")
    assert (again.status_code, again.json()["code"]) == (400, "invite_invalid")


def test_invalid_expired_and_mismatched_invites_are_rejected(auth):
    assert signup(auth, "k9-not-a-real-code").json()["code"] == "invite_invalid"
    expired = auth.invite()
    with auth.db.transaction() as session:
        session.query(Invite).one().expires_at = now() - timedelta(minutes=1)
    assert signup(auth, expired).json()["code"] == "invite_invalid"
    bound = auth.invite(email="only@example.com")
    assert signup(auth, bound, "other@example.com").json()["code"] == "invite_invalid"
    assert signup(auth, bound, "only@example.com").status_code == 200


def test_signup_validates_password_and_rejects_duplicate_email(auth):
    short = auth.client.post("/api/v1/auth/signup", json={
        "inviteCode": auth.invite(), "email": "ada@example.com", "password": "short"})
    assert short.status_code == 422
    assert auth.client.post("/api/v1/auth/signup", json={
        "inviteCode": auth.invite(), "email": "not-an-email", "password": PASSWORD}).status_code == 422
    assert signup(auth, auth.invite()).status_code == 200
    duplicate = signup(auth, auth.invite())
    assert (duplicate.status_code, duplicate.json()["code"]) == (409, "account_exists")
    with auth.db.transaction() as session:
        # Rejected attempts must not burn their invites: four were issued, one was redeemed.
        assert session.query(Membership).count() == 1
        assert session.query(Invite).filter(Invite.redeemed_at.is_(None)).count() == 3


def test_login_does_not_reveal_which_part_was_wrong(auth):
    signup(auth, auth.invite())
    auth.client.cookies.clear()
    wrong = auth.client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": "wrong password!"})
    unknown = auth.client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    ok = auth.client.post("/api/v1/auth/login", json={"email": "ADA@example.com", "password": PASSWORD})
    assert ok.status_code == 200
    assert auth.client.get("/api/v1/auth/session").status_code == 200


def test_repeated_failures_lock_the_account_even_for_the_right_password(auth):
    signup(auth, auth.invite())
    auth.client.cookies.clear()
    for _ in range(10):
        assert auth.client.post("/api/v1/auth/login", json={
            "email": "ada@example.com", "password": "wrong password!"}).status_code == 401
    locked = auth.client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    assert (locked.status_code, locked.json()["code"]) == (401, "invalid_credentials")
    from kleos.db import Account
    with auth.db.transaction() as session:
        session.query(Account).one().locked_until = now() - timedelta(seconds=1)
    assert auth.client.post("/api/v1/auth/login", json={
        "email": "ada@example.com", "password": PASSWORD}).status_code == 200


def test_native_clients_get_a_bearer_token_without_cookies(auth):
    response = signup(auth, auth.invite(), native=True)
    token = response.json()["token"]
    assert token and "kleos_session" not in auth.client.cookies
    bearer = {"Authorization": f"Bearer {token}"}
    assert auth.client.get("/api/v1/auth/session", headers=bearer).status_code == 200
    assert auth.client.post("/api/v1/auth/logout", headers=bearer).status_code == 200
    assert auth.client.get("/api/v1/auth/session", headers=bearer).status_code == 401
    assert auth.client.get("/api/v1/auth/session", headers={"Authorization": "Bearer forged"}).status_code == 401


def test_admins_mint_invites_and_only_owners_mint_owners(auth):
    signup(auth, auth.invite("member"), "member@example.com")
    csrf = {"X-CSRF-Token": auth.client.cookies["kleos_csrf"]}
    assert auth.client.post("/api/v1/auth/invites", json={}, headers=csrf).status_code == 403
    auth.client.cookies.clear()
    signup(auth, auth.invite("admin"), "admin@example.com")
    csrf = {"X-CSRF-Token": auth.client.cookies["kleos_csrf"]}
    assert auth.client.post("/api/v1/auth/invites", json={"role": "owner"}, headers=csrf).status_code == 403
    minted = auth.client.post("/api/v1/auth/invites", json={"role": "viewer"}, headers=csrf)
    assert minted.status_code == 200
    auth.client.cookies.clear()
    assert signup(auth, minted.json()["code"], "viewer@example.com").status_code == 200
    assert auth.client.get("/api/v1/auth/session").json()["role"] == "viewer"


def test_auth_config_advertises_invite_signup(auth):
    config = auth.client.get("/api/v1/auth/config").json()
    assert (config["authMode"], config["signupRequiresInvite"]) == ("password", True)


def test_cloud_environments_require_password_auth():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", auth_mode="development",
                 development_token="x" * 32, storage_backend="s3",
                 database_url="postgresql+psycopg://localhost/kleos")


def test_operator_password_reset_unlocks_and_signs_out_everywhere(auth):
    signup(auth, auth.invite())
    assert auth.client.get("/api/v1/auth/session").status_code == 200
    with auth.db.transaction() as session:
        temporary = reset_password(session, " ADA@example.com ")
    assert auth.client.get("/api/v1/auth/session").status_code == 401
    assert auth.client.post("/api/v1/auth/login", json={
        "email": "ada@example.com", "password": PASSWORD}).status_code == 401
    assert auth.client.post("/api/v1/auth/login", json={
        "email": "ada@example.com", "password": temporary}).status_code == 200
    with auth.db.transaction() as session, pytest.raises(Exception, match="No account"):
        reset_password(session, "nobody@example.com")
