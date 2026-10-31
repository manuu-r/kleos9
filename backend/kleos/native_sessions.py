"""Trusted durable native-tool ledger; tool diagnostics never grant validation authority."""
import hashlib
import json
from datetime import UTC, timedelta
from typing import Literal

from dbos import DBOS
from fastapi import Header
from pydantic import Field
from sqlalchemy import func, select

from .contracts import DomainError, WireModel
from .db import Attempt, Invocation, NativeJob, NativeSessionStep, Project, Run, Task, now
from .telemetry import redact

ACTIONS = frozenset({"read", "patch", "build", "analyze", "render", "finish"})
MAX_BYTES = 2 * 1024 * 1024


def _encoded(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise DomainError("Session payload must be finite JSON", 422) from exc
    if len(raw) > MAX_BYTES:
        raise DomainError("Session payload exceeds 2 MiB", 413)
    return hashlib.sha256(raw).hexdigest()


def session_limits(job, settings):
    """Operator reductions apply immediately; increases never extend a pinned job."""
    pinned = job.request.get("sessionLimits", {})
    if not isinstance(pinned, dict):
        raise DomainError("Invalid native session limits", 409)
    result = {}
    for key, current in (("maxSteps", settings.native_session_max_steps),
                         ("maxSeconds", settings.native_session_max_seconds)):
        original = pinned.get(key, current)
        if isinstance(original, bool) or not isinstance(original, int) or original <= 0:
            raise DomainError("Invalid pinned native session limits", 409)
        result[key] = min(current, original)
    return result


def session_step_summary(step):
    """Bounded inspector projection: source and image bodies stay in the private ledger."""
    def safe(value, depth=0):
        if depth > 8:
            return "[omitted]"
        if isinstance(value, dict):
            result = {}
            for key, item in list(value.items())[:100]:
                lowered = key.lower()
                if lowered in {"content", "text", "source", "body"}:
                    result[key + "Sha256"] = hashlib.sha256(str(item).encode()).hexdigest()
                elif "base64" not in lowered:
                    result[key] = safe(item, depth + 1)
            return result
        if isinstance(value, list):
            return [safe(item, depth + 1) for item in value[:64]]
        if isinstance(value, str):
            return value[:2000]
        return value

    return redact({"id": step.id, "action": step.action, "status": step.status,
                   "sequence": step.sequence, "arguments": safe(step.arguments),
                   "result": safe(step.result)})


class SessionResult(WireModel):
    status: Literal["succeeded", "failed"]
    result: dict = Field(default_factory=dict)


class NativeSessions:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def _live(self, session, job_id, attempt_id=None):
        if not self.settings.native_sessions_enabled:
            raise DomainError("Native sessions are disabled", 403, "session_disabled")
        # Serialize sequence allocation, finalization and competing native pollers on PostgreSQL.
        job = session.scalar(select(NativeJob).where(NativeJob.id == job_id).with_for_update())
        if not job or job.purpose != "session":
            raise DomainError("Native session not found", 404)
        if attempt_id is not None and attempt_id != job.attempt_id:
            raise DomainError("Session attempt does not match", 409)
        attempt = session.get(Attempt, job.attempt_id)
        task = session.get(Task, job.task_id)
        project = session.get(Project, task.project_id) if task else None
        run = session.get(Run, task.run_id) if task else None
        if (not attempt or not task or not project or not run or project.deleted_at
                or attempt.task_id != task.id
                or any(row.tenant_id != job.tenant_id for row in (attempt, task, project, run))
                or task.fence != attempt.fence or task.revision != project.engineering_revision
                or run.revision != project.engineering_revision
                or task.status not in {"running", "waiting"} or attempt.status not in {"running", "waiting"}
                or run.status not in {"running", "waiting"}
                or job.status not in {"pending", "dispatching", "submitted", "running"}):
            raise DomainError("Native session is no longer active", 409, "stale_attempt")
        deadline = min(job.expires_at.replace(tzinfo=UTC), job.created_at.replace(tzinfo=UTC)
                       + timedelta(seconds=session_limits(job, self.settings)["maxSeconds"]))
        if now() >= deadline:
            raise DomainError("Native session deadline exceeded", 409, "session_expired")
        return job

    @staticmethod
    def _emit(session, job, step, kind):
        from .domain import emit

        task = session.get(Task, job.task_id)
        project = session.scalar(select(Project).where(Project.id == task.project_id).with_for_update())
        emit(session, project, kind, {"jobId": job.id, "stepId": step.id,
                                     "action": step.action, "sequence": step.sequence,
                                     **({"status": step.status} if kind.endswith("completed") else {})})

    @staticmethod
    def _view(step):
        return {"id": step.id, "jobId": step.job_id, "attemptId": step.attempt_id,
                "sequence": step.sequence, "action": step.action, "arguments": step.arguments,
                "status": step.status, "result": step.result}

    @DBOS.step()
    def queue(self, attempt_id, job_id, request_key, action, arguments):
        """Trusted worker only. request_key is the durable model tool-call identity."""
        if action not in ACTIONS or not isinstance(arguments, dict):
            raise DomainError("Unsupported native session action", 422)
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
            raise DomainError("Session request key is required", 422)
        request_hash = _encoded({"action": action, "arguments": arguments})
        with self.db.transaction() as session:
            job = self._live(session, job_id, attempt_id)
            previous = session.scalar(select(NativeSessionStep).where(
                NativeSessionStep.job_id == job_id, NativeSessionStep.request_key == request_key))
            if previous:
                if previous.request_hash != request_hash:
                    raise DomainError("Session request key has different contents", 409, "idempotency_conflict")
                return self._view(previous)
            if session.scalar(select(NativeSessionStep.id).where(
                    NativeSessionStep.job_id == job_id, NativeSessionStep.action == "finish")):
                raise DomainError("Native session is already finishing", 409)
            if action != "finish" and session.get(Attempt, attempt_id).status != "running":
                raise DomainError("Native session is awaiting its final result", 409)
            if action != "finish" and not session.scalar(select(Invocation.id).where(
                    Invocation.attempt_id == attempt_id, Invocation.tenant_id == job.tenant_id,
                    Invocation.status == "pending")):
                raise DomainError("Native session requires a reserved model invocation", 409)
            count = session.scalar(select(func.count()).select_from(NativeSessionStep).where(
                NativeSessionStep.job_id == job_id))
            limit = session_limits(job, self.settings)["maxSteps"]
            if count >= limit or (action != "finish" and count >= limit - 1):
                raise DomainError("Native session step limit reached", 409, "session_step_limit")
            step = NativeSessionStep(job_id=job_id, attempt_id=attempt_id, tenant_id=job.tenant_id,
                                     request_key=request_key, sequence=count + 1, action=action,
                                     arguments=arguments, request_hash=request_hash)
            session.add(step)
            session.flush()
            self._emit(session, job, step, "native.session.queued")
            return self._view(step)

    @DBOS.step()
    def read(self, step_id):
        with self.db.transaction() as session:
            step = session.get(NativeSessionStep, step_id)
            if not step:
                raise DomainError("Session step not found", 404)
            return self._view(step)

    get = read

    def next_local(self, job_id):
        with self.db.transaction() as session:
            self._live(session, job_id)
            step = session.scalar(select(NativeSessionStep).where(
                NativeSessionStep.job_id == job_id, NativeSessionStep.status.in_(["queued", "running"]))
                .order_by(NativeSessionStep.sequence).limit(1))
            if not step:
                return {"step": None}
            step.status = "running"
            return {"step": {"id": step.id, "action": step.action, "arguments": step.arguments}}

    def complete_local(self, job_id, step_id, envelope):
        """Trusted worker transport, applying identical checks as the authenticated callback."""
        try:
            value = SessionResult.model_validate(envelope).model_dump()
        except ValueError as exc:
            raise DomainError("Invalid session result", 422) from exc
        result = value["result"]
        if any(key in result for key in ("validations", "acceptedRelease", "releaseAuthority")) or result.get("checks"):
            raise DomainError("Session results cannot confer validation or release authority", 422)
        result_hash = _encoded(value)
        with self.db.transaction() as session:
            job = self._live(session, job_id)
            step = session.get(NativeSessionStep, step_id)
            if not step or step.job_id != job_id:
                raise DomainError("Session step not found", 404)
            if step.result_hash:
                if step.result_hash != result_hash:
                    raise DomainError("Session result conflicts with its recorded completion", 409, "idempotency_conflict")
                return self._view(step)
            # A local request file may be delivered directly without HTTP next polling.
            if session.scalar(select(NativeSessionStep.id).where(
                    NativeSessionStep.job_id == job_id, NativeSessionStep.sequence < step.sequence,
                    NativeSessionStep.status.in_(["queued", "running"]))):
                raise DomainError("An earlier native step has not completed", 409)
            step.status, step.result, step.result_hash = value["status"], result, result_hash
            step.completed_at = now()
            self._emit(session, job, step, "native.session.completed")
            return self._view(step)

    def nativeNext(self, job_id, token, execution):
        execution.authorize(job_id, token)
        return self.next_local(job_id)

    def nativeResult(self, job_id, step_id, token, result, execution):
        execution.authorize(job_id, token)
        return self.complete_local(job_id, step_id, result)


def register_session_routes(app, execution, service):
    @app.get("/api/v1/internal/jobs/{job_id}/session/next")
    def next_step(job_id: str, authorization: str = Header(default="")):
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        return service.nativeNext(job_id, token, execution)

    @app.post("/api/v1/internal/jobs/{job_id}/session/{step_id}/result")
    def complete_step(job_id: str, step_id: str, result: SessionResult,
                      authorization: str = Header(default="")):
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        return service.nativeResult(job_id, step_id, token, result.model_dump(), execution)
