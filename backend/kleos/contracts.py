from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
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
    provider: Literal["meta", "google"]
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


class ClarificationOption(WireModel):
    value: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=400)


class ClarificationQuestion(WireModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
    prompt: str = Field(min_length=1, max_length=500)
    help_text: str = Field(default="", max_length=600)
    type: Literal["single_select", "multi_select", "text", "number"] = "text"
    options: list[ClarificationOption] = Field(default_factory=list, max_length=12)
    required: bool = True
    allow_custom: bool = False
    min: float | None = Field(default=None, allow_inf_nan=False)
    max: float | None = Field(default=None, allow_inf_nan=False)
    unit: str = Field(default="", max_length=30)

    @model_validator(mode="after")
    def valid_choices(self):
        if self.type in {"single_select", "multi_select"} and not self.options:
            raise ValueError("Choice questions require options")
        if self.type not in {"single_select", "multi_select"} and self.options:
            raise ValueError("Only choice questions may declare options")
        if len({o.value for o in self.options}) != len(self.options):
            raise ValueError("Option values must be unique")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("Question minimum cannot exceed maximum")
        return self


class ConceptVector(WireModel):
    x: float = Field(ge=-40, le=40, allow_inf_nan=False)
    y: float = Field(ge=-40, le=40, allow_inf_nan=False)
    z: float = Field(ge=-40, le=40, allow_inf_nan=False)


class ConceptSize(WireModel):
    x: float = Field(gt=0, le=20, allow_inf_nan=False)
    y: float = Field(gt=0, le=20, allow_inf_nan=False)
    z: float = Field(gt=0, le=20, allow_inf_nan=False)


class ConceptRotation(WireModel):
    x: float = Field(default=0, ge=-360, le=360, allow_inf_nan=False)
    y: float = Field(default=0, ge=-360, le=360, allow_inf_nan=False)
    z: float = Field(default=0, ge=-360, le=360, allow_inf_nan=False)


class ConceptPart(WireModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
    label: str = Field(min_length=1, max_length=100)
    role: Literal["shell", "cover", "board", "battery", "display", "sensor", "control", "connector", "strap", "other"]
    shape: Literal["box", "rounded_box", "cylinder", "sphere"] = "box"
    size: ConceptSize
    position: ConceptVector
    rotation: ConceptRotation = Field(default_factory=ConceptRotation)
    explode: ConceptVector = Field(default_factory=lambda: ConceptVector(x=0, y=0, z=0))
    color: str = Field(default="#858B99", pattern=r"^#[0-9a-fA-F]{6}$")
    detail: str = Field(default="", max_length=500)
    source_choices: list[str] = Field(default_factory=list, max_length=24)


class ConceptScene(WireModel):
    schema_version: Literal["concept-scene-v1"] = "concept-scene-v1"
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=800)
    device_kind: str = Field(default="device", min_length=1, max_length=100)
    units: Literal["relative"] = "relative"
    parts: list[ConceptPart] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def unique_part_ids(self):
        ids = [part.id for part in self.parts]
        if len(ids) != len(set(ids)):
            raise ValueError("Concept part IDs must be unique")
        return self


class AgentOutput(WireModel):
    summary: str = Field(max_length=10000)
    concept_scene: ConceptScene | None = None
    research_required: bool = False
    questions: list[str] = Field(default_factory=list, max_length=12)
    clarification_questions: list[ClarificationQuestion] = Field(default_factory=list, max_length=12)
    requirements: dict = Field(default_factory=dict)
    interfaces: dict = Field(default_factory=dict)
    files: list[GeneratedFile] = Field(default_factory=list, max_length=50)
    sources: list[str] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_question_ids(self):
        ids = [q.id for q in self.clarification_questions]
        if len(ids) != len(set(ids)):
            raise ValueError("Clarification question IDs must be unique")
        return self


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
