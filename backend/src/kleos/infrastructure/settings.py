"""Fresh runtime settings with narrowly scoped reuse of existing provider credentials."""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Configuration comes from process environment variables named KLEOS_<SETTING>. The preserved old backend `.env`
    # shares that prefix (including its database URL), so it is NEVER general configuration: ProviderFileSource reads
    # it for the three provider credentials only (see settings_customise_sources).
    model_config = SettingsConfigDict(env_prefix="KLEOS_", env_file=Path(__file__).resolve().parents[3] / '.env',
        extra="ignore", populate_by_name=True, hide_input_in_errors=True)
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://localhost/kleos_rewrite"
    artifact_root: Path = Path(".data/rewrite/artifacts")
    native_tools: list[dict] = Field(default_factory=list)
    native_control_root: Path = Path(".data/rewrite/native")
    native_daemon_control_root: str | None = None
    native_docker_desktop_compatibility: bool = False
    docker_socket: str = "/var/run/docker.sock"
    native_capacity_pool_id: str = Field(default="local-docker", pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
    native_capacity_cpus: int = Field(default=4, ge=1, le=256)
    native_capacity_memory_bytes: int = Field(default=6 * 1024**3, ge=512 * 1024**2, le=1024**4)
    native_capacity_scratch_bytes: int = Field(default=5 * 1024**3, ge=128 * 1024**2, le=1024**4)
    native_capacity_max_jobs: int = Field(default=2, ge=1, le=128)
    slicer_installation: dict | None = None
    slicer_control_root: Path = Path(".data/rewrite/slicing")
    cad_checker_installation: dict | None = None
    pcb_checker_installation: dict | None = None
    firmware_checker_installation: dict | None = None
    application_checker_installation: dict | None = None
    checker_control_root: Path = Path(".data/rewrite/checkers")
    simulation_installation: dict | None = None
    simulation_control_root: Path = Path(".data/rewrite/simulation")
    external_installations: list[dict] = Field(default_factory=list)
    web_search_installation: dict | None = None
    mcp_installations: list[dict] = Field(default_factory=list, max_length=8)
    mcp_control_root: Path = Path(".data/rewrite/mcp")
    mcp_daemon_control_root: str | None = None
    external_credentials: dict[str, SecretStr] = Field(default_factory=dict, exclude=True, repr=False)
    development_no_login: bool = False
    session_secret: SecretStr | None = None
    setup_token: SecretStr | None = None
    frontend_origin: str = "http://localhost:5180"
    api_origin: str = "http://localhost:8000"
    preview_origin: str = "http://localhost:8004"
    companion_preview_enabled: bool = False
    companion_preview_ttl_seconds: int = Field(default=300, ge=30, le=600)
    max_context_chars: int = Field(default=60000, ge=4000, le=200000)
    max_tasks: int = Field(default=32, ge=1, le=128)
    max_actions: int = Field(default=24, ge=1, le=64)
    max_run_seconds: int = Field(default=1800, ge=30, le=7200)
    max_objective_lifetime_seconds: int = Field(default=7 * 86400, ge=7200, le=30 * 86400)
    max_model_calls: int = Field(default=100, ge=1, le=1000)
    max_model_cost_micro_usd: int = Field(default=5_000_000, ge=1, le=1_000_000_000)
    model_catalog: list[dict] = Field(default_factory=list)
    text_model_key: str | None = None
    vision_model_key: str | None = None
    modal_rate_limit_fallback_key: str | None = None
    printer_encryption_key: SecretStr | None = None
    bambu_local_enabled: bool = False
    model_provider: Literal["meta", "modal"] = "meta"
    meta_api_key: SecretStr | None = Field(default=None, repr=False,
        validation_alias=AliasChoices('KLEOS_META_API_KEY'))
    modal_api_key: SecretStr | None = Field(default=None, repr=False,
        validation_alias=AliasChoices('KLEOS_MODAL_API_KEY', 'KLEOS_MODAL_LLM_KEY'))
    modal_base_url: str | None = Field(default=None,
        validation_alias=AliasChoices('KLEOS_MODAL_BASE_URL', 'KLEOS_MODAL_LLM_URL'))
    physical_actions_enabled: bool = False

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        from kleos.infrastructure.provider_defaults import ProviderFileSource
        providers = ProviderFileSource(settings_cls, env_file=dotenv_settings.env_file)
        return init_settings, env_settings, file_secret_settings, providers

    @model_validator(mode="after")
    def boundaries(self):
        from kleos.infrastructure.provider_defaults import muse_catalog
        if 'model_catalog' not in self.model_fields_set:
            meta = bool(self.meta_api_key and self.meta_api_key.get_secret_value())
            modal = bool(self.modal_api_key and self.modal_api_key.get_secret_value() and self.modal_base_url)
            self.model_catalog = muse_catalog(meta_enabled=meta, modal_enabled=modal, modal_url=self.modal_base_url)
            default = 'meta-muse-spark' if self.model_provider == 'meta' and meta else (
                'modal-muse-spark-open' if self.model_provider == 'modal' and modal else None)
            # With only Modal configured, it is the sole explicit installation.
            if not meta and modal:
                default = 'modal-muse-spark-open'
            if 'text_model_key' not in self.model_fields_set:
                self.text_model_key = default
            if 'vision_model_key' not in self.model_fields_set:
                self.vision_model_key = default
        if self.environment == "production":
            if self.native_docker_desktop_compatibility:
                raise ValueError("Docker Desktop compatibility is restricted to local development")
            if self.development_no_login:
                raise ValueError("Development authentication is not allowed in production")
            if not self.session_secret or len(self.session_secret.get_secret_value()) < 32:
                raise ValueError("Production requires a persistent strong session secret")
            if not self.frontend_origin.startswith("https://") or not self.preview_origin.startswith(
                "https://"
            ):
                raise ValueError("Production origins require HTTPS")
            if self.companion_preview_enabled and not self.api_origin.startswith('https://'):
                raise ValueError('Production companion previews require an HTTPS API origin')
        if self.frontend_origin.rstrip("/") == self.preview_origin.rstrip("/"):
            raise ValueError("Generated previews require a separate origin")
        if self.physical_actions_enabled:
            raise ValueError(
                "Live physical actions require qualified integration policy; not yet implemented"
            )
        return self
