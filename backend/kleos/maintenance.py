"""Bounded retention and explicit project erasure; accepted engineering files never age out."""

import argparse
import hashlib
import json
import shutil
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, or_, select

from .checkpoint_retention import CheckpointRetention
from .contracts import DomainError
from .db import (
    Artifact,
    Attempt,
    CommandReceipt,
    Consent,
    DailyBudget,
    Dataset,
    Event,
    ExternalIntent,
    Inbox,
    Invocation,
    NativeJob,
    NativeSessionStep,
    Organization,
    Outbox,
    Project,
    Release,
    Run,
    Source,
    Task,
    TrainedModel,
    Validation,
    now,
)
from .domain import ControlPlane, emit, lock_platform, settle_native_reservation


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Maintenance:
    def __init__(self, db, settings, store, memory=None, cancel_modal=None, checkpoint_client=None):
        self.db, self.settings, self.store = db, settings, store
        self.memory, self.cancel_modal = memory, cancel_modal
        self.checkpoints = CheckpointRetention(db, settings, checkpoint_client)

    def purge_object(self, key):
        """Explicitly remove all object versions; a delete marker alone is not erasure."""
        if not key:
            return
        if not self.store.s3:
            self.store.delete(key)
            return
        client, bucket = self.store.s3, self.settings.artifact_bucket
        paginator = client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket, Prefix=key):
            versions = [{"Key": entry["Key"], "VersionId": entry["VersionId"]}
                        for category in ("Versions", "DeleteMarkers") for entry in page.get(category, [])
                        if entry["Key"] == key]
            if versions:
                result = client.delete_objects(Bucket=bucket, Delete={"Objects": versions, "Quiet": True})
                if result.get("Errors"):
                    raise RuntimeError("Some artifact object versions could not be erased")

    def purge_prefix(self, prefix):
        """Remove scoped objects even when an interrupted DB transaction left no row referencing them."""
        if not prefix or not prefix.endswith("/") or prefix.startswith("/") or ".." in prefix.split("/"):
            raise ValueError("An explicit relative directory prefix is required")
        if not self.store.s3:
            path = self.store._path(prefix)
            if path.exists():
                shutil.rmtree(path)
            return
        client, bucket = self.store.s3, self.settings.artifact_bucket
        for page in client.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=prefix):
            versions = [{"Key": entry["Key"], "VersionId": entry["VersionId"]}
                        for category in ("Versions", "DeleteMarkers") for entry in page.get(category, [])
                        if entry["Key"].startswith(prefix)]
            for offset in range(0, len(versions), 1000):
                result = client.delete_objects(Bucket=bucket, Delete={"Objects": versions[offset:offset + 1000], "Quiet": True})
                if result.get("Errors"):
                    raise RuntimeError("Some project object versions could not be erased")

    @staticmethod
    def operational_job_request(job):
        dispatch = {key: value for key, value in job.request.get("dispatch", {}).items()
                    if key in {"cluster", "backend", "volume"}}
        return {"retentionExpired": True, "jobId": job.id,
                "dispatch": dispatch,
                "reservation": job.request.get("reservation", {}),
                "toolVersion": job.request.get("toolVersion")}

    @staticmethod
    def operational_job_result(job):
        return {key: value for key, value in job.result.items() if key in {
            "status", "terminationRequested", "terminationObservedAt", "terminationObservation", "localCleaned"}}

    def mark_deletion_started(self, project_id):
        with self.db.transaction() as session:
            project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if project and project.deleted_at:
                deletion = project.state.get("deletion", {})
                if not deletion.get("applicationPurged"):
                    project.state = {**project.state, "deletion": {**deletion, "status": "retrying",
                        "leaseUntil": (now() + timedelta(seconds=max(300, self.settings.worker_timeout_seconds))).isoformat()}}

    def record_deletion_failure(self, project_id, error_type, attempt, exhausted):
        with self.db.transaction() as session:
            project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if project and project.deleted_at and not project.state.get("deletion", {}).get("applicationPurged"):
                deletion = project.state.get("deletion", {})
                project.state = {**project.state, "deletion": {**deletion,
                    "status": "retry_pending" if exhausted else "retrying", "lastErrorType": str(error_type)[:100],
                    "lastAttempt": attempt, "lastFailureAt": now().isoformat(),
                    "nextAttemptAt": (now() + timedelta(minutes=5)).isoformat() if exhausted else None}}

    def schedule_deletion_retries(self, at=None, limit=25):
        """Recover failed erasures with fresh workflow IDs, without replaying live work."""
        at = _utc(at or now())
        count = 0
        with self.db.transaction() as session:
            undelivered = select(Outbox.id).where(Outbox.kind == "delete_project",
                Outbox.payload["projectId"].as_string() == Project.id,
                Outbox.delivered_at.is_(None)).correlate(Project).exists()
            recently_delivered = select(Outbox.id).where(Outbox.kind == "delete_project",
                Outbox.payload["projectId"].as_string() == Project.id,
                Outbox.delivered_at > at - timedelta(hours=1)).correlate(Project).exists()
            # Older workers left a queued tombstone after their one-shot workflow
            # failed. Recover it once only after a generous grace period, and never
            # duplicate an outbox entry that has not yet reached DBOS.
            legacy_queued = and_(Project.state["deletion"]["status"].as_string() == "queued",
                Project.deleted_at <= at - timedelta(hours=1),
                Project.state["deletion"]["retryGeneration"].as_integer().is_(None),
                Project.state["deletion"]["leaseUntil"].as_string().is_(None), ~undelivered, ~recently_delivered)
            projects = session.scalars(select(Project).where(Project.deleted_at.is_not(None), or_(
                and_(Project.state["deletion"]["status"].as_string() == "retry_pending",
                     Project.state["deletion"]["nextAttemptAt"].as_string() <= at.isoformat()),
                and_(Project.state["deletion"]["status"].as_string() == "retrying",
                     Project.state["deletion"]["leaseUntil"].as_string() <= at.isoformat()), legacy_queued)
            ).order_by(Project.deleted_at).limit(limit).with_for_update(skip_locked=True)).all()
            for project in projects:
                deletion = project.state.get("deletion", {})
                due = deletion.get("nextAttemptAt") if deletion.get("status") == "retry_pending" else deletion.get("leaseUntil")
                if deletion.get("applicationPurged") or (deletion.get("status") != "queued" and (
                        not due or _utc(datetime.fromisoformat(due)) > at)):
                    continue
                generation = deletion.get("retryGeneration", 0) + 1
                session.add(Outbox(key=f"delete-project:{project.id}:retry:{generation}", kind="delete_project",
                                  payload={"projectId": project.id}))
                project.state = {**project.state, "deletion": {**deletion, "status": "queued", "retryGeneration": generation,
                                                               "nextAttemptAt": None, "leaseUntil": None}}
                count += 1
        return count

    def reclassify_failed_invocations(self, at=None, limit=100):
        """Upgrade older failed-call holds without freeing money or replaying inference."""
        at = _utc(at or now())
        count = 0
        with self.db.transaction() as session:
            lock_platform(session)
            calls = session.scalars(select(Invocation).join(Attempt, Attempt.id == Invocation.attempt_id).join(
                Task, Task.id == Attempt.task_id).where(Invocation.status == "pending", or_(
                    Task.status.in_(["blocked", "failed"]), and_(Task.status == "cancelled",
                    Invocation.created_at <= at - timedelta(seconds=self.settings.model_timeout_seconds))
                )).limit(limit)).all()
            for call in calls:
                task = session.get(Task, session.get(Attempt, call.attempt_id).task_id)
                if task.status == "cancelled" and _utc(call.created_at) + timedelta(seconds=self.settings.model_timeout_seconds) > at:
                    continue
                call.status = "uncertain"
                call.usage = {**call.usage, "financialOutcome": "uncertain", "recoveredFromLegacyPending": True}
                count += 1
        return count

    def request_delete(self, principal, project_id, expected_revision):
        principal.require_admin()
        with self.db.transaction() as session:
            # Consent changes can also lock a shared dataset and another
            # project's training reservation. Serialize before locking either.
            session.scalar(select(Organization).where(
                Organization.id == principal.tenant_id).with_for_update())
            lock_platform(session)
            project = session.scalar(select(Project).where(
                Project.id == project_id, Project.tenant_id == principal.tenant_id).with_for_update())
            if not project:
                raise DomainError("Project not found", 404)
            if project.deleted_at:
                return {"projectId": project.id, "status": "deletion_queued", "deleted": True}
            if project.revision != expected_revision:
                raise DomainError("Project changed; reload before deleting", 409, "stale_revision")
            ControlPlane(self.db, self.settings, self.store)._cancel(session, project, "Project deletion requested")
            project.deleted_at = now()
            project.state = {"deletion": {"status": "queued", "actorId": principal.subject}}
            consent = session.scalar(select(Consent).where(Consent.project_id == project_id))
            if consent:
                consent.opted_in, consent.version, consent.updated_at = False, consent.version + 1, now()
            for source in session.scalars(select(Source).where(Source.project_id == project_id)):
                source.revoked = True
            # Invalidating derived datasets happens in the same transaction as hiding the project.
            for dataset in session.scalars(select(Dataset).where(Dataset.tenant_id == principal.tenant_id)):
                if any(p["projectId"] == project_id for p in dataset.manifest.get("projects", [])):
                    dataset.status = "excluded"
                    dataset.manifest = {**dataset.manifest, "excludedByProjectDeletion": project_id}
                    for model in session.scalars(select(TrainedModel).where(TrainedModel.dataset_id == dataset.id)):
                        model.status = "retired"
            from .training_consent import exclude_project_training
            exclude_project_training(session, project, reason="project_deleted")
            emit(session, project, "project.deletionRequested", {"actorId": principal.subject})
            self.checkpoints.capture_project(session, project)
            session.add(Outbox(key=f"delete-project:{project_id}", kind="delete_project", payload={"projectId": project_id}))
            return {"projectId": project.id, "status": "deletion_queued", "deleted": True}

    def purge_project(self, project_id):
        """Worker entry point; works on a tombstone and can resume after any external deletion failure."""
        with self.db.transaction() as session:
            tenant_id = session.scalar(select(Project.tenant_id).where(Project.id == project_id))
            if not tenant_id:
                raise DomainError("Project deletion was not explicitly requested", 409)
            session.scalar(select(Organization).where(Organization.id == tenant_id).with_for_update())
            project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if not project or not project.deleted_at:
                raise DomainError("Project deletion was not explicitly requested", 409)
            self.checkpoints.capture_project(session, project)
            artifacts = session.scalars(select(Artifact).where(Artifact.project_id == project_id)).all()
            calls = session.scalars(select(Invocation).where(Invocation.project_id == project_id)).all()
            intents = session.scalars(select(ExternalIntent).where(ExternalIntent.project_id == project_id)).all()
            source_ids = list(session.scalars(select(Source.id).where(Source.project_id == project_id)))
            keys = {a.key for a in artifacts} | {c.transcript_key for c in calls if c.transcript_key}
            keys.update(i.receipt["resultKey"] for i in intents if i.receipt.get("resultKey"))
            keys.update(f"compute/{project.tenant_id}/{project_id}/{i.id}/result.json" for i in intents if i.mode == "compute")
            datasets = [d for d in session.scalars(select(Dataset).where(Dataset.tenant_id == project.tenant_id))
                        if any(p["projectId"] == project_id for p in d.manifest.get("projects", []))]
            dataset_ids = {d.id for d in datasets}
            related_training = [i for i in session.scalars(select(ExternalIntent).where(
                ExternalIntent.tenant_id == project.tenant_id, ExternalIntent.mode == "compute",
                ExternalIntent.operation.like("training:%")))
                if i.payload.get("input", {}).get("datasetId") in dataset_ids and i.project_id != project_id]
            related_training_ids = [i.id for i in related_training]
            keys.update(f"training/{project.tenant_id}/{d.id}/training.jsonl" for d in datasets)
            archive_rows = list(session.scalars(select(Outbox).where(Outbox.kind == "event_archive")))
            keys.update(a.payload["key"] for a in archive_rows if a.payload.get("projectId") == project_id)
            external_ids = {i.receipt["externalId"] for i in [*intents, *related_training] if i.mode == "compute" and
                            (i.status in {"reserved", "submitted", "uncertain"} or i.receipt.get("cancelRequired"))
                            and i.receipt.get("externalId")}
            prefixes = [f"{namespace}/{project.tenant_id}/{project_id}/"
                        for namespace in ("artifacts", "logs", "transcripts", "compute", "event-archives")]
            prefixes.extend(f"training/{project.tenant_id}/{d.id}/" for d in datasets)
        for external_id in external_ids:
            if self.cancel_modal:
                self.cancel_modal(external_id)
            else:
                import modal
                modal.FunctionCall.from_id(external_id).cancel(terminate_containers=True)
        for key in sorted(keys):
            self.purge_object(key)
        for prefix in prefixes:
            self.purge_prefix(prefix)
        if source_ids:
            if self.memory is None:
                from .memory import MemoryIndex
                self.memory = MemoryIndex(self.db, self.settings)
            self.memory.delete_points(source_ids)
        # Erase data only after external objects/indexes were removed, retaining retryable references until then.
        with self.db.transaction() as session:
            session.scalar(select(Organization).where(Organization.id == tenant_id).with_for_update())
            lock_platform(session)
            project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if not project.deleted_at:
                raise DomainError("Project deletion tombstone was removed", 409)
            tasks = session.scalars(select(Task).where(Task.project_id == project_id)).all()
            task_ids = [task.id for task in tasks]
            for call in session.scalars(select(Invocation).where(Invocation.project_id == project_id)):
                if call.status in {"pending", "uncertain"}:
                    self._settle_deleted_reservation(session, project, call.reserved_usd, call.usage.get("reservationDay"))
                    call.status, call.estimated_usd = "failed", call.reserved_usd
                call.output, call.transcript_key = {}, None
                call.usage = {**call.usage, "contentDeleted": True}
            for intent in session.scalars(select(ExternalIntent).where(ExternalIntent.project_id == project_id)):
                if intent.mode == "compute" and intent.status in {"reserved", "submitted", "uncertain"}:
                    self._settle_deleted_reservation(session, project, intent.payload.get("reservedUsd", 0),
                                                     intent.payload.get("day"))
                    intent.status = "failed"
                intent.payload = {k: v for k, v in intent.payload.items() if k in {"fingerprint", "reservedUsd", "day"}}
                intent.receipt = {k: v for k, v in intent.receipt.items()
                                  if k in {"externalId", "allowanceDebitUsd", "costBasis"}}
                intent.receipt = {**intent.receipt, "contentDeleted": True}
            for intent in session.scalars(select(ExternalIntent).where(ExternalIntent.id.in_(related_training_ids))):
                funding_project = session.scalar(select(Project).where(Project.id == intent.project_id).with_for_update())
                if intent.status in {"reserved", "submitted", "uncertain"}:
                    self._settle_deleted_reservation(session, funding_project, intent.payload.get("reservedUsd", 0),
                                                     intent.payload.get("day"))
                    intent.status = "failed"
                intent.receipt = {**intent.receipt, "cancelRequired": False, "trainingDatasetExcluded": True}
            for task in tasks:
                task.result = {}
            for attempt in session.scalars(select(Attempt).where(Attempt.task_id.in_(task_ids))):
                attempt.context, attempt.output, attempt.error = {}, {}, None
            for job in session.scalars(select(NativeJob).where(NativeJob.task_id.in_(task_ids))):
                for step in session.scalars(select(NativeSessionStep).where(NativeSessionStep.job_id == job.id)):
                    step.arguments, step.result = {}, {"contentDeleted": True}
                    step.request_key = f"purged:{step.id}"
                    if step.status in {"queued", "running"}:
                        step.status = "cancelled"
                reservation = job.request.get("reservation", {})
                if reservation and not reservation.get("settled"):
                    settle_native_reservation(session, job, project,
                                              refund=not reservation.get("dispatchAttempted", False))
                if job.status in {"pending", "dispatching", "submitted", "running"}:
                    job.status = "cancelled"
                job.request = self.operational_job_request(job)
                job.result, job.token_hash = {**self.operational_job_result(job), "contentDeleted": True}, ""
            for run in session.scalars(select(Run).where(Run.project_id == project_id)):
                run.blocker = "Project deleted"
            for validation in session.scalars(select(Validation).where(Validation.project_id == project_id)):
                validation.details = {}
            for release in session.scalars(select(Release).where(Release.project_id == project_id)):
                release.manifest = {"contentDeleted": True}
            for inbox in session.scalars(select(Inbox)):
                if inbox.payload.get("projectId") == project_id:
                    inbox.payload = {"projectId": project_id, "contentDeleted": True}
            for receipt in session.scalars(select(CommandReceipt).where(CommandReceipt.project_id == project_id)):
                receipt.response = {"projectId": project_id, "deleted": True}
            for archive in session.scalars(select(Outbox).where(Outbox.kind == "event_archive")):
                if archive.payload.get("projectId") == project_id:
                    session.delete(archive)
            session.execute(delete(Source).where(Source.project_id == project_id))
            session.execute(delete(Artifact).where(Artifact.project_id == project_id))
            session.execute(delete(Event).where(Event.project_id == project_id))
            project.name, project.brief = "Deleted project", ""
            project.requirements, project.interfaces, project.model_policy = {}, {}, {}
            project.accepted_release_id, project.event_floor = None, project.event_sequence
            project.state = {"deletion": {"status": "checkpoint_cleanup_pending", "applicationPurged": True,
                                         "applicationPurgedAt": now().isoformat(), "trainedModelLineageRetained": True}}
            project.status = "deleted"
        self.checkpoints.sweep(project_id=project_id)
        with self.db.transaction() as session:
            return {"projectId": project_id, "status": session.get(Project, project_id).state["deletion"]["status"]}

    @staticmethod
    def _settle_deleted_reservation(session, project, amount, day):
        # Never refund an ambiguous paid call; debit the reserved allowance exactly once.
        if amount:
            daily = session.get(DailyBudget, day) if day else None
            project.reserved_usd = max(0, project.reserved_usd - amount)
            project.spent_usd += amount
            if daily:
                daily.reserved = max(0, daily.reserved - amount)
                daily.spent += amount

    def retention(self, at=None, batch_size=250):
        at = _utc(at or now())
        detailed_cutoff = at - timedelta(days=self.settings.transcript_retention_days)
        event_cutoff = at - timedelta(days=self.settings.event_retention_days)
        counts = {"transcripts": 0, "nativeLogs": 0, "computeResults": 0, "events": 0,
                  "archives": 0, "excludedDatasets": 0, "nativeSessionSteps": 0}
        with self.db.transaction() as session:
            old_calls = session.scalars(select(Invocation).where(
                Invocation.created_at < detailed_cutoff, Invocation.status != "pending",
                Invocation.transcript_key.is_not(None)).limit(batch_size)).all()
            for call in old_calls:
                self.purge_object(call.transcript_key)
                call.transcript_key = None
                attempt = session.get(Attempt, call.attempt_id)
                if not attempt or attempt.status not in {"running", "waiting"}:
                    call.output = {}
                counts["transcripts"] += 1
            for intent in session.scalars(select(ExternalIntent).where(
                    ExternalIntent.created_at < detailed_cutoff, ExternalIntent.operation.like("modal:%"),
                    ExternalIntent.status.in_(["succeeded", "failed"]),
                    ExternalIntent.receipt["resultKey"].as_string().is_not(None)).limit(batch_size)):
                self.purge_object(intent.receipt["resultKey"])
                intent.receipt = {**{k: v for k, v in intent.receipt.items() if k != "resultKey"}, "resultExpired": True}
                counts["computeResults"] += 1
            for attempt in session.scalars(select(Attempt).where(
                    Attempt.created_at < detailed_cutoff, Attempt.status.not_in(["running", "waiting"]),
                    Attempt.context["retentionExpired"].as_boolean().is_not(True)).limit(batch_size)):
                attempt.context, attempt.output, attempt.error = {"retentionExpired": True}, {}, None
            for job in session.scalars(select(NativeJob).where(
                    NativeJob.created_at < detailed_cutoff,
                    NativeJob.status.in_(["succeeded", "failed", "cancelled", "unsupported"]),
                    NativeJob.request["retentionExpired"].as_boolean().is_not(True)).limit(batch_size)):
                if job.result.get("logKey"):
                    self.purge_object(job.result["logKey"])
                    counts["nativeLogs"] += 1
                job.request = self.operational_job_request(job)
                job.result = self.operational_job_result(job)
            for step in session.scalars(select(NativeSessionStep).join(
                    NativeJob, NativeJob.id == NativeSessionStep.job_id).where(
                    NativeSessionStep.created_at < detailed_cutoff,
                    NativeJob.status.in_(["succeeded", "failed", "cancelled", "unsupported"]),
                    NativeSessionStep.result["retentionExpired"].as_boolean().is_not(True)
            ).limit(batch_size)):
                step.arguments, step.result = {}, {"retentionExpired": True}
                step.request_key = f"expired:{step.id}"
                if step.status in {"queued", "running"}:
                    step.status = "cancelled"
                counts["nativeSessionSteps"] += 1
            for task in session.scalars(select(Task).where(
                    Task.created_at < detailed_cutoff, Task.status.not_in(["queued", "running", "waiting"]),
                    Task.result["retentionExpired"].as_boolean().is_not(True)).limit(batch_size)):
                task.result = {"retentionExpired": True}
            rows = session.scalars(select(Event).where(Event.created_at < event_cutoff)
                                   .order_by(Event.created_at, Event.id).limit(batch_size)).all()
            groups = defaultdict(list)
            for event in rows:
                groups[(event.project_id, _utc(event.created_at).date().isoformat())].append(event)
            for (project_id, date), events in groups.items():
                project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
                if not project:
                    continue
                expiry = max(_utc(e.created_at) for e in events) + timedelta(days=365)
                if expiry > at and not project.deleted_at:
                    metadata = [{"id": e.id, "sequence": e.sequence, "type": e.type,
                                 "traceId": e.trace_id, "createdAt": _utc(e.created_at).isoformat()} for e in events]
                    digest = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
                    key = f"event-archives/{project.tenant_id}/{project_id}/{date}/{digest}.json"
                    self.store.put_json(key, {"projectId": project_id, "events": metadata, "expiresAt": expiry.isoformat()})
                    session.add(Outbox(key=f"event-archive:{digest}", kind="event_archive", delivered_at=at,
                                       payload={"projectId": project_id, "key": key, "expiresAt": expiry.isoformat()}))
                project.event_floor = max(project.event_floor, max(e.sequence for e in events))
                for event in events:
                    session.delete(event)
                    counts["events"] += 1
            for archive in session.scalars(select(Outbox).where(
                    Outbox.kind == "event_archive", Outbox.payload["expiresAt"].as_string() <= at.isoformat()
            ).limit(batch_size)):
                self.purge_object(archive.payload["key"])
                session.delete(archive)
                counts["archives"] += 1
        # A consent withdrawal invalidates future datasets immediately in LearningService; clean derived copies here.
        with self.db.transaction() as session:
            cursor = session.scalar(select(Outbox).where(Outbox.key == "maintenance:dataset-scan").with_for_update())
            if not cursor:
                cursor = Outbox(key="maintenance:dataset-scan", kind="maintenance_cursor", delivered_at=at, payload={})
                session.add(cursor)
            query = select(Dataset).order_by(Dataset.id).limit(batch_size)
            if cursor.payload.get("after"):
                query = query.where(Dataset.id > cursor.payload["after"])
            datasets = session.scalars(query).all()
            for dataset in datasets:
                entries = dataset.manifest.get("projects", [])
                invalid = dataset.status == "excluded"
                for entry in entries:
                    project = session.get(Project, entry["projectId"])
                    consent = session.get(Consent, entry["consentId"])
                    if not project or project.deleted_at or not consent or not consent.opted_in or (
                            consent.version != entry["consentVersion"]):
                        invalid = True
                        break
                if invalid and not dataset.manifest.get("derivedCopyPurged"):
                    self.purge_prefix(f"training/{dataset.tenant_id}/{dataset.id}/")
                    dataset.status = "excluded"
                    dataset.manifest = {**dataset.manifest, "derivedCopyPurged": True,
                                        "excludedAt": at.isoformat()}
                    counts["excludedDatasets"] += 1
            cursor.payload = {"after": datasets[-1].id if len(datasets) == batch_size else None}
        self.checkpoints.schedule_expired(at, batch_size)
        counts.update(self.checkpoints.sweep(at=at, batch_size=batch_size))
        return counts


def main():
    from .config import get_settings
    from .db import Database
    from .storage import ArtifactStore
    parser = argparse.ArgumentParser(description="Apply Kleos9 retention or resume an authorized project deletion")
    parser.add_argument("operation", choices=["retention", "purge-project"])
    parser.add_argument("--project-id")
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000:
        parser.error("batch-size must be between 1 and 1000")
    if args.operation == "purge-project" and not args.project_id:
        parser.error("purge-project requires --project-id and an existing deletion tombstone")
    settings = get_settings()
    db = Database(settings.database_url)
    try:
        maintenance = Maintenance(db, settings, ArtifactStore(settings))
        result = (maintenance.retention(batch_size=args.batch_size) if args.operation == "retention"
                  else maintenance.purge_project(args.project_id))
        print(json.dumps(result))
    finally:
        db.close()


if __name__ == "__main__":
    main()
