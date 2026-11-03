"""Existing workbench contracts for scoped sourcing and human-work services."""
import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy import select

from kleos.artifacts.service import Artifact
from kleos.capabilities.external import (
    ArtifactBinding,
    BOMItem,
    ExternalJob,
    ExternalRequest,
    WorkOrder,
    job_view,
)
from kleos.capabilities.human_work import EvidenceReading, EvidenceSubmission, HumanEvidence, ReviewRequest
from kleos.capabilities.human_work.evidence import EvidenceAttachment
from kleos.capabilities.human_work.rentahuman import HumanSearch
from kleos.contracts.models import DomainError, WireModel
from kleos.projects.store import Event, Receipt, fingerprint

QUERIES = {"external_jobs", "external_job", "external_job_events", "human_provider_status", "human_search",
           "human_bounties", "human_task_drafts"}
ACTIONS = {"create_human_draft", "human_draft_preview", "human_draft_link", "human_draft_sync",
           "delete_human_draft", "submit_human_evidence", "review_human_evidence"}
COMMANDS = {"project.requestQuotes", "project.placeOrders", "project.createPhysicalTasks", "project.acceptPhysical",
            "project.prepareKit", "project.acceptKit", "project.ship", "project.activate"}
BLOCKED_COMMANDS = {"project.placeOrders", "project.acceptPhysical", "project.acceptKit", "project.ship",
                    "project.activate"}


class HumanLocation(WireModel):
    city: str = Field(default="", max_length=100)
    country: str = Field(default="", pattern=r"^([A-Z]{2})?$", max_length=2)
    is_remote_allowed: bool = True


class HumanTask(WireModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=5000)
    completion_criteria: str = Field(min_length=10, max_length=2000)
    price: float = Field(gt=0, le=10000, allow_inf_nan=False)
    price_type: Literal["fixed"] = "fixed"
    estimated_hours: float = Field(ge=0.083, le=168, allow_inf_nan=False)
    category: Literal["labor-gigs", "computer-gigs", "creative-gigs"] = "labor-gigs"
    skills_needed: list[str] = Field(default_factory=list, max_length=8)
    evidence_types: list[Literal["text", "photo", "video", "link"]] = Field(min_length=1, max_length=4)
    location: HumanLocation = Field(default_factory=HumanLocation)

    @model_validator(mode="after")
    def bounded_scope(self):
        amount = Decimal(str(self.price))
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Specify a whole-cent worker budget")
        if any(not s.strip() or len(s) > 100 for s in self.skills_needed):
            raise ValueError("Skills must be bounded nonempty strings")
        if len(set(self.evidence_types)) != len(self.evidence_types):
            raise ValueError("Evidence types must be unique")
        if not self.location.is_remote_allowed and (not self.location.city or not self.location.country):
            raise ValueError("In-person work needs a city and country")
        return self


class HumanDraftRequest(WireModel):
    # Optional for focused tasks. A supplied release must be this project's accepted association.
    release_id: str | None = Field(default=None, min_length=1, max_length=36)
    expected_engineering_revision: int = Field(ge=1)
    task: HumanTask


class DraftAction(WireModel):
    draft_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class DraftLink(DraftAction):
    bounty_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")


class Measurement(WireModel):
    criterion_id: str = Field(min_length=1, max_length=100)
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=40)


class EvidenceFile(WireModel):
    criterion_id: str = Field(min_length=1, max_length=100)
    artifact_id: str = Field(min_length=1, max_length=36)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class HumanSubmission(WireModel):
    expected_engineering_revision: int = Field(ge=1)
    event_key: str = Field(min_length=1, max_length=150)
    specimen: dict
    measurements: list[Measurement] = Field(default_factory=list, max_length=100)
    files: list[EvidenceFile] = Field(default_factory=list, max_length=100)


class HumanReview(WireModel):
    expected_engineering_revision: int = Field(ge=1)
    submission_event_id: str = Field(min_length=1, max_length=36)
    decision: Literal["ACCEPTED", "REVISION_REQUESTED", "DISPUTED"]
    notes: str = Field(default="", max_length=4000)


class SpecimenProvenance(WireModel):
    id: str = Field(min_length=1, max_length=200)
    artifact_bindings: list[ArtifactBinding] = Field(min_length=1, max_length=100)
    # Each entry is a complete EvidenceReading except its measured value/unit, supplied separately.
    measurement_provenance: dict[str, dict] = Field(default_factory=dict)


def iso(value):
    return datetime.fromtimestamp(value, UTC).isoformat()


def cursor(value):
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= 100:
        raise DomainError("Invalid cursor", "invalid_request", 422)
    return value


def summary(job, project, now):
    engineering_revision = job.presentation.get("engineeringRevision", job.revision)
    return {"id": job.id, "agentInstanceId": None, "agentType": job.kind,
        "engineeringRevision": engineering_revision,
        "historical": job.presentation.get("_contextRevision", job.revision) != project.context_revision,
        "lifecycle": job.status, "state": job.status, "deadline": iso(job.deadline_at),
        "expired": now >= job.deadline_at, "inputHash": job.request_hash,
        "pluginId": job.provider, "pluginVersion": job.provider_version,
        "remainingAllowanceUsd": 0.0,
        "settlementStatus": "not_reserved" if job.effect_state == "none" else "unknown"}


def draft_view(view):
    presentation = view["presentation"]
    status = (view["status"] if view["status"] in {"cancelled", "expired"}
              else "linked" if view["externalId"] else "draft")
    observation = ({**view["observation"], "status": view["observation"].get("state", "unknown"),
                    "observedAt": iso(view["observedAt"]) if view["observedAt"] is not None else None,
                    "physicalAcceptance": False} if view["observation"] else {})
    return {"id": view["id"], "projectId": view["projectId"],
        "engineeringRevision": presentation["engineeringRevision"], "releaseId": presentation["releaseId"],
        "status": status, "draftSha256": presentation["draftSha256"], "task": presentation["task"],
        "preview": {}, "observation": observation, "bountyId": view["externalId"],
        "physicalAcceptance": False}


class ExternalFacade:
    def __init__(self, access_service, jobs, evidence=None, *, human_provider_id="rentahuman",
                 sourcing_provider_id="digikey"):
        self.access, self.jobs, self.projects = access_service, jobs, jobs.projects
        self.evidence = evidence or HumanEvidence(jobs)
        self.human_provider_id, self.sourcing_provider_id = human_provider_id, sourcing_provider_id

    def _member(self, actor):
        with self.projects.transaction() as session:
            self.access._member(session, actor)

    def _provider(self, actor):
        self._member(actor)
        provider = self.jobs.providers.get((actor.tenant_id, actor.user_id, self.human_provider_id))
        if provider is None or provider.kind != "human_work":
            raise DomainError("Human-work provider is not configured for this account", "provider_unavailable", 503)
        return provider

    async def _provider_read(self, provider, operation, *args):
        method = getattr(provider, operation, None)
        if not callable(method):
            raise DomainError("Provider operation is not installed", "operation_unavailable", 503)
        try:
            async with asyncio.timeout(20):
                return await method(*args)
        except DomainError:
            raise
        except Exception:
            raise DomainError("Provider lookup is unavailable", "provider_error", 502) from None

    def _bound(self, actor, project_id, job_id, *, write=False, draft=False):
        with self.projects.transaction() as session:
            job, project = self.jobs._load(session, actor, job_id, write=write)
            if job.project_id != project_id or (draft and job.presentation.get("type") != "human_draft"):
                raise DomainError("External job not found in this project", "not_found", 404)
            return job_view(job), project.revision

    async def query(self, actor, operation, params):
        if operation == "human_provider_status":
            self._member(actor)
            provider = self.jobs.providers.get((actor.tenant_id, actor.user_id, self.human_provider_id))
            configured = provider is not None and provider.kind == "human_work"
            account = configured and getattr(provider, "authenticated", False)
            return {"provider": "rentahuman.ai", "accountConfigured": bool(account), "livePostingEnabled": False,
                "capabilities": ["local_draft"] + (["public_search"] if configured else [])
                    + (["owned_bounties", "owned_observation"] if account else []),
                "setupUrl": "https://rentahuman.ai/account/api-keys", "limitations": [
                    "Purchasing, hiring, messaging, funding and physical acceptance are blocked.",
                    "Provider posting previews are not installed; local drafts can be copied for manual review.",
                    "No provider is configured." if not configured else "Live access has not been verified; provider observations remain candidates."]}
        if operation == "human_search":
            provider = self._provider(actor)
            filters = HumanSearch.model_validate({k: v for k, v in params.items() if k != "_transport"})
            return await self._provider_read(provider, "search", filters)
        if operation == "human_bounties":
            provider = self._provider(actor)
            return await self._provider_read(provider, "owned_bounties", params.get("cursor"))
        project_id = params.get("projectId")
        if operation == "external_job":
            with self.projects.transaction() as session:
                job, project = self.jobs._load(session, actor, params.get("jobId"))
                if job.project_id != project_id:
                    raise DomainError("External job not found in this project", "not_found", 404)
                return summary(job, project, self.jobs.clock())
        if operation in {"external_jobs", "human_task_drafts"}:
            with self.projects.transaction() as session:
                project = self.projects.authorized(session, actor, project_id)
                query = select(ExternalJob).where(ExternalJob.project_id == project_id,
                                                 ExternalJob.tenant_id == actor.tenant_id)
                after = cursor(params.get("cursor"))
                if after:
                    query = query.where(ExternalJob.id > after)
                if operation == "human_task_drafts":
                    query = query.where(ExternalJob.kind == "human_work",
                        ExternalJob.presentation["type"].as_string() == "human_draft")
                rows = list(session.scalars(query.order_by(ExternalJob.id).limit(51)))
                if operation == "human_task_drafts":
                    # This frozen list has no cursor field. Refuse to silently truncate.
                    if len(rows) > 50:
                        raise DomainError("Too many drafts for this view; use external jobs pagination", "page_limit", 422)
                    return {"drafts": [draft_view(job_view(row)) for row in rows]}
                return {"externalJobs": [summary(row, project, self.jobs.clock()) for row in rows[:50]],
                        "nextCursor": rows[49].id if len(rows) > 50 else None}
        if operation == "external_job_events":
            self._bound(actor, project_id, params.get("jobId"))
            after = cursor(params.get("cursor"))
            if after is not None and (not after.isdecimal() or int(after) < 0):
                raise DomainError("Invalid event cursor", "invalid_request", 422)
            with self.projects.transaction() as session:
                job, project = self.jobs._load(session, actor, params["jobId"])
                rows = list(session.scalars(select(Event).where(Event.project_id == project_id,
                    Event.data["payload"]["jobId"].as_string() == job.id,
                    Event.sequence > int(after or 0)).order_by(Event.sequence).limit(51)))
                return {"events": [{"id": f"{job.id}:{row.sequence}", "jobId": job.id,
                    "disposition": row.data["type"],
                    "engineeringRevision": job.presentation.get("engineeringRevision", job.revision),
                    "observedAt": row.data["createdAt"], "receivedAt": row.data["createdAt"],
                    "bodyHash": fingerprint(row.data)} for row in rows[:50]],
                    "nextCursor": str(rows[49].sequence) if len(rows) > 50 else None}
        raise DomainError("External query is unavailable", "capability_unavailable", 501)

    def _draft_request(self, actor, project_id, body):
        parsed = HumanDraftRequest.model_validate(body)
        with self.projects.transaction() as session:
            project = self.projects.authorized(session, actor, project_id, write=True)
            if project.data.get("engineeringRevision", 1) != parsed.expected_engineering_revision:
                raise DomainError("Engineering context changed", "revision_conflict")
            if parsed.release_id is not None and parsed.release_id != project.data.get("acceptedReleaseId"):
                raise DomainError("Release is not accepted in this project", "release_scope", 422)
            ids = list(project.data.get("sourceManifest", {}).values())
            rows = list(session.scalars(select(Artifact).where(Artifact.project_id == project.id,
                                                               Artifact.id.in_(ids)))) if ids else []
            task = parsed.task
            task_body = task.model_dump(mode="json", by_alias=True)
            presentation = {"type": "human_draft", "engineeringRevision": parsed.expected_engineering_revision,
                            "releaseId": parsed.release_id or "", "task": task_body}
            presentation["draftSha256"] = fingerprint(presentation)
            presentation["apiRequestHash"] = fingerprint({"projectId": project_id,
                "request": parsed.model_dump(mode="json", by_alias=True)})
            request = ExternalRequest(project_id=project.id, revision=project.revision, kind="human_work",
                provider=self.human_provider_id, objective=task.title,
                artifact_bindings=[ArtifactBinding(artifact_id=a.id, sha256=a.sha256, revision=a.revision) for a in rows],
                ceiling_minor_units=int(Decimal(str(task.price)) * 100), currency="USD",
                work_order=WorkOrder(title=task.title,
                    procedure=task.description + "\n\nCompletion criteria: " + task.completion_criteria,
                    specimen=f"project:{project.id}:engineering:{parsed.expected_engineering_revision}",
                    evidence_required=task.evidence_types, skill=", ".join(task.skills_needed)[:200],
                    city=task.location.city, country=task.location.country))
        return request, presentation

    async def action(self, actor, operation, params, body, idempotency_key=None):
        project_id = params.get("projectId")
        if operation == "create_human_draft":
            parsed = HumanDraftRequest.model_validate(body)
            incoming = fingerprint({"projectId": project_id, "request": parsed.model_dump(mode="json", by_alias=True)})
            with self.projects.transaction() as session:
                self.projects.authorized(session, actor, project_id, write=True)
                prior = session.scalar(select(Receipt).where(Receipt.tenant_id == actor.tenant_id,
                    Receipt.user_id == actor.user_id, Receipt.key == idempotency_key))
                if prior:
                    saved = prior.result.get("job", {})
                    if saved.get("presentation", {}).get("apiRequestHash") != incoming:
                        raise DomainError("Idempotency key was used for another request", "idempotency_conflict")
                    return draft_view(saved)
            request, presentation = self._draft_request(actor, project_id, parsed)
            result = self.jobs.create(actor, request, idempotency_key, presentation=presentation)
            return draft_view(result["job"])
        if operation in {"submit_human_evidence", "review_human_evidence"}:
            view, _ = self._bound(actor, project_id, params.get("jobId"), write=True)
            return self._evidence_action(actor, operation, view, body, idempotency_key)
        if operation not in ACTIONS:
            raise DomainError("External operation is unavailable", "capability_unavailable", 501)
        job_id = params.get("ident")
        view, _ = self._bound(actor, project_id, job_id, write=True, draft=True)
        if operation == "delete_human_draft":
            WireModel.model_validate(body)
            result = self.jobs.cancel(actor, job_id, idempotency_key or "cancel-human-" + job_id)
            return draft_view(result["job"])
        parsed = (DraftLink if operation == "human_draft_link" else DraftAction).model_validate(body)
        if parsed.draft_sha256 != view["presentation"]["draftSha256"]:
            raise DomainError("Saved task changed; review its current contents", "draft_conflict")
        if operation == "human_draft_preview":
            raise DomainError("Provider posting previews are unavailable; the saved local draft is unchanged",
                              "operation_unavailable", 501)
        if operation == "human_draft_link":
            key = idempotency_key or "human-link-" + fingerprint({"job": job_id, "bounty": parsed.bounty_id})
            result = await self.jobs.observe_existing(actor, job_id, parsed.bounty_id, key)
            return draft_view(result["job"])
        if operation == "human_draft_sync":
            return draft_view(await self.jobs.poll(actor, job_id))
        raise DomainError("External operation is unavailable", "capability_unavailable", 501)

    def _evidence_action(self, actor, operation, view, body, key):
        if view["kind"] != "human_work":
            raise DomainError("This job does not accept human evidence", "invalid_request", 422)
        engineering_revision = view["presentation"].get("engineeringRevision", view["revision"])
        if operation == "review_human_evidence":
            parsed = HumanReview.model_validate(body)
            if parsed.expected_engineering_revision != engineering_revision:
                raise DomainError("Evidence revision changed", "revision_conflict")
            decisions = {"ACCEPTED": "accept", "REVISION_REQUESTED": "request_changes", "DISPUTED": "reject"}
            result = self.evidence.review(actor, view["id"], parsed.submission_event_id,
                ReviewRequest(decision=decisions[parsed.decision], rationale=parsed.notes), key)
            return {"jobId": view["id"], "reviewId": result["commandId"], "decision": parsed.decision,
                    "physicalAcceptance": False}
        parsed = HumanSubmission.model_validate(body)
        if parsed.expected_engineering_revision != engineering_revision:
            raise DomainError("Evidence revision changed", "revision_conflict")
        specimen = SpecimenProvenance.model_validate(parsed.specimen)
        attachments = []
        with self.projects.transaction() as session:
            self.projects.authorized(session, actor, view["projectId"], write=True)
            for item in parsed.files:
                row = session.get(Artifact, item.artifact_id)
                if row is None or row.project_id != view["projectId"] or row.sha256 != item.sha256:
                    raise DomainError("Evidence artifact provenance does not match", "artifact_scope", 422)
                attachments.append(EvidenceAttachment(artifact_id=row.id, sha256=row.sha256,
                    revision=row.revision, requirement=item.criterion_id))
        readings = []
        for reading in parsed.measurements:
            provenance = specimen.measurement_provenance.get(reading.criterion_id, {})
            readings.append(EvidenceReading.model_validate({**provenance, "name": reading.criterion_id,
                                                           "unit": reading.unit, "value": reading.value}))
        result = self.evidence.submit(actor, view["id"], EvidenceSubmission(event_key=parsed.event_key, revision=view["revision"],
            specimen=specimen.id, specimen_bindings=specimen.artifact_bindings, attachments=attachments,
            readings=readings, synthetic=True), key)
        return {"jobId": view["id"], "eventId": result["evidence"]["id"], "disposition": "submitted",
                "evidenceKind": "synthetic", "physicalAcceptance": False}

    async def command(self, actor, body, idempotency_key):
        kind, payload = body.get("type"), body.get("payload", {})
        project_id = payload.get("projectId")
        with self.projects.transaction() as session:
            project = self.projects.authorized(session, actor, project_id, write=True)
            revision = body.get("expectedRevision")
            bom = payload.get("bom", project.data.get("interfaces", {}).get("bom"))
        if kind in BLOCKED_COMMANDS:
            raise DomainError("Physical purchasing, acceptance, shipping and activation are blocked by server policy",
                              "physical_policy", 403)
        if kind == "project.requestQuotes" and bom:
            request = ExternalRequest(project_id=project_id, revision=revision, kind="sourcing",
                provider=self.sourcing_provider_id, objective="Observe component pricing and availability",
                bom=[BOMItem.model_validate(item) for item in bom], currency="USD")
            if (actor.tenant_id, actor.user_id, self.sourcing_provider_id) not in self.jobs.providers:
                raise DomainError("Catalog provider is not configured", "provider_unavailable", 503)
            receipt = self.jobs.create(actor, request, idempotency_key)
            await self.jobs.quote(actor, receipt["job"]["id"])
            return {"commandId": receipt["commandId"], "projectId": project_id, "revision": revision}
        objectives = {
            "project.requestQuotes": "Prepare component sourcing and obtain read-only catalog observations from project context.",
            "project.createPhysicalTasks": "Prepare a human work order with the specimen, procedure and measurable evidence criteria. Do not hire or pay anyone.",
            "project.prepareKit": "Prepare a digital kit manifest and assembly instructions from current project artifacts. Do not order or ship anything.",
        }
        if kind not in objectives:
            raise DomainError("External command is unavailable", "capability_unavailable", 501)
        return self.projects.submit(actor, project_id, objectives[kind], revision, idempotency_key)
