from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class WireModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class Lane(StrEnum):
    coordination = "coordination"
    research = "research"
    mechanics = "mechanics"
    electronics = "electronics"
    firmware = "firmware"
    application = "application"
    review = "review"


class ModelChoice(WireModel):
    provider: Literal["meta", "anthropic", "google"]
    model: str = Field(min_length=1, max_length=100)


class Command(WireModel):
    type: str = Field(min_length=1, max_length=100)
    payload: dict = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=1)


class CommandAccepted(WireModel):
    command_id: str
    project_id: str
    run_id: str | None = None
    revision: int


class GeneratedFile(WireModel):
    path: str
    content: str = Field(max_length=24 * 1024 * 1024)
    media_type: str = "text/plain"
    encoding: Literal["utf8", "base64"] = "utf8"

    @field_validator("path")
    @classmethod
    def safe_path(cls, value):
        p = PurePosixPath(value)
        if (p.is_absolute() or ".." in p.parts or "\\" in value or ":" in value or not p.name
                or len(value) > 240 or any(ord(char) < 32 for char in value)):
            raise ValueError("Artifact paths must be relative and cannot traverse directories")
        return p.as_posix()


class AgentOutput(WireModel):
    summary: str = Field(max_length=10000)
    questions: list[str] = Field(default_factory=list, max_length=12)
    requirements: dict = Field(default_factory=dict)
    interfaces: dict = Field(default_factory=dict)
    files: list[GeneratedFile] = Field(default_factory=list, max_length=50)
    sources: list[str] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list)


class ArtifactUpload(WireModel):
    file: GeneratedFile
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_engineering_revision: int = Field(ge=1)


class CheckResult(WireModel):
    name: str
    status: Literal["PASS", "FAIL", "UNKNOWN", "ERROR", "NOT_APPLICABLE"]
    evidence_kind: Literal["digital", "physical", "synthetic"] = "digital"
    details: dict = Field(default_factory=dict)


class JobResult(WireModel):
    status: Literal["succeeded", "failed", "unsupported"]
    files: list[GeneratedFile] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    measurements: dict = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    logs: str = Field(default="", max_length=100000)


class ConnectorManifest(WireModel):
    name: str
    version: str
    transport: Literal["rest", "mcp", "portal", "bridge", "builtin"]
    capabilities: list[str]
    mode: Literal["read_only", "simulated", "live"]
    endpoint: str | None = None
    supports_idempotency: bool = False
    supports_cancellation: bool = False
    disclosure: list[str] = Field(default_factory=list)


class DomainError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "invalid_request"):
        self.message, self.status, self.code = message, status, code
        super().__init__(message)
