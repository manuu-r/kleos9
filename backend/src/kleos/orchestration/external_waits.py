"""Authorized external-event correlation; LangGraph remains the only workflow engine."""
import hashlib
import json
import re
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import JSON, Float, Integer, String, UniqueConstraint, case, select
from sqlalchemy.orm import Mapped, mapped_column

from kleos.capabilities.external import ExternalJob, ExternalRequest
from kleos.capabilities.fabrication.slicing import PrintPreparation, current_source
from kleos.contracts.models import Actor, DomainError, Observation, TaskResult, TaskSpec, TaskStatus
from kleos.orchestration.engine import RunScope
from kleos.projects.store import Base, Event, Objective, Project, fingerprint

KINDS = {'sourcing', 'human_work', 'print_preparation'}
TERMINAL = {'completed', 'failed', 'cancelled', 'expired', 'ready', 'stale'}


class ExternalWait(Base):
    __tablename__ = 'kleos2_external_waits'
    __table_args__ = (UniqueConstraint('run_id', 'task_id', 'kind', 'job_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str] = mapped_column(String(100), index=True)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), index=True)
    deadline_at: Mapped[float] = mapped_column(Float)
    next_poll_at: Mapped[float] = mapped_column(Float, index=True)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON)


class ExternalWaitEvent(Base):
    __tablename__ = 'kleos2_external_wait_events'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    wait_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)


def bounded(value, limit=32000):
    if len(json.dumps(value, allow_nan=False, separators=(',', ':')).encode()) > limit:
        raise DomainError('External wait record exceeds its bound', 'wait_payload_limit')
    return value


def implementation_hash():
    sources = [Path(__file__), Path(__file__).parents[1]/'capabilities'/'external_wait_tools.py']
    return fingerprint({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources})


class ExternalWaits:
    def __init__(self, projects, repository, *, external_jobs=None, preparations=None, clock=time.time):
        self.projects, self.repository = projects, repository
        self.external_jobs, self.preparations, self.clock = external_jobs, preparations, clock
        self.implementation_hash = implementation_hash()

    def _source(self, session, actor, project, run, task, kind, job_id):
        if kind in {'sourcing', 'human_work'}:
            if self.external_jobs is None or task.capability.value != kind:
                raise DomainError('External wait source is not configured for this task', 'wait_scope')
            # Existing provider writers lock job then project. Read its committed
            # snapshot without a job lock while the already-held project lock fences
            # their publication; never invert those locks for wait correlation.
            job = session.get(ExternalJob, job_id)
            if job is None or job.tenant_id != actor.tenant_id or job.project_id != project.id:
                raise DomainError('External wait job not found', 'not_found', 404)
            if project.context_revision != (job.presentation or {}).get('_contextRevision', job.revision):
                raise DomainError('External wait project context changed', 'stale_context')
            provider = self.external_jobs._provider(job, actor)
            if (job.project_id != project.id or job.creator_id != actor.user_id or job.kind != kind
                    or provider.effect_capable is not False):
                raise DomainError('External wait account or project differs', 'wait_scope')
            account = self.external_jobs.installation_pin(actor, job.provider)
            if account is not None:
                matching = [p for p in run.data.get('runtimePins', {}).get('external', []) if p.get('provider') == job.provider]
                if len(matching) != 1 or matching[0].get('installationHash') != account:
                    raise DomainError('External wait account installation changed', 'provider_changed')
            request = ExternalRequest.model_validate(job.request)
            if not {a.artifact_id for a in request.artifact_bindings}.issubset(task.input_artifact_ids):
                raise DomainError('External job inputs are outside the assigned task', 'wait_scope')
            from kleos.capabilities.external import bind_artifacts
            bind_artifacts(session, project.id, request.artifact_bindings)
            if not job.external_id or job.effect_state != 'observed':
                raise DomainError('Only already-linked external work can be awaited', 'not_dispatched')
            binding = {'requestHash': job.request_hash, 'provider': job.provider, 'version': job.provider_version,
                       'installationHash': account, 'externalId': job.external_id, 'creatorId': job.creator_id}
            observation = dict(job.observation)
            observation.pop('retryAfterSeconds', None)
            observation.pop('retry_after_seconds', None)
            material = bounded({'status': job.status, 'externalId': job.external_id, 'observation': observation})
            return binding, material, job.deadline_at, job.next_poll_at
        if kind != 'print_preparation' or task.capability.value != 'fabrication' or self.preparations is None:
            raise DomainError('Unsupported external wait source', 'wait_scope')
        job = session.get(PrintPreparation, job_id)
        if (job is None or job.project_id != project.id or job.tenant_id != actor.tenant_id
                or job.creator_id != actor.user_id or job.source_artifact_id not in task.input_artifact_ids):
            raise DomainError('Print preparation is outside the assigned task', 'wait_scope')
        from kleos.artifacts.service import Artifact
        source = session.get(Artifact, job.source_artifact_id)
        if (job.engineering_revision != project.data.get('engineeringRevision', 1)
                or not current_source(session, project, source, job.source_sha256)):
            raise DomainError('Print preparation source is stale', 'stale_context')
        if self.preparations.settings is None or self.preparations.settings.profile(job.profile_id) != job.profile:
            raise DomainError('Print preparation installation changed', 'provider_changed')
        binding = {'creatorId': job.creator_id, 'sourceId': job.source_artifact_id, 'sha256': job.source_sha256,
                   'profile': job.profile, 'engineeringRevision': job.engineering_revision}
        material = {'status': job.status, 'preparedArtifactId': job.prepared_artifact_id,
                    'preparedSha256': job.prepared_sha256, 'physicalAcceptance': False}
        return binding, material, job.created_at+1200, self.clock()+30

    def _guard(self, session, wait, *, active_scope=None):
        scope = RunScope.model_validate(wait.data['scope'])
        actor = scope.actor
        project = self.projects.authorized(session, actor, wait.project_id, write=True)
        session.refresh(wait, with_for_update=True)
        run = session.get(Objective, wait.run_id)
        if run is None or run.project_id != project.id or run.status not in {'pending', 'running', 'waiting'}:
            raise DomainError('External wait objective is no longer active', 'run_inactive')
        if run.revision != scope.revision or project.context_revision != scope.revision:
            raise DomainError('External wait project context changed', 'stale_context')
        if wait.data.get('implementationHash') != self.implementation_hash:
            raise DomainError('External wait implementation changed', 'installation_conflict')
        if (run.data.get('scope') != wait.data['scope']
                or fingerprint(run.data.get('runtimePins', {})) != wait.data['runtimePinsHash']):
            raise DomainError('External wait scope or budget/tool pins changed', 'scope_conflict')
        if self.clock() >= scope.deadline_at.timestamp():
            raise DomainError('External wait lifetime expired', 'deadline_exceeded')
        if active_scope is not None:
            if active_scope.model_dump(mode='json') != wait.data['scope']:
                raise DomainError('External wait scope differs from graph', 'scope_conflict')
            self.repository._check(session, active_scope)
        task = TaskSpec.model_validate(wait.data['task'])
        self._task(run, task)
        binding, material, _, next_poll = self._source(session, actor, project, run, task, wait.kind, wait.job_id)
        if fingerprint(binding) != wait.data['bindingHash']:
            raise DomainError('External wait source binding changed', 'wait_scope')
        return project, run, material, next_poll

    @staticmethod
    def _task(run, task):
        planned = next((t for t in run.data.get('tasks', []) if t['id'] == task.id), None)
        if planned is None:
            raise DomainError('External wait task is not in the current plan', 'wait_scope')
        original = TaskSpec.model_validate(planned)
        fields = ('capability', 'objective', 'allowed_paths', 'acceptance_criteria', 'max_actions')
        if any(getattr(original, field) != getattr(task, field) for field in fields):
            raise DomainError('External wait task authority differs from its plan', 'wait_scope')

    @staticmethod
    def _artifacts(session, project, result):
        from kleos.artifacts.service import Artifact
        from kleos.contracts.models import ArtifactRef
        for ref in result.artifacts:
            row = session.get(Artifact, ref.id)
            if row is None or row.project_id != project.id:
                raise DomainError('Waiting task artifact is unavailable', 'artifact_scope_conflict')
            exact = ArtifactRef(id=row.id, path=row.name, sha256=row.sha256, size=row.size,
                media_type=row.media_type, revision=row.revision)
            if exact != ref:
                raise DomainError('Waiting task artifact provenance changed', 'artifact_conflict')

    async def bind(self, scope, task, kind, job_id):
        if kind not in KINDS or not isinstance(job_id, str) or not 1 <= len(job_id) <= 36:
            raise DomainError('Invalid external wait identity', 'invalid_request', 422)
        await self.repository.resolve_artifacts(scope, task.input_artifact_ids)
        with self.projects.transaction() as session:
            project, run = self.repository._check(session, scope)
            if run.data.get('scope') != scope.model_dump(mode='json'):
                raise DomainError('External waits require the persisted objective scope', 'scope_conflict')
            self._task(run, task)
            prior = session.scalar(select(ExternalWait).where(ExternalWait.run_id == run.id,
                ExternalWait.task_id == task.id, ExternalWait.kind == kind, ExternalWait.job_id == job_id))
            if prior:
                self._guard(session, prior, active_scope=scope)
                if prior.data['task'] != task.model_dump(mode='json'):
                    raise DomainError('External wait task changed during replay', 'scope_conflict')
                return prior.id
            if len(list(session.scalars(select(ExternalWait.id).where(ExternalWait.run_id == run.id)))) >= 128:
                raise DomainError('External wait allowance exhausted', 'budget_exhausted')
            binding, material, deadline, next_poll = self._source(session, scope.actor, project, run, task, kind, job_id)
            ident = str(uuid4())
            wait = ExternalWait(id=ident, project_id=project.id, run_id=run.id, task_id=task.id, job_id=job_id,
                kind=kind, status='waiting', deadline_at=min(deadline, scope.deadline_at.timestamp()),
                next_poll_at=max(self.clock(), next_poll), last_sequence=project.event_sequence,
                data=bounded({'scope': scope.model_dump(mode='json'), 'task': task.model_dump(mode='json'),
                    'runtimePinsHash': fingerprint(run.data.get('runtimePins', {})), 'bindingHash': fingerprint(binding),
                    'implementationHash': self.implementation_hash,
                    'materialHash': fingerprint(material)}))
            session.add(wait)
            session.flush()
            self.projects._emit(session, project, 'external.wait.bound', {'waitId': ident, 'runId': run.id, 'taskId': task.id})
            self._observe(session, wait, project, material, project.event_sequence)
            return ident

    async def validate(self, scope, wait_ids):
        if (not isinstance(wait_ids, list) or not wait_ids or len(wait_ids) > 128
                or any(not isinstance(key, str) or not 1 <= len(key) <= 36 for key in wait_ids)
                or len(set(wait_ids)) != len(wait_ids)):
            raise DomainError('Invalid graph external wait set', 'wait_scope')
        with self.projects.transaction() as session:
            _, run = self.repository._check(session, scope)
            for ident in wait_ids:
                wait = session.get(ExternalWait, ident)
                if wait is None or wait.run_id != run.id:
                    raise DomainError('External wait does not belong to this graph', 'wait_scope')
                project, _, _, _ = self._guard(session, wait, active_scope=scope)
                if wait.status not in {'waiting', 'ready', 'consumed'}:
                    raise DomainError('External wait is no longer active', 'wait_scope')
                result = TaskResult.model_validate(run.data.get('results', {}).get(wait.task_id, {}))
                self._artifacts(session, project, result)
                if wait.status == 'consumed' and wait.data.get('resolvedResult') == result.model_dump(mode='json', by_alias=True):
                    continue
                if result.status.value != 'waiting' or ident not in result.wait_ids:
                    raise DomainError('External wait is not bound to the published waiting task', 'wait_scope')

    def _observe(self, session, wait, project, material, sequence):
        if wait.status == 'consumed' or sequence < wait.last_sequence:
            return False
        wait.last_sequence = sequence
        expired = self.clock() >= wait.deadline_at
        state = 'expired' if expired and material['status'] not in TERMINAL else material['status']
        if state not in TERMINAL:
            wait.data = {**wait.data, 'materialHash': fingerprint(material)}
            return False  # Progress/no-op polls do not call the planner.
        material = bounded({**material, 'status': state, 'physicalAcceptance': False})
        event_id = fingerprint({'waitId': wait.id, 'material': material})
        if wait.data.get('readyEventId') == event_id:
            return False
        if session.get(ExternalWaitEvent, event_id) is None:
            session.add(ExternalWaitEvent(id=event_id, wait_id=wait.id, sequence=sequence,
                data={'material': material, 'materialHash': fingerprint(material)}))
        wait.status = 'ready'
        wait.data = {**wait.data, 'readyEventId': event_id, 'materialHash': fingerprint(material)}
        self.projects._emit(session, project, 'external.wait.ready', {'waitId': wait.id, 'runId': wait.run_id, 'eventId': event_id})
        return True

    def _refresh(self, ident, *, sequence=None):
        with self.projects.transaction() as session:
            wait = session.get(ExternalWait, ident)
            if wait is None or wait.status not in {'waiting', 'ready'}:
                return False
            project, _, material, next_poll = self._guard(session, wait)
            if sequence is not None and sequence <= wait.last_sequence:
                return False
            wait.next_poll_at = max(self.clock()+10, next_poll)
            return self._observe(session, wait, project, material, sequence if sequence is not None else project.event_sequence)

    def ingest(self, project_id, event_sequence):
        """Consume an existing authoritative event, never provider-supplied status payloads."""
        if type(event_sequence) is not int or event_sequence < 1:
            raise DomainError('Invalid source event sequence', 'invalid_request', 422)
        with self.projects.transaction() as session:
            event = session.get(Event, (project_id, event_sequence))
            if event is None or not (event.data['type'].startswith('external.') or event.data['type'].startswith('print.preparation.')):
                return 0
            if event.data['type'].startswith('external.wait.'):
                return 0
            payload = event.data.get('payload', {})
            job_id = payload.get('jobId', payload.get('id'))
            ids = list(session.scalars(select(ExternalWait.id).where(ExternalWait.project_id == project_id,
                ExternalWait.job_id == job_id, ExternalWait.status.in_(['waiting', 'ready'])).limit(128)))
        changed = 0
        for ident in ids:
            try:
                changed += bool(self._refresh(ident, sequence=event_sequence))
            except DomainError as exc:
                self._block(ident, exc.code)
        return changed

    def _block(self, ident, code):
        with self.projects.transaction() as session:
            wait = session.get(ExternalWait, ident)
            if wait is None or wait.status not in {'waiting', 'ready'}:
                return
            project = session.get(Project, wait.project_id, with_for_update=True)
            session.refresh(wait, with_for_update=True)
            if wait.status not in {'waiting', 'ready'}:
                return
            run = session.get(Objective, wait.run_id)
            wait.status, wait.data = 'blocked', {**wait.data, 'blocker': code}
            if run.status in {'waiting', 'pending', 'running'}:
                run.status, run.data = 'blocked', {**run.data, 'blocker': code}
                self.projects.clear_pending(project, run.id, 'blocked')
            self.projects._emit(session, project, 'external.wait.blocked', {'waitId': ident, 'runId': run.id, 'blocker': code})

    async def poll_due(self, limit=16):
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError('External wait poll limit must be bounded')
        with self.projects.transaction() as session:
            ids = list(session.scalars(select(ExternalWait.id).where(ExternalWait.status == 'waiting',
                ((ExternalWait.next_poll_at <= self.clock()) | (ExternalWait.deadline_at <= self.clock())))
                .order_by(case((ExternalWait.deadline_at < ExternalWait.next_poll_at, ExternalWait.deadline_at),
                               else_=ExternalWait.next_poll_at)).limit(limit)))
        counts = {'polled': 0, 'changed': 0, 'errors': 0}
        for ident in ids:
            try:
                with self.projects.transaction() as session:
                    wait = session.get(ExternalWait, ident)
                    _, _, _, _ = self._guard(session, wait)
                    if wait.status != 'waiting' or (wait.next_poll_at > self.clock() and wait.deadline_at > self.clock()):
                        continue
                    wait.next_poll_at = self.clock()+60
                    actor = Actor.model_validate(wait.data['scope']['actor'])
                    kind, job_id, expired = wait.kind, wait.job_id, self.clock() >= wait.deadline_at
                # No database lock, run lock, native slot or model call spans provider IO.
                if kind in {'sourcing', 'human_work'} and not expired:
                    try:
                        await self.external_jobs.poll(actor, job_id)
                    except DomainError as exc:
                        if exc.code == 'provider_error':
                            counts['errors'] += 1
                            continue
                        raise
                counts['polled'] += 1
                counts['changed'] += bool(self._refresh(ident))
            except DomainError as exc:
                self._block(ident, exc.code)
        counts['woken'] = self.wake_ready()
        return counts

    def wake_ready(self):
        with self.projects.transaction() as session:
            run_ids = list(session.scalars(select(ExternalWait.run_id).where(ExternalWait.status == 'ready').distinct().limit(64)))
        woken = 0
        for run_id in run_ids:
            try:
                with self.projects.transaction() as session:
                    run = session.get(Objective, run_id)
                    project = session.get(Project, run.project_id, with_for_update=True)
                    session.refresh(run)
                    question = run.data.get('question') or {}
                    if (run.status not in {'waiting', 'pending'} or question.get('kind') != 'external_wait'
                            or not run.data.get('interruptId')):
                        continue
                    ids = question.get('waitIds', [])
                    if not isinstance(ids, list) or not ids or len(ids) > 128:
                        raise DomainError('Invalid persisted graph wait set', 'wait_scope')
                    waits = {ident: session.get(ExternalWait, ident) for ident in ids}
                    events = []
                    for ident, wait in waits.items():
                        if wait is None or wait.run_id != run.id:
                            raise DomainError('Graph wait binding differs', 'wait_scope')
                        self._guard(session, wait)
                        result = TaskResult.model_validate(run.data.get('results', {}).get(wait.task_id, {}))
                        if result.status.value != 'waiting' or not set(result.wait_ids).issubset(waits):
                            continue
                        if all(waits[key].status == 'ready' for key in result.wait_ids):
                            events.extend(waits[key].data['readyEventId'] for key in result.wait_ids)
                    if not events:
                        continue
                    value = {'kind': 'external_wait', 'eventIds': sorted(set(events))}
                    answer = {'interruptId': run.data['interruptId'], 'value': value}
                    if run.data.get('pendingAnswer') == answer:
                        continue
                    pending = run.data.get('pendingAnswer')
                    if pending and (not isinstance(pending.get('value'), dict) or pending['value'].get('kind') != 'external_wait'):
                        raise DomainError('Another graph answer is already pending', 'run_conflict')
                    run.status, run.data = 'pending', {**run.data, 'pendingAnswer': answer}
                    self.projects._emit(session, project, 'external.wait.wake', {'runId': run.id, 'eventIds': value['eventIds']})
                    woken += 1
            except DomainError as exc:
                with self.projects.transaction() as session:
                    ids = list(session.scalars(select(ExternalWait.id).where(ExternalWait.run_id == run_id,
                        ExternalWait.status.in_(['waiting', 'ready']))))
                for ident in ids:
                    self._block(ident, exc.code)
        return woken

    async def resolve(self, scope, wait_ids, answer):
        if (not isinstance(answer, dict) or set(answer) != {'kind', 'eventIds'} or answer['kind'] != 'external_wait'
                or not isinstance(answer['eventIds'], list) or not 1 <= len(answer['eventIds']) <= 128
                or any(not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{64}', key) for key in answer['eventIds'])
                or len(set(answer['eventIds'])) != len(answer['eventIds'])):
            raise DomainError('External wait requires a bound server event', 'wait_event_invalid')
        await self.validate(scope, wait_ids)
        with self.projects.transaction() as session:
            project, run = self.repository._check(session, scope)
            waits = {ident: session.get(ExternalWait, ident) for ident in wait_ids}
            selected = {}
            for event_id in answer['eventIds']:
                event = session.get(ExternalWaitEvent, event_id)
                if event is None or event.wait_id not in waits:
                    raise DomainError('External resume event is not bound to this graph', 'wait_event_invalid')
                wait = waits[event.wait_id]
                _, _, material, _ = self._guard(session, wait, active_scope=scope)
                if wait.data.get('readyEventId') != event.id or wait.status not in {'ready', 'consumed'}:
                    raise DomainError('External resume event was superseded', 'wait_event_invalid')
                expected_material = {**material, 'physicalAcceptance': False}
                if (event.data['material']['status'] == 'expired' and self.clock() >= wait.deadline_at
                        and material['status'] not in TERMINAL):
                    expected_material['status'] = 'expired'
                if wait.status != 'consumed' and fingerprint(expected_material) != event.data['materialHash']:
                    raise DomainError('External state changed before graph resume', 'wait_event_invalid')
                selected[wait.id] = event
            results = []
            saved = dict(run.data.get('results', {}))
            for task_id in sorted({waits[key].task_id for key in selected}):
                current = TaskResult.model_validate(saved[task_id])
                self._artifacts(session, project, current)
                task_waits = current.wait_ids if current.status.value == 'waiting' else [key for key in selected if waits[key].task_id == task_id]
                if not set(task_waits).issubset(selected):
                    raise DomainError('Resume must cover all waits of the selected task', 'wait_event_invalid')
                existing = waits[task_waits[0]].data.get('resolvedResult')
                if existing:
                    if saved[task_id] != existing:
                        raise DomainError('Resolved external task changed during replay', 'result_conflict')
                    results.append(TaskResult.model_validate(existing))
                    continue
                if current.status.value != 'waiting':
                    raise DomainError('Only a waiting task can receive external event evidence', 'result_conflict')
                observations = [Observation(name='external.wait.'+waits[key].kind, status='UNKNOWN', independent=False,
                    details={'waitId': key, 'jobId': waits[key].job_id, 'eventId': selected[key].id,
                             **selected[key].data['material'], 'providerClaimOnly': True, 'physicalAcceptance': False}) for key in task_waits]
                result = current.model_copy(update={'status': TaskStatus.BLOCKED, 'wait_ids': [],
                    'summary': 'External observation is available; continue from the recorded evidence within the existing objective allowance.',
                    'observations': [*current.observations, *observations], 'blockers': ['external_observation_requires_continuation']})
                encoded = bounded(result.model_dump(mode='json', by_alias=True), limit=64000)
                saved[task_id] = encoded
                for key in task_waits:
                    waits[key].status = 'consumed'
                    waits[key].data = {**waits[key].data, 'resolvedResult': encoded}
                self.projects._emit(session, project, 'task.external_observed', {'runId': run.id, 'result': encoded})
                results.append(result)
            run.data = {**run.data, 'results': saved}
            return results
