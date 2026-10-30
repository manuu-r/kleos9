from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .contracts import EngineeringToolDefinition


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KLEOS_", env_file=".env", extra="ignore", populate_by_name=True)
    environment: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = "postgresql+psycopg://kleos:kleos@localhost:5432/kleos"
    dbos_database_url: str = "postgresql://kleos:kleos@localhost:5432/kleos_dbos"
    dbos_application_version: str = "0.1.0"
    conductor_url: str | None = None
    conductor_key: SecretStr | None = None
    demo_mode: bool = True
    auth_mode: Literal["password", "development"] = "password"
    development_token: SecretStr | None = None
    development_no_login: bool = False
    # Lets the operator create the first owner from the sign-in page while no account exists.
    setup_token: SecretStr | None = Field(default=None, min_length=16)
    frontend_url: str = "http://localhost:5180"
    companion_preview_base_url: str | None = None
    companion_preview_signing_key: SecretStr | None = Field(default=None, min_length=32)
    session_key: SecretStr | None = None
    cors_origins: list[str] = ["http://localhost:5180", "http://127.0.0.1:5180"]
    aws_region: str = "us-east-1"
    storage_backend: Literal["s3", "local"] = "local"
    artifact_bucket: str = ""
    local_artifact_dir: Path = Path(".data/artifacts")
    qdrant_url: str = "http://localhost:6333"
    qdrant_key: SecretStr | None = None
    embedding_model: str = "gemini-embedding-2"
    meta_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = Field(default=None,
        validation_alias=AliasChoices("KLEOS_GEMINI_API_KEY", "GEMINI_API_KEY"))
    meta_model: str = "muse-spark-1.3"
    gemini_model: str = "gemini-3.8-flash"
    default_model_provider: Literal["meta", "google"] | None = None
    development_unlimited_usage: bool = False
    # Limits are operator allowances, never claims about provider credit balances.
    project_budget_usd: float = Field(default=0, ge=0, allow_inf_nan=False)
    daily_budget_usd: float | None = Field(default=0, ge=0, allow_inf_nan=False)

    @field_validator("daily_budget_usd", mode="before")
    @classmethod
    def parse_daily_allowance(cls, value):
        # Explicit operator choice; zero still blocks unconfigured paid work.
        return None if isinstance(value, str) and value.strip().lower() == "unlimited" else value

    max_active_projects: int = Field(default=10, ge=1)
    max_org_projects: int = Field(default=2, ge=1)
    max_model_calls: int = Field(default=10, ge=1)
    max_native_jobs: int = Field(default=4, ge=1)
    max_attempts: int = Field(default=4, ge=1, le=4)
    max_model_turns: int = Field(default=40, ge=1, le=40)
    max_context_chars: int = Field(default=80000, ge=2000)
    planning_max_output_tokens: int = Field(default=5000, ge=1000, le=8000)
    planning_max_model_turns: int = Field(default=4, ge=1, le=8)
    invocation_reservation_usd: float = Field(default=1, gt=0, allow_inf_nan=False)
    embedding_reservation_usd: float = Field(default=0.01, gt=0, allow_inf_nan=False)
    native_job_reservation_usd: float = Field(default=1, gt=0, allow_inf_nan=False)
    model_timeout_seconds: int = Field(default=180, ge=1, le=1800)
    worker_timeout_seconds: int = Field(default=1800, ge=1, le=3600)
    execution_backend: Literal["ecs", "docker", "disabled"] = "disabled"
    docker_socket: str = "/var/run/docker.sock"
    docker_jobs_volume: str = "kleos9_native_jobs"
    docker_jobs_dir: Path = Path("/native-jobs")
    docker_native_images: dict[str, str] = {}
    engineering_tools: list[EngineeringToolDefinition] = Field(default_factory=list, max_length=100)
    ecs_cluster: str = ""
    ecs_subnets: list[str] = []
    ecs_security_groups: list[str] = []
    ecs_task_definitions: dict[str, str] = {}
    internal_base_url: str = "http://localhost:8000"
    sqs_completion_queue: str = ""
    modal_app: str = "kleos-models"
    modal_function: str = "infer"
    digikey_client_id: str = ""
    digikey_client_secret: SecretStr | None = None
    digikey_sandbox: bool = True
    webhook_secrets: dict[str, str] = {}
    otlp_endpoint: str | None = None
    event_retention_days: int = 90
    transcript_retention_days: int = 30
    bridge_stale_seconds: int = 120

    @model_validator(mode="after")
    def deployment_guards(self):
        if self.companion_preview_base_url:
            preview = urlsplit(self.companion_preview_base_url)
            if (preview.scheme not in {'http', 'https'} or not preview.hostname or preview.username
                    or preview.password or preview.query or preview.fragment or preview.path not in {'', '/'}):
                raise ValueError('Companion previews require a dedicated absolute origin without credentials or a path')
            if self.environment in {'staging', 'production'} and preview.scheme != 'https':
                raise ValueError('Cloud companion previews require HTTPS')
            if self.environment in {'staging', 'production'} and not self.companion_preview_signing_key:
                raise ValueError('Cloud companion previews require a dedicated preview signing key')
            preview_origin = (preview.scheme, preview.hostname, preview.port or (443 if preview.scheme == 'https' else 80))
            for address in [self.frontend_url, self.internal_base_url, *self.cors_origins]:
                target = urlsplit(address)
                origin = (target.scheme, target.hostname, target.port or (443 if target.scheme == 'https' else 80))
                if preview_origin == origin:
                    raise ValueError('Companion previews must use an origin separate from the control UI and API')
        if self.development_unlimited_usage and self.environment not in {"development", "test"}:
            raise ValueError("Unlimited development usage is restricted to development/test environments")
        tool_keys = [(tool.id, tool.version) for tool in self.engineering_tools]
        if len(set(tool_keys)) != len(tool_keys) or any(
                tool.id in {"build123d", "atopile-kicad"} for tool in self.engineering_tools):
            raise ValueError("Engineering tools must be unique and cannot replace built-in defaults")
        if not self.demo_mode:
            raise ValueError("This MVP only permits demo fulfillment; live dispatch is not implemented")
        if self.environment in {"staging", "production"}:
            if self.execution_backend == "docker":
                raise ValueError("Docker execution is restricted to local development")
            if self.auth_mode != "password" or self.storage_backend != "s3":
                raise ValueError("Cloud deployments require password authentication and S3")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("Cloud deployments require PostgreSQL")
        if self.development_no_login and (
            self.environment not in {"development", "test"} or self.auth_mode != "development"
        ):
            raise ValueError("No-login access is restricted to development authentication in local environments")
        if self.auth_mode == "development" and not self.development_no_login and not self.development_token:
            raise ValueError("Development authentication requires an explicit random token")
        if self.execution_backend in {"ecs", "docker"} and not self.session_key:
            raise ValueError("Native execution requires a persistent random session key")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
