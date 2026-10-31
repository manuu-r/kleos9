"""Consent-aware learning records and bounded, explicitly dispatched Modal compute."""

import hashlib
import json
import math
from contextlib import nullcontext
from datetime import UTC, datetime

from opentelemetry import trace
from sqlalchemy import func, select

from .contracts import DomainError
from .db import (
    Artifact,
    Consent,
    DailyBudget,
    Dataset,
    ExternalIntent,
    Invocation,
    Outbox,
    Project,
    Release,
    TrainedModel,
    uid,
)
from .domain import emit, get_project, lock_platform
from .telemetry import redact, tracer
from .training_consent import (
    cancel_undispatched_training,
    dataset_binding,
    lock_training_organization,
    request_training_cancel,
    training_inputs_current,
)


class ComputeLedger:
    """Persist paid intent before dispatch. An ambiguous call is never automatically submitted twice."""
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def begin(self, principal, project_id, operation_id, kind, payload, reserve=None):
        principal.require_write()
        if not operation_id or len(operation_id) > 64:
            raise DomainError("Compute operations need a stable ID of at most 64 characters", 422)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        operation = f"{kind}:{operation_id}"
        reserve = reserve if reserve is not None else self.settings.invocation_reservation_usd
        if not isinstance(reserve, (int, float)) or not math.isfinite(reserve) or reserve <= 0:
            raise DomainError("A positive compute allowance reservation is required", 422)
        with self.db.transaction() as session:
            if kind == "training":
                # Consent commands take this same lock, so admission observes a
                # complete consent change rather than a previously materialized copy.
                lock_training_organization(session, principal.tenant_id)
            lock_platform(session)
            project = get_project(session, project_id, principal, write=True)
            if payload.get("revision", project.engineering_revision) != project.engineering_revision:
                raise DomainError("Compute inputs belong to an outdated engineering revision", 409, "stale_revision")
            if kind in {"modal", "training"} and payload.get("modelId"):
                model = session.get(TrainedModel, payload["modelId"])
                dataset = session.get(Dataset, model.dataset_id) if model else None
                expected = "promoted" if kind == "modal" else "candidate"
                if not model or not dataset or dataset.tenant_id != principal.tenant_id or model.status != expected or (
                        kind == "training" and dataset.status != "ready"):
                    raise DomainError("Model or training dataset is no longer eligible for dispatch", 409)
                if kind == "training":
                    LearningService(self.db, self.settings, None)._dataset(session, principal, dataset.id)
                    if payload.get("datasetBinding") != dataset_binding(dataset):
                        raise DomainError("Training dataset consent or provenance changed", 409, "consent_changed")
            existing = session.scalar(select(ExternalIntent).where(
                ExternalIntent.project_id == project_id, ExternalIntent.operation == operation,
                ExternalIntent.revision == project.engineering_revision))
            if existing:
                if existing.payload.get("fingerprint") != fingerprint:
                    raise DomainError("Compute operation ID was reused with different input", 409)
                if existing.status in {"reserved", "uncertain"}:
                    raise DomainError("Previous dispatch has an uncertain outcome; reconcile before retrying", 409,
                                      "compute_uncertain")
                return existing.id, False
            if not self.settings.unlimited_spending and (self.settings.daily_budget_usd == 0 or project.budget_usd <= 0):
                raise DomainError("Configure project and daily allowances before paid compute", 409,
                                  "budget_unconfigured")
            if kind == "embedding":
                hosted = session.scalar(select(func.count()).select_from(Invocation).where(
                    Invocation.status == "pending"))
                embedding = session.scalar(select(func.count()).select_from(ExternalIntent).where(
                    ExternalIntent.mode == "compute", ExternalIntent.operation.like("embedding:%"),
                    ExternalIntent.status.in_(["reserved", "submitted"])))
                if hosted + embedding >= self.settings.max_model_calls:
                    raise DomainError("Hosted inference capacity is full; retry after queued work progresses", 429,
                                      "model_capacity")
            if kind in {"modal", "training"}:
                active = session.scalar(select(func.count()).select_from(ExternalIntent).where(
                    ExternalIntent.mode == "compute", ExternalIntent.operation.like(f"{kind}:%"),
                    ExternalIntent.status.in_(["reserved", "submitted", "uncertain"])))
                if active >= (2 if kind == "modal" else 1):
                    raise DomainError("Custom compute capacity is full; retry after current jobs finish", 429,
                                      "compute_capacity")
            day = datetime.now(UTC).date().isoformat()
            daily = session.get(DailyBudget, day)
            if not daily:
                daily = DailyBudget(day=day, spent=0, reserved=0)
                session.add(daily)
            if not self.settings.unlimited_spending and (
                    project.spent_usd + project.reserved_usd + reserve > project.budget_usd or (
                    self.settings.daily_budget_usd is not None
                    and daily.spent + daily.reserved + reserve > self.settings.daily_budget_usd)):
                raise DomainError("Compute allowance exhausted", 409, "budget_exhausted")
            intent = ExternalIntent(
                id=uid(), tenant_id=principal.tenant_id, project_id=project_id,
                operation=operation, revision=project.engineering_revision, mode="compute", status="reserved",
                payload={"fingerprint": fingerprint, "input": payload, "reservedUsd": reserve, "day": day,
                         "traceId": format(trace.get_current_span().get_span_context().trace_id, "032x")},
            )
            project.reserved_usd += reserve
            daily.reserved += reserve
            session.add(intent)
            emit(session, project, "compute.reserved", {"intentId": intent.id, "kind": kind, "reservedUsd": reserve})
            return intent.id, True

    def view(self, principal, intent_id):
        with self.db.transaction() as session:
            intent = session.scalar(select(ExternalIntent).where(
                ExternalIntent.id == intent_id, ExternalIntent.tenant_id == principal.tenant_id,
                ExternalIntent.mode == "compute"))
            if not intent:
                raise DomainError("Compute operation not found", 404)
            get_project(session, intent.project_id, principal)
            return {"id": intent.id, "status": intent.status, "projectId": intent.project_id,
                    "revision": intent.revision, "receipt": intent.receipt, "payload": intent.payload}

    def submitted(self, intent_id, external_id):
        with self.db.transaction() as session:
            tenant_id = session.scalar(select(ExternalIntent.tenant_id).where(ExternalIntent.id == intent_id))
            lock_training_organization(session, tenant_id)
            intent = session.scalar(select(ExternalIntent).where(ExternalIntent.id == intent_id).with_for_update())
            project = session.get(Project, intent.project_id)
            dataset_id = intent.payload.get("input", {}).get("datasetId")
            dataset = session.get(Dataset, dataset_id) if dataset_id else None
            deleted_dataset_project = dataset.manifest.get("excludedByProjectDeletion") if dataset else None
            if project.deleted_at or deleted_dataset_project:
                intent.receipt = {**intent.receipt, "externalId": external_id, "cancelRequired": True}
                if not session.scalar(select(Outbox.id).where(Outbox.key == f"delete-late-compute:{intent_id}")):
                    session.add(Outbox(key=f"delete-late-compute:{intent_id}", kind="delete_project",
                                       payload={"projectId": project.id if project.deleted_at else deleted_dataset_project}))
                return
            if intent.operation.startswith("training:") and (
                    intent.receipt.get("cancelRequired") or not training_inputs_current(session, intent)):
                intent.status = "submitted"
                intent.receipt = {**intent.receipt, "externalId": external_id}
                request_training_cancel(session, intent, "training_inputs_changed")
                return
            if intent.status not in {"reserved", "submitted"}:
                raise DomainError("Compute operation can no longer be submitted", 409)
            if intent.receipt.get("externalId") not in {None, external_id}:
                raise DomainError("Compute operation already has a different external job", 409)
            intent.status = "submitted"
            intent.receipt = {**intent.receipt, "externalId": external_id}

    def claim_training_dispatch(self, principal, intent_id):
        rejected = False
        with self.db.transaction() as session:
            lock_training_organization(session, principal.tenant_id)
            intent = session.scalar(select(ExternalIntent).where(
                ExternalIntent.id == intent_id, ExternalIntent.tenant_id == principal.tenant_id).with_for_update())
            if not intent or intent.status != "reserved":
                raise DomainError("Training consent or inputs changed before dispatch", 409, "consent_changed")
            if intent.receipt.get("dispatchClaimedAt"):
                raise DomainError("Training dispatch already has an uncertain outcome", 409, "compute_uncertain")
            model = session.get(TrainedModel, intent.payload["input"]["modelId"])
            if not training_inputs_current(session, intent) or not model or model.status != "candidate":
                cancel_undispatched_training(session, intent)
                rejected = True
            else:
                intent.receipt = {**intent.receipt, "dispatchClaimedAt": datetime.now(UTC).isoformat()}
        if rejected:
            raise DomainError("Training consent or inputs changed before dispatch", 409, "consent_changed")

    def uncertain(self, intent_id):
        with self.db.transaction() as session:
            intent = session.scalar(select(ExternalIntent).where(ExternalIntent.id == intent_id).with_for_update())
            if intent.status == "reserved":
                intent.status = "uncertain"
                # Retain the reservation until an operator reconciles provider usage.

    def finish(self, intent_id, receipt, status="succeeded", _session=None):
        if status not in {"succeeded", "failed"}:
            raise ValueError("Invalid compute terminal status")
        with (nullcontext(_session) if _session else self.db.transaction()) as session:
            training = session.execute(select(ExternalIntent.tenant_id, ExternalIntent.operation).where(
                ExternalIntent.id == intent_id)).one()
            if training.operation.startswith("training:"):
                lock_training_organization(session, training.tenant_id)
            lock_platform(session)
            intent = session.get(ExternalIntent, intent_id)
            if intent.status in {"succeeded", "failed"}:
                return
            project = get_project(session, intent.project_id)
            amount, day = intent.payload["reservedUsd"], intent.payload["day"]
            daily = session.get(DailyBudget, day)
            project.reserved_usd = max(0, project.reserved_usd - amount)
            project.spent_usd += amount
            daily.reserved, daily.spent = max(0, daily.reserved - amount), daily.spent + amount
            intent.status = status
            intent.receipt = {**intent.receipt, **receipt, "allowanceDebitUsd": amount,
                              "costBasis": "conservative_reservation"}
            emit(session, project, f"compute.{status}", {
                "intentId": intent.id, "allowanceDebitUsd": amount,
                "externalId": intent.receipt.get("externalId"),
            })

    def reconcile(self, principal, intent_id, note, external_id=None):
        principal.require_admin()
        if not isinstance(note, str) or not 20 <= len(note.strip()) <= 4000:
            raise DomainError("Record the provider evidence used to reconcile this operation", 422)
        if external_id is not None and (not isinstance(external_id, str) or not 1 <= len(external_id) <= 200):
            raise DomainError("Invalid provider job identity", 422)
        with self.db.transaction() as session:
            lock_training_organization(session, principal.tenant_id)
            lock_platform(session)
            intent = session.scalar(select(ExternalIntent).where(
                ExternalIntent.id == intent_id, ExternalIntent.tenant_id == principal.tenant_id,
                ExternalIntent.mode == "compute").with_for_update())
            if not intent:
                raise DomainError("Compute operation not found", 404)
            project = get_project(session, intent.project_id, principal)
            if intent.status not in {"uncertain", "reserved"}:
                raise DomainError("Only an unconfirmed compute operation can be reconciled", 409)
            if external_id and intent.operation.startswith("embedding:"):
                raise DomainError("Synchronous embeddings cannot attach to an asynchronous provider job", 422)
            intent.receipt = {**intent.receipt, "reconciliation": {
                "actorId": principal.subject, "note": redact(note), "observedAt": datetime.now(UTC).isoformat()}}
            if external_id:
                intent.receipt = {**intent.receipt, "externalId": external_id}
                intent.status = "submitted"
                if intent.operation.startswith("training:") and (
                        intent.receipt.get("cancelRequired") or not training_inputs_current(session, intent)):
                    request_training_cancel(session, intent, "training_inputs_changed")
            emit(session, project, "compute.reconciled", {"intentId": intent.id, "actorId": principal.subject,
                                                          "attachedExternalJob": bool(external_id)})
        if not external_id:
            self.finish(intent_id, {"failure": "operator_reconciled_unknown_result"}, status="failed")
        return self.view(principal, intent_id)


class LearningService:
    def __init__(self, db, settings, store):
        self.db, self.settings, self.store = db, settings, store

    def _eligible_project(self, session, principal, project_id):
        lock_training_organization(session, principal.tenant_id)
        project = get_project(session, project_id, principal)
        consent = session.scalar(select(Consent).where(
            Consent.tenant_id == principal.tenant_id, Consent.project_id == project_id,
            Consent.opted_in.is_(True)))
        if not consent:
            raise DomainError("Every customer project must explicitly opt in to training", 409, "consent_required")
        release = session.get(Release, project.accepted_release_id) if project.accepted_release_id else None
        if not release or release.revision != project.engineering_revision or release.mode != "digital":
            raise DomainError("Training requires a current accepted digital release", 409, "release_required")
        artifacts = session.scalars(select(Artifact).where(
            Artifact.project_id == project.id, Artifact.revision == project.engineering_revision,
            Artifact.status == "accepted")).all()
        accepted = release.manifest.get("artifacts", {})
        artifacts = [a for a in artifacts if accepted.get(a.id) == a.sha256]
        if not artifacts:
            raise DomainError("The accepted release has no eligible artifacts", 409)
        return project, consent, artifacts

    def create_dataset(self, principal, project_ids):
        principal.require_admin()
        project_ids = sorted(set(project_ids))
        if len(project_ids) < 2 or len(project_ids) > 100:
            raise DomainError("Use 2–100 projects so training and evaluation are separated by project", 422)
        with self.db.transaction() as session:
            lock_training_organization(session, principal.tenant_id)
            records = []
            validation_projects = set(project_ids[-max(1, len(project_ids) // 5):])
            for project_id in project_ids:
                project, consent, artifacts = self._eligible_project(session, principal, project_id)
                records.append({
                    "projectId": project_id, "revision": project.engineering_revision,
                    "consentId": consent.id, "consentVersion": consent.version,
                    "split": "evaluation" if project_id in validation_projects else "training",
                    "artifacts": [{"id": a.id, "sha256": a.sha256, "name": a.name, "lane": a.lane}
                                  for a in artifacts],
                })
            dataset = Dataset(id=uid(), tenant_id=principal.tenant_id, status="draft", manifest={
                "schemaVersion": "1", "projects": records, "physicalEvidenceIncluded": False,
                "rightsReview": {"approved": False}, "createdBy": principal.subject,
            })
            session.add(dataset)
            return {"id": dataset.id, "status": dataset.status, "manifest": dataset.manifest}

    def _dataset(self, session, principal, dataset_id):
        lock_training_organization(session, principal.tenant_id)
        dataset = session.scalar(select(Dataset).where(
            Dataset.id == dataset_id, Dataset.tenant_id == principal.tenant_id))
        if not dataset:
            raise DomainError("Dataset not found", 404)
        for entry in sorted(dataset.manifest["projects"], key=lambda item: item["projectId"]):
            project, consent, artifacts = self._eligible_project(session, principal, entry["projectId"])
            if consent.id != entry["consentId"] or consent.version != entry["consentVersion"]:
                raise DomainError("Dataset consent changed; create a new dataset", 409, "consent_changed")
            actual = {a.id: a.sha256 for a in artifacts}
            if project.engineering_revision != entry["revision"] or any(
                    actual.get(a["id"]) != a["sha256"] for a in entry["artifacts"]):
                raise DomainError("Dataset artifacts are stale", 409, "stale_dataset")
        return dataset

    def approve_dataset_rights(self, principal, dataset_id, rights_statement):
        principal.require_admin()
        if not isinstance(rights_statement, str) or len(rights_statement.strip()) < 20:
            raise DomainError("Record the data and model-output rights permitting this training use", 422)
        with self.db.transaction() as session:
            dataset = self._dataset(session, principal, dataset_id)
            dataset.manifest = {**dataset.manifest, "rightsReview": {
                "approved": True, "reviewer": principal.subject, "statement": rights_statement,
                "reviewedAt": datetime.now(UTC).isoformat(),
            }}
            dataset.status = "ready"
            return {"id": dataset.id, "status": dataset.status}

    def register_candidate(self, principal, dataset_id, config):
        principal.require_admin()
        required = {"baseModel", "weightsLicense", "recipe", "budgetUsd", "maxGpuSeconds"}
        try:
            budget, runtime = float(config.get("budgetUsd", 0)), int(config.get("maxGpuSeconds", 0))
        except (ValueError, TypeError, OverflowError) as exc:
            raise DomainError("Invalid candidate budget or runtime", 422) from exc
        if not required.issubset(config) or not math.isfinite(budget) or budget <= 0 or not 1 <= runtime <= 3600 or (
                any(not isinstance(config.get(k), str) or not config[k].strip()
                    for k in ("baseModel", "weightsLicense", "recipe"))):
            raise DomainError("Specify a licensed base model, recipe, and positive spend/runtime ceilings", 422)
        config = {**config, "budgetUsd": budget, "maxGpuSeconds": runtime}
        if config["recipe"] != "causal_lm_sft":
            raise DomainError("This deployment supports only the causal_lm_sft training recipe", 422)
        with self.db.transaction() as session:
            dataset = self._dataset(session, principal, dataset_id)
            if dataset.status != "ready":
                raise DomainError("Review dataset rights before creating a training candidate", 409)
            model = TrainedModel(id=uid(), dataset_id=dataset.id, status="candidate", config=redact(config))
            session.add(model)
            return {"id": model.id, "status": model.status, "datasetId": model.dataset_id}

    def materialize_training(self, principal, dataset_id):
        """Materialize approved text artifacts only; evaluation projects never enter this file."""
        principal.require_admin()
        with self.db.transaction() as session:
            dataset = self._dataset(session, principal, dataset_id)
            if dataset.status != "ready" or not dataset.manifest["rightsReview"]["approved"]:
                raise DomainError("Dataset is not approved for training", 409)
            examples = []
            for project in dataset.manifest["projects"]:
                if project["split"] != "training":
                    continue
                for item in project["artifacts"]:
                    artifact = session.get(Artifact, item["id"])
                    content = self.store.get(artifact.key)
                    if hashlib.sha256(content).hexdigest() != item["sha256"]:
                        raise DomainError("Dataset artifact checksum mismatch", 409)
                    try:
                        text = content.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    if len(text) > 100000:
                        continue
                    examples.append({"text": redact(text), "split": "training", "projectId": project["projectId"],
                                     "artifactId": artifact.id, "sha256": artifact.sha256})
            if not examples:
                raise DomainError("No eligible text examples are available", 409)
            body = "\n".join(json.dumps(e, sort_keys=True) for e in examples).encode()
            if len(body) > 20_000_000:
                raise DomainError("Training dataset exceeds the first-version 20 MB limit", 413)
            key = f"training/{principal.tenant_id}/{dataset.id}/training.jsonl"
            self.store.put(key, body, "application/x-ndjson")
            return {"key": key, "sha256": hashlib.sha256(body).hexdigest(), "examples": len(examples),
                    "datasetBinding": dataset_binding(dataset)}

    def record_evaluation(self, principal, model_id, evaluation):
        principal.require_admin()
        required = {"heldOutExamples", "criticalFailures", "passRate", "baselinePassRate", "costRatio",
                    "latencyRatio", "reportArtifactId"}
        if not required.issubset(evaluation):
            raise DomainError("An evaluation needs held-out quality, cost, latency and a report artifact", 422)
        try:
            counts = {key: int(evaluation[key]) for key in ("heldOutExamples", "criticalFailures")}
            scores = {key: float(evaluation[key]) for key in (
                "passRate", "baselinePassRate", "costRatio", "latencyRatio")}
        except (TypeError, ValueError, OverflowError) as exc:
            raise DomainError("Evaluation metrics must be finite numbers", 422) from exc
        if any(not math.isfinite(value) for value in scores.values()) or counts["criticalFailures"] < 0 or (
                scores["costRatio"] <= 0 or scores["latencyRatio"] <= 0):
            raise DomainError("Evaluation counts and cost/latency ratios are invalid", 422)
        if any(not isinstance(evaluation.get(key, False), bool) for key in ("shadowPassed", "canaryPassed")):
            raise DomainError("Shadow and canary outcomes must be explicit booleans", 422)
        evaluation = {**evaluation, **counts, **scores}
        with self.db.transaction() as session:
            lock_training_organization(session, principal.tenant_id)
            model = session.get(TrainedModel, model_id)
            if not model:
                raise DomainError("Model not found", 404)
            self._dataset(session, principal, model.dataset_id)
            report = session.scalar(select(Artifact).where(
                Artifact.id == evaluation["reportArtifactId"], Artifact.tenant_id == principal.tenant_id))
            if not report:
                raise DomainError("Evaluation report artifact not found", 404)
            get_project(session, report.project_id, principal)
            if int(evaluation["heldOutExamples"]) < 20:
                raise DomainError("At least 20 held-out examples are required", 422)
            if not 0 <= float(evaluation["passRate"]) <= 1 or not 0 <= float(evaluation["baselinePassRate"]) <= 1:
                raise DomainError("Pass rates must be between zero and one", 422)
            model.evaluation = {**redact(evaluation), "reportSha256": report.sha256,
                                "recordedBy": principal.subject}
            return {"id": model.id, "evaluation": model.evaluation}

    def promote(self, principal, model_id, mode="shadow"):
        principal.require_admin()
        if mode not in {"shadow", "canary", "promoted"}:
            raise DomainError("Choose shadow, canary or promoted", 422)
        with self.db.transaction() as session:
            lock_training_organization(session, principal.tenant_id)
            model = session.get(TrainedModel, model_id)
            if not model:
                raise DomainError("Model not found", 404)
            self._dataset(session, principal, model.dataset_id)
            e = model.evaluation
            if not e or e.get("criticalFailures") != 0 or e.get("heldOutExamples", 0) < 20 or (
                    e["passRate"] < e["baselinePassRate"]):
                raise DomainError("Model has not passed the held-out engineering gates", 409, "evaluation_failed")
            if not (e["passRate"] > e["baselinePassRate"] or e["costRatio"] < 1 or e["latencyRatio"] < 1):
                raise DomainError("Model must improve measured quality, cost or latency", 409)
            transitions = {"shadow": {"candidate"}, "canary": {"shadow"}, "promoted": {"canary"}}
            if model.status not in transitions[mode]:
                raise DomainError("Promote through shadow, then canary, then general use", 409)
            if mode != "shadow" and not e.get(f"{model.status}Passed", False):
                raise DomainError(f"Record a successful {model.status} evaluation first", 409)
            model.status, model.promoted_by = mode, principal.subject
            return {"id": model.id, "status": model.status}

    def rollback(self, principal, model_id):
        principal.require_admin()
        with self.db.transaction() as session:
            lock_training_organization(session, principal.tenant_id)
            model = session.get(TrainedModel, model_id)
            if not model:
                raise DomainError("Model not found", 404)
            dataset = session.get(Dataset, model.dataset_id)
            if dataset.tenant_id != principal.tenant_id:
                raise DomainError("Model not found", 404)
            model.status = "retired"
            return {"id": model.id, "status": model.status}

    def datasets(self, principal):
        principal.require_admin()
        with self.db.transaction() as session:
            rows = session.scalars(select(Dataset).where(Dataset.tenant_id == principal.tenant_id)
                                   .order_by(Dataset.created_at.desc()).limit(100)).all()
            return {"datasets": [{"id": row.id, "status": row.status, "manifest": row.manifest} for row in rows]}

    def models(self, principal):
        with self.db.transaction() as session:
            rows = session.scalars(select(TrainedModel).join(Dataset).where(
                Dataset.tenant_id == principal.tenant_id).order_by(TrainedModel.created_at.desc()).limit(100)).all()
            return {"models": [{"id": row.id, "datasetId": row.dataset_id, "status": row.status,
                                "config": redact(row.config), "evaluation": row.evaluation} for row in rows]}


class ModalCompute:
    def __init__(self, db, settings, store, modal_client=None):
        self.db, self.settings, self.store = db, settings, store
        self.ledger = ComputeLedger(db, settings)
        self.client = modal_client

    async def submit(self, principal, project_id, operation_id, model_id, artifact_ids):
        principal.require_write()
        with self.db.transaction() as session:
            project = get_project(session, project_id, principal, write=True)
            model = session.get(TrainedModel, model_id)
            dataset = session.get(Dataset, model.dataset_id) if model else None
            if not model or not dataset or dataset.tenant_id != principal.tenant_id or model.status != "promoted":
                raise DomainError("Choose a promoted model belonging to this organization", 409)
            artifacts = session.scalars(select(Artifact).where(
                Artifact.id.in_(artifact_ids), Artifact.project_id == project_id,
                Artifact.tenant_id == principal.tenant_id, Artifact.revision == project.engineering_revision,
                Artifact.status != "stale")).all()
            if not 1 <= len(artifact_ids) <= 10 or len(artifacts) != len(set(artifact_ids)):
                raise DomainError("Choose current, accessible project artifacts", 422)
            inputs = []
            for artifact in artifacts:
                url = self.store.download_url(artifact.key, expires=900)
                if not url:
                    raise DomainError("Modal needs scoped S3 artifact URLs", 503, "storage_unconfigured")
                inputs.append({"id": artifact.id, "sha256": artifact.sha256, "url": url})
            request = {"projectId": project_id, "revision": project.engineering_revision,
                       "modelId": model.id, "artifacts": [{"id": a.id, "sha256": a.sha256} for a in artifacts]}
        intent_id, fresh = self.ledger.begin(principal, project_id, operation_id, "modal", request)
        if fresh:
            try:
                if self.client:
                    call_id = await self.client.spawn({"jobId": intent_id, **request, "inputs": inputs})
                else:
                    import modal
                    function = modal.Function.from_name(self.settings.modal_app, self.settings.modal_function).with_options(
                        timeout=600, max_containers=2, retries=0)
                    call = await function.spawn.aio({"jobId": intent_id, **request, "inputs": inputs})
                    call_id = call.object_id
                self.ledger.submitted(intent_id, call_id)
            except Exception as exc:
                self.ledger.uncertain(intent_id)
                raise DomainError("Modal dispatch could not be confirmed; reconcile before retrying", 502,
                                  "compute_uncertain") from exc
        return self.ledger.view(principal, intent_id)

    async def poll(self, principal, intent_id):
        job = self.ledger.view(principal, intent_id)
        if job["status"] != "submitted" or job["receipt"].get("cancelRequired"):
            return job
        external_id = job["receipt"]["externalId"]
        try:
            with tracer.start_as_current_span("compute.modal.poll", attributes={"compute.intent_id": intent_id}):
                if self.client:
                    output = await self.client.result(external_id)
                else:
                    import modal
                    output = await modal.FunctionCall.from_id(external_id).get.aio(timeout=0)
        except TimeoutError:
            return job
        except Exception as exc:
            # Output expiry is an operational failure, not evidence of success.
            raise DomainError("Modal output is unavailable; reconcile the provider job", 502) from exc
        try:
            encoded_output = json.dumps(redact(output), sort_keys=True, allow_nan=False).encode()
        except (ValueError, TypeError) as exc:
            raise DomainError("Modal result must contain finite, JSON-compatible values", 502) from exc
        if not isinstance(output, dict) or len(encoded_output) > 2_000_000 or (
                output.get("jobId", intent_id) != intent_id):
            raise DomainError("Modal result does not match the bounded result contract", 502)
        key = f"compute/{principal.tenant_id}/{job['projectId']}/{intent_id}/result.json"
        with self.db.transaction() as session:
            lock_training_organization(session, principal.tenant_id)
            lock_platform(session)
            get_project(session, job["projectId"], principal, write=True)
            intent = session.scalar(select(ExternalIntent).where(ExternalIntent.id == intent_id).with_for_update())
            if intent.operation.startswith("training:") and (
                    intent.receipt.get("cancelRequired") or not training_inputs_current(session, intent)):
                request_training_cancel(session, intent, "training_inputs_changed")
            elif intent.status == "submitted":
                self.store.put_json(key, redact(output))
                self.ledger.finish(intent_id, {"resultKey": key, "externalId": external_id,
                                              "computeUsage": {"executionSeconds": output.get("executionSeconds"),
                                                               "gpuType": output.get("gpuType"),
                                                               "basis": "function_elapsed_excludes_startup"},
                                              "sha256": hashlib.sha256(encoded_output).hexdigest()},
                                   _session=session)
        return self.ledger.view(principal, intent_id)

    async def cancel_training(self, intent_id):
        """Trusted outbox handler; no generated output is accepted after a consent fence."""
        with self.db.transaction() as session:
            intent = session.get(ExternalIntent, intent_id)
            if not intent or not intent.operation.startswith("training:") or not intent.receipt.get("cancelRequired"):
                return
            external_id = intent.receipt.get("externalId")
            if not external_id:
                # A late submission/reconciliation receipt enqueues a fresh cancellation.
                # An unknown provider outcome retains its reservation until reconciled.
                return
        if self.client:
            await self.client.cancel(external_id)
        else:
            import modal
            await modal.FunctionCall.from_id(external_id).cancel.aio(terminate_containers=True)
        self.ledger.finish(intent_id, {"cancelRequired": False, "cancelled": True,
                                      "failure": "training_inputs_changed"}, status="failed")

    async def start_training(self, principal, funding_project_id, operation_id, model_id):
        """Explicit operator action, separate from consent, dataset creation and model promotion."""
        principal.require_admin()
        learning = LearningService(self.db, self.settings, self.store)
        with self.db.transaction() as session:
            lock_training_organization(session, principal.tenant_id)
            get_project(session, funding_project_id, principal, write=True)
            model = session.get(TrainedModel, model_id)
            if not model or model.status != "candidate":
                raise DomainError("Select a training candidate", 409)
            dataset = learning._dataset(session, principal, model.dataset_id)
            config, dataset_id = model.config, dataset.id
            if not config.get("baseModelPath") or not 1 <= int(config["maxGpuSeconds"]) <= 3600:
                raise DomainError("Configure a provisioned baseModelPath and a runtime up to 3600 seconds", 422)
        materialized = learning.materialize_training(principal, dataset_id)
        url = self.store.download_url(materialized["key"], expires=900)
        if not url:
            raise DomainError("Modal training needs scoped S3 dataset access", 503, "storage_unconfigured")
        request = {"modelId": model_id, "datasetId": dataset_id, "datasetSha256": materialized["sha256"],
                   "baseModelPath": config["baseModelPath"], "maxGpuSeconds": config["maxGpuSeconds"],
                   "datasetBinding": materialized["datasetBinding"]}
        intent_id, fresh = self.ledger.begin(principal, funding_project_id, operation_id, "training", request,
                                            reserve=float(config["budgetUsd"]))
        if fresh:
            # This durable claim is the dispatch authorization boundary. A later
            # withdrawal fences the in-flight result and requests cancellation.
            self.ledger.claim_training_dispatch(principal, intent_id)
            try:
                invocation = {**request, "jobId": intent_id, "dataset": {"url": url, "sha256": materialized["sha256"]}}
                if self.client:
                    call_id = await self.client.train(invocation)
                else:
                    import modal
                    function = modal.Function.from_name(self.settings.modal_app, "train").with_options(
                        timeout=int(config["maxGpuSeconds"]), max_containers=1, retries=0)
                    call = await function.spawn.aio(invocation)
                    call_id = call.object_id
                self.ledger.submitted(intent_id, call_id)
            except Exception as exc:
                self.ledger.uncertain(intent_id)
                raise DomainError("Training dispatch is uncertain; reconcile before retrying", 502,
                                  "compute_uncertain") from exc
        return self.ledger.view(principal, intent_id)
