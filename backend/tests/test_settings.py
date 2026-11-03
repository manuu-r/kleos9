import pytest
from pydantic import ValidationError

from kleos.infrastructure.settings import Settings


def test_database_url_comes_from_the_process_environment_only(monkeypatch):
    monkeypatch.delenv("KLEOS_DATABASE_URL", raising=False)
    assert Settings(_env_file=None).database_url.endswith("/kleos_rewrite")
    monkeypatch.setenv("KLEOS_DATABASE_URL", "postgresql+psycopg://localhost/explicit_choice")
    assert Settings(_env_file=None).database_url.endswith("/explicit_choice")


def test_the_old_backend_env_file_never_supplies_runtime_configuration(tmp_path, monkeypatch):
    # The preserved old `.env` shares the KLEOS_ prefix; its database, auth and policy values must not cross over.
    for key in ("KLEOS_DATABASE_URL", "KLEOS_DEVELOPMENT_NO_LOGIN", "KLEOS_PHYSICAL_ACTIONS_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    old = tmp_path / ".env"
    old.write_text("KLEOS_DATABASE_URL=postgresql://old-backend/data\nKLEOS_DEVELOPMENT_NO_LOGIN=true\n"
                   "KLEOS_PHYSICAL_ACTIONS_ENABLED=true\n")
    loaded = Settings(_env_file=old)
    assert loaded.database_url.endswith("/kleos_rewrite")
    assert loaded.development_no_login is False and loaded.physical_actions_enabled is False


@pytest.mark.parametrize(
    "options",
    [
        {"environment": "production", "development_no_login": True},
        {"environment": "production", "native_docker_desktop_compatibility": True},
        {"environment": "production"},
        {"physical_actions_enabled": True},
        {"preview_origin": "http://localhost:5180"},
    ],
)
def test_unsafe_settings_rejected(options):
    with pytest.raises(ValidationError):
        Settings(**options)


def test_enabled_production_companion_preview_requires_https_api():
    options = dict(environment='production', session_secret='x' * 40,
        frontend_origin='https://workbench.example', preview_origin='https://preview.example',
        companion_preview_enabled=True, _env_file=None)
    with pytest.raises(ValidationError, match='HTTPS API origin'):
        Settings(**options)
    assert Settings(**options, api_origin='https://api.example').companion_preview_enabled
