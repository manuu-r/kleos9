"""Stable public transport schemas, exported to the Flutter client through OpenAPI."""
from .contracts import EngineeringToolChoice, ModelChoice, WireModel


class BudgetView(WireModel):
    allowance_usd: float | None
    unlimited: bool = False
    spent_estimate_usd: float
    reserved_usd: float


class CircuitSimulationView(WireModel):
    revision: int
    engineering_revision: int
    status: str
    artifact_id: str | None
    artifact_sha256: str | None
    result: dict | None
    reason: str | None


class ProjectView(WireModel):
    id: str
    name: str
    brief: str
    revision: int
    engineering_revision: int
    policy_revision: int
    model_policy: dict[str, ModelChoice]
    stage: int
    status: str
    requirements: dict
    interfaces: dict
    state: dict
    accepted_release_id: str | None
    budget: BudgetView
    mode: str
    created_at: str


class ProjectsView(WireModel):
    projects: list[ProjectView]
    next_cursor: str | None


class RunSummary(WireModel):
    id: str
    project_id: str
    kind: str
    status: str
    revision: int
    trace_id: str
    blocker: str | None
    created_at: str


class ArtifactView(WireModel):
    id: str
    name: str
    lane: str
    revision: int
    sha256: str
    size: int
    status: str
    media_type: str
    provenance: dict


class ValidationView(WireModel):
    id: str
    name: str
    status: str
    evidence_kind: str
    details: dict


class WorkspaceView(WireModel):
    project: ProjectView
    revision: int
    event_cursor: int
    runs: list[RunSummary]
    artifacts: list[ArtifactView]
    checks: list[ValidationView]
    training_opted_in: bool


class InvocationView(WireModel):
    id: str
    operation: str
    status: str
    usage: dict
    estimated_usd: float
    has_transcript: bool


class AttemptView(WireModel):
    id: str
    number: int
    status: str
    provider: str
    model: str
    policy_revision: int
    error: str | None
    context_manifest: dict
    output: dict
    calls: list[InvocationView]


class TaskView(WireModel):
    id: str
    lane: str
    status: str
    dependencies: list
    result: dict
    attempts: list[AttemptView]
    native_jobs: list[dict]


class RunView(RunSummary):
    tasks: list[TaskView]


class ModelView(WireModel):
    provider: str
    model: str
    configured: bool
    qualification: str
    enabled_lanes: list[str]
    capabilities: list[str]
    credit_balance: float | None
    context_character_ceiling: int
    pricing_version: str
    context_limit: int | None


class ModelCatalogView(WireModel):
    models: list[ModelView]
    default_policy: dict[str, ModelChoice]
    billing: str


class EngineeringToolView(WireModel):
    id: str
    version: str
    name: str
    lane: str
    runner: str
    source_contract: str
    output_files: list[str]
    qualified: bool
    qualification_reference: str | None
    limitations: list[str]
    configured: bool
    is_default: bool


class EngineeringToolCatalogView(WireModel):
    tools: list[EngineeringToolView]
    default_policy: dict[str, EngineeringToolChoice]


class EventView(WireModel):
    id: str
    sequence: int
    type: str
    data: dict
    trace_id: str | None
    created_at: str


class ArtifactDownloadView(WireModel):
    url: str
    sha256: str
    name: str
