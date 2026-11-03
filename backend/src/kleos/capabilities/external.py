"""Scoped external work. An observation is never proof of physical acceptance."""

import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from typing import Literal, Protocol
from uuid import uuid4

import httpx
from pydantic import Field, model_validator
from sqlalchemy import JSON, Float, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from kleos.contracts.models import Actor, DomainError, WireModel
from kleos.projects.store import Base, Membership, fingerprint


class ArtifactBinding(WireModel):
    artifact_id: str = Field(min_length=1, max_length=100)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision: int = Field(ge=1)


class BOMItem(WireModel):
    reference: str = Field(min_length=1, max_length=100)
    supplier_part: str = Field(min_length=1, max_length=120)
    quantity: int = Field(ge=1, le=100000)


class ReadingRequirement(WireModel):
    name: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=30)
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Reading range is reversed")
        return self


class WorkOrder(WireModel):
    title: str = Field(min_length=1, max_length=200)
    procedure: str = Field(min_length=1, max_length=12000)
    evidence_required: list[str] = Field(min_length=1, max_length=30)
    specimen: str = Field(min_length=1, max_length=200)
    readings: list[ReadingRequirement] = Field(default_factory=list, max_length=100)
    skill: str = Field(default="", max_length=200)
    city: str = Field(default="", max_length=100)
    country: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def unique_readings(self):
        if len({r.name for r in self.readings}) != len(self.readings):
            raise ValueError("Reading names must be unique")
        if any(not item.strip() or len(item) > 500 for item in self.evidence_required):
            raise ValueError("Evidence requirements must be bounded and nonempty")
        if len(set(self.evidence_required)) != len(self.evidence_required):
            raise ValueError("Evidence requirements must be unique")
        return self


class ExternalRequest(WireModel):
    project_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)
    kind: Literal["sourcing", "human_work"]
    provider: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=12000)
    artifact_bindings: list[ArtifactBinding] = Field(default_factory=list, max_length=100)
    bom: list[BOMItem] = Field(default_factory=list, max_length=100)
    work_order: WorkOrder | None = None
    ceiling_minor_units: int = Field(default=0, ge=0, le=100000000)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    timeout_seconds: int = Field(default=86400, ge=60, le=604800)

    @model_validator(mode="after")
    def context_for_kind(self):
        if self.kind == "sourcing" and (not self.bom or self.work_order is not None):
            raise ValueError("Sourcing requires a BOM and no work order")
        if self.kind == "human_work" and (self.work_order is None or self.bom):
            raise ValueError("Human work requires a work order and no BOM")
        if len({b.reference for b in self.bom}) != len(self.bom):
            raise ValueError("BOM references must be unique")
        if len({a.artifact_id for a in self.artifact_bindings}) != len(self.artifact_bindings):
            raise ValueError("Artifact bindings must be unique")
        return self


class ProviderObservation(WireModel):
    state: Literal["unknown", "waiting", "completed", "failed", "cancelled"] = "unknown"
    external_id: str | None = Field(default=None, min_length=1, max_length=160)
    details: dict = Field(default_factory=dict)
    retry_after_seconds: int = Field(default=60, ge=10, le=3600)
    ownership_verified: bool = False


class ExternalProvider(Protocol):
    id: str
    version: str
    kind: str
    effect_capable: bool

    async def discover(self, request: ExternalRequest) -> dict: ...
    async def quote(self, request: ExternalRequest) -> dict: ...
    async def draft(self, request: ExternalRequest) -> dict: ...
    async def status(self, external_id: str) -> ProviderObservation: ...
    async def reconcile(self, idempotency_key: str, external_id: str | None) -> ProviderObservation: ...
    async def submit(self, request: ExternalRequest, idempotency_key: str) -> ProviderObservation: ...


class ExternalJob(Base):
    __tablename__ = "kleos2_external_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    creator_id: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(100))
    provider_version: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    request: Mapped[dict] = mapped_column(JSON)
    request_hash: Mapped[str] = mapped_column(String(64))
    action_key: Mapped[str] = mapped_column(String(64), unique=True)
    approval: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # This durable claim is written BEFORE external I/O. A crash can only reconcile it.
    effect_state: Mapped[str] = mapped_column(String(20), default="none")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    deadline_at: Mapped[float] = mapped_column(Float)
    next_poll_at: Mapped[float] = mapped_column(Float, default=0)
    observed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    observation: Mapped[dict] = mapped_column(JSON, default=dict)
    preparations: Mapped[dict] = mapped_column(JSON, default=dict)
    presentation: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")


def job_view(job):
    return {
        "id": job.id, "projectId": job.project_id, "revision": job.revision,
        "kind": job.kind, "provider": job.provider, "providerVersion": job.provider_version,
        "status": job.status, "request": job.request, "requestHash": job.request_hash,
        "approval": job.approval, "externalId": job.external_id, "effectState": job.effect_state,
        "deadlineAt": job.deadline_at, "nextPollAt": job.next_poll_at,
        "observedAt": job.observed_at, "observation": job.observation,
        "preparations": job.preparations,
        "presentation": job.presentation,
        "physicalAcceptance": False,
    }


def bind_artifacts(session, project_id, bindings):
    from kleos.artifacts.service import Artifact

    for binding in bindings:
        row = session.get(Artifact, binding.artifact_id)
        if (row is None or row.project_id != project_id or row.sha256 != binding.sha256
                or row.revision != binding.revision):
            raise DomainError("Artifact provenance does not match this project", "artifact_scope", 422)


class ExternalJobs:
    def __init__(self, projects, providers=None, *, installation_pins=None, clock=time.time,
                 effect_authorizer: Callable[[Actor, ExternalRequest], bool] | None = None):
        self.projects, self.providers, self.clock = projects, dict(providers or {}), clock
        self.installation_pins = dict(installation_pins or {})
        if any(key not in self.providers or not isinstance(value, str) or len(value) != 64
               or any(character not in "0123456789abcdef" for character in value)
               for key, value in self.installation_pins.items()):
            raise ValueError("Installation pins must identify configured provider accounts")
        if any(not isinstance(key, tuple) or len(key) != 3 for key in self.providers):
            raise ValueError("Provider installations must be keyed by (tenant_id, user_id, provider_id)")
        # Only trusted server configuration can provide this hook. No production policy currently does.
        self.effect_authorizer = effect_authorizer

    def installation_pin(self, actor, provider, *, creator_id=None):
        return self.installation_pins.get((actor.tenant_id, creator_id or actor.user_id, provider))

    def _assert_installation(self, job, actor):
        current = self.installation_pin(actor, job.provider, creator_id=job.creator_id)
        if (job.presentation or {}).get("_installationHash") != current:
            raise DomainError("Pinned external account installation changed", "provider_changed")

    def _load(self, session, actor, job_id, *, write=False, current=False):
        job = session.scalar(select(ExternalJob).where(
            ExternalJob.id == job_id, ExternalJob.tenant_id == actor.tenant_id).with_for_update())
        if job is None:
            raise DomainError("External job not found", "not_found", 404)
        project = self.projects.authorized(session, actor, job.project_id, write=write)
        if current and project.context_revision != (job.presentation or {}).get("_contextRevision", job.revision):
            raise DomainError("Project changed; prepare a new scoped job", "revision_conflict")
        if current:
            self._assert_installation(job, actor)
        return job, project

    def _provider(self, job, actor):
        self._assert_installation(job, actor)
        provider = self.providers.get((actor.tenant_id, job.creator_id, job.provider))
        if provider is None:
            raise DomainError("No external provider is configured for this job", "provider_unavailable", 503)
        if provider.version != job.provider_version or provider.kind != job.kind:
            raise DomainError("Pinned external provider changed", "provider_changed")
        return provider

    def create(self, actor: Actor, request: ExternalRequest, key: str, *, presentation=None):
        body = request.model_dump(mode="json", by_alias=True)
        presentation = bounded_json(presentation or {})

        def operation(session):
            project = self.projects.authorized(session, actor, request.project_id, write=True)
            if project.revision != request.revision:
                raise DomainError("Project revision changed", "revision_conflict")
            bind_artifacts(session, project.id, request.artifact_bindings)
            provider = self.providers.get((actor.tenant_id, actor.user_id, request.provider))
            if provider is not None and provider.kind != request.kind:
                raise DomainError("Provider does not support this work", "provider_mismatch", 422)
            job_id, digest = str(uuid4()), fingerprint(body)
            job = ExternalJob(id=job_id, tenant_id=actor.tenant_id, project_id=project.id,
                revision=request.revision, creator_id=actor.user_id, kind=request.kind,
                provider=request.provider, provider_version=provider.version if provider else "unconfigured",
                status="draft" if provider else "unavailable", request=body, request_hash=digest,
                action_key=hashlib.sha256(f"{actor.tenant_id}:{job_id}:{digest}".encode()).hexdigest(),
                effect_state="none", generation=0, deadline_at=self.clock() + request.timeout_seconds,
                next_poll_at=0, observation={}, preparations={}, presentation={**presentation, "_contextRevision": project.context_revision,
                    "_installationHash": self.installation_pin(actor, request.provider)})
            session.add(job)
            self.projects._emit(session, project, "external.created", {"jobId": job.id, "kind": job.kind})
            return {"projectId": project.id, "job": job_view(job)}

        return self.projects._command(actor, key, {"operation": "external.create", **body,
                                                  "presentation": presentation}, operation)

    def get(self, actor, job_id):
        with self.projects.transaction() as session:
            return job_view(self._load(session, actor, job_id)[0])

    def list(self, actor, project_id):
        with self.projects.transaction() as session:
            self.projects.authorized(session, actor, project_id)
            return [job_view(job) for job in session.scalars(select(ExternalJob).where(
                ExternalJob.project_id == project_id, ExternalJob.tenant_id == actor.tenant_id)
                .order_by(ExternalJob.id).limit(100))]

    async def prepare(self, actor, job_id, operation: Literal["discover", "quote", "draft"]):
        if operation not in {"discover", "quote", "draft"}:
            raise DomainError("Unsupported preparation operation", "invalid_request", 422)
        with self.projects.transaction() as session:
            job, _ = self._load(session, actor, job_id, write=True, current=True)
            provider = self._provider(job, actor)
            request, generation = ExternalRequest.model_validate(job.request), job.generation
            if job.status in {"cancelled", "expired"}:
                raise DomainError("Job is no longer active", "job_closed")
        try:
            async with asyncio.timeout(20):
                result = await getattr(provider, operation)(request)
            result = bounded_json(result)
        except DomainError:
            raise
        except Exception:
            raise DomainError("External preparation failed", "provider_error", 502) from None
        with self.projects.transaction() as session:
            job, project = self._load(session, actor, job_id, write=True, current=True)
            if job.generation != generation:
                raise DomainError("Job changed during preparation", "job_changed")
            job.preparations = {**job.preparations, operation: {
                "result": result, "observedAt": self.clock(), "requestHash": job.request_hash}}
            self.projects._emit(session, project, "external.prepared", {"jobId": job.id, "operation": operation})
        return {"jobId": job_id, "operation": operation, "result": result, "committed": False}

    async def discover(self, actor, job_id):
        return await self.prepare(actor, job_id, "discover")

    async def quote(self, actor, job_id):
        return await self.prepare(actor, job_id, "quote")

    async def draft(self, actor, job_id):
        return await self.prepare(actor, job_id, "draft")

    async def observe_existing(self, actor, job_id, external_id, key):
        """Link existing work only for its creator-account owner; no implicit account delegation."""
        if not isinstance(external_id, str) or not 1 <= len(external_id) <= 160:
            raise DomainError("Invalid external job identity", "invalid_request", 422)
        with self.projects.transaction() as session:
            job, project = self._load(session, actor, job_id, write=True, current=True)
            if session.get(Membership, (project.id, actor.user_id)).role != "owner" or job.creator_id != actor.user_id:
                raise DomainError("Only the project owner using their own provider account can link external work", "forbidden", 403)
            provider, generation = self._provider(job, actor), job.generation
        try:
            async with asyncio.timeout(20):
                observation = ProviderObservation.model_validate(await provider.status(external_id))
        except DomainError:
            raise
        except Exception:
            raise DomainError("External ownership could not be verified", "provider_error", 502) from None
        if observation.external_id != external_id or not observation.ownership_verified:
            raise DomainError("Provider has not verified ownership of this job", "external_ownership", 403)

        def operation(session):
            job, project = self._load(session, actor, job_id, write=True, current=True)
            if session.get(Membership, (project.id, actor.user_id)).role != "owner" or job.creator_id != actor.user_id:
                raise DomainError("Only the project owner using their own provider account can link external work", "forbidden", 403)
            if job.generation != generation or job.effect_state != "none" or job.status != "draft":
                raise DomainError("Job changed or already has an external action", "job_changed")
            job.external_id, job.effect_state, job.status = external_id, "observed", "waiting"
            job.generation += 1
            job.observation = bounded_json(observation.model_dump(mode="json", by_alias=True))
            job.observed_at, job.next_poll_at = self.clock(), self.clock() + observation.retry_after_seconds
            self.projects._emit(session, project, "external.linked", {"jobId": job.id,
                "linkedBy": actor.user_id, "scopeMatch": "operator_supplied", "physicalAcceptance": False})
            return {"projectId": project.id, "job": job_view(job)}
        return self.projects._command(actor, key, {"operation": "external.link", "jobId": job_id,
                                                 "externalId": external_id}, operation)

    def approve(self, actor, job_id, key):
        def operation(session):
            job, project = self._load(session, actor, job_id, write=True, current=True)
            member = session.get(Membership, (project.id, actor.user_id))
            if member.role != "owner":
                raise DomainError("Project owner approval is required", "forbidden", 403)
            self._provider(job, actor)
            if job.status not in {"draft", "approved"} or self.clock() >= job.deadline_at:
                raise DomainError("Job cannot be approved", "job_closed")
            job.approval = {"actorId": actor.user_id, "requestHash": job.request_hash,
                            "approvedAt": self.clock(), "revision": job.revision}
            job.status, job.generation = "approved", job.generation + 1
            self.projects._emit(session, project, "external.approved", {"jobId": job.id})
            return {"projectId": project.id, "job": job_view(job)}
        return self.projects._command(actor, key, {"operation": "external.approve", "jobId": job_id}, operation)

    async def dispatch(self, actor, job_id):
        with self.projects.transaction() as session:
            job, project = self._load(session, actor, job_id, write=True, current=True)
            provider, request = self._provider(job, actor), ExternalRequest.model_validate(job.request)
            if job.effect_state != "none":
                # Includes claimed after crash and uncertain after timeout. Never re-submit.
                return job_view(job)
            if job.status != "approved" or not job.approval or job.approval["requestHash"] != job.request_hash:
                raise DomainError("Approve the exact prepared job first", "approval_required", 403)
            if self.clock() >= job.deadline_at:
                raise DomainError("Job deadline expired", "job_expired")
            if (self.effect_authorizer is None or not provider.effect_capable
                    or not self.effect_authorizer(actor, request)):
                raise DomainError("Purchasing and paid hiring are blocked by server policy", "physical_policy", 403)
            bind_artifacts(session, project.id, request.artifact_bindings)
            job.status, job.effect_state = "dispatching", "claimed"
            job.generation += 1
            generation, action_key = job.generation, job.action_key
            self.projects._emit(session, project, "external.dispatch_claimed", {"jobId": job.id})
        try:
            async with asyncio.timeout(20):
                observation = ProviderObservation.model_validate(await provider.submit(request, action_key))
            bounded_json(observation.model_dump(mode="json"))
        except Exception:
            with self.projects.transaction() as session:
                job, project = self._load(session, actor, job_id, write=True)
                if job.generation == generation:
                    job.status, job.effect_state = "uncertain", "uncertain"
                    self.projects._emit(session, project, "external.uncertain", {"jobId": job.id})
                return job_view(job)
        return self._record(actor, job_id, generation, observation, submitted=True)

    def _record(self, actor, job_id, generation, observation, *, submitted=False):
        with self.projects.transaction() as session:
            job, project = self._load(session, actor, job_id, write=True)
            self._assert_installation(job, actor)
            closed = job.status in {"cancelled", "expired"}
            if job.generation != generation and not (submitted and closed):
                return job_view(job)
            if job.external_id and observation.external_id and job.external_id != observation.external_id:
                raise DomainError("Provider job identity changed", "provider_identity")
            if observation.external_id:
                job.external_id = observation.external_id
            job.observation = bounded_json(observation.model_dump(mode="json", by_alias=True))
            job.observed_at, job.next_poll_at = self.clock(), self.clock() + observation.retry_after_seconds
            if job.external_id:
                job.effect_state = "observed"
                if not closed:
                    job.status = "waiting" if observation.state == "unknown" else observation.state
            elif submitted or job.effect_state in {"claimed", "uncertain"}:
                job.effect_state = "uncertain"
                if not closed:
                    job.status = "uncertain"
            self.projects._emit(session, project, "external.observed", {"jobId": job.id, "status": job.status})
            return job_view(job)

    async def poll(self, actor, job_id, *, reconcile=False):
        with self.projects.transaction() as session:
            job, project = self._load(session, actor, job_id, write=True)
            provider = self._provider(job, actor)
            if job.status in {"completed", "failed"} or (job.status in {"cancelled", "expired"} and not reconcile):
                return job_view(job)
            if self.clock() >= job.deadline_at and not reconcile:
                job.status = "expired"
                self.projects._emit(session, project, "external.expired", {"jobId": job.id})
                return job_view(job)
            if self.clock() < job.next_poll_at:
                return job_view(job)
            if job.effect_state == "none":
                raise DomainError("No external action has been dispatched", "not_dispatched")
            if not reconcile and not job.external_id:
                raise DomainError("Uncertain action needs read-only reconciliation", "reconciliation_required")
            generation, external_id, action_key = job.generation, job.external_id, job.action_key
            # Coalesce concurrent polls and impose a durable bound even when provider errors.
            job.next_poll_at = self.clock() + 60
        try:
            async with asyncio.timeout(20):
                observation = (await provider.reconcile(action_key, external_id) if reconcile
                               else await provider.status(external_id))
            observation = ProviderObservation.model_validate(observation)
            bounded_json(observation.model_dump(mode="json"))
        except Exception:
            raise DomainError("External status is unavailable; previous observation retained",
                              "provider_error", 502) from None
        return self._record(actor, job_id, generation, observation)

    async def reconcile(self, actor, job_id):
        return await self.poll(actor, job_id, reconcile=True)

    def cancel(self, actor, job_id, key):
        def operation(session):
            job, project = self._load(session, actor, job_id, write=True)
            job.status, job.generation = "cancelled", job.generation + 1
            self.projects._emit(session, project, "external.cancelled", {"jobId": job.id,
                "externalCancellationConfirmed": False})
            return {"projectId": project.id, "job": job_view(job), "externalCancellationConfirmed": False}
        return self.projects._command(actor, key, {"operation": "external.cancel", "jobId": job_id}, operation)


def bounded_json(value):
    if not isinstance(value, dict):
        raise DomainError("Malformed external response", "provider_response", 502)
    try:
        content = json.dumps(value, allow_nan=False).encode()
    except (ValueError, TypeError):
        raise DomainError("Malformed external response", "provider_response", 502) from None
    if len(content) > 256000:
        raise DomainError("External response exceeds limit", "provider_response", 502)
    return value


class ReadOnlyHTTP:
    """A fixed origin, GET-only transport. No redirects, ambient credentials or retry."""
    def __init__(self, origin: str, headers=None, *, transport=None):
        self._client = httpx.AsyncClient(base_url=origin, headers=headers, timeout=10,
            follow_redirects=False, trust_env=False, transport=transport)

    async def get(self, path, params=None):
        try:
            async with asyncio.timeout(15):
                return await self._get(path, params)
        except TimeoutError:
            raise DomainError("External lookup timed out", "provider_error", 502) from None

    async def _get(self, path, params):
        try:
            async with self._client.stream("GET", path, params=params) as response:
                if response.status_code != 200:
                    raise DomainError("External service returned an error", "provider_error", 502)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 256000:
                        raise DomainError("External response exceeds limit", "provider_response", 502)
                result = bounded_json(json.loads(data))
                if result.get("success") is False:
                    raise DomainError("External service rejected the lookup", "provider_error", 502)
                return result
        except DomainError:
            raise
        except Exception:
            raise DomainError("External lookup failed", "provider_error", 502) from None

    async def close(self):
        await self._client.aclose()


class ReadOnlyProvider:
    effect_capable = False

    async def draft(self, request):
        return {"scope": request.model_dump(mode="json", by_alias=True), "submitted": False,
                "physicalAcceptance": False}

    async def submit(self, request, idempotency_key):
        raise DomainError("Provider permits read-only operations", "physical_policy", 403)

    async def status(self, external_id):
        raise DomainError("Order status is not configured for this provider", "operation_unavailable", 503)

    async def reconcile(self, idempotency_key, external_id):
        if external_id:
            return await self.status(external_id)
        return ProviderObservation(details={"reason": "No verified provider identity; no action repeated"})
