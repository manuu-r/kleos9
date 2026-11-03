"""Durable effect receipts for LangGraph; unknown effects never silently replay."""

from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from kleos.contracts.models import ArtifactRef, DomainError, TaskResult
from kleos.projects.store import Base, Objective, fingerprint

# Trusted worker context, inherited by its async tool tasks. Never supplied by models.
execution_owner = ContextVar("kleos_execution_owner", default=None)
# Codes after which nothing durable happened, so the claim may be released: the model spend ledger had already
# settled the attempt (rejected, rate limited or unusable), or the native gateway refused an action on a closed
# job before it recorded anything, or the request was refused before it was sent.
SETTLED_MODEL_FAILURES = frozenset({"model_provider_error", "model_rate_limited", "model_response_invalid",
                                    "native_closed", "context_too_large",
                                    # The call settled and its reply failed validation, or the request never left: no side
                                    # effect exists to reconcile, so a retry asks again as a new metered attempt.
                                    "invalid_geometry", "invalid_interfaces", "vision_input_unavailable"})


def resolve_dependencies(task_ids, superseded, dependencies):
    """Match graph dependency supersession without rewriting the saved task identities."""
    known = set(task_ids)
    if (len(known) > 128 or not isinstance(superseded, dict) or len(superseded) > len(known)
            or any(not isinstance(key, str) or not isinstance(value, str)
                   or key not in known or value not in known for key, value in superseded.items())):
        raise DomainError('Dependency supersession references a missing task', 'source_conflict')

    def resolve(ident):
        if not isinstance(ident, str) or ident not in known:
            raise DomainError('Dependency references a missing task', 'source_conflict')
        seen = set()
        while ident in superseded:
            if ident in seen:
                raise DomainError('Dependency supersession contains a cycle', 'source_conflict')
            seen.add(ident)
            ident = superseded[ident]
        return ident

    for ident in superseded:
        resolve(ident)
    return [resolve(ident) for ident in dependencies]


class Effect(Base):
    __tablename__ = "kleos2_effects"
    __table_args__ = (UniqueConstraint("run_id", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))
    value: Mapped[dict] = mapped_column(JSON)


class GraphRepository:
    def __init__(self, store):
        self.store = store

    def _check(self, session, scope):
        project = self.store.authorized(session, scope.actor, scope.project_id, write=True)
        run = session.get(Objective, scope.run_id)
        if run is None or run.project_id != project.id:
            raise DomainError("Objective not found", "not_found", 404)
        if run.status not in {"pending", "running", "waiting"}:
            raise DomainError("Objective is no longer active", "run_inactive")
        if run.data.get("workerToken") and execution_owner.get() != (run.id, run.data["workerToken"]):
            raise DomainError("Objective execution belongs to another worker", "worker_fenced")
        from kleos.projects.compute import assert_active
        assert_active(run, scope, run.data.get('workerToken'))
        if run.revision != scope.revision or project.context_revision != scope.revision:
            raise DomainError("Project changed since this objective started", "stale_context")
        if scope.deadline_at.tzinfo is None or datetime.now(UTC) >= scope.deadline_at:
            raise DomainError("Objective deadline exceeded", "deadline_exceeded")
        return project, run

    async def context(self, scope):
        context = self.store.context(scope.actor, scope.project_id)
        with self.store.transaction() as session:
            project, run = self._check(session, scope)
            repairs = self._repair_refs(session, project, run)
            merged = {ref.path: ref for ref in context.artifacts}
            merged.update({ref.path: ref for ref in repairs.values()})
            return context.model_copy(update={"artifacts": list(merged.values())})

    @staticmethod
    def _repair_refs(session, project, run):
        """Server-pinned candidates are read inputs, never accepted source-manifest edits."""
        from kleos.artifacts.service import Artifact

        encoded = run.data.get("repairInputs", [])
        if not isinstance(encoded, list) or len(encoded) > 256:
            raise DomainError("Invalid repair input snapshot", "artifact_scope_conflict")
        result, paths = {}, set()
        for raw in encoded:
            ref = ArtifactRef.model_validate(raw)
            row = session.get(Artifact, ref.id)
            if (row is None or row.project_id != project.id
                    or row.details.get("origin") != "native_candidate"
                    or row.details.get("runId") != run.data.get("repairSourceRunId")
                    or ref.id in result or ref.path in paths):
                raise DomainError("Repair input provenance changed", "artifact_scope_conflict")
            exact = ArtifactRef(id=row.id, path=row.name, sha256=row.sha256,
                revision=row.revision, media_type=row.media_type, size=row.size)
            if ref != exact:
                raise DomainError("Repair input snapshot changed", "artifact_scope_conflict")
            result[ref.id] = ref
            paths.add(ref.path)
        return result

    async def resolve_artifacts(self, scope, artifact_ids):
        from kleos.artifacts.service import Artifact

        if len(artifact_ids) > 256 or len(set(artifact_ids)) != len(artifact_ids):
            raise DomainError("Invalid artifact selection", "invalid_request", 422)
        with self.store.transaction() as session:
            project, run = self._check(session, scope)
            permitted = set(project.data.get("sourceManifest", {}).values())
            permitted.update(self._repair_refs(session, project, run))
            permitted.update(
                ref["id"]
                for result in run.data.get("results", {}).values()
                for ref in result.get("artifacts", [])
            )
            if run.data.get('compositionResults'):
                from kleos.projects.composition_results import historical_input_refs
                projected = historical_input_refs(session, run)
                # Exact read projection excludes replaced authoring exports.
                original_ids = {ref['id'] for value in run.data.get('results', {}).values()
                                for ref in value.get('artifacts', [])}
                permitted.difference_update(original_ids - set(projected))
                permitted.update(projected)
            references = []
            for artifact_id in artifact_ids:
                row = session.get(Artifact, artifact_id) if artifact_id in permitted else None
                if row is None or row.project_id != project.id:
                    raise DomainError("Artifact is not an authorized task input", "artifact_scope_conflict")
                references.append(
                    ArtifactRef(
                        id=row.id,
                        path=row.name,
                        sha256=row.sha256,
                        revision=row.revision,
                        media_type=row.media_type,
                        size=row.size,
                    )
                )
            return references

    async def planning_results(self, scope, results):
        return (await self.planning_inputs(scope, results))['results']

    async def planning_inputs(self, scope, results):
        """Project verified read references and their applicability, not authoring history."""
        from kleos.projects.composition_results import input_projection

        with self.store.transaction() as session:
            _, run = self._check(session, scope)
            view = input_projection(session, run) if run.data.get('compositionResults') else None
            projected = []
            for result in results:
                saved = run.data.get('results', {}).get(result.task_id)
                if saved is None or TaskResult.model_validate(saved) != result:
                    raise DomainError('Planning result differs from saved history', 'artifact_conflict')
                if view is not None and result.task_id not in run.data.get('superseded', {}):
                    result = result.model_copy(update={'artifacts': view['artifacts'].get(result.task_id, [])})
                projected.append(result)
            metadata = None if view is None else {
                'compositionId': view['compositionId'], 'historical': view['historical'],
                'historicalTaskIds': view['historicalTaskIds'],
                'authority': 'Read-only candidate context. Historical outputs cannot establish current validation or release coverage.'}
            return {'results': projected, 'artifact_context': metadata}

    async def dependency_artifacts(self, scope, dependencies):
        from kleos.projects.composition_results import input_artifacts

        with self.store.transaction() as session:
            _, run = self._check(session, scope)
            if len(dependencies) > 128 or len(set(dependencies)) != len(dependencies):
                raise DomainError('Invalid dependency selection', 'invalid_plan')
            selected = {}
            for name in dependencies:
                saved = run.data.get('results', {}).get(name)
                if saved is None or TaskResult.model_validate(saved).status != 'succeeded':
                    raise DomainError('Dependency is not completed', 'artifact_conflict')
                selected[name] = (input_artifacts(session, run, name) if run.data.get('compositionResults')
                                  else TaskResult.model_validate(saved).artifacts)
            return selected

    async def select_dependency_sources(self, scope, task, references, context_refs, dependencies):
        """Choose exact declared dependency successors; never merge edits or accept sources."""
        from kleos.artifacts.service import Artifact, NativePublication
        from kleos.execution.gateway import NativeJob

        with self.store.transaction() as session:
            project, run = self._check(session, scope)
            if set(dependencies) != set(task.depends_on):
                raise DomainError('Dependency selection differs from the task', 'source_conflict')
            saved = run.data.get('results', {})
            superseded = run.data.get('superseded', {})
            declared = {}
            from kleos.projects.composition_results import input_artifacts
            for name, refs in dependencies.items():
                if name not in saved:
                    raise DomainError('Declared dependency result is missing', 'artifact_conflict')
                result = TaskResult.model_validate(saved[name])
                current_refs = (input_artifacts(session, run, name) if run.data.get('compositionResults') else result.artifacts)
                if name in superseded or result.status.value != 'succeeded' or current_refs != refs:
                    raise DomainError('Dependency result changed before source selection', 'artifact_conflict')
                for ref in refs:
                    declared[ref.id] = ref
            all_refs = {}
            for ref in [*context_refs, *references]:
                if ref.id in all_refs and all_refs[ref.id] != ref:
                    raise DomainError('Dependency artifact identity is ambiguous', 'artifact_conflict')
                all_refs[ref.id] = ref
            rows, groups = {}, {}
            for ident, ref in all_refs.items():
                row = session.get(Artifact, ident)
                exact = (ArtifactRef(id=row.id, path=row.name, sha256=row.sha256, size=row.size,
                                    revision=row.revision, media_type=row.media_type) if row else None)
                if row is None or row.project_id != project.id or ref != exact:
                    raise DomainError('Dependency source binding changed', 'artifact_conflict')
                rows[ident] = row
                if row.details.get('role') != 'output':
                    groups.setdefault(row.name, set()).add(ident)
            baseline = project.data.get('sourceManifest', {})
            repair_sources = {ref.path: ref for ref in self._repair_refs(session, project, run).values()
                              if session.get(Artifact, ref.id).details.get('role') == 'source'}
            plans = {value['id']: value for value in run.data.get('tasks', [])}
            shadowed = set()
            for path, group in groups.items():
                if len(group) < 2:
                    continue
                baseline_id = baseline.get(path)
                repair = repair_sources.get(path)
                base_ids = {baseline_id, repair.id if repair else None}
                candidate_ids = (group & declared.keys()) - base_ids
                if not candidate_ids or group - candidate_ids - base_ids:
                    raise DomainError('Source versions have no declared dependency precedence', 'source_conflict')
                baseline_row = session.get(Artifact, baseline_id) if baseline_id else None
                if baseline_id and (baseline_row is None or baseline_row.project_id != project.id or baseline_row.name != path):
                    raise DomainError('Authoritative source baseline changed', 'artifact_conflict')
                jobs = {}
                for ident in candidate_ids:
                    # Immutable publication can be consumed after finish even
                    # while container termination remains separately unresolved.
                    row = rows[ident]
                    detail = row.details
                    producer = detail.get('taskId')
                    job = session.get(NativeJob, detail.get('nativeJobId'))
                    publication = session.scalar(select(NativePublication).where(
                        NativePublication.run_id == run.id, NativePublication.task_id == producer,
                        NativePublication.job_id == detail.get('nativeJobId')))
                    if producer not in saved:
                        raise DomainError('Original dependency producer result is missing', 'artifact_conflict')
                    original_result = TaskResult.model_validate(saved[producer])
                    producer_plan = plans.get(producer, {})
                    if (row.status != 'candidate' or row.revision != scope.revision
                            or detail.get('origin') != 'native_candidate' or detail.get('role') != 'source'
                            or detail.get('runId') != run.id or producer in superseded
                            or original_result.status.value != 'succeeded' or all_refs[ident] not in original_result.artifacts
                            or path not in producer_plan.get('allowedPaths', producer_plan.get('allowed_paths', []))
                            or job is None or job.project_id != project.id or job.run_id != run.id
                            or job.task_id != producer or job.status != 'finished' or job.active_action is not None
                            or job.request_hash != detail.get('requestHash') or fingerprint(job.request) != job.request_hash
                            or job.request.get('imageDigest') != detail.get('imageDigest')
                            or path not in job.request.get('baselines', {})
                            or job.request['baselines'][path] != (baseline_row.sha256 if baseline_row else None)
                            or job.source_manifest.get(path) != row.sha256
                            or detail.get('sourceClosure', {}).get(path) != ident
                            or publication is None
                            or all_refs[ident] not in [ArtifactRef.model_validate(value) for value in publication.references]):
                        raise DomainError('Dependency source lacks exact native publication provenance', 'artifact_conflict')
                    jobs[ident] = job
                # A later declared producer can supersede an earlier one only by reading
                # that exact artifact/hash; independent competing edits never get ordered.
                parents, repair_children = {}, set()
                for ident, job in jobs.items():
                    producer = rows[ident].details['taskId']
                    plan = plans[producer]
                    producer_deps = resolve_dependencies(plans, superseded,
                        plan.get('dependsOn', plan.get('depends_on', [])))
                    matches = [value for value in job.request.get('sources', []) if value.get('path') == path]
                    if len(matches) > 1:
                        raise DomainError('Producer input path is ambiguous', 'source_conflict')
                    if matches and matches[0].get('artifactId') in candidate_ids:
                        parent = matches[0]['artifactId']
                        if (parent == ident or matches[0].get('sha256') != rows[parent].sha256
                                or rows[parent].details['taskId'] not in producer_deps):
                            raise DomainError('Dependency source ancestry is not declared', 'source_conflict')
                        parents[ident] = parent
                    elif repair and matches and matches[0].get('artifactId') == repair.id:
                        if matches[0].get('sha256') != repair.sha256:
                            raise DomainError('Dependency repair ancestry changed', 'source_conflict')
                        repair_children.add(ident)
                def ancestry(ident):
                    visited = {ident}
                    while ident in parents:
                        ident = parents[ident]
                        if ident in visited:
                            raise DomainError('Dependency source ancestry contains a cycle', 'source_conflict')
                        visited.add(ident)
                    return visited
                winners = [ident for ident in candidate_ids if ancestry(ident) == candidate_ids]
                if len(winners) != 1:
                    raise DomainError('Competing dependency source edits need an explicit lineage', 'source_conflict')
                # A server-pinned repair snapshot is the current read base even
                # though publication still compares against the accepted source.
                if repair and not (ancestry(winners[0]) & repair_children):
                    raise DomainError('Dependency source does not descend from the pinned repair input', 'source_conflict')
                shadowed.update(group - {winners[0]})
            return [ref for ref in references if ref.id not in shadowed], shadowed

    async def assert_active(self, scope):
        with self.store.transaction() as session:
            self._check(session, scope)

    async def run_once(self, scope, key, operation):
        try:
            with self.store.transaction() as session:
                self._check(session, scope)
                prior = session.scalar(select(Effect).where(Effect.run_id == scope.run_id, Effect.key == key))
                if prior:
                    if prior.status == "completed":
                        return prior.value
                    raise DomainError("Prior operation needs reconciliation before retry", "effect_uncertain")
                effect_id = str(uuid4())
                session.add(Effect(id=effect_id, run_id=scope.run_id, key=key, status="claimed", value={}))
        except IntegrityError:
            raise DomainError(
                "Operation is already claimed; reconcile before retry", "effect_uncertain"
            ) from None
        # No transaction is held across external work. Failure leaves the durable claim uncertain,
        # except a model failure the spend ledger already settled exactly: nothing was produced, so
        # a retry may ask again (as a new, separately metered attempt) instead of being refused.
        try:
            result = await operation()
        except DomainError as exc:
            if exc.code in SETTLED_MODEL_FAILURES:
                with self.store.transaction() as session:
                    row = session.get(Effect, effect_id)
                    if row is not None and row.status == "claimed":
                        session.delete(row)
            raise
        with self.store.transaction() as session:
            row = session.get(Effect, effect_id)
            row.status, row.value = "completed", result
        # Record observed effect even if cancellation occurred, then fence its publication.
        await self.assert_active(scope)
        return result

    async def publish(self, scope, result):
        from kleos.artifacts.service import Artifact

        with self.store.transaction() as session:
            project, run = self._check(session, scope)
            inputs = set(project.data.get("sourceManifest", {}).values())
            inputs.update(self._repair_refs(session, project, run))
            for ref in result.artifacts:
                row = session.get(Artifact, ref.id)
                if row is None or row.project_id != project.id:
                    raise DomainError("Task returned an unauthorized artifact", "artifact_scope_conflict")
                exact = ArtifactRef(
                    id=row.id,
                    path=row.name,
                    sha256=row.sha256,
                    revision=row.revision,
                    media_type=row.media_type,
                    size=row.size,
                )
                if ref != exact or (row.id not in inputs and row.details.get("runId") != run.id):
                    raise DomainError(
                        "Task returned an unregistered or changed artifact", "artifact_scope_conflict"
                    )
            saved = dict(run.data.get("results", {}))
            data = result.model_dump(mode="json", by_alias=True)
            if result.task_id in saved:
                if TaskResult.model_validate(saved[result.task_id]) != result:
                    raise DomainError("Task result changed during replay", "result_conflict")
                return
            saved[result.task_id] = data
            run.data = {**run.data, "results": saved}
            self.store._emit(session, project, "task.observed", {"runId": run.id, "result": data})

    async def record_activity(self, scope, step):
        """Keep the run's latest public progress steps; a step with a known id replaces itself."""
        from kleos.orchestration.activity import STEP_LIMIT

        with self.store.transaction() as session:
            project, run = self._check(session, scope)
            steps = [item for item in run.data.get("activity", []) if item.get("id") != step["id"]]
            run.data = {**run.data, "activity": [*steps, step][-STEP_LIMIT:]}
            self.store._emit(session, project, "objective.activity", {"runId": run.id, "step": step})

    async def publish_plan(self, scope, *, tasks, superseded, turn, summary, question):
        data = {
            "tasks": [task.model_dump(mode="json", by_alias=True) for task in tasks],
            "superseded": superseded,
            "turn": turn,
            "summary": summary,
            "question": question,
        }
        digest = fingerprint(data)
        with self.store.transaction() as session:
            project, run = self._check(session, scope)
            allowed = run.data.get("repairAllowedPaths", run.data.get("editAllowedPaths"))
            if allowed is not None and any(not set(task.allowed_paths).issubset(set(allowed)) for task in tasks):
                raise DomainError("Plan expands the authorized source edit scope", "scope_conflict")
            receipts = dict(run.data.get("planPublications", {}))
            if str(turn) in receipts:
                if receipts[str(turn)] != digest:
                    raise DomainError("Published plan changed during replay", "result_conflict")
                return
            if receipts and turn <= max(map(int, receipts)):
                raise DomainError("Plan publication is out of order", "result_conflict")
            receipts[str(turn)] = digest
            run.data = {**run.data, **data, "planPublications": receipts}
            self.store._emit(session, project, "objective.planned", {"runId": run.id, **data})

    def cancel(self, actor, project_id, run_id):
        with self.store.transaction() as session:
            project = self.store.authorized(session, actor, project_id, write=True)
            run = session.get(Objective, run_id)
            if run is None or run.project_id != project_id:
                raise DomainError("Objective not found", "not_found", 404)
            if run.status != "cancelled":
                run.status = "cancelled"
                self.store.clear_pending(project, run.id, "cancelled")
                self.store._emit(session, project, "objective.cancelled", {"runId": run.id})
