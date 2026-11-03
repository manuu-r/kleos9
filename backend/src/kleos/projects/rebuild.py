"""Explicit bounded native-only rebuild grants linked to immutable earlier job history."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from kleos.contracts.models import DomainError
from kleos.execution.gateway import NativeAction, NativeJob
from kleos.orchestration.engine import RunScope
from kleos.projects.store import Objective, fingerprint


class NativeRebuildService:
    def __init__(self, projects, gateway, *, max_seconds=300, max_per_source=3, clock=lambda: datetime.now(UTC)):
        if not 1 <= max_seconds <= 600 or not 1 <= max_per_source <= 10:
            raise ValueError('Native rebuild grant limits are invalid')
        self.projects, self.gateway = projects, gateway
        self.max_seconds, self.max_per_source, self.clock = max_seconds, max_per_source, clock

    def submit(self, actor, project_id, job_id, expected_revision, key):
        def operation(session):
            project = self.projects.authorized(session, actor, project_id, write=True)
            if project.revision != expected_revision:
                raise DomainError('Project changed; refresh before rebuilding', 'revision_conflict')
            job = session.get(NativeJob, job_id)
            if job is None or job.project_id != project.id:
                raise DomainError('Native job not found', 'not_found', 404)
            if job.request.get('revision') != project.context_revision:
                raise DomainError('Native job source context is no longer current', 'stale_context')
            runs = list(session.scalars(select(Objective).where(Objective.project_id == project.id)))
            if any(run.status in {'pending', 'running', 'waiting'} and run.revision == project.context_revision for run in runs):
                raise DomainError('Finish or cancel the active objective before native-only rebuilding', 'run_conflict')
            parent = session.get(Objective, job.run_id)
            if parent is None:
                raise DomainError('Native job has no durable parent objective', 'native_history_missing')
            root_job = parent.data.get('nativeRebuild', {}).get('grant', {}).get('rootJobId', job.id)
            prior_grants = [run for run in runs if run.data.get('nativeRebuild', {}).get('grant', {}).get('rootJobId') == root_job]
            if len(prior_grants) >= self.max_per_source:
                raise DomainError('Explicit native rebuild allowance for this source history is exhausted', 'native_rebuild_budget_exhausted')
            # The gateway verifies closed action history, exact current bytes and installation pins.
            snapshot = self.gateway.rebuild_snapshot(session, actor, job)
            if (snapshot.get('sourceJobId') != job.id or snapshot.get('sourceRequestHash') != job.request_hash
                    or snapshot.get('sourceManifest') != job.source_manifest
                    or snapshot.get('originalRequest') != job.request):
                raise DomainError('Native snapshot differs from its original job contract', 'native_history_missing')
            request = snapshot['originalRequest']
            if request.get('lane') not in {'cad', 'pcb', 'firmware', 'application'}:
                raise DomainError('Native rebuild lane is unsupported', 'tool_unavailable')
            actions = ['build', 'render', 'finish'] if request['lane'] == 'cad' else ['build', 'finish']
            if request.get('maxActions', 0) < len(actions):
                raise DomainError('Original native action contract cannot support a rebuild', 'native_rebuild_budget_exhausted')
            lifetimes = [request[name] for name in ('durationSeconds', 'sessionSeconds') if name in request]
            if not lifetimes or any(isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 1800
                                    for value in lifetimes):
                raise DomainError('Original native lifetime contract is invalid', 'native_history_missing')
            seconds = min(self.max_seconds, *lifetimes)
            now = self.clock()
            run_id = str(uuid4())
            grant = {'id': run_id, 'rootJobId': root_job, 'parentJobId': job.id, 'parentRunId': parent.id,
                     'parentActionCount': session.scalar(select(func.count()).select_from(NativeAction).where(
                         NativeAction.job_id == job.id, ~NativeAction.action_id.startswith('_', autoescape=True))),
                     'ordinal': len(prior_grants)+1, 'maxActions': len(actions), 'actions': actions,
                     'maxDurationSeconds': seconds, 'authorizedBy': actor.user_id, 'admittedAt': now.isoformat(),
                     'snapshotFingerprint': fingerprint(snapshot), 'modelCallsAllowed': 0, 'retrievalCallsAllowed': 0}
            project.advance_revision()
            scope = RunScope(actor=actor, project_id=project.id, run_id=run_id, revision=project.context_revision,
                             deadline_at=now+timedelta(seconds=seconds), max_plan_calls=2, max_tasks=1)
            run = Objective(id=run_id, project_id=project.id, revision=project.context_revision, status='pending', data={
                'kind': 'project.nativeRebuild', 'objective': 'Rebuild exact saved native sources with the original pinned tool contract. Produce candidate files only.',
                'submittedBy': actor.user_id, 'createdAt': now.isoformat(), 'scope': scope.model_dump(mode='json'),
                'tasks': [], 'nativeRebuild': {'snapshot': snapshot, 'grant': grant},
                'editAllowedPaths': request['allowedPaths']})
            session.add(run)
            state = project.data.get('state', {})
            project.data = {**project.data, 'status': 'queued', 'state': {**state,
                'planning': {'phase': 'engineering', 'pending': True, 'runId': run_id}}}
            self.projects._emit(session, project, 'native.rebuild_authorized', {
                'runId': run_id, 'parentJobId': job.id, 'parentRunId': parent.id, 'rootJobId': root_job,
                'sourceManifestHash': fingerprint(snapshot['sourceManifest']), 'maxActions': len(actions),
                'maxDurationSeconds': seconds, 'modelCallsAllowed': 0})
            return {'projectId': project.id, 'runId': run_id, 'revision': project.revision}
        return self.projects._command(actor, key, {'type': 'project.retryNative', 'projectId': project_id,
            'jobId': job_id, 'expectedRevision': expected_revision}, operation)
