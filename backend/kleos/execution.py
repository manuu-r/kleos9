"""Durable submission and scoped access for isolated ECS engineering jobs.

The application owns admission and publication. A native task owns only its
input closure and a single candidate result; it never receives database keys.
"""
import base64
import hashlib
import hmac
import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import httpx
from sqlalchemy import and_, func, or_, select

from .contracts import DomainError, JobResult
from .db import Artifact, Attempt, DailyBudget, Invocation, NativeJob, Project, Run, Task, now, uid
from .domain import contract_hash, emit, get_project, lock_platform, settle_native_reservation
from .engineering_tools import DEFAULT_TOOL_POLICY, EngineeringToolRegistry
from .native_resources import NativeResources
from .source_closure import current_sources, reference, resolve_sources
from .storage import digest
from .telemetry import metric

TERMINAL = {"succeeded", "failed", "unsupported", "cancelled"}
INFLIGHT = {"dispatching", "submitted", "running"}
MAX_INPUT_BYTES = 32 * 1024 * 1024


class ECSExecution:
    def __init__(self, db, settings, store, ecs_client=None, sqs_client=None):
        self.db, self.settings, self.store = db, settings, store
        self.ecs = ecs_client
        self.sqs = sqs_client

    def _client(self):
        if self.ecs is None:
            self.ecs = boto3.client("ecs", region_name=self.settings.aws_region)
        return self.ecs

    def _cluster(self, job):
        return job.request.get("dispatch", {}).get("cluster", self.settings.ecs_cluster)

    def _owns(self, job):
        # Legacy native jobs predate the Docker backend and belong to ECS.
        return job.request.get("dispatch", {}).get("backend", "ecs") == self.settings.execution_backend

    def _job_scope(self):
        backend = NativeJob.request["dispatch"]["backend"].as_string()
        if self.settings.execution_backend == "ecs":
            return or_(backend == "ecs", backend.is_(None))
        return backend == self.settings.execution_backend

    def _token(self, job_id):
        if not self.settings.session_key:
            raise DomainError("A server signing key is required for native execution", 503)
        return hmac.new(
            self.settings.session_key.get_secret_value().encode(),
            f"native-job-v1:{job_id}".encode(), hashlib.sha256,
        ).hexdigest()

    def _definition(self, image_kind):
        definition = self.settings.ecs_task_definitions.get(image_kind)
        if not definition:
            raise DomainError(f"No qualified native image configured for {image_kind}", 503, "tool_unconfigured")
        if not re.fullmatch(r".+:[1-9][0-9]*", definition):
            raise DomainError("Native ECS tasks must pin a task-definition revision", 503, "tool_unconfigured")
        return definition

    def _dispatch_configuration(self):
        if not self.settings.ecs_cluster or not self.settings.ecs_subnets or not self.settings.ecs_security_groups:
            raise DomainError("ECS networking is not configured", 503)
        return {"cluster": self.settings.ecs_cluster, "backend": "ecs", "subnets": self.settings.ecs_subnets,
                "securityGroups": self.settings.ecs_security_groups,
                "callbackBaseUrl": self.settings.internal_base_url}

    def pin_engineering_tool(self, snapshot, tenant_id, lane):
        registry = EngineeringToolRegistry(self.settings)
        profile = registry.pinned(snapshot, tenant_id, lane)
        return {**snapshot, "executionDefinition": self._definition(registry.image_key(profile)),
                "executionBackend": self.settings.execution_backend}

    def _retry_inputs(self, session, task, attempt, project, purpose):
        retry = attempt.context.get("nativeRetry")
        if purpose != "build" or not isinstance(retry, dict):
            raise DomainError("Native retry requires an explicit source closure", 409, "stale_artifacts")
        current_contract = contract_hash(project.requirements, project.interfaces)
        if retry.get("sourceKind") == "attempt":
            source_attempt = session.get(Attempt, retry.get("sourceAttemptId"))
            source_task = session.get(Task, source_attempt.task_id) if source_attempt else None
            recorded_model = session.scalar(select(Invocation.id).where(
                Invocation.attempt_id == retry.get("sourceAttemptId"), Invocation.operation == "generate",
                Invocation.status == "succeeded"))
            if (not source_attempt or not source_task or source_attempt.tenant_id != project.tenant_id
                    or source_task.tenant_id != project.tenant_id or source_task.project_id != project.id
                    or source_task.lane != task.lane or source_task.revision != task.revision
                    or source_task.id != retry.get("sourceTaskId")
                    or source_task.kind not in {"project.generate", "project.repair", "project.pivot"}
                    or source_task.status not in {"succeeded", "blocked", "failed", "cancelled"}
                    or source_attempt.status not in {"succeeded", "blocked", "failed"} or not recorded_model
                    or not (source_attempt.context.get("sourceArtifacts") or source_attempt.output.get("files"))
                    or source_attempt.context.get("retentionExpired")
                    or retry.get("contractHash") != current_contract
                    or contract_hash(source_attempt.context.get("requirements", {}),
                                     source_attempt.context.get("interfaces", {})) != current_contract):
                raise DomainError("Native retry source attempt is no longer applicable", 409, "stale_artifacts")
            root_attempt = source_attempt.id
        else:
            source = session.get(NativeJob, retry.get("sourceJobId"))
            source_task = session.get(Task, source.task_id) if source else None
            if (not source or not source_task or source.tenant_id != project.tenant_id
                    or source_task.project_id != project.id or source_task.lane != task.lane
                    or source.purpose not in {"build", "session"} or source.status not in {"succeeded", "failed", "unsupported"}
                    or source_task.revision != task.revision or source.request.get("revision") != task.revision
                    or source.attempt_id != retry.get("sourceAttemptId") or source.task_id != retry.get("sourceTaskId")
                    or retry.get("contractHash") != current_contract
                    or contract_hash(source.request.get("requirements", {}), source.request.get("interfaces", {})) != current_contract
                    or retry.get("inputArtifacts") != source.result.get("sourceArtifacts", source.request.get("inputArtifacts"))):
                raise DomainError("Native retry source is no longer applicable", 409, "stale_artifacts")
            root_attempt = source.request.get("nativeRetry", {}).get("rootSourceAttemptId", source.attempt_id)
        refs = retry["inputArtifacts"]
        if (retry.get("rootSourceAttemptId") != root_attempt or not isinstance(refs, list) or not refs
                or any(not isinstance(ref, dict) or not ref.get("id") for ref in refs)
                or len({ref["id"] for ref in refs}) != len(refs)):
            raise DomainError("Native retry source lineage is invalid", 409, "stale_artifacts")
        if retry.get("sourceKind") == "attempt":
            output_files = source_attempt.output.get("files")
            if (not isinstance(output_files, list) or any(not isinstance(item, dict) for item in output_files)
                    or len({item.get("path") for item in output_files}) != len(output_files)
                    or not {item.get("path") for item in output_files}.issubset({ref.get("name") for ref in refs})):
                raise DomainError("Native retry source output closure changed", 409, "stale_artifacts")
        if retry.get("sourceKind") == "attempt" and source_attempt.context.get("sourceArtifacts") is not None:
            if refs != source_attempt.context["sourceArtifacts"]:
                raise DomainError("Native source manifest changed", 409, "stale_artifacts")
        try:
            return resolve_sources(session, project, task.lane, refs)
        except DomainError as exc:
            raise DomainError("Native retry input closure changed", exc.status, exc.code) from exc

    def submit(self, attempt_id, purpose):
        if purpose not in {"build", "validate", "session"}:
            raise DomainError("Unsupported native operation", 422)
        if purpose == "session" and not self.settings.native_sessions_enabled:
            raise DomainError("Native sessions are disabled", 409, "session_disabled")
        with self.db.transaction() as session:
            lock_platform(session)
            attempt = session.get(Attempt, attempt_id)
            if not attempt:
                raise DomainError("Attempt not found", 404)
            task = session.get(Task, attempt.task_id)
            project = get_project(session, task.project_id)
            self._fresh(session, task, attempt, project)
            previous = session.scalar(select(NativeJob).where(NativeJob.attempt_id == attempt_id))
            if previous:
                return previous.id
            image_kind = "validate" if purpose == "validate" else task.lane
            engineering_tool = None
            if purpose in {"build", "session"} and task.lane in DEFAULT_TOOL_POLICY:
                registry = EngineeringToolRegistry(self.settings)
                pinned = attempt.context.get("engineeringTool")
                profile = registry.pinned(pinned, project.tenant_id, task.lane)
                engineering_tool = pinned or registry.describe(profile)
                image_kind = registry.image_key(profile)
            # Ordinary attempts retain the installation resolved before model
            # generation. Explicit default-tool source rebuilds may use the
            # current qualified image and must be independently checked again.
            refresh_default = task.kind == "project.retryNative" and engineering_tool and engineering_tool["id"] in {
                "build123d", "atopile-kicad"}
            if engineering_tool and engineering_tool.get("executionDefinition") and not refresh_default:
                if engineering_tool.get("executionBackend") != self.settings.execution_backend:
                    raise DomainError("The attempt belongs to another execution backend", 409, "tool_changed")
                definition = engineering_tool["executionDefinition"]
            else:
                definition = self._definition(image_kind)
            if refresh_default:
                engineering_tool = {**engineering_tool, "executionDefinition": definition,
                                    "executionBackend": self.settings.execution_backend,
                                    "installationHash": registry.installation_hash(profile)}
            dispatch = self._dispatch_configuration()
            q = select(Artifact).where(
                Artifact.project_id == project.id, Artifact.revision == task.revision,
                Artifact.status.in_(["candidate", "accepted"]),
            )
            if task.kind == "project.retryNative":
                artifacts = self._retry_inputs(session, task, attempt, project, purpose)
            elif purpose in {"build", "session"}:
                refs = attempt.context.get("sourceArtifacts")
                artifacts = (resolve_sources(session, project, task.lane, refs) if refs is not None
                             else current_sources(session, project, task.lane))
                attempt.context = {**attempt.context, "sourceArtifacts": [reference(a) for a in artifacts]}
            else:
                artifacts = session.scalars(q.order_by(Artifact.name)).all()
            deterministic_circuit = (purpose == "build" and task.lane == "electronics" and engineering_tool
                                     and engineering_tool.get("id") == "atopile-kicad" and engineering_tool.get("version") == "2")
            if not artifacts and not deterministic_circuit and purpose != "session":
                raise DomainError("No input artifacts are available for this native task", 409)
            if sum(a.size for a in artifacts) > MAX_INPUT_BYTES:
                raise DomainError("Native input exceeds the 32 MiB transfer limit; split the task", 413)
            reserve = self.settings.native_job_reservation_usd
            if reserve <= 0 or (not self.settings.development_unlimited_usage and (
                    project.budget_usd <= 0 or self.settings.daily_budget_usd == 0)):
                raise DomainError("Configure positive project and daily allowances before native execution", 409, "budget_unconfigured")
            day = now().date().isoformat()
            daily = session.get(DailyBudget, day)
            if daily is None:
                daily = DailyBudget(day=day, spent=0, reserved=0)
                session.add(daily)
            if not self.settings.development_unlimited_usage and project.spent_usd + project.reserved_usd + reserve > project.budget_usd:
                raise DomainError("Project native compute allowance exhausted", 409, "budget_exhausted")
            if (not self.settings.development_unlimited_usage and self.settings.daily_budget_usd is not None
                    and daily.spent + daily.reserved + reserve > self.settings.daily_budget_usd):
                raise DomainError("Daily native compute allowance exhausted", 409, "budget_exhausted")
            project.reserved_usd += reserve
            daily.reserved += reserve
            job_id = uid()
            expires = now() + timedelta(seconds=self.settings.worker_timeout_seconds)
            request = {
                "jobId": job_id, "tenantId": project.tenant_id, "projectId": project.id,
                "taskId": task.id, "attemptId": attempt.id, "revision": task.revision,
                "traceId": session.get(Run, task.run_id).trace_id,
                "fence": attempt.fence, "lane": task.lane, "purpose": purpose,
                "schemaVersion": 1, "sandboxPolicyVersion": 3, "toolVersion": definition,
                "resources": NativeResources.model_validate(self.settings.native_resource_profiles.get(
                    image_kind, self.settings.native_resource_profiles["_default"])).model_dump(),
                "reservation": {"day": day, "reservedUsd": reserve, "settled": False, "dispatchAttempted": False},
                "modelPolicyVersion": attempt.policy_revision,
                "model": {"provider": attempt.provider, "id": attempt.model},
                "contextManifest": attempt.context.get("manifest", {}),
                "dispatch": dispatch,
                "interfaces": project.interfaces, "requirements": project.requirements,
                "expiresAt": expires.isoformat(), "timeoutSeconds": self.settings.worker_timeout_seconds,
                "inputArtifacts": [
                    {"id": a.id, "name": a.name, "lane": a.lane, "sha256": a.sha256,
                     "size": a.size, "mediaType": a.media_type, "provenance": a.provenance}
                    for a in artifacts
                ],
            }
            if task.kind == "project.retryNative":
                request["nativeRetry"] = {key: value for key, value in attempt.context["nativeRetry"].items()
                                          if key != "inputArtifacts"}
            if purpose == "session":
                request["sessionLimits"] = {"maxSteps": self.settings.native_session_max_steps,
                                            "maxSeconds": self.settings.native_session_max_seconds}
            if engineering_tool:
                request["engineeringTool"] = engineering_tool
            session.add(NativeJob(
                id=job_id, tenant_id=project.tenant_id, task_id=task.id,
                attempt_id=attempt.id, purpose=purpose, status="pending",
                token_hash=digest(self._token(job_id).encode()), request=request,
                expires_at=expires,
            ))
            emit(session, project, "native.queued", {"jobId": job_id, "purpose": purpose, "lane": task.lane})
        return job_id

    @staticmethod
    def _fresh(session, task, attempt, project):
        run = session.get(Run, task.run_id)
        if task.fence != attempt.fence or project.engineering_revision != task.revision or (
            run.status == "cancelled" or task.status == "cancelled"
        ):
            raise DomainError("This job has been superseded or cancelled", 409, "stale_attempt")

    def authorize(self, job_id, token):
        with self.db.transaction() as session:
            job = session.get(NativeJob, job_id)
            if not job or not hmac.compare_digest(job.token_hash, digest(token.encode())):
                raise DomainError("Invalid native job credential", 401)
            if job.expires_at.replace(tzinfo=UTC) <= now():
                raise DomainError("Native job access has expired", 401)
            attempt = session.get(Attempt, job.attempt_id)
            task = session.get(Task, job.task_id)
            project = get_project(session, task.project_id)
            self._fresh(session, task, attempt, project)
            if job.status == "cancelled":
                raise DomainError("Native job cancelled", 409)
            return job

    def cancel_session(self, job_id):
        """Fence this attempt's session only; reconciliation observes actual termination."""
        with self.db.transaction() as session:
            lock_platform(session)
            job = session.get(NativeJob, job_id)
            if job and job.purpose == "session" and job.status not in TERMINAL - {"cancelled"}:
                project = session.get(Project, session.get(Task, job.task_id).project_id)
                if project:
                    settle_native_reservation(
                        session, job, project,
                        refund=not job.request.get("reservation", {}).get("dispatchAttempted", False),
                    )
                job.status = "cancelled"
                if project and not project.deleted_at:
                    emit(session, project, "native.cancelled", {"jobId": job.id, "reason": "model_session_stopped"})

    def payload(self, job_id, token):
        job = self.authorize(job_id, token)
        request = dict(job.request)
        inputs = []
        total = 0
        with self.db.transaction() as session:
            for ref in request["inputArtifacts"]:
                artifact = session.get(Artifact, ref["id"])
                if (not artifact or artifact.tenant_id != job.tenant_id or artifact.sha256 != ref["sha256"]
                        or artifact.status not in {"candidate", "accepted"}):
                    raise DomainError("Native input closure changed", 409, "stale_artifacts")
                content = self.store.get(artifact.key)
                total += len(content)
                if total > MAX_INPUT_BYTES or digest(content) != ref["sha256"]:
                    raise DomainError("Native input integrity check failed", 409)
                inputs.append({**ref, "contentBase64": base64.b64encode(content).decode()})
        request["inputArtifacts"] = inputs
        return request

    def dispatch_pending(self):
        """Safe to call from multiple workers; persisted dispatch claims count as capacity."""
        while True:
            with self.db.transaction() as session:
                lock_platform(session)
                occupied = or_(
                    NativeJob.status.in_(INFLIGHT),
                    and_(NativeJob.status == "cancelled",
                         NativeJob.request["reservation"]["dispatchAttempted"].as_boolean().is_(True),
                         NativeJob.result["terminationObservedAt"].as_string().is_(None)))
                # Orphaned cancellation tombstones still hold global capacity.
                active = session.scalar(select(func.count(NativeJob.id)).where(occupied))
                if active >= self.settings.max_native_jobs:
                    return
                projects = (select(Task.project_id.label("project_id"), func.count(NativeJob.id).label("active"))
                    .select_from(NativeJob).join(Task, Task.id == NativeJob.task_id)
                    .where(occupied).group_by(Task.project_id).subquery())
                occupancy = func.coalesce(projects.c.active, 0)
                job = session.scalar(select(NativeJob).join(Task, Task.id == NativeJob.task_id)
                    .outerjoin(projects, projects.c.project_id == Task.project_id).where(
                        NativeJob.status == "pending", NativeJob.expires_at > now(), self._job_scope(),
                        occupancy < self.settings.max_native_jobs_per_project)
                    .order_by(occupancy, NativeJob.created_at, NativeJob.id)
                    .with_for_update(skip_locked=True, of=NativeJob).limit(1))
                if not job:
                    return
                if not self._dispatchable(session, job):
                    continue
                job.status = "dispatching"
                job_id = job.id
                metric("NativeInFlight", active + 1)
                metric("NativeQueueAgeSeconds", max(0, (now()-job.created_at.replace(tzinfo=UTC)).total_seconds()),
                       unit="Seconds", Lane=job.request.get("lane", "unknown"))
            self._dispatch(job_id)

    def _dispatch(self, job_id):
        with self.db.transaction() as session:
            lock_platform(session)
            job = session.get(NativeJob, job_id)
            if not job or job.status != "dispatching":
                return
            if not self._dispatchable(session, job):
                return
            request = job.request
            reservation = request.get("reservation", {})
            if reservation and not reservation.get("dispatchAttempted"):
                job.request = {**request, "reservation": {**reservation, "dispatchAttempted": True}}
                request = job.request
            dispatch = request.get("dispatch", {})
        # The same parameters and client token are reused after an uncertain submission.
        result = self._client().run_task(
            cluster=dispatch.get("cluster", self.settings.ecs_cluster), taskDefinition=request["toolVersion"],
            clientToken=job_id, count=1, launchType="FARGATE",
            startedBy=job_id, group=f"kleos-{request['purpose']}",
            networkConfiguration={"awsvpcConfiguration": {
                "subnets": dispatch.get("subnets", self.settings.ecs_subnets),
                "securityGroups": dispatch.get("securityGroups", self.settings.ecs_security_groups),
                "assignPublicIp": "DISABLED",
            }},
            overrides={**({"cpu": str(request["resources"]["cpuUnits"]),
                           "memory": str(request["resources"]["memoryMiB"]),
                           "ephemeralStorage": {"sizeInGiB": request["resources"]["ephemeralStorageGiB"]}}
                          if request.get("resources") else {}),
                       "containerOverrides": [{
                "name": "native",
                "environment": [
                    {"name": "KLEOS_JOB_ID", "value": job_id},
                    {"name": "KLEOS_JOB_TOKEN", "value": self._token(job_id)},
                    {"name": "KLEOS_INTERNAL_BASE_URL", "value": dispatch.get("callbackBaseUrl", self.settings.internal_base_url)},
                ],
            }]},
            enableExecuteCommand=False,
            tags=[{"key": "kleos-job-id", "value": job_id}],
        )
        if result.get("failures") or not result.get("tasks"):
            # Explicit rejection has no running task. Retry admission rather than losing the job.
            with self.db.transaction() as session:
                lock_platform(session)
                job = session.get(NativeJob, job_id)
                if job.status == "dispatching":
                    job.status = "pending"
            raise DomainError("AWS could not provision native compute; the job remains queued", 503, "compute_capacity")
        arn = result["tasks"][0]["taskArn"]
        with self.db.transaction() as session:
            lock_platform(session)
            job = session.get(NativeJob, job_id)
            job.external_id = arn
            if job.status == "dispatching":
                job.status = "submitted"
            cancelled = job.status == "cancelled"
        if cancelled:
            self._terminate_cancelled(job_id, arn)

    def _dispatchable(self, session, job):
        if not self._owns(job):
            return False
        attempt = session.get(Attempt, job.attempt_id)
        task = session.get(Task, job.task_id)
        project = session.get(Project, task.project_id)
        try:
            self._fresh(session, task, attempt, project)
            if job.purpose == "build" and job.request.get("engineeringTool"):
                EngineeringToolRegistry(self.settings).pinned(
                    job.request["engineeringTool"], project.tenant_id, task.lane)
            if project.deleted_at:
                raise DomainError("Project deleted", 409)
            return True
        except DomainError:
            settle_native_reservation(session, job, project,
                                      refund=not job.request.get("reservation", {}).get("dispatchAttempted"))
            job.status = "cancelled"
            job.result = {**job.result, "cancellationReason": "Superseded project revision or run"}
            return False

    def record_task_timing(self, job_id, observed):
        """Infrastructure observations are diagnostics, never validation or a provider bill."""
        values = {}
        def timestamp(value):
            if isinstance(value, str):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if not isinstance(value, datetime):
                raise ValueError("Missing task timestamp")
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        for name, start, end in [("imagePullSeconds", "pullStartedAt", "pullStoppedAt"),
                                 ("launchToStartSeconds", "createdAt", "startedAt"),
                                 ("runningSeconds", "startedAt", "stoppedAt")]:
            try:
                seconds = (timestamp(observed.get(end))-timestamp(observed.get(start))).total_seconds()
                if 0 <= seconds <= 7*86400:
                    values[name] = round(seconds, 3)
            except (ValueError, TypeError, OverflowError):
                pass
        if not values:
            return
        with self.db.transaction() as session:
            lock_platform(session)
            job = session.get(NativeJob, job_id)
            if job:
                job.result = {**job.result, "infrastructureUsage": {**job.result.get("infrastructureUsage", {}), **values}}
        for key, value in values.items():
            metric("Native"+key[0].upper()+key[1:], value, unit="Seconds", Backend=self.settings.execution_backend)

    def reconcile(self):
        """Return terminal infrastructure failures for the engine to record as job results."""
        self.receive_completion_hints()
        with self.db.transaction() as session:
            jobs = session.scalars(select(NativeJob).where(NativeJob.status.in_(
                {"pending", "dispatching", "submitted", "running", "cancelled"}
            ), self._job_scope())).all()
        failures = []
        for job in jobs:
            if job.status == "cancelled":
                self._reconcile_cancelled(job)
            elif job.expires_at.replace(tzinfo=UTC) <= now():
                if job.external_id:
                    self._client().stop_task(cluster=self._cluster(job), task=job.external_id,
                                             reason="Kleos job deadline exceeded")
                failures.append((job.id, JobResult(status="failed", limitations=["Native job deadline exceeded"])))
            elif job.status == "dispatching":
                self._dispatch(job.id)
            elif job.external_id:
                observed = self._client().describe_tasks(cluster=self._cluster(job), tasks=[job.external_id])
                tasks = observed.get("tasks", [])
                if tasks:
                    self.record_task_timing(job.id, tasks[0])
                if tasks and tasks[0].get("lastStatus") == "STOPPED":
                    failures.append((job.id, JobResult(
                        status="failed", limitations=["Native task stopped before a result was committed"],
                        logs=tasks[0].get("stoppedReason", "")[:10000],
                    )))
                elif tasks and tasks[0].get("lastStatus") == "RUNNING":
                    with self.db.transaction() as session:
                        lock_platform(session)
                        current = session.get(NativeJob, job.id)
                        if current.status == "submitted":
                            current.status = "running"
        self.dispatch_pending()
        return failures

    def receive_completion_hints(self):
        """SQS is a wakeup hint, never acceptance evidence. Polling ECS remains authoritative."""
        if not self.settings.sqs_completion_queue:
            return
        if self.sqs is None:
            self.sqs = boto3.client("sqs", region_name=self.settings.aws_region)
        messages = self.sqs.receive_message(
            QueueUrl=self.settings.sqs_completion_queue, MaxNumberOfMessages=10,
            WaitTimeSeconds=0, VisibilityTimeout=120,
        ).get("Messages", [])
        for message in messages:
            try:
                event = json.loads(message["Body"])
                detail = event["detail"]
                if event["source"] != "aws.ecs" or event["detail-type"] != "ECS Task State Change":
                    # Malformed/unexpected messages redrive to the configured DLQ.
                    continue
                with self.db.transaction() as session:
                    lock_platform(session)
                    job = session.scalar(select(NativeJob).where(NativeJob.external_id == detail["taskArn"]))
                    if job and self._cluster(job) != detail["clusterArn"]:
                        continue
                    if not job and detail["clusterArn"] != self.settings.ecs_cluster:
                        known_cluster = session.scalar(select(NativeJob.id).where(
                            NativeJob.request["dispatch"]["cluster"].as_string() == detail["clusterArn"]
                        ).limit(1))
                        if not known_cluster:
                            continue
                    if job and job.status not in TERMINAL:
                        job.result = {**(job.result or {}), "infrastructureHint": {
                            "eventId": event["id"], "lastStatus": detail["lastStatus"],
                            "receivedAt": now().isoformat(),
                        }}
                self.sqs.delete_message(QueueUrl=self.settings.sqs_completion_queue,
                                        ReceiptHandle=message["ReceiptHandle"])
            except (KeyError, ValueError, TypeError):
                continue

    def cancel_run(self, run_id):
        with self.db.transaction() as session:
            lock_platform(session)
            jobs = session.scalars(select(NativeJob).join(Task, NativeJob.task_id == Task.id).where(
                Task.run_id == run_id,
                NativeJob.status.not_in(TERMINAL - {"cancelled"})
            )).all()
            pending = [(job.id, job.external_id) for job in jobs
                       if self._owns(job) and job.external_id and not (job.result or {}).get("terminationRequested")]
            for job in jobs:
                reservation = job.request.get("reservation", {})
                if reservation and not reservation.get("settled"):
                    task = session.get(Task, job.task_id)
                    project = session.get(Project, task.project_id)
                    settle_native_reservation(session, job, project,
                                              refund=not reservation.get("dispatchAttempted", False))
                job.status = "cancelled"
        for job_id, arn in pending:
            self._terminate_cancelled(job_id, arn)

    def _terminate_cancelled(self, job_id, arn):
        with self.db.transaction() as session:
            cluster = self._cluster(session.get(NativeJob, job_id))
        response = self._client().stop_task(cluster=cluster, task=arn, reason="Kleos run cancelled")
        with self.db.transaction() as session:
            lock_platform(session)
            job = session.get(NativeJob, job_id)
            job.result = {**(job.result or {}), "terminationRequested": True}
            if response.get("task", {}).get("lastStatus") == "STOPPED":
                job.result = {**job.result, "terminationObservedAt": now().isoformat()}

    def _reconcile_cancelled(self, job):
        if (job.result or {}).get("terminationObservedAt"):
            return
        if not job.request.get("reservation", {}).get("dispatchAttempted"):
            return
        arn = job.external_id
        if not arn:
            # Covers a crash after AWS accepted RunTask but before its ARN was committed.
            tasks = self._client().list_tasks(cluster=self._cluster(job), startedBy=job.id).get("taskArns", [])
            if tasks:
                arn = tasks[0]
                with self.db.transaction() as session:
                    lock_platform(session)
                    session.get(NativeJob, job.id).external_id = arn
            elif job.expires_at.replace(tzinfo=UTC) <= now():
                with self.db.transaction() as session:
                    lock_platform(session)
                    current = session.get(NativeJob, job.id)
                    current.result = {**current.result, "terminationObservedAt": now().isoformat(),
                                      "terminationObservation": "No running ECS task found after deadline"}
                return
        if not arn:
            return
        if not (job.result or {}).get("terminationRequested"):
            self._terminate_cancelled(job.id, arn)
        observation = self._client().describe_tasks(cluster=self._cluster(job), tasks=[arn]).get("tasks", [])
        if observation and observation[0].get("lastStatus") == "STOPPED":
            with self.db.transaction() as session:
                lock_platform(session)
                current = session.get(NativeJob, job.id)
                current.result = {**current.result, "terminationObservedAt": now().isoformat()}


class DockerTasks:
    """Development-only Docker Engine transport; native containers receive no socket."""

    def __init__(self, execution, client=None):
        self.execution = execution
        self.http = client or httpx.Client(
            transport=httpx.HTTPTransport(uds=execution.settings.docker_socket),
            base_url="http://docker/v1.45", timeout=30, trust_env=False,
        )

    def request(self, method, path, **kwargs):
        response = self.http.request(method, path, **kwargs)
        if response.status_code not in {200, 201, 204, 304, 404, 409}:
            # Docker error text can contain paths; never serialize request headers or env.
            raise DomainError(f"Local Docker operation failed ({response.status_code})", 503, "compute_capacity")
        return response

    def inspect(self, identity):
        response = self.request("GET", f"/containers/{identity}/json")
        return None if response.status_code == 404 else response.json()

    def run_task(self, **kwargs):
        job_id = kwargs["clientToken"]
        name = f"kleos-native-{job_id}"
        found = self.inspect(name)
        if found:
            if found["Config"].get("Labels", {}).get("kleos.job") != job_id:
                raise DomainError("Local container identity conflict", 409)
            if found["State"]["Status"] == "created":
                self.request("POST", f"/containers/{found['Id']}/start")
            return {"tasks": [{"taskArn": found["Id"]}]}
        execution = self.execution
        request = execution.payload(job_id, execution._token(job_id))
        folder = execution.control_directory(job_id)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder.chmod(0o700)
        content = json.dumps(request, separators=(",", ":")).encode()
        source = folder / "request.json"
        # A prior uncertain create must reuse the same immutable input closure.
        if source.exists() and source.read_bytes() != content:
            raise DomainError("Local native input changed during recovery", 409)
        temporary = folder / "request.tmp"
        temporary.write_bytes(content)
        temporary.chmod(0o600)
        temporary.replace(source)
        resources = (NativeResources.model_validate(request["resources"]).model_dump() if request.get("resources") else
                     {"cpuUnits": 1024, "memoryMiB": 1024, "tmpMiB": 2048, "pidsLimit": 256})
        specification = {
            "Image": request["toolVersion"], "Entrypoint": ["python", "-m", "kleos.native_runner"],
            "Cmd": ["--local-files"], "Env": ["KLEOS_NATIVE_SANDBOX=1", "KLEOS_NATIVE_DOCKER_NETWORK_NONE=1"],
            "User": "0", "Labels": {"kleos.job": job_id, "kleos.execution": "development"},
            "HostConfig": {
                "NetworkMode": "none", "ReadonlyRootfs": request.get("sandboxPolicyVersion", 1) >= 2 or request["lane"] != "application",
                "Memory": resources["memoryMiB"] * 1024**2, "NanoCpus": resources["cpuUnits"] * 1_000_000_000 // 1024,
                "PidsLimit": resources["pidsLimit"],
                "CapDrop": ["ALL"], "CapAdd": ["CHOWN", "SETUID", "SETGID", "KILL", "DAC_OVERRIDE"],
                "SecurityOpt": ["no-new-privileges:true"],
                "Tmpfs": {"/tmp": f"rw,exec,nosuid,size={resources['tmpMiB'] * 1024**2}"},
                "Mounts": [{"Type": "volume", "Source": request["dispatch"]["volume"],
                            "Target": "/control", "VolumeOptions": {"Subpath": job_id}}],
                "LogConfig": {"Type": "json-file", "Config": {"max-size": "1m", "max-file": "1"}},
            },
        }
        if request["lane"] == "application" and request.get("sandboxPolicyVersion", 1) >= 3:
            # Android's private toolchain is several GiB. Use disposable disk
            # scratch, like Fargate, instead of charging that copy to RAM.
            specification["HostConfig"]["Tmpfs"] = {}
            specification["HostConfig"]["Mounts"].append({
                "Type": "volume", "Target": "/tmp",
                "VolumeOptions": {"NoCopy": True, "Labels": {"kleos.job": job_id, "kleos.role": "native-scratch"}},
            })
        response = self.request("POST", "/containers/create", params={"name": name, "platform": "linux/amd64"},
                                json=specification)
        if response.status_code == 409:
            return self.run_task(**kwargs)
        identity = response.json()["Id"]
        self.request("POST", f"/containers/{identity}/start")
        return {"tasks": [{"taskArn": identity}]}

    def stop_task(self, *, task, **_):
        self.request("POST", f"/containers/{task}/stop", params={"t": 3})
        observed = self.inspect(task)
        return {"task": {"lastStatus": "STOPPED" if not observed or not observed["State"]["Running"] else "RUNNING"}}

    def list_tasks(self, *, startedBy, **_):
        found = self.inspect(f"kleos-native-{startedBy}")
        return {"taskArns": [found["Id"]] if found else []}

    def describe_tasks(self, *, tasks, **_):
        found = self.inspect(tasks[0])
        if not found:
            return {"tasks": [{"taskArn": tasks[0], "lastStatus": "STOPPED", "stoppedReason": "Container absent"}]}
        state = found["State"]
        return {"tasks": [{"taskArn": found["Id"], "lastStatus": "RUNNING" if state["Running"] else "STOPPED",
                           "stoppedReason": f"Container exit {state.get('ExitCode')}", "exitCode": state.get("ExitCode"),
                           "oomKilled": state.get("OOMKilled", False), "createdAt": found.get("Created"),
                           "startedAt": state.get("StartedAt"), "stoppedAt": state.get("FinishedAt")}]}

    def logs(self, identity):
        response = self.request("GET", f"/containers/{identity}/logs",
                                params={"stdout": "true", "stderr": "true", "tail": "30"})
        data, chunks = response.content, []
        while len(data) >= 8 and data[0] in {1, 2}:
            size = int.from_bytes(data[4:8], "big")
            chunks.append(data[8:8 + size])
            data = data[8 + size:]
        return b"".join(chunks)[-10000:].decode(errors="replace")


class DockerExecution(ECSExecution):
    """Same admission, authorization, fencing and acceptance contract on local Docker."""

    def __init__(self, db, settings, store, docker_client=None):
        if settings.environment not in {"development", "test"}:
            raise ValueError("Local Docker execution is prohibited in cloud environments")
        super().__init__(db, settings, store)
        self.ecs = DockerTasks(self, docker_client)

    def control_directory(self, job_id):
        if not re.fullmatch(r"[a-f0-9-]{32,36}", job_id):
            raise DomainError("Invalid native job identifier", 422)
        root = Path(self.settings.docker_jobs_dir)
        path = root / job_id
        if root.is_symlink() or path.is_symlink():
            raise DomainError("Unsafe local native workspace", 409)
        return path

    def _definition(self, image_kind):
        image = self.settings.docker_native_images.get(image_kind)
        if not image:
            raise DomainError(f"No local native image configured for {image_kind}", 503, "tool_unconfigured")
        response = self._client().request("GET", f"/images/{image}/json")
        if response.status_code == 404:
            raise DomainError(f"Local image for {image_kind} has not been built", 503, "tool_unconfigured")
        identity = response.json().get("Id", "")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", identity):
            raise DomainError("Docker returned an unpinned image identity", 503)
        return identity

    def _dispatch_configuration(self):
        return {"cluster": "local-docker", "backend": "docker", "volume": self.settings.docker_jobs_volume,
                "subnets": [], "securityGroups": [], "callbackBaseUrl": ""}

    def receive_completion_hints(self):
        return

    def reconcile(self):
        with self.db.transaction() as session:
            jobs = session.scalars(select(NativeJob).where(
                NativeJob.request["dispatch"]["backend"].as_string() == "docker",
                NativeJob.result["localCleaned"].as_boolean().is_not(True),
            )).all()
        results = []
        for job in jobs:
            if job.status == "cancelled":
                self._reconcile_cancelled(job)
                if job.result.get("terminationObservedAt") or not job.request.get("reservation", {}).get("dispatchAttempted"):
                    self._cleanup(job)
                continue
            if job.status in TERMINAL:
                self._cleanup(job)
                continue
            if job.expires_at.replace(tzinfo=UTC) <= now():
                identity = job.external_id
                if not identity and job.request.get("reservation", {}).get("dispatchAttempted"):
                    found = self._client().list_tasks(startedBy=job.id)["taskArns"]
                    identity = found[0] if found else None
                    if not identity and job.status == "dispatching":
                        # A create request may still be in flight. Keep capacity
                        # reserved until reconciliation discovers a durable identity.
                        continue
                if identity:
                    response = self._client().stop_task(task=identity)
                    if response["task"]["lastStatus"] != "STOPPED":
                        continue
                    if not job.external_id:
                        with self.db.transaction() as session:
                            lock_platform(session)
                            session.get(NativeJob, job.id).external_id = identity
                results.append((job.id, JobResult(status="failed", limitations=["Local native job deadline exceeded"])))
                continue
            if job.status == "dispatching":
                self._dispatch(job.id)
                continue
            if not job.external_id:
                continue
            observed = self._client().describe_tasks(tasks=[job.external_id])["tasks"][0]
            self.record_task_timing(job.id, observed)
            if observed["lastStatus"] == "RUNNING":
                with self.db.transaction() as session:
                    lock_platform(session)
                    current = session.get(NativeJob, job.id)
                    if current.status == "submitted":
                        current.status = "running"
                continue
            folder = self.control_directory(job.id)
            result_file = folder / "result.json"
            if observed.get("oomKilled"):
                results.append((job.id, JobResult(status="failed", measurements={"failureKind": "resource"},
                    limitations=["Local native job exceeded its memory allowance"],
                    logs=self._client().logs(job.external_id))))
                continue
            if observed.get("exitCode") != 0 or not result_file.is_file():
                results.append((job.id, JobResult(status="failed", limitations=[
                    "Local native supervisor exited without a valid result"],
                    logs=observed.get("stoppedReason", "") + "\n" + self._client().logs(job.external_id))))
                continue
            try:
                if result_file.is_symlink() or result_file.stat().st_size > 64 * 1024**2:
                    raise ValueError("Unsafe or oversized native result")
                envelope = json.loads(result_file.read_bytes())
                if (envelope["jobId"] != job.id
                        or envelope["requestSha256"] != digest((folder / "request.json").read_bytes())):
                    raise ValueError("Local native result identity mismatch")
                result = JobResult.model_validate(envelope["result"])
                if job.purpose == "session":
                    from .session_bridge import SessionBridge
                    SessionBridge(self.db, self.settings, self).ingest_local_finish(job.id)
                results.append((job.id, result))
            except (ValueError, KeyError, OSError, DomainError) as exc:
                results.append((job.id, JobResult(status="failed", limitations=[f"Invalid native result: {type(exc).__name__}"])))
        self.dispatch_pending()
        return results

    def _cleanup(self, job):
        if job.result.get("localCleaned"):
            return
        if job.external_id:
            response = self._client().request("DELETE", f"/containers/{job.external_id}",
                                             params={"force": "false", "v": "true"})
            if response.status_code not in {204, 404}:
                return
        try:
            shutil.rmtree(self.control_directory(job.id))
        except FileNotFoundError:
            pass
        except OSError:
            # Do not claim purge completion if private inputs/results remain.
            return
        with self.db.transaction() as session:
            lock_platform(session)
            current = session.get(NativeJob, job.id)
            current.result = {**current.result, "localCleaned": True}


def execution_factory(db, settings, store):
    if settings.execution_backend == "docker":
        return DockerExecution(db, settings, store)
    return ECSExecution(db, settings, store)
