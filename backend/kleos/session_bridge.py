"""Trusted worker bridge to a bounded native session; no generated code runs here."""
import asyncio
import hashlib
import json
import math
import os
import re
import stat
import tempfile
from datetime import UTC, timedelta
from pathlib import Path

from dbos import DBOS
from sqlalchemy import select

from .contracts import DomainError
from .db import Attempt, Invocation, NativeJob, NativeSessionStep, Project, Task, now
from .native_sessions import MAX_BYTES, NativeSessions, session_limits

_STEP_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


def _read_regular(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DomainError("Unsafe native session control file", 409) from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise DomainError("Native session control file must be regular", 409)
        if details.st_size > MAX_BYTES:
            raise DomainError("Native session control file exceeds 2 MiB", 413)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise DomainError("Native session control file exceeds 2 MiB", 413)
        return raw
    finally:
        os.close(descriptor)


class SessionBridge:
    def __init__(self, db, settings, execution):
        self.db, self.settings, self.execution = db, settings, execution
        self.sessions = NativeSessions(db, settings)

    def assert_live(self, job_id):
        """Uncached authorization for model-request hooks, even during durable replay."""
        with self.db.transaction() as session:
            return self.sessions._live(session, job_id)

    def remaining_seconds(self, job_id):
        """Fresh remaining compute lifetime, including time already spent queued."""
        job = self.assert_live(job_id)
        expires = job.expires_at.replace(tzinfo=UTC)
        created = job.created_at.replace(tzinfo=UTC)
        deadline = min(expires, created + timedelta(seconds=session_limits(job, self.settings)["maxSeconds"]))
        remaining = (deadline - now()).total_seconds()
        if remaining <= 0:
            raise DomainError("Native session deadline exceeded", 409, "session_expired")
        return remaining

    @staticmethod
    def _observed_numbers(provider, model, usage):
        if not isinstance(usage, dict) or not isinstance(provider, str) or not isinstance(model, str):
            raise DomainError("Invalid native session usage", 422)
        fields = {"inputTokens", "outputTokens", "totalTokens", "cachedInputTokens", "cacheReadTokens",
                  "requests", "searchCalls", "searchUsd", "estimatedUsd"}
        observed = {}
        for key in fields & usage.keys():
            number = usage[key]
            if number is None:
                continue
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not 0 <= number <= 1e15 or not math.isfinite(number):
                raise DomainError("Session usage must be finite nonnegative numbers", 422)
            observed[key] = number
        if not provider or len(provider) > 40 or not model or len(model) > 100:
            raise DomainError("Invalid native session provider/model", 422)
        return observed

    def record_usage(self, attempt_id, job_id, provider, model, usage):
        """Observe live SDK usage without settling or charging the reservation twice."""
        observed = self._observed_numbers(provider, model, usage)
        with self.db.transaction() as session:
            job = self.sessions._live(session, job_id, attempt_id)
            call = session.scalar(select(Invocation).where(
                Invocation.attempt_id == attempt_id, Invocation.tenant_id == job.tenant_id,
                Invocation.status == "pending").with_for_update())
            if not call:
                raise DomainError("Session usage requires an active reserved invocation", 409)
            previous = call.usage.get("sessionObserved", {})
            observed = {key: max(value, previous.get(key, 0)) for key, value in observed.items()}
            call.usage = {**call.usage, "sessionObserved": {**previous, **observed,
                                                          "provider": provider, "model": model}}

    def record_observed_usage(self, attempt_id, job_id, provider, model, usage):
        """Trusted post-response accounting only, including a cancelled in-flight call.

        This has no HTTP route, grants no tool/publication authority, and never
        changes reservation amounts, accepted state or a settled invocation.
        """
        observed = self._observed_numbers(provider, model, usage)
        with self.db.transaction() as session:
            job = session.scalar(select(NativeJob).where(NativeJob.id == job_id).with_for_update())
            attempt = session.get(Attempt, attempt_id)
            task = session.get(Task, job.task_id) if job else None
            project = session.get(Project, task.project_id) if task else None
            if (not job or job.purpose != "session" or job.attempt_id != attempt_id
                    or not attempt or not task or not project or attempt.task_id != task.id
                    or any(row.tenant_id != job.tenant_id for row in (attempt, task, project))):
                raise DomainError("Session accounting identity does not match", 409)
            call = session.scalar(select(Invocation).where(
                Invocation.attempt_id == attempt_id, Invocation.operation == "generate",
                Invocation.tenant_id == job.tenant_id, Invocation.project_id == project.id).with_for_update())
            if not call:
                raise DomainError("Session accounting requires its recorded invocation", 409)
            if call.status not in {"pending", "uncertain"}:
                return
            previous = call.usage.get("sessionObserved", {})
            observed = {key: max(value, previous.get(key, 0)) for key, value in observed.items()}
            call.usage = {**call.usage, "sessionObserved": {**previous, **observed,
                                                          "provider": provider, "model": model}}

    async def _sleep(self):
        if DBOS.workflow_id:
            await DBOS.sleep_async(.25)
        else:
            await asyncio.sleep(.25)

    def _directory(self, job_id):
        control = Path(self.execution.control_directory(job_id))
        if control.is_symlink():
            raise DomainError("Unsafe native session control directory", 409)
        control.mkdir(mode=0o700, parents=True, exist_ok=True)
        control.chmod(0o700)
        folder = control / "steps"
        if folder.is_symlink():
            raise DomainError("Unsafe native session step directory", 409)
        folder.mkdir(mode=0o700, exist_ok=True)
        # The trusted native supervisor shares this directory, generated UID 10001 does not.
        folder.chmod(0o700)
        return folder

    @DBOS.step()
    def _flush_local(self, job_id, step):
        self.assert_live(job_id)
        if not _STEP_ID.fullmatch(str(step["id"])):
            raise DomainError("Invalid native session step identifier", 422)
        folder = self._directory(job_id)
        target = folder / f"{step['id']}.request.json"
        body = json.dumps({"id": step["id"], "action": step["action"], "arguments": step["arguments"]},
                          sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(body) > MAX_BYTES:
            raise DomainError("Native session request exceeds 2 MiB", 413)
        existing = _read_regular(target)
        if existing is not None:
            if hashlib.sha256(existing).digest() != hashlib.sha256(body).digest():
                raise DomainError("Native session request changed during recovery", 409, "idempotency_conflict")
            target.chmod(0o600)
            return
        descriptor, temporary = tempfile.mkstemp(prefix=".step-", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            # Make rename durable before a native supervisor can consume the request.
            directory_fd = os.open(folder, os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _local_result(self, job_id, step_id):
        if not _STEP_ID.fullmatch(str(step_id)):
            raise DomainError("Invalid native session step identifier", 422)
        raw = _read_regular(self._directory(job_id) / f"{step_id}.result.json")
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise DomainError("Invalid native session result JSON", 422) from exc
        return self.sessions.complete_local(job_id, step_id, value)

    async def await_ready(self, job_id):
        """Capacity waits precede inference; only the normal dispatcher launches compute."""
        while True:
            job = self.assert_live(job_id)
            if job.status in {"submitted", "running"}:
                return
            await self._sleep()

    async def execute(self, attempt_id, job_id, request_key, action, arguments):
        if action == "finish":
            raise DomainError("Only the trusted worker can finish a native session", 403)
        self.assert_live(job_id)
        step = self.sessions.queue(attempt_id, job_id, request_key, action, arguments)
        if self.settings.execution_backend == "docker":
            self._flush_local(job_id, step)
        while True:
            self.assert_live(job_id)
            current = self.sessions.read(step["id"])
            if current["status"] in {"succeeded", "failed"}:
                self.assert_live(job_id)
                return {"status": current["status"], "result": current["result"]}
            if self.settings.execution_backend == "docker":
                current = self._local_result(job_id, step["id"])
                if current is not None:
                    self.assert_live(job_id)
                    return {"status": current["status"], "result": current["result"]}
            await self._sleep()

    def finish(self, attempt_id, job_id):
        """Queue the trusted handshake and return immediately, before task becomes waiting."""
        self.assert_live(job_id)
        step = self.sessions.queue(attempt_id, job_id, f"finish:{attempt_id}", "finish", {})
        if self.settings.execution_backend == "docker":
            self._flush_local(job_id, step)
        return step["id"]

    def ingest_local_finish(self, job_id):
        """Observe the final local handshake before the job becomes terminal/cleaned."""
        with self.db.transaction(read_only=True) as session:
            step = session.scalar(select(NativeSessionStep).where(
                NativeSessionStep.job_id == job_id, NativeSessionStep.action == "finish"))
            step_id = step.id if step else None
        if step_id is None:
            return None
        return self._local_result(job_id, step_id)
