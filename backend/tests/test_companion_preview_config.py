import pytest
from pydantic import ValidationError

from kleos.companion_preview import CompanionPreviews
from kleos.config import Settings
from kleos.contracts import DomainError

FAKE_KEY = 'preview-test-only-key-at-least-32-characters'


def cloud(**changes):
    return Settings(_env_file=None, **{
        'environment': 'production', 'auth_mode': 'password', 'storage_backend': 's3',
        'database_url': 'postgresql://fixture:fixture@localhost/test',
        'frontend_url': 'https://control.example.test', 'cors_origins': ['https://control.example.test'],
        'internal_base_url': 'https://api.example.test', 'companion_preview_base_url': 'https://preview.example.test',
        'companion_preview_signing_key': FAKE_KEY, **changes})


@pytest.mark.parametrize('key', ['', 'short', 'x' * 31])
def test_dedicated_preview_signing_keys_require_at_least_32_characters(key):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, companion_preview_signing_key=key)
    assert Settings(_env_file=None, companion_preview_signing_key='x' * 32).companion_preview_signing_key


@pytest.mark.parametrize('environment', ['staging', 'production'])
def test_cloud_preview_requires_https_and_a_dedicated_key(environment):
    assert cloud(environment=environment).companion_preview_signing_key.get_secret_value() == FAKE_KEY
    with pytest.raises(ValidationError, match='dedicated preview signing key'):
        cloud(environment=environment, companion_preview_signing_key=None, session_key=FAKE_KEY)
    with pytest.raises(ValidationError, match='require HTTPS'):
        cloud(environment=environment, companion_preview_base_url='http://preview.example.test')


@pytest.mark.parametrize('url', ['https://control.example.test', 'https://control.example.test:443',
                                'https://api.example.test'])
def test_cloud_preview_origin_must_be_separate_from_control_and_api(url):
    with pytest.raises(ValidationError, match='origin separate'):
        cloud(companion_preview_base_url=url)


def test_local_preview_configuration_does_not_reuse_control_signing_credentials():
    settings = Settings(_env_file=None, environment='development', companion_preview_signing_key=None,
                        companion_preview_base_url='http://localhost:8004', session_key=FAKE_KEY)
    assert settings.companion_preview_signing_key is None
    with pytest.raises(DomainError) as exc:
        CompanionPreviews(None, settings, None).signer()
    assert exc.value.code == 'preview_unconfigured'
