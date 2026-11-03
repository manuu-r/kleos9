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


def test_development_cors_accepts_both_loopback_spellings_of_the_frontend_only():
    from kleos.infrastructure.runtime import frontend_origins
    dev = Settings(_env_file=None, environment='development', frontend_origin='http://127.0.0.1:5180/',
                   preview_origin='http://127.0.0.1:8004')
    assert frontend_origins(dev) == ['http://127.0.0.1:5180', 'http://localhost:5180']
    test = Settings(_env_file=None, environment='test', frontend_origin='http://localhost:5180',
                    preview_origin='http://localhost:8004')
    assert frontend_origins(test) == ['http://localhost:5180']
    hosted = Settings(_env_file=None, environment='development', frontend_origin='https://workbench.example',
                      preview_origin='https://preview.example')
    assert frontend_origins(hosted) == ['https://workbench.example']


def test_native_session_length_is_configurable_within_the_native_bound(monkeypatch):
    monkeypatch.delenv("KLEOS_NATIVE_SESSION_SECONDS", raising=False)
    assert Settings(_env_file=None).native_session_seconds == 600
    monkeypatch.setenv("KLEOS_NATIVE_SESSION_SECONDS", "14400")
    assert Settings(_env_file=None).native_session_seconds == 14400
    monkeypatch.setenv("KLEOS_NATIVE_SESSION_SECONDS", "14401")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_every_validator_of_the_run_compute_budget_accepts_the_configured_maximum(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from kleos.contracts.models import MAX_COMPUTE_BUDGET_SECONDS, Actor
    from kleos.orchestration.engine import RunScope
    monkeypatch.setenv("KLEOS_MAX_RUN_SECONDS", str(MAX_COMPUTE_BUDGET_SECONDS))
    assert Settings(_env_file=None).max_run_seconds == MAX_COMPUTE_BUDGET_SECONDS
    scope = RunScope(actor=Actor(tenant_id="t", user_id="u"), project_id="p", run_id="r", revision=1,
                     deadline_at=datetime.now(UTC) + timedelta(days=1), compute_budget_seconds=MAX_COMPUTE_BUDGET_SECONDS)
    assert scope.compute_budget_seconds == MAX_COMPUTE_BUDGET_SECONDS
    monkeypatch.setenv("KLEOS_MAX_RUN_SECONDS", str(MAX_COMPUTE_BUDGET_SECONDS + 1))
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
