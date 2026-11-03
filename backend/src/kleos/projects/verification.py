"""Durable independent checks of a completed objective, without a new generation run."""
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import JSON, Float, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from kleos.artifacts.service import Artifact
from kleos.contracts.models import DomainError
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Base, Objective, Project, fingerprint
from kleos.validation.policies import POLICY_LANES, include_candidate, published_native_rows, select_policy
from kleos.validation.service import Candidate, CheckerAdmissionDeferred, ValidationService, _ref

verification_owner = ContextVar('kleos_verification_owner', default=None)


class VerificationScope(RunScope):
    verification_id: str


class VerificationJob(Base):
    __tablename__ = 'kleos2_verification_jobs'
    __table_args__ = (UniqueConstraint('run_id', 'request_hash'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), index=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    data: Mapped[dict] = mapped_column(JSON)


def current_candidates(session, run, policy_key=None):
    """Only published, nonsuperseded results; staging files are never implicitly selected."""
    rows = published_native_rows(session, run)
    refs = {row.id: _ref(row).model_dump(mode='json') for row in rows
            if policy_key not in POLICY_LANES or include_candidate(policy_key, row)}
    if not refs or len(refs) > 256:
        raise DomainError('Select an objective with bounded published native candidates', 'verification_inputs_missing')
    return [refs[key] for key in sorted(refs)]


class VerificationRepository(GraphRepository):
    """A narrowly scoped checker authority; it does not reactivate the objective graph."""
    def __init__(self, store, *, clock=lambda: datetime.now(UTC)):
        super().__init__(store)
        self.clock = clock

    def _check(self, session, scope):
        if not isinstance(scope, VerificationScope):
            return super()._check(session, scope)
        project = self.store.authorized(session, scope.actor, scope.project_id, write=True)
        job = session.get(VerificationJob, scope.verification_id)
        run = session.get(Objective, scope.run_id)
        if (job is None or job.project_id != project.id or job.run_id != scope.run_id
                or run is None or run.project_id != project.id):
            raise DomainError('Verification job not found', 'not_found', 404)
        if job.status != 'running' or verification_owner.get() != (job.id, job.data.get('workerToken')):
            raise DomainError('Verification worker is no longer authorized', 'worker_fenced')
        if VerificationScope.model_validate(job.data['scope']) != scope:
            raise DomainError('Verification scope changed', 'scope_conflict')
        if run.status != 'succeeded':
            raise DomainError('Objective is not completed', 'run_inactive')
        if (run.revision != scope.revision or project.context_revision != scope.revision
                or job.data['baseline'] != project.data.get('sourceManifest', {})):
            raise DomainError('Verification context changed', 'stale_context')
        if self.clock() >= scope.deadline_at:
            raise DomainError('Verification deadline exceeded', 'deadline_exceeded')
        if current_candidates(session, run, job.data['policy']['policy']['key']) != job.data['artifacts']:
            raise DomainError('Verification candidate selection changed', 'artifact_conflict')
        return project, run

    async def resolve_artifacts(self, scope, artifact_ids):
        if not isinstance(scope, VerificationScope):
            return await super().resolve_artifacts(scope, artifact_ids)
        with self.store.transaction() as session:
            project, _ = self._check(session, scope)
            job = session.get(VerificationJob, scope.verification_id)
            permitted = set(project.data.get('sourceManifest', {}).values())
            permitted.update(ref['id'] for ref in job.data['artifacts'])
            if len(artifact_ids) > 256 or len(set(artifact_ids)) != len(artifact_ids):
                raise DomainError('Invalid checker input selection', 'invalid_request', 422)
            refs = []
            for ident in artifact_ids:
                row = session.get(Artifact, ident) if ident in permitted else None
                if row is None or row.project_id != project.id:
                    raise DomainError('Checker artifact is outside the pinned selection', 'artifact_scope_conflict')
                refs.append(_ref(row))
            return refs


class VerificationService:
    def __init__(self, projects, registry, *, clock=lambda: datetime.now(UTC)):
        self.projects, self.registry, self.clock = projects, registry, clock
        self.repository = VerificationRepository(projects, clock=clock)
        self.validation = ValidationService(projects, registry, repository=self.repository, clock=clock)

    def submit(self, actor, project_id, expected_revision, key, *, policy_key, run_id=None):
        def operation(session):
            project = self.projects.authorized(session, actor, project_id, write=True)
            if project.revision != expected_revision:
                raise DomainError('Project changed before verification', 'revision_conflict')
            runs = list(session.scalars(select(Objective).where(Objective.project_id == project.id,
                Objective.revision == project.context_revision)))
            if any(run.status in {'pending', 'running', 'waiting'} for run in runs):
                raise DomainError('Finish the active objective before independent verification', 'run_conflict')
            selected = [run for run in runs if run.status == 'succeeded' and
                        (run.id == run_id if run_id else run.data.get('kind') != 'project.capture')]
            if run_id is None and selected:
                selected = [max(selected, key=lambda run: (run.data.get('createdAt', ''), run.id))]
            if len(selected) != 1:
                raise DomainError('Select one completed current objective', 'run_conflict')
            run = selected[0]
            selected_policy = policy_key
            if selected_policy is None:
                all_refs = current_candidates(session, run)
                selected_policy = select_policy(self.registry, [session.get(Artifact, ref['id']) for ref in all_refs])
            pins = self.registry.snapshot(selected_policy)
            artifacts = current_candidates(session, run, selected_policy)
            digest = fingerprint({'artifacts': artifacts, 'policy': pins,
                                  'baseline': project.data.get('sourceManifest', {})})
            prior = session.scalar(select(VerificationJob).where(VerificationJob.run_id == run.id,
                                                                 VerificationJob.request_hash == digest))
            if prior:
                return self._response(project, prior)
            ident = str(uuid4())
            scope = VerificationScope(actor=actor, project_id=project.id, run_id=run.id,
                revision=project.context_revision, deadline_at=self.clock()+timedelta(minutes=15),
                verification_id=ident)
            job = VerificationJob(id=ident, project_id=project.id, run_id=run.id, request_hash=digest,
                status='queued', lease_until=0, data={'scope': scope.model_dump(mode='json'),
                    'artifacts': artifacts, 'policy': pins, 'baseline': dict(project.data.get('sourceManifest', {})),
                    'createdAt': self.clock().isoformat()})
            active = self._active(session, project)
            if active:
                raise DomainError('A verification job is already active', 'run_conflict')
            session.add(job)
            state = project.data.get('state', {})
            project.data = {**project.data, 'status': 'queued', 'state': {**state, 'planning': {
                'phase': 'verification', 'pending': True, 'runId': run.id, 'verificationId': ident}}}
            self.projects._emit(session, project, 'verification.queued', {'verificationId': ident, 'runId': run.id})
            return self._response(project, job)
        return self.projects._command(actor, key, {'type': 'project.verify', 'projectId': project_id,
            'expectedRevision': expected_revision, 'runId': run_id, 'policyKey': policy_key}, operation)

    @staticmethod
    def _response(project, job):
        return {'projectId': project.id, 'runId': job.run_id, 'verificationId': job.id,
                'revision': project.revision, 'status': job.status, 'candidateId': job.data.get('candidateId'),
                'blocker': job.data.get('blocker'), 'policyKey': job.data['policy']['policy']['key'],
                'checkStatuses': job.data.get('checkStatuses', {})}

    def get(self, actor, project_id, ident):
        with self.projects.transaction() as session:
            project = self.projects.authorized(session, actor, project_id)
            job = session.get(VerificationJob, ident)
            if job is None or job.project_id != project.id:
                raise DomainError('Verification job not found', 'not_found', 404)
            return self._response(project, job)

    @staticmethod
    def _clear_pending(project, job, status):
        state = project.data.get('state', {})
        planning = state.get('planning', {})
        if planning.get('verificationId') == job.id:
            project.data = {**project.data, 'status': status, 'state': {**state,
                'planning': {**planning, 'pending': False}}}

    def has_active(self, actor, project_id, run_id=None):
        with self.projects.transaction() as session:
            project = self.projects.authorized(session, actor, project_id, write=True)
            return bool(self._active(session, project, run_id))

    @staticmethod
    def _active(session, project, run_id=None):
        return list(session.scalars(select(VerificationJob).join(Objective, Objective.id == VerificationJob.run_id)
            .where(VerificationJob.project_id == project.id,
                   VerificationJob.status.in_(['queued', 'running']),
                   Objective.revision == project.context_revision,
                   *([VerificationJob.run_id == run_id] if run_id else []))))

    def cancel_current(self, actor, project_id, expected_revision, key, *, run_id=None):
        def operation(session):
            project = self.projects.authorized(session, actor, project_id, write=True)
            if project.revision != expected_revision:
                raise DomainError('Project changed before cancellation', 'revision_conflict')
            jobs = self._active(session, project, run_id)
            if len(jobs) != 1:
                raise DomainError('Select one active verification to cancel', 'run_conflict')
            job = jobs[0]
            job.status = 'cancelled'
            self._clear_pending(project, job, 'cancelled')
            self.projects._emit(session, project, 'verification.cancelled',
                                {'verificationId': job.id, 'runId': job.run_id})
            return self._response(project, job)
        return self.projects._command(actor, key, {'type': 'project.cancel', 'projectId': project_id,
            'expectedRevision': expected_revision, 'runId': run_id}, operation)

    def cancel(self, actor, project_id, ident):
        with self.projects.transaction() as session:
            project = self.projects.authorized(session, actor, project_id, write=True)
            job = session.get(VerificationJob, ident)
            if job is None or job.project_id != project.id:
                raise DomainError('Verification job not found', 'not_found', 404)
            if job.status in {'queued', 'running'}:
                job.status = 'cancelled'
                self._clear_pending(project, job, 'cancelled')
                self.projects._emit(session, project, 'verification.cancelled', {'verificationId': job.id})
            return self._response(project, job)

    async def process_next(self):
        now = self.clock().timestamp()
        with self.projects.transaction() as session:
            job = session.scalar(select(VerificationJob).where(
                (VerificationJob.status == 'queued') |
                ((VerificationJob.status == 'running') & (VerificationJob.lease_until < now)))
                .order_by(VerificationJob.id).with_for_update(skip_locked=True).limit(1))
            if job is None:
                return False
            ident, data = job.id, dict(job.data)
            token = str(uuid4())
            job.status, job.lease_until = 'running', now+960
            job.data = {**data, 'workerToken': token}
        owner = verification_owner.set((ident, token))
        status, blocker, check_statuses = 'completed', None, {}
        try:
            scope = VerificationScope.model_validate(data['scope'])
            if self.registry.snapshot(data['policy']['policy']['key']) != data['policy']:
                raise DomainError('Pinned checker installation changed', 'checker_conflict')
            candidate = self.validation.prepare(scope, [ref['id'] for ref in data['artifacts']],
                data['policy']['policy']['key'], 'verification:'+ident, reuse_subject=True)
            with self.projects.transaction() as session:
                self.repository._check(session, scope)
                job = session.get(VerificationJob, ident)
                job.data = {**job.data, 'candidateId': candidate}
                candidate_row = session.get(Candidate, candidate)
                candidate_row.data = {**candidate_row.data, 'verificationJobId': ident}
            evidence = await self.validation.run(scope, candidate)
            check_statuses = {row['checkerKey']: row['status'] for row in evidence}
        except CheckerAdmissionDeferred:
            status, blocker = 'queued', 'native_capacity_busy'
        except DomainError as exc:
            status, blocker = 'blocked', exc.code
        except Exception:
            status, blocker = 'blocked', 'verification_failed'
        finally:
            verification_owner.reset(owner)
        with self.projects.transaction() as session:
            # Project-first locking serializes cancellation with final publication.
            project = session.get(Project, data['scope']['project_id'], with_for_update=True)
            job = session.get(VerificationJob, ident, with_for_update=True)
            if job.status == 'running' and job.data.get('workerToken') == token:
                if status == 'completed':
                    final_owner = verification_owner.set((ident, token))
                    try:
                        self.repository._check(session, scope)
                        if self.registry.snapshot(data['policy']['policy']['key']) != data['policy']:
                            raise DomainError('Pinned checker installation changed', 'checker_conflict')
                    except DomainError as exc:
                        status, blocker = 'blocked', exc.code
                    finally:
                        verification_owner.reset(final_owner)
                job.status = status
                if status != 'queued':
                    self._clear_pending(project, job, 'review' if status == 'completed' else 'blocked')
                job.data = {**job.data, 'blocker': blocker, 'checkStatuses': check_statuses,
                            'finishedAt': self.clock().isoformat()}
                self.projects._emit(session, project, 'verification.'+status,
                    {'verificationId': job.id, 'runId': job.run_id, 'candidateId': job.data.get('candidateId'),
                     'checkStatuses': check_statuses, 'blocker': blocker})
        return True
