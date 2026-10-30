from pathlib import Path

import pytest
from pydantic import ValidationError

from kleos.config import Settings


def test_gemini_dotenv_alias_and_explicit_prefix_priority(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("KLEOS_GEMINI_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=fixture-standard-key\n")
    assert Settings(_env_file=env).gemini_api_key.get_secret_value() == "fixture-standard-key"
    env.write_text(env.read_text() + "KLEOS_GEMINI_API_KEY=fixture-prefixed-key\n")
    assert Settings(_env_file=env).gemini_api_key.get_secret_value() == "fixture-prefixed-key"
    assert Settings(_env_file=env, gemini_api_key="fixture-explicit").gemini_api_key.get_secret_value() == "fixture-explicit"


def test_local_native_execution_requires_authentication_and_cannot_enter_cloud():
    with pytest.raises(ValidationError, match="persistent random session key"):
        Settings(_env_file=None, execution_backend="docker")
    with pytest.raises(ValidationError, match="restricted to local development"):
        Settings(_env_file=None, execution_backend="docker", session_key="fixture-signing-key",
                 environment="production", auth_mode="cognito", storage_backend="s3")
    settings = Settings(_env_file=None, execution_backend="docker", session_key="fixture-signing-key")
    assert settings.docker_jobs_dir == Path("/native-jobs")
