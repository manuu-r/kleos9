import pytest
from pydantic import ValidationError

from kleos.infrastructure.settings import Settings


def test_new_runtime_does_not_reuse_legacy_database_configuration(monkeypatch):
    monkeypatch.delenv("KLEOS2_DATABASE_URL", raising=False)  # CI exports it for the PostgreSQL tests
    monkeypatch.setenv("KLEOS_DATABASE_URL", "postgresql://legacy/kleos")
    assert Settings().database_url.endswith("/kleos_rewrite")


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
