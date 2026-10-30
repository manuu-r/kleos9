import pytest
from pydantic import ValidationError

from kleos.config import Settings


def test_no_login_uses_existing_development_membership(platform):
    platform.settings.development_no_login = True
    platform.client.headers.pop('Authorization')
    assert platform.client.get('/api/v1/auth/config').json()['loginRequired'] is False
    session = platform.client.get('/api/v1/auth/session')
    assert session.status_code == 200
    assert session.json()['subject'] == 'development'
    assert session.json()['tenantId'] == 'org-a'
    assert session.json()['loginRequired'] is False
    assert platform.client.get('/api/v1/projects').status_code == 200
    created = platform.client.post('/api/v1/commands',
        headers={'Idempotency-Key': 'no-login-create'},
        json={'type': 'project.create', 'payload': {'brief': 'Local no-login test'}})
    assert created.status_code == 202
    # Removing login does not grant arbitrary organization access.
    assert platform.client.get('/api/v1/projects', headers={'X-Organization-Id': 'org-b'}).status_code == 403


def test_default_development_still_requires_token(platform):
    platform.client.headers.pop('Authorization')
    assert platform.client.get('/api/v1/auth/config').json()['loginRequired'] is True
    assert platform.client.get('/api/v1/projects').status_code == 401


def test_no_login_does_not_need_a_token():
    settings = Settings(_env_file=None, environment='test', auth_mode='development',
                        development_no_login=True, development_token=None)
    assert settings.development_no_login


@pytest.mark.parametrize('environment', ['staging', 'production'])
def test_cloud_cannot_disable_login(environment):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment=environment, auth_mode='password',
                 storage_backend='s3', database_url='postgresql+psycopg://localhost/kleos',
                 development_no_login=True)


def test_password_auth_cannot_disable_login():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment='development', auth_mode='password',
                 development_no_login=True)
