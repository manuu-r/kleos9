"""Shared boundaries: candidates and observations never grant acceptance authority."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# Ceiling for an objective's active-compute budget (KLEOS_MAX_RUN_SECONDS), shared by every validator of it.
MAX_COMPUTE_BUDGET_SECONDS = 12 * 3600

class WireModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class Actor(WireModel):
    tenant_id: str = Field(min_length=1, max_length=100)
    user_id: str = Field(min_length=1, max_length=100)


class Capability(StrEnum):
    CAD = "cad"
    PCB = "pcb"
    FIRMWARE = "firmware"
    APPLICATION = "application"
    SOURCING = "sourcing"
    HUMAN_WORK = "human_work"
    FABRICATION = "fabrication"
    RESEARCH = "research"
    VALIDATION = "validation"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ArtifactRef(WireModel):
    id: str
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision: int = Field(ge=1)
    media_type: str = "application/octet-stream"
    size: int = Field(ge=0)


class TaskSpec(WireModel):
    id: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=12000)
    capability: Capability
    depends_on: list[str] = Field(default_factory=list, max_length=100)
    input_artifact_ids: list[str] = Field(default_factory=list, max_length=256)
    allowed_paths: list[str] = Field(default_factory=list, max_length=256)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)
    max_actions: int = Field(default=24, ge=1, le=64)


class Observation(WireModel):
    name: str
    status: CheckStatus
    details: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list)
    independent: bool = False


class TaskResult(WireModel):
    task_id: str
    status: TaskStatus
    summary: str = Field(max_length=12000)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    wait_ids: list[str] = Field(default_factory=list, max_length=32)


class ProjectContext(WireModel):
    tenant_id: str
    project_id: str
    revision: int = Field(ge=1)
    brief: str = ""
    requirements: dict[str, Any] = Field(default_factory=dict)
    interfaces: dict[str, Any] = Field(default_factory=dict)
    approved_design: dict[str, Any] | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    reference_images: list[ArtifactRef] = Field(default_factory=list, max_length=8)
    repository_metadata: list[ArtifactRef] = Field(default_factory=list, max_length=64)
    repository_metadata_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class ObjectiveRequest(WireModel):
    project_id: str
    objective: str = Field(min_length=1, max_length=12000)
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)


class DomainError(Exception):
    def __init__(self, message: str, code: str = "conflict", status: int = 409):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status
