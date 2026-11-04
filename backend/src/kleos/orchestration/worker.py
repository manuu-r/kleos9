"""Thin objective dispatcher. LangGraph alone owns workflow checkpoints and resumption."""

import asyncio
import hashlib
import logging
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, text

from kleos.contracts.models import Actor, DomainError
from kleos.orchestration.engine import RunScope
from kleos.projects import compute
from kleos.projects.graph_repository import execution_owner
from kleos.projects.store import Objective, Project

logger = logging.getLogger(__name__)


class PostgresRunLocks:
    """Session locks disappear on process/connection loss; no workflow lease state machine."""

    def __init__(self, engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("Production workers require PostgreSQL run locks")
        self.engine = engine

    @asynccontextmanager
    async def hold(self, run_id):
        key = int.from_bytes(hashlib.sha256(run_id.encode()).digest()[:8], "big", signed=True)
        with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            try:
                yield connection if acquired else None
            finally:
                if acquired:
                    try:
                        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    except Exception:
                        # Never return a pooled connection that might retain a session lock.
                        connection.invalidate()


class ObjectiveWorker:
    def __init__(
        self,
        projects,
        engine_factory,
        *,
        locks=None,
        max_run_seconds=1800,
        max_lifetime_seconds=None,
        max_tasks=32,
        heartbeat_seconds=2,
        on_observed=None,
    ):
        self.projects, self.engine_factory = projects, engine_factory
        self.locks = locks or PostgresRunLocks(projects.engine)
        self.max_run_seconds, self.max_tasks = max_run_seconds, max_tasks
        if max_lifetime_seconds is not None and not max_run_seconds <= max_lifetime_seconds <= 30 * 86400:
            raise ValueError('Objective lifetime must bound active work and remain within thirty days')
        self.max_lifetime_seconds = max_lifetime_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.on_observed = on_observed

    def pending(self, limit=20):
        with self.projects.transaction() as session:
            return list(
                session.scalars(
                    select(Objective.id)
                    .where(Objective.status.in_(["pending", "running"]))
                    .order_by(Objective.id)
                    .limit(limit)
                )
            )

    def _begin(self, run_id):
        with self.projects.transaction() as session:
            run = session.get(Objective, run_id)
            if run is None or run.status not in {"pending", "running"}:
                return None
            project = session.get(Project, run.project_id)
            actor = Actor(tenant_id=project.tenant_id, user_id=run.data["submittedBy"])
            self.projects.authorized(session, actor, project.id, write=True)
            session.refresh(run)
            if run.status not in {"pending", "running"}:
                return None
            scope = (
                RunScope.model_validate(run.data["scope"])
                if run.data.get("scope")
                else RunScope(
                    actor=actor,
                    project_id=project.id,
                    run_id=run.id,
                    revision=run.revision,
                    deadline_at=datetime.fromisoformat(run.data["createdAt"])
                    + timedelta(seconds=self.max_lifetime_seconds or self.max_run_seconds),
                    max_tasks=self.max_tasks,
                    compute_budget_seconds=self.max_run_seconds if self.max_lifetime_seconds is not None else None,
                )
            )
            data = {**run.data, "scope": scope.model_dump(mode="json"), "workerToken": str(uuid4())}
            if run.status != "running":
                run.status = "running"
                self.projects._emit(session, project, "objective.running", {"runId": run.id})
            run.data = data
            compute.begin(run, scope, data['workerToken'])
            return scope, data["objective"], data.get("pendingAnswer"), data["workerToken"]

    def _renew(self, scope):
        if scope.compute_budget_seconds is None:
            return
        with self.projects.transaction() as session:
            project = self.projects.authorized(session, scope.actor, scope.project_id, write=True)
            run = session.get(Objective, scope.run_id)
            if (run is None or run.status != 'running' or run.revision != scope.revision
                    or execution_owner.get() != (run.id, run.data.get('workerToken'))):
                raise DomainError('Active-work owner changed', 'worker_fenced')
            if project.context_revision != scope.revision:
                raise DomainError('Project changed during active work', 'stale_context')
            compute.renew(run, scope, run.data['workerToken'])

    def _finish(self, scope, owner):
        if scope.compute_budget_seconds is None:
            return
        with self.projects.transaction() as session:
            session.scalar(select(Project).where(Project.id == scope.project_id).with_for_update())
            run = session.get(Objective, scope.run_id)
            if run is not None and run.data.get('workerToken') == owner:
                compute.finish(run, scope, owner)

    async def _guarded(self, coroutine, engine, scope, connection):
        operation = asyncio.create_task(coroutine)
        try:
            while not operation.done():
                done, _ = await asyncio.wait({operation}, timeout=self.heartbeat_seconds)
                if done:
                    break
                # Losing the run lock or authority fences further local execution.
                if connection is not None:
                    connection.execute(text("SELECT 1"))
                self._renew(scope)
                await engine.repository.assert_active(scope)
            return await operation
        finally:
            if not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)

    def _save(self, scope, state, *, error=None):
        with self.projects.transaction() as session:
            project = self.projects.authorized(session, scope.actor, scope.project_id, write=True)
            run = session.get(Objective, scope.run_id)
            if run.status == "cancelled" or execution_owner.get() != (run.id, run.data.get("workerToken")):
                return
            values = state.values if state else {}
            interrupts = list(state.interrupts) if state else []
            data = {
                **run.data,
                "tasks": list(values.get("tasks", {}).values()),
                "superseded": values.get("superseded", {}),
                "summary": values.get("summary", ""),
                "question": interrupts[0].value if interrupts else None,
                "interruptId": interrupts[0].id if interrupts else None,
            }
            pending = data.get("pendingAnswer")
            if pending and not any(i.id == pending["interruptId"] for i in interrupts):
                data.pop("pendingAnswer", None)
            if project.context_revision != scope.revision:
                error = DomainError("Project changed since the objective started", "stale_context")
            if error is None:
                try:
                    compute.assert_active(run, scope, run.data.get('workerToken'))
                    if 'activeBudget' in run.data:
                        data['activeBudget'] = run.data['activeBudget']
                except DomainError as exc:
                    error = exc
            if error:
                run.status = "blocked"
                # Exception details may contain credentials or provider payloads. Keep stable codes only.
                data["blocker"] = error.code if isinstance(error, DomainError) else "execution_error"
            elif interrupts:
                run.status = "waiting"
                data["blocker"] = None
            elif values.get("done") and not state.next:
                run.status = "succeeded"
                data["blocker"] = None
            else:
                run.status = "blocked"
                data["blocker"] = "incomplete_objective"
            run.data = data
            if run.status == "blocked":
                self.projects.clear_pending(project, run.id, "blocked")
            elif run.status == "succeeded" and run.data.get("kind") not in {"project.capture", "project.solveInterfaces"}:
                self.projects.clear_pending(project, run.id, "review")
                if project.context_revision == scope.revision:
                    has_artifacts = any(result.get("artifacts") for result in run.data.get("results", {}).values())
                    project.data = {**project.data, "status": "review",
                        "displayStage": 5 if has_artifacts else project.data.get("displayStage", 0)}
            self.projects._emit(
                session,
                project,
                "objective.updated",
                {
                    "runId": run.id,
                    "status": run.status,
                    "blocker": data.get("blocker"),
                    "question": data.get("question"),
                    "interruptId": data.get("interruptId"),
                },
            )

    async def process(self, run_id):
        async with self.locks.hold(run_id) as connection:
            if connection is None:
                return False
            try:
                started = self._begin(run_id)
            except DomainError as exc:
                self._block(run_id, exc.code)
                return True
            except Exception as exc:
                # One run that cannot start must not take the worker, and every other run, down with it.
                self._log_failure(run_id, exc)
                self._block(run_id, "execution_error")
                return True
            if started is None:
                return False
            scope, objective, answer, worker_token = started
            token = execution_owner.set((run_id, worker_token))
            try:
                return await self._process_owned(scope, objective, answer, connection)
            except Exception as exc:
                self._log_failure(run_id, exc)
                self._block(
                    run_id,
                    exc.code if isinstance(exc, DomainError) else "execution_error",
                    worker_token=worker_token,
                )
                return True
            finally:
                try:
                    self._finish(scope, worker_token)
                finally:
                    execution_owner.reset(token)

    @staticmethod
    def _log_failure(run_id, exc):
        # Type, stable code and frames only: messages can carry credentials or provider payloads.
        frames = " <- ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}"
                             for f in reversed(traceback.extract_tb(exc.__traceback__)[-4:]))
        code = f" ({exc.code})" if isinstance(exc, DomainError) else ""
        logger.error("objective %s failed with %s%s at %s", run_id, type(exc).__name__, code, frames)

    def _block(self, run_id, code, *, worker_token=None):
        """Worker maintenance may stop unauthorized work, never perform it or publish files."""
        with self.projects.transaction() as session:
            run = session.get(Objective, run_id)
            if run is None:
                return
            project = session.scalar(select(Project).where(Project.id == run.project_id).with_for_update())
            session.refresh(run)
            if project is None or run.status not in {"pending", "running", "waiting"}:
                return
            if worker_token and run.data.get("workerToken") != worker_token:
                return
            run.status, run.data = "blocked", {**run.data, "blocker": code}
            self.projects.clear_pending(project, run.id, "blocked")
            self.projects._emit(
                session,
                project,
                "objective.updated",
                {
                    "runId": run.id,
                    "status": "blocked",
                    "blocker": code,
                },
            )

    async def _process_owned(self, scope, objective, answer, connection):
        engine = self.engine_factory(scope)
        config = engine._config(scope)
        before = await engine.graph.aget_state(config)
        error = None
        try:
            if before.values:
                # Match an answer to its exact persisted interruption. A later question
                # must never consume a stale answer after a worker crashes.
                resume = None
                if answer and any(i.id == answer["interruptId"] for i in before.interrupts):
                    resume = {answer["interruptId"]: answer["value"]}
                call = engine.resume(scope, answer=resume)
            else:
                call = engine.start(scope, objective)
            await self._guarded(call, engine, scope, connection)
        except Exception as exc:
            error = exc
            self._log_failure(scope.run_id, exc)
        after = await engine.graph.aget_state(config)
        if self.on_observed and error is None and after.values.get("done") and not after.next:

            async def record_memory():
                return await self.on_observed(scope, after.values)

            memory_status = "recorded"
            try:
                await self._guarded(
                    engine.repository.run_once(scope, "memory:final", record_memory),
                    engine,
                    scope,
                    connection,
                )
            except Exception as exc:
                memory_status = exc.code if isinstance(exc, DomainError) else "memory_error"
            with self.projects.transaction() as session:
                project, run = engine.repository._check(session, scope)
                run.data = {**run.data, "memoryStatus": memory_status}
                self.projects._emit(
                    session,
                    project,
                    "memory.observed",
                    {
                        "runId": run.id,
                        "status": memory_status,
                    },
                )
        self._save(scope, after, error=error)
        return True

    def answer(self, actor, project_id, run_id, interrupt_id, value, expected_revision, key):
        if not isinstance(value, str) or not value.strip() or len(value) > 12000:
            raise DomainError("Provide a bounded answer", "invalid_request", 422)

        def operation(session):
            project = self.projects.authorized(session, actor, project_id, write=True)
            run = session.get(Objective, run_id)
            if run is None or run.project_id != project.id:
                raise DomainError("Objective not found", "not_found", 404)
            if project.revision != expected_revision or run.revision != project.context_revision:
                raise DomainError("Objective context changed", "stale_context")
            if run.status != "waiting" or not interrupt_id or run.data.get("interruptId") != interrupt_id:
                raise DomainError("Objective is not waiting for this answer", "run_conflict")
            if (run.data.get('question') or {}).get('kind') == 'interface_prerequisite':
                raise DomainError('This objective requires a proposal-bound interface reply', 'prerequisite_reply_required')
            if (run.data.get('question') or {}).get('kind') == 'external_wait':
                raise DomainError('This objective awaits a verified external event', 'wait_event_invalid')
            run.data = {**run.data, "pendingAnswer": {"interruptId": interrupt_id, "value": value}}
            run.status = "pending"
            self.projects._emit(session, project, "objective.answered", {"runId": run.id})
            return {"projectId": project.id, "runId": run.id, "revision": project.revision}

        return self.projects._command(
            actor,
            key,
            {
                "operation": "objective.answer",
                "projectId": project_id,
                "runId": run_id,
                "interruptId": interrupt_id,
                "value": value,
                "expectedRevision": expected_revision,
            },
            operation,
        )

    async def serve(self, stop: asyncio.Event, poll_seconds=1):
        while not stop.is_set():
            for run_id in self.pending():
                if stop.is_set():
                    return
                await self.process(run_id)
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass
