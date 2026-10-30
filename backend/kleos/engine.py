import asyncio
import base64
import json
import logging
import traceback
from contextlib import nullcontext
from datetime import UTC, datetime

from dbos import DBOS
from opentelemetry.trace import StatusCode
from sqlalchemy import func, select

from .auth import Principal
from .clarification import answer_context, retain_unanswered_questions
from .contracts import AgentOutput, DomainError, JobResult
from .db import (
    Artifact,
    Attempt,
    DailyBudget,
    ExternalIntent,
    Invocation,
    NativeJob,
    Outbox,
    Run,
    Source,
    Task,
    Validation,
    now,
    uid,
)
from .domain import (
    ENGINEERING_LANES,
    ControlPlane,
    contract_hash,
    emit,
    get_project,
    lock_platform,
    settle_native_reservation,
)
from .engineering_tools import EngineeringToolRegistry
from .planning import (
    PLANNING_KINDS,
    append_message,
    concept_snapshot,
    context_manifest,
    conversation_context,
    planning_context,
    planning_state,
    update_concept,
)
from .storage import artifact_key, digest
from .telemetry import metric, redact, tracer

log = logging.getLogger(__name__)


class EngineeringEngine:
    def __init__(self, db, settings, store, models, execution=None, research=None, memory=None):
        self.db, self.settings, self.store, self.models, self.execution = db, settings, store, models, execution
        self.research, self.memory = research, memory

    @DBOS.step()
    def admit_run(self, run_id):
        with self.db.transaction() as session:
            lock_platform(session)
            run = session.get(Run, run_id)
            if not run or run.status in {"cancelled", "succeeded", "failed", "blocked"}:
                return []
            project = get_project(session, run.project_id)
            if run.revision != project.engineering_revision:
                run.status, run.blocker = "cancelled", "Superseded engineering revision"
                return []
            if run.status == "queued":
                planning = planning_state(project)
                not_before = planning.get("notBefore")
                if run.kind in PLANNING_KINDS and planning["autoResume"] and not_before and (
                        datetime.fromisoformat(not_before) > now()):
                    return None
                active = session.scalar(select(func.count(Run.id)).where(Run.status == "running"))
                own = session.scalar(select(func.count(Run.id)).where(
                    Run.status == "running", Run.tenant_id == run.tenant_id))
                if not self.settings.development_unlimited_usage and (
                        active >= self.settings.max_active_projects or own >= self.settings.max_org_projects):
                    return None
                run.status, project.status = "running", "running"
                emit(session, project, "run.started", {"runId": run_id}, run.trace_id)
            return session.scalars(select(Task.id).where(Task.run_id == run_id).order_by(Task.created_at)).all()

    @DBOS.step()
    def begin_attempt(self, task_id, retrieved=None):
        with self.db.transaction() as session:
            task = session.get(Task, task_id)
            project = get_project(session, task.project_id)
            run = session.get(Run, task.run_id)
            if task.status in {"succeeded", "cancelled", "blocked", "failed"} or run.status == "cancelled":
                return None
            if task.revision != project.engineering_revision:
                task.status = "cancelled"
                return None
            attempt = session.scalar(select(Attempt).where(
                Attempt.task_id == task.id, Attempt.status.in_(["running", "waiting"])).order_by(Attempt.number.desc()))
            if attempt:
                return attempt.id
            if task.kind == "project.retryNative":
                raise DomainError("Native rebuild has no pinned source attempt", 409, "native_retry_invalid")
            if self.settings.development_unlimited_usage or task.kind in {"project.capture", "project.requestReview", "project.research"}:
                # Each planning turn has its own persisted retry bound. In
                # unlimited development, manual engineering turns also start
                # fresh finite chains instead of sharing lifetime attempt caps.
                prior_count = session.scalar(select(func.count(Attempt.id)).where(Attempt.task_id == task.id))
            else:
                prior_count = session.scalar(select(func.count(Attempt.id)).join(Task, Task.id == Attempt.task_id).where(
                    Task.project_id == task.project_id, Task.revision == task.revision, Task.lane == task.lane,
                    Task.kind.in_(["project.generate", "project.repair", "project.pivot"])
                    if task.lane in ENGINEERING_LANES else Task.kind == task.kind))
            ceiling = min(3, self.settings.max_attempts) if task.lane in ENGINEERING_LANES and (
                task.kind != "project.pivot") else self.settings.max_attempts
            pivots = session.scalar(select(func.count(Attempt.id)).join(Task, Task.id == Attempt.task_id).where(
                Task.project_id == task.project_id, Task.revision == task.revision,
                Task.lane == task.lane, Task.kind == "project.pivot"))
            if prior_count >= ceiling or (not self.settings.development_unlimited_usage and task.kind == "project.pivot" and pivots):
                task.status, task.result = "blocked", {"reason": (
                    "Automatic attempts stopped after repeated failures; retry this run to continue"
                    if self.settings.development_unlimited_usage else "Attempt budget exhausted")}
                return None
            task.attempt_count += 1
            task.fence += 1
            choice = project.model_policy[task.lane]
            artifacts = session.scalars(select(Artifact).where(
                Artifact.project_id == project.id, Artifact.revision == task.revision,
                Artifact.status.in_(["candidate", "accepted"]))).all()
            sources = session.scalars(select(Source).where(
                Source.project_id == project.id, Source.tenant_id == project.tenant_id,
                Source.revoked.is_(False), Source.revision == task.revision).limit(12)).all()
            if retrieved:
                ranked_ids = [r["sourceId"] for r in retrieved.get("sources", [])]
                sources = sorted(sources, key=lambda s: ranked_ids.index(s.id) if s.id in ranked_ids else len(ranked_ids))
            failures = session.scalars(select(Validation).where(
                Validation.project_id == project.id, Validation.revision == project.engineering_revision,
                Validation.status != "PASS").limit(20)).all()
            context = {
                "projectId": project.id, "revision": project.engineering_revision,
                "task": task.kind, "lane": task.lane, "brief": project.brief,
                "requirements": project.requirements, "interfaces": project.interfaces,
                "proposal": project.state.get("proposal", {}),
                "clarification": answer_context(project),
                "planning": planning_context(project, bool(task.result.get("autoPlanning"))),
                "concept": concept_snapshot(project),
                "conversation": conversation_context(project),
                "inputArtifacts": [{"id": a.id, "sha256": a.sha256, "name": a.name} for a in artifacts],
                "acceptanceCriteria": ["requirements coverage", "shared interfaces", "independent tool validation"],
                "failures": [{"name": c.name, "status": c.status, "details": c.details} for c in failures],
                "remainingAllowanceUsd": None if self.settings.development_unlimited_usage else (
                    project.budget_usd - project.spent_usd - project.reserved_usd),
                "pivotReason": task.result.get("pivotReason"),
                "retrieval": {"method": "qdrant" if retrieved and retrieved.get("sources") else "bounded_source_records",
                              "limitation": (retrieved or {}).get("limitation")},
            }
            engineering_tool = EngineeringToolRegistry(self.settings).snapshot(project, task.lane)
            if (engineering_tool and self.execution and self.settings.execution_backend != "disabled"
                    and hasattr(self.execution, "pin_engineering_tool")):
                try:
                    engineering_tool = self.execution.pin_engineering_tool(engineering_tool, project.tenant_id, task.lane)
                except DomainError as exc:
                    if engineering_tool["id"] not in {"build123d", "atopile-kicad"} or exc.code != "tool_unconfigured":
                        raise
                    # Preserve the existing source-only default flow when local
                    # tool setup is incomplete; it cannot claim an executed tool.
                    engineering_tool["executionUnavailable"] = True
            if engineering_tool:
                context["engineeringTool"] = engineering_tool
            own_sources = []
            for artifact in artifacts:
                if artifact.lane == task.lane and artifact.media_type.startswith(("text/", "application/json")):
                    content = self.store.get(artifact.key)
                    if digest(content) != artifact.sha256:
                        raise DomainError("Input artifact failed its integrity check", 409, "artifact_integrity")
                    own_sources.append({"name": artifact.name, "sha256": artifact.sha256,
                                        "content": content.decode("utf-8", errors="replace")})
            context["previousSourceFiles"] = own_sources
            prior = session.scalars(select(Attempt).join(Task, Task.id == Attempt.task_id).where(
                Task.project_id == task.project_id, Task.revision == task.revision, Task.lane == task.lane)
                .order_by(Attempt.created_at.desc()).limit(4)).all()
            automatic = bool(context["planning"]["automatic"])
            context["repairHistory"] = [
                {"attemptId": a.id, "error": a.error,
                 "output": {"summary": a.output.get("summary", "")} if automatic else a.output}
                for a in prior]
            if automatic:
                context["proposal"] = {k: v for k, v in context["proposal"].items()
                                       if k in {"summary", "requirements", "sources", "limitations"}}
                context["acceptanceCriteria"] = ["user intent coverage", "illustrative concept", "saved answer lineage"]
            # The current scene is the reference. Older scenes remain in their
            # immutable attempt records rather than being repeated in prompts.
            context["manifest"] = context_manifest(context)
            # Mandatory context is never silently truncated.
            if len(json.dumps(context)) > self.settings.max_context_chars:
                raise DomainError("Mandatory context exceeds the budget; split the task", 409, "context_too_large")
            remaining = self.settings.max_context_chars - len(json.dumps(context))
            excerpts = []
            for source in sources[:3] if automatic else sources:
                text = source.text[:1200] if automatic else source.text
                if len(text) <= remaining:
                    excerpt = {"sourceId": source.id, "sha256": source.sha256, "uri": source.uri,
                               "authority": source.authority, "text": text}
                    if automatic:
                        excerpt["excerpted"] = len(text) < len(source.text)
                    excerpts.append(excerpt)
                    remaining -= len(text)
            context["sourceExcerpts"] = excerpts
            context["manifest"] = context_manifest(context)
            for _ in range(3):
                context["manifest"]["inputCharacterCount"] = len(json.dumps(context))
            if len(json.dumps(context)) > self.settings.max_context_chars:
                raise DomainError("Context manifest exceeds the budget; split the task", 409, "context_too_large")
            attempt = Attempt(id=uid(), tenant_id=task.tenant_id, task_id=task.id,
                              number=task.attempt_count, fence=task.fence,
                              policy_revision=project.policy_revision, provider=choice["provider"],
                              model=choice["model"], context=context)
            session.add(attempt)
            task.status = "running"
            emit(session, project, "task.started", {
                "runId": run.id, "taskId": task.id, "attemptId": attempt.id, "lane": task.lane,
                "provider": attempt.provider, "model": attempt.model, "policyRevision": attempt.policy_revision,
            }, run.trace_id)
            return attempt.id

    @DBOS.step()
    def reserve_invocation(self, attempt_id):
        with self.db.transaction() as session:
            lock_platform(session)
            attempt = session.get(Attempt, attempt_id)
            task = session.get(Task, attempt.task_id)
            project = get_project(session, task.project_id)
            self._fresh(session, task, attempt, project)
            existing = session.scalar(select(Invocation).where(
                Invocation.attempt_id == attempt_id, Invocation.operation == "generate"))
            if existing:
                if existing.status == "uncertain":
                    raise DomainError("This model call has an uncertain billed outcome; reconcile it before replaying", 409, "invocation_uncertain")
                if existing.status not in {"pending", "succeeded"}:
                    raise DomainError("This model call was already reconciled", 409, "invocation_settled")
                return existing.id, existing.output if existing.status == "succeeded" else None
            if task.kind in PLANNING_KINDS and attempt.context.get("planning", {}).get("inputVersion", 0) != planning_state(project)["inputVersion"]:
                raise DomainError("New requirements arrived before this model call started", 409, "planning_input_superseded")
            hosted = session.scalar(select(func.count(Invocation.id)).where(Invocation.status == "pending"))
            embeddings = session.scalar(select(func.count(ExternalIntent.id)).where(
                ExternalIntent.mode == "compute", ExternalIntent.operation.like("embedding:%"),
                ExternalIntent.status.in_(["reserved", "submitted"])))
            if hosted + embeddings >= self.settings.max_model_calls:
                raise DomainError("Hosted model capacity is reserved; retry after current calls finish", 429,
                                  "provider_capacity")
            if not self.models.registry.configured(attempt.provider):
                raise DomainError(f"{attempt.provider} API credentials are not configured", 503, "provider_unconfigured")
            reserve = self.settings.invocation_reservation_usd
            if not self.settings.development_unlimited_usage and (self.settings.daily_budget_usd == 0 or project.budget_usd <= 0):
                raise DomainError("An operator must configure project and daily allowances", 409, "budget_unconfigured")
            day = datetime.now(UTC).date().isoformat()
            daily = session.get(DailyBudget, day)
            if not daily:
                daily = DailyBudget(day=day, spent=0, reserved=0)
                session.add(daily)
            if not self.settings.development_unlimited_usage and project.spent_usd + project.reserved_usd + reserve > project.budget_usd:
                raise DomainError("Project allowance exhausted", 409, "budget_exhausted")
            if (not self.settings.development_unlimited_usage and self.settings.daily_budget_usd is not None
                    and daily.spent + daily.reserved + reserve > self.settings.daily_budget_usd):
                raise DomainError("Daily allowance exhausted", 409, "budget_exhausted")
            project.reserved_usd += reserve
            daily.reserved += reserve
            if task.status == "waiting":
                task.status = "running"
                emit(session, project, "task.resumed", {"taskId": task.id, "reason": "model_capacity_available"})
            invocation = Invocation(
                id=uid(), tenant_id=task.tenant_id, project_id=project.id, attempt_id=attempt_id,
                operation="generate", provider=attempt.provider, model=attempt.model,
                reserved_usd=reserve, usage={"reservationDay": day},
            )
            session.add(invocation)
            emit(session, project, "model.started", {"invocationId": invocation.id, "model": attempt.model,
                                                    "provider": attempt.provider})
            return invocation.id, None

    @DBOS.step()
    def wait_for_capacity(self, attempt_id):
        with self.db.transaction() as session:
            attempt = session.get(Attempt, attempt_id)
            task = session.get(Task, attempt.task_id)
            project = get_project(session, task.project_id)
            self._fresh(session, task, attempt, project)
            if task.status != "waiting":
                task.status = "waiting"
                emit(session, project, "task.waiting", {"taskId": task.id, "reason": "model_capacity"})

    async def reserve_when_available(self, attempt_id):
        # Embedding calls share hosted capacity but use a separate queue. A brief
        # overlap must wait durably without consuming another engineering attempt.
        polls = max(1, min(300, self.settings.worker_timeout_seconds) // 2)
        for index in range(polls + 1):
            try:
                return self.reserve_invocation(attempt_id)
            except DomainError as exc:
                if exc.code != "provider_capacity" or index == polls:
                    raise
                self.wait_for_capacity(attempt_id)
                if DBOS.workflow_id:
                    await DBOS.sleep_async(2)
                else:
                    await asyncio.sleep(2)

    async def run_task(self, task_id):
        attempt_id = None
        provider = "unknown"
        try:
            # Task kind is immutable. Deterministic native work must not embed a retrieval query.
            with self.db.transaction(read_only=True) as session:
                task = session.get(Task, task_id)
                task_kind = task.kind
                lean_planning = task_kind == "project.capture" or (task_kind == "project.requestReview" and task.result.get("autoPlanning"))
            retrieved = None if lean_planning or task_kind in {"project.retryNative", "project.verify"} else await self.retrieve_context(task_id)
            attempt_id = self.begin_attempt(task_id, retrieved)
            if not attempt_id:
                return
            context, provider, model, kind = self.attempt_input(attempt_id)
            if kind == "project.verify":
                return self.request_native(attempt_id, "validate")
            if kind == "project.retryNative":
                return self.request_native(attempt_id, "build")
            invocation_id, output = await self.reserve_when_available(attempt_id)
            if output is None:
                with tracer.start_as_current_span("model.generate", record_exception=False,
                        set_status_on_exception=False, attributes={
                        "kleos.attempt.id": attempt_id, "gen_ai.provider.name": provider,
                        "gen_ai.request.model": model}) as model_span:
                    try:
                        result, usage, transcript = await self.models.generate(provider, model, context)
                    except Exception as exc:
                        model_span.set_status(StatusCode.ERROR, type(exc).__name__)
                        raise
                output = result.model_dump(mode="json")
                self.complete_invocation(invocation_id, output, usage, transcript)
            self.store_candidate(attempt_id, AgentOutput.model_validate(output))
            if kind == "project.research" and self.research:
                output = {**output, "sourceRecords": await self.collect_sources(attempt_id, output.get("sources", []))}
            if kind in {"project.generate", "project.repair", "project.pivot"}:
                return self.request_native(attempt_id, "build")
            self.finish_attempt(attempt_id, output)
        except DomainError as exc:
            if attempt_id:
                self.mark_invocation_uncertain(attempt_id, exc.code)
            self.block_task(task_id, attempt_id, exc.message)
        except Exception as exc:
            # Do not put provider HTTP bodies, headers or credentials into activity.
            frames = [{"file": f.filename.rsplit("/", 1)[-1], "line": f.lineno, "function": f.name}
                      for f in traceback.extract_tb(exc.__traceback__)]
            status = getattr(exc, "status_code", None)
            if status == 429:
                metric("ProviderThrottled", 1, Provider=provider)
            log.error("Task %s failed (%s, HTTP %s); frames=%s", task_id, type(exc).__name__, status, frames)
            detail = f" (HTTP {status})" if status else ""
            if attempt_id:
                self.mark_invocation_uncertain(attempt_id, type(exc).__name__)
            self.block_task(task_id, attempt_id,
                            f"{type(exc).__name__}{detail}: execution interrupted; retry or switch provider")

    @DBOS.step()
    def mark_invocation_uncertain(self, attempt_id, failure_type):
        """A returned SDK failure ends live capacity, never the financial hold.

        The same recorded operation may not issue another paid request until an
        operator reconciles it. A later recorded response can still settle it.
        """
        with self.db.transaction() as session:
            lock_platform(session)
            invocation = session.scalar(select(Invocation).where(
                Invocation.attempt_id == attempt_id, Invocation.operation == "generate"))
            if invocation and invocation.status == "pending":
                invocation.status = "uncertain"
                invocation.usage = {**invocation.usage, "financialOutcome": "uncertain",
                                    "failureType": str(failure_type)[:100], "failedAt": now().isoformat()}

    @DBOS.step()
    async def retrieve_context(self, task_id):
        if not self.memory:
            return {}
        with self.db.transaction() as session:
            task = session.get(Task, task_id)
            project = get_project(session, task.project_id)
            indexed = session.scalar(select(Source.id).where(Source.project_id == project.id,
                Source.revision == project.engineering_revision, Source.revoked.is_(False),
                Source.indexed_at.is_not(None)).limit(1))
            if not indexed or task.lane == "research":
                return {}
            principal = Principal(project.owner_id, project.tenant_id, "member")
            query, project_id = f"{task.lane}: {project.brief[:3000]}", project.id
        try:
            return await asyncio.to_thread(self.memory.retrieve, principal, project_id, query)
        except Exception as exc:
            return {"limitation": f"Semantic retrieval unavailable ({type(exc).__name__}); using source records"}

    @DBOS.step()
    async def collect_sources(self, attempt_id, urls):
        with self.db.transaction() as session:
            attempt = session.get(Attempt, attempt_id)
            task = session.get(Task, attempt.task_id)
            project = get_project(session, task.project_id)
            self._fresh(session, task, attempt, project)
            principal = Principal(project.owner_id, project.tenant_id, "member")
            project_id = project.id
        records = []
        for url in list(dict.fromkeys(urls))[:6]:
            try:
                records.append(await self.research.collect(principal, project_id, url))
            except Exception as exc:
                records.append({"uri": redact(url), "status": "unavailable", "reason": type(exc).__name__})
        return records

    @DBOS.step()
    def attempt_input(self, attempt_id):
        with self.db.transaction() as session:
            attempt = session.get(Attempt, attempt_id)
            task = session.get(Task, attempt.task_id)
            return attempt.context, attempt.provider, attempt.model, task.kind

    @DBOS.step()
    def complete_invocation(self, invocation_id, output, usage, transcript):
        with self.db.transaction() as session:
            lock_platform(session)
            invocation = session.get(Invocation, invocation_id)
            if invocation.status == "succeeded":
                return
            if invocation.status not in {"pending", "uncertain"}:
                raise DomainError("Invocation was reconciled by an operator", 409, "invocation_settled")
            project = get_project(session, invocation.project_id)
            key = f"transcripts/{invocation.tenant_id}/{project.id}/{invocation.id}.json"
            messages = json.loads(transcript) if isinstance(transcript, str) else transcript
            self.store.put_json(key, redact({"output": output, "messages": messages}))
            # Until a billing reconciliation is available, retain a conservative allowance debit.
            reserved = invocation.reserved_usd
            amount = max(reserved, usage.get("estimatedUsd") or 0)
            daily = session.get(DailyBudget, invocation.usage["reservationDay"])
            project.reserved_usd = max(0, project.reserved_usd - reserved)
            project.spent_usd += amount
            daily.reserved, daily.spent = max(0, daily.reserved - reserved), daily.spent + amount
            invocation.status, invocation.output, invocation.transcript_key = "succeeded", output, key
            invocation.estimated_usd = usage.get("estimatedUsd") if usage.get("estimatedUsd") is not None else amount
            invocation.usage = {**invocation.usage, **usage, "costBasis": "conservative_reservation",
                                "allowanceDebitUsd": amount}
            emit(session, project, "model.completed", {"invocationId": invocation.id, "usage": usage,
                                                      "allowanceDebitUsd": amount})

    @DBOS.step()
    def store_candidate(self, attempt_id, output, _session=None, _provenance=None):
        with (nullcontext(_session) if _session else self.db.transaction()) as session:
            attempt = session.get(Attempt, attempt_id)
            task = session.get(Task, attempt.task_id)
            project = get_project(session, task.project_id)
            self._fresh(session, task, attempt, project)
            retry_sources = {ref["id"] for ref in attempt.context.get("nativeRetry", {}).get("inputArtifacts", [])}
            if output.files or task.lane in {"research", "coordination", "review"}:
                for previous in session.scalars(select(Artifact).where(
                        Artifact.project_id == project.id, Artifact.revision == task.revision,
                        Artifact.lane == task.lane, Artifact.attempt_id != attempt.id,
                        Artifact.status != "stale")):
                    if previous.id not in retry_sources:
                        previous.status = "stale"
            for file in output.files:
                content = (base64.b64decode(file.content, validate=True)
                           if file.encoding == "base64" else file.content.encode())
                key = artifact_key(task.tenant_id, task.project_id, attempt.id, file.path, content)
                if session.scalar(select(Artifact.id).where(Artifact.key == key)):
                    continue
                self.store.put(key, content, file.media_type)
                artifact = Artifact(id=uid(), tenant_id=task.tenant_id, project_id=task.project_id,
                                    task_id=task.id, attempt_id=attempt.id, revision=task.revision, lane=task.lane,
                                    name=file.path, key=key, sha256=digest(content), size=len(content),
                                    media_type=file.media_type,
                                    provenance={"provider": attempt.provider, "model": attempt.model,
                                                "policyRevision": attempt.policy_revision,
                                                "toolVersion": self.settings.dbos_application_version,
                                                **({"engineeringTool": {key: value for key, value in
                                                    attempt.context["engineeringTool"].items() if key in
                                                    {"id", "version", "definitionHash", "installationHash", "policyVersion"}}}
                                                   if attempt.context.get("engineeringTool") else {}),
                                                **(_provenance or {})},
                                    dependencies=(attempt.context["nativeRetry"]["inputArtifacts"] if retry_sources
                                                  else attempt.context["inputArtifacts"]))
                session.add(artifact)
                emit(session, project, "artifact.created", {"artifactId": artifact.id, "name": file.path,
                                                           "sha256": artifact.sha256, "lane": task.lane})
            # Large file bodies live in immutable storage, never in workspace responses.
            metadata = output.model_dump(mode="json", exclude={"files"})
            metadata["files"] = [{"path": f.path, "mediaType": f.media_type} for f in output.files]
            attempt.output = {**attempt.output, **metadata}

    @DBOS.step()
    def request_native(self, attempt_id, purpose):
        if not self.execution or self.settings.execution_backend == "disabled":
            raise DomainError("Native worker images are not configured; candidate files are available", 503,
                              "native_unconfigured")
        with self.db.transaction() as session:
            attempt = session.get(Attempt, attempt_id)
            task = session.get(Task, attempt.task_id)
            project = get_project(session, task.project_id)
            self._fresh(session, task, attempt, project)
            attempt.status, task.status = "waiting", "waiting"
            emit(session, project, "task.waiting", {"taskId": task.id, "reason": "native_job"})
        self.execution.submit(attempt_id, purpose)

    def finish_native(self, job_id, result: JobResult):
        stale_validation = False
        with self.db.transaction() as session:
            lock_platform(session)
            job = session.get(NativeJob, job_id)
            attempt = session.get(Attempt, job.attempt_id)
            task = session.get(Task, attempt.task_id)
            project = get_project(session, task.project_id)
            session.refresh(job)
            self._fresh(session, task, attempt, project)
            if job.status in {"succeeded", "failed", "unsupported"}:
                return
            if job.purpose == "validate":
                hashes = {a.id: a.sha256 for a in session.scalars(select(Artifact).where(
                    Artifact.project_id == project.id, Artifact.revision == task.revision,
                    Artifact.status.in_(["candidate", "accepted"]))) }
                pinned = {a["id"]: a["sha256"] for a in job.request["inputArtifacts"]}
                pinned_contract = contract_hash(job.request.get("requirements", {}), job.request.get("interfaces", {}))
                if hashes != pinned or pinned_contract != contract_hash(project.requirements, project.interfaces):
                    stale_validation = True
                    result = JobResult(status="failed", limitations=[
                        "Candidate files changed during validation; run validation again"])
                for check in result.checks:
                    if check.evidence_kind != "digital":
                        raise DomainError("Native validators cannot submit physical evidence", 403)
                    if result.status != "succeeded" and check.status in {"PASS", "NOT_APPLICABLE"}:
                        continue
                    session.add(Validation(tenant_id=project.tenant_id, project_id=project.id, task_id=task.id,
                                           revision=task.revision, name=check.name, status=check.status,
                                           evidence_kind="digital", artifact_hashes=pinned,
                                           details={**check.details, "contractHash": pinned_contract},
                                           validator=job.request["toolVersion"]))
            log_content = redact(result.model_dump(mode="json", exclude={"files"}))
            log_hash = digest(json.dumps(log_content, sort_keys=True, default=str).encode())
            result_key = f"logs/{project.tenant_id}/{project.id}/{job.id}/{log_hash}.json"
            self.store.put_json(result_key, log_content)
            job.status, job.result = result.status, {"status": result.status, "logKey": result_key,
                                                   "limitations": result.limitations}
            reservation = job.request.get("reservation", {})
            settle_native_reservation(session, job, project, refund=not reservation.get("dispatchAttempted", True))
            emit(session, project, "native.completed", {"jobId": job.id, "status": result.status})
            if result.files:
                self.store_candidate(job.attempt_id, AgentOutput(summary="Native outputs", files=result.files),
                                     _session=session, _provenance={"nativeJobId": job.id,
                                                                 **({"engineeringTool": {
                                                                     key: value for key, value in job.request["engineeringTool"].items()
                                                                     if key in {"id", "version", "definitionHash", "installationHash", "policyVersion"}}}
                                                                    if job.request.get("engineeringTool") else {}),
                                                                 "toolVersion": job.request["toolVersion"]})
            if result.status != "succeeded":
                self.block_task(task.id, attempt.id,
                                "; ".join(result.limitations) or "Native execution did not pass", _session=session)
                attempt.output = {**attempt.output, "nativeFailure": redact(result.logs[-12000:]),
                                  "nativeChecks": [c.model_dump(mode="json") for c in result.checks]}
                if (job.purpose == "build" and task.kind != "project.retryNative"
                        and result.measurements.get("failureKind") == "engineering"):
                    used = session.scalar(select(func.count(Attempt.id)).join(Task, Task.id == Attempt.task_id).where(
                        Task.project_id == project.id, Task.revision == task.revision, Task.lane == task.lane,
                        Task.kind.in_(["project.generate", "project.repair", "project.pivot"])))
                    if self.settings.development_unlimited_usage:
                        # User-requested retry turns are unlimited; each automatic
                        # repair chain remains finite within its original task.
                        used = session.scalar(select(func.count(Attempt.id)).where(Attempt.task_id == task.id))
                    if used < min(3, self.settings.max_attempts) and task.kind != "project.pivot" and (
                            self.settings.development_unlimited_usage or project.spent_usd + project.reserved_usd
                            + self.settings.invocation_reservation_usd <= project.budget_usd):
                        task.status = "queued"
                        session.add(Outbox(key=f"repair:{task.id}:{task.attempt_count + 1}", kind="task",
                                           payload={"taskId": task.id}))
                        emit(session, project, "repair.scheduled", {"taskId": task.id, "lane": task.lane,
                                                                  "nextAttempt": task.attempt_count + 1})
            else:
                self.finish_attempt(attempt.id, {"nativeJobId": job.id, "checks": [
                    c.model_dump(mode="json") for c in result.checks]}, _session=session)
            session.flush()
            self.finish_run(task.run_id, _session=session)
        if stale_validation:
            raise DomainError("Candidate files changed during validation; run validation again", 409,
                              "stale_validation")

    @DBOS.step()
    def finish_attempt(self, attempt_id, output, _session=None):
        with (nullcontext(_session) if _session else self.db.transaction()) as session:
            attempt = session.get(Attempt, attempt_id)
            task = session.get(Task, attempt.task_id)
            project = get_project(session, task.project_id)
            self._fresh(session, task, attempt, project)
            if attempt.status == "succeeded":
                return
            attempt.status, task.status, task.result = "succeeded", "succeeded", output
            if task.kind in PLANNING_KINDS:
                planning = planning_state(project)
                input_version = attempt.context.get("planning", {}).get("inputVersion", 0)
                if input_version != planning["inputVersion"]:
                    task.result = {"supersededPlanningInput": True, "inputVersion": input_version}
                    emit(session, project, "planning.superseded", {"attemptId": attempt.id, "inputVersion": input_version,
                                                                 "currentInputVersion": planning["inputVersion"]})
                    return
                proposal = {**output, "clarificationQuestions": retain_unanswered_questions(output, attempt.context)}
                proposal.pop("clarification_questions", None)
                project.state = {**project.state, "proposal": proposal, "confirmed": False,
                                 "proposalReview": task.kind == "project.requestReview",
                                 "clarificationReviewedVersion": attempt.context.get("clarification", {}).get("version", 0)}
                planning = {**planning, "captureComplete": True, "pending": False,
                            "appliedInputVersion": input_version,
                            "feasibilityComplete": planning["feasibilityComplete"] or task.kind == "project.research"}
                if task.kind == "project.research":
                    planning = {**planning, "pendingResearch": False, "lastResearchInputVersion": input_version}
                elif output.get("researchRequired", output.get("research_required", False)) and planning["lastResearchInputVersion"] < input_version:
                    planning["pendingResearch"] = True
                phase = "capture" if task.kind == "project.capture" else "feasibility" if task.kind == "project.research" else "refining"
                update_concept(project, output, phase, task.run_id, attempt.id, input_version)
                pending = bool(answer_context(project)["remainingRequired"])
                planning["phase"] = "clarifying" if pending else "ready" if planning["feasibilityComplete"] else "capture"
                project.state = {**project.state, "planning": planning}
                message = append_message(project, "assistant", output.get("summary", "Concept updated."), "planning",
                                         runId=task.run_id, attemptId=attempt.id)
                emit(session, project, "planning.assistantMessage", message)
                project.stage = 2 if planning["feasibilityComplete"] or task.kind != "project.capture" else 0
            elif task.kind == "project.solveInterfaces":
                if not output.get("interfaces"):
                    raise DomainError("The model did not establish interfaces", 409, "interfaces_unknown")
                project.interfaces = output["interfaces"]
                from .circuit_validation import validate_circuit_contract
                circuit_check = validate_circuit_contract(project.interfaces).model_dump(mode="json", by_alias=True)
                circuit_check["details"] = {**circuit_check["details"], "engineeringRevision": task.revision,
                                            "contractHash": contract_hash(project.requirements, project.interfaces)}
                project.state = {**project.state, "circuitContractCheck": circuit_check}
                task.result = {**task.result, "circuitContractCheck": circuit_check}
                project.stage = 4
            elif task.kind in {"project.generate", "project.repair", "project.pivot", "project.retryNative"}:
                project.stage = 5
            elif task.kind == "project.prepareKit":
                project.state = {**project.state, "kit": output}
                project.stage = 8
            emit(session, project, "task.completed", {"taskId": task.id, "attemptId": attempt.id,
                                                     "summary": output.get("summary", "")})

    @DBOS.step()
    def block_task(self, task_id, attempt_id, reason, _session=None):
        with (nullcontext(_session) if _session else self.db.transaction()) as session:
            task = session.get(Task, task_id)
            project = get_project(session, task.project_id)
            if task.status == "cancelled":
                return
            task.status, task.result = "blocked", {"reason": reason}
            if attempt_id:
                attempt = session.get(Attempt, attempt_id)
                attempt.status, attempt.error = "blocked", reason
            emit(session, project, "task.blocked", {"taskId": task_id, "reason": reason})

    @DBOS.step()
    def finish_run(self, run_id, _session=None):
        with (nullcontext(_session) if _session else self.db.transaction()) as session:
            run = session.get(Run, run_id)
            project = get_project(session, run.project_id)
            if run.status in {"cancelled", "succeeded", "blocked", "failed"}:
                return
            tasks = session.scalars(select(Task).where(Task.run_id == run.id)).all()
            if any(t.status in {"queued", "running", "waiting"} for t in tasks):
                return
            blocked = [t for t in tasks if t.status != "succeeded"]
            run.status = "blocked" if blocked else "succeeded"
            run.blocker = "; ".join(t.result.get("reason", "Task cancelled") for t in blocked) or None
            run.finished_at, project.status = now(), run.status
            emit(session, project, f"run.{run.status}", {"runId": run.id, "reason": run.blocker})
            if run.kind in PLANNING_KINDS:
                planning = planning_state(project)
                if blocked and not planning["pending"]:
                    project.state = {**project.state, "planning": {**planning, "phase": "blocked"}}
                elif not blocked or planning["pending"]:
                    ControlPlane(self.db, self.settings, self.store)._resume_planning(session, project)

    @staticmethod
    def _fresh(session, task, attempt, project):
        run = session.get(Run, task.run_id)
        if task.fence != attempt.fence or project.engineering_revision != task.revision or (
                run.status == "cancelled" or task.status == "cancelled"):
            raise DomainError("This attempt has been superseded or cancelled", 409, "stale_attempt")
