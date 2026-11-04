"""Independent digital evidence and immutable source-closure release authority."""
import asyncio
import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Awaitable, Callable, Literal
from uuid import uuid4

from pydantic import ConfigDict, Field
from sqlalchemy import JSON, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from kleos.artifacts.service import Artifact
from kleos.artifacts.storage import source_path
from kleos.contracts.models import ArtifactRef, CheckStatus, DomainError, WireModel
from kleos.projects.graph_repository import GraphRepository, execution_owner
from kleos.projects.store import Base, Objective, Project, fingerprint


class CheckerConfiguration(WireModel):
    model_config = ConfigDict(**(WireModel.model_config | {'frozen': True}))
    key: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,79}$')
    version: str = Field(min_length=1, max_length=80)
    installation_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    execution: Literal['isolated'] = 'isolated'
    qualified: bool = False
    timeout_seconds: int = Field(default=120, ge=1, le=600)


class CheckRequirement(WireModel):
    model_config = ConfigDict(**(WireModel.model_config | {'frozen': True}))
    checker_key: str
    allow_not_applicable: bool = False


class ReleasePolicy(WireModel):
    model_config = ConfigDict(**(WireModel.model_config | {'frozen': True}))
    key: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,79}$')
    version: str = Field(min_length=1, max_length=80)
    kind: Literal['digital'] = 'digital'
    required_checks: tuple[CheckRequirement, ...] = Field(min_length=1, max_length=32)


class ValidationInput(WireModel):
    candidate_id: str
    subject_hash: str
    project_id: str
    revision: int
    sources: list[ArtifactRef]
    outputs: list[ArtifactRef]


class CheckReport(WireModel):
    subject_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    status: CheckStatus
    execution_id: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    evidence_artifact_ids: list[str] = Field(default_factory=list, max_length=64)
    measurements: dict = Field(default_factory=dict)


class CheckerAdmissionDeferred(Exception):
    """Trusted adapter guarantees no container create or checker execution was attempted."""


@dataclass(frozen=True)
class Checker:
    config: CheckerConfiguration
    invoke: Callable[..., Awaitable[CheckReport]]


class CheckerRegistry:
    """Only trusted deployment wiring can install qualified isolated checker adapters."""
    def __init__(self):
        self._checkers: dict[str, Checker] = {}
        self._policies: dict[str, ReleasePolicy] = {}
        self._revoked: set[str] = set()

    def register(self, config: CheckerConfiguration, invoke):
        if not config.qualified:
            raise DomainError('Checker has not been qualified', 'checker_unqualified')
        if config.key in self._checkers:
            raise DomainError('Checker registrations are immutable', 'checker_conflict')
        self._checkers[config.key] = Checker(config, invoke)

    def register_policy(self, policy: ReleasePolicy):
        if policy.key in self._policies or len({r.checker_key for r in policy.required_checks}) != len(policy.required_checks):
            raise DomainError('Release policy must be immutable with distinct checks', 'policy_conflict')
        for requirement in policy.required_checks:
            self.checker(requirement.checker_key)
        self._policies[policy.key] = policy

    def revoke(self, key):
        self._revoked.add(key)

    def checker(self, key):
        checker = self._checkers.get(key)
        if checker is None or key in self._revoked:
            raise DomainError('Independent checker is unavailable', 'checker_unavailable')
        return checker

    def snapshot(self, policy_key):
        policy = self._policies.get(policy_key)
        if policy is None:
            raise DomainError('Release policy is unavailable', 'policy_unavailable')
        return {'policy': policy.model_dump(mode='json'), 'checkers': {
            r.checker_key: self.checker(r.checker_key).config.model_dump(mode='json') for r in policy.required_checks}}


class Candidate(Base):
    __tablename__ = 'kleos2_validation_candidates'
    __table_args__ = (UniqueConstraint('run_id', 'request_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    request_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    subject_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict] = mapped_column(JSON)


class ValidationCheck(Base):
    __tablename__ = 'kleos2_validation_checks'
    candidate_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    checker_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    status: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict] = mapped_column(JSON)


class Release(Base):
    __tablename__ = 'kleos2_releases'
    __table_args__ = (UniqueConstraint('candidate_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    candidate_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)


def _ref(row):
    return ArtifactRef(id=row.id, path=row.name, sha256=row.sha256, revision=row.revision,
                       media_type=row.media_type, size=row.size)


def _interface_authority(project):
    """Current configuration authority, not engineering evidence or acceptance."""
    return {'schemaVersion': 1, 'interfaces': copy.deepcopy(project.data.get('interfaces', {})),
        'acceptedInterfaces': copy.deepcopy(project.data.get('acceptedInterfaces')),
        'acceptedCircuitIdentity': copy.deepcopy(project.data.get('acceptedCircuitIdentity'))}


class ValidationService:
    def __init__(self, projects, registry: CheckerRegistry, *, repository=None, clock=lambda: datetime.now(UTC)):
        self.projects, self.registry = projects, registry
        self.repository = repository or GraphRepository(projects)
        self.clock = clock

    def _candidate(self, session, scope, candidate_id):
        project, run = self.repository._check(session, scope)
        row = session.get(Candidate, candidate_id)
        if row is None or row.project_id != project.id or row.run_id != run.id:
            raise DomainError('Validation candidate not found', 'not_found', 404)
        if row.revision != project.context_revision or row.data['baseline'] != project.data.get('sourceManifest', {}):
            raise DomainError('Validation source baseline changed', 'stale_context')
        self._verify_subject(session, row)
        return project, row

    def _verify_subject(self, session, candidate):
        # Preserve the original subject format for historical candidates. New
        # snapshots are part of the same hash given to every independent checker;
        # removing the field cannot silently downgrade a new subject to legacy.
        subject = {key: candidate.data[key] for key in ('projectId', 'revision', 'sources', 'outputs', 'policy')}
        if 'interfaceAuthority' in candidate.data:
            subject['interfaceAuthority'] = candidate.data['interfaceAuthority']
        if (fingerprint(subject) != candidate.subject_hash or subject['projectId'] != candidate.project_id
                or subject['revision'] != candidate.revision):
            raise DomainError('Validation subject binding changed', 'artifact_conflict')
        if 'interfaceAuthority' in candidate.data:
            project = session.get(Project, candidate.project_id)
            if project is None or candidate.data['interfaceAuthority'] != _interface_authority(project):
                raise DomainError('Validation interface authority changed', 'stale_context')
        for encoded in candidate.data['sources'] + candidate.data['outputs']:
            reference = ArtifactRef.model_validate(encoded)
            row = session.get(Artifact, reference.id)
            if row is None or row.project_id != candidate.project_id or _ref(row) != reference:
                raise DomainError('Validation subject changed', 'artifact_conflict')

    def prepare(self, scope, artifact_ids: list[str], policy_key: str, request_key: str, *, reuse_subject=False):
        if (not artifact_ids or len(artifact_ids) > 256 or len(set(artifact_ids)) != len(artifact_ids)
                or not isinstance(request_key, str) or not 1 <= len(request_key) <= 128):
            raise DomainError('Invalid validation selection', 'invalid_request', 422)
        from kleos.validation.policies import POLICY_LANES, check_policy_artifacts
        if policy_key in POLICY_LANES:
            check_policy_artifacts(self.repository, scope, artifact_ids, policy_key)
        pins = self.registry.snapshot(policy_key)
        request_hash = fingerprint({'artifacts': artifact_ids, 'policy': pins})
        with self.projects.transaction() as session:
            project, run = self.repository._check(session, scope)
            from kleos.validation.policies import assert_run_coverage
            assert_run_coverage(self.registry, session, run, policy_key, artifact_ids)
            prior = session.scalar(select(Candidate).where(Candidate.run_id == run.id,
                                                          Candidate.request_key == request_key))
            if prior:
                if prior.request_hash != request_hash:
                    raise DomainError('Validation request changed during replay', 'idempotency_conflict')
                self._verify_subject(session, prior)
                return prior.id
            baseline = dict(project.data.get('sourceManifest', {}))
            closure = dict(baseline)
            retained = {}
            if run.data.get('compositionResults'):
                from kleos.projects.composition_results import retained_repair_sources
                retained = {ref.id: ref for ref in retained_repair_sources(session, run).values()}
                closure.update({ref.path: ref.id for ref in retained.values()})
            selected, changed, outputs, producers = [], set(), [], set()
            for ref in retained.values():
                row = session.get(Artifact, ref.id)
                if (row is None or row.project_id != project.id or _ref(row) != ref
                        or row.details.get('role') != 'source' or not row.details.get('nativeJobId')):
                    raise DomainError('Retained repair source binding changed', 'artifact_scope_conflict')
                # Read-only prior-run sources still have generators. Their
                # executions cannot independently approve this proposed closure.
                producers.add(row.details['nativeJobId'])
            tasks = {task['id']: task for task in run.data.get('tasks', [])}
            for artifact_id in artifact_ids:
                row = session.get(Artifact, artifact_id)
                if artifact_id in retained:
                    if (row is None or row.project_id != project.id or _ref(row) != retained[artifact_id]
                            or row.details.get('role') != 'source'):
                        raise DomainError('Retained repair source binding changed', 'artifact_scope_conflict')
                    # The completed compilation projection already proved this exact
                    # prior repair input. It is read-only source history, not a new
                    # current-run producer or an edit to the accepted baseline.
                    continue
                if (row is None or row.project_id != project.id or row.revision != scope.revision
                        or row.status != 'candidate' or row.details.get('runId') != run.id):
                    raise DomainError('Artifact is not a current candidate for this run', 'artifact_scope_conflict')
                if row.details.get('origin') != 'native_candidate' or not row.details.get('nativeJobId'):
                    raise DomainError('Candidate lacks native provenance', 'invalid_provenance')
                task = tasks.get(row.details.get('taskId'))
                if task is None:
                    raise DomainError('Candidate task is not in the authorized plan', 'scope_conflict')
                selected.append(row)
                producers.add(row.details['nativeJobId'])
                if row.details.get('role') == 'source':
                    path = source_path(row.name)
                    if path in changed or path not in task.get('allowedPaths', task.get('allowed_paths', [])):
                        raise DomainError('Candidate source edit is outside task scope', 'scope_conflict')
                    changed.add(path)
                    closure[path] = row.id
                elif row.details.get('role') == 'output':
                    outputs.append(_ref(row))
                else:
                    raise DomainError('Candidate role is unsupported', 'invalid_provenance')
            if not outputs:
                raise DomainError('A digital release needs actual outputs', 'release_incomplete')
            # Outputs must have been produced against this exact composed source closure.
            # Cross-lane outputs built against different closures require an explicit rebuild.
            for row in selected:
                if row.details.get('role') == 'output' and row.details.get('sourceClosure') != closure:
                    raise DomainError('Output does not match the proposed source closure', 'source_closure_conflict')
            sources = []
            for path, artifact_id in sorted(closure.items()):
                row = session.get(Artifact, artifact_id)
                if row is None or row.project_id != project.id or row.name != path:
                    raise DomainError('Source closure is invalid', 'source_closure_conflict')
                sources.append(_ref(row))
            if not sources:
                raise DomainError('Release has no exact source closure', 'release_incomplete')
            candidate_id = str(uuid4())
            subject = {'projectId': project.id, 'revision': scope.revision,
                'sources': [ref.model_dump(mode='json') for ref in sources],
                'outputs': [ref.model_dump(mode='json') for ref in sorted(outputs, key=lambda item: item.id)],
                'policy': pins, 'interfaceAuthority': _interface_authority(project)}
            digest = fingerprint(subject)
            if reuse_subject:
                prior = session.scalar(select(Candidate).where(Candidate.run_id == run.id,
                    Candidate.subject_hash == digest).order_by(Candidate.id).limit(1))
                if prior is not None:
                    self._verify_subject(session, prior)
                    return prior.id
            session.add(Candidate(id=candidate_id, project_id=project.id, run_id=run.id, revision=scope.revision,
                request_key=request_key, request_hash=request_hash, subject_hash=digest, status='prepared',
                data={**subject, 'baseline': baseline, 'closure': closure, 'changedPaths': sorted(changed),
                      'producerJobs': sorted(producers),
                      **({'verificationJobId': scope.verification_id} if hasattr(scope, 'verification_id') else {})}))
            self.projects._emit(session, project, 'validation.prepared', {'candidateId': candidate_id, 'subjectHash': digest})
            return candidate_id

    async def run(self, scope, candidate_id):
        with self.projects.transaction() as session:
            _, candidate = self._candidate(session, scope, candidate_id)
            requirements = candidate.data['policy']['policy']['required_checks']
        for requirement in requirements:
            await self._check(scope, candidate_id, requirement['checker_key'])
        with self.projects.transaction() as session:
            _, candidate = self._candidate(session, scope, candidate_id)
            qualified = all(session.get(ValidationCheck, (candidate_id, requirement['checker_key'])).status in (
                {CheckStatus.PASS.value, CheckStatus.NOT_APPLICABLE.value}
                if requirement['allow_not_applicable'] else {CheckStatus.PASS.value}) for requirement in requirements)
            candidate.status = 'qualified' if qualified else 'blocked'
        return self.evidence(scope.actor, scope.project_id, candidate_id)

    async def _check(self, scope, candidate_id, key):
        checker = self.registry.checker(key)
        with self.projects.transaction() as session:
            project, candidate = self._candidate(session, scope, candidate_id)
            pinned = candidate.data['policy']['checkers'].get(key)
            if pinned != checker.config.model_dump(mode='json'):
                raise DomainError('Independent checker installation changed', 'checker_conflict')
            existing = session.get(ValidationCheck, (candidate_id, key))
            if existing and existing.status != 'queued':
                if existing.status == 'running':
                    raise DomainError('Prior check needs reconciliation', 'effect_uncertain')
                return
            claim = {'subjectHash': candidate.subject_hash, 'checker': pinned, 'independent': True,
                     'startedAt': self.clock().isoformat(),
                     'admissionAttempts': (existing.data.get('admissionAttempts', 0) if existing else 0)+1}
            if existing:
                existing.status, existing.data = 'running', {**existing.data, **claim}
            else:
                session.add(ValidationCheck(candidate_id=candidate_id, checker_key=key, status='running', data=claim))
            candidate.status = 'checking'
            subject = ValidationInput(candidate_id=candidate.id, subject_hash=candidate.subject_hash,
                project_id=project.id, revision=candidate.revision,
                sources=candidate.data['sources'], outputs=candidate.data['outputs'])
            producer_jobs = set(candidate.data['producerJobs'])
        report = None
        try:
            remaining = (scope.deadline_at - self.clock()).total_seconds()
            async with asyncio.timeout(max(0, min(remaining, checker.config.timeout_seconds))):
                report = CheckReport.model_validate(await checker.invoke(scope, subject))
            if report.subject_hash != subject.subject_hash or report.execution_id in producer_jobs:
                raise DomainError('Checker evidence is not independent for this exact subject', 'invalid_evidence')
            if len(str(report.measurements)) > 32000:
                raise DomainError('Checker measurements exceed their bound', 'invalid_evidence')
        except CheckerAdmissionDeferred:
            # Only a trusted pre-dispatch signal can return a claim to admission waiting.
            # A timeout, transport failure or lost worker remains an error/uncertain check.
            with self.projects.transaction() as session:
                project, candidate = self._candidate(session, scope, candidate_id)
                check = session.get(ValidationCheck, (candidate_id, key))
                check.status = 'queued'
                check.data = {**check.data, 'deferredAt': self.clock().isoformat(), 'reason': 'native_capacity_busy'}
                candidate.status = 'prepared'
                self.projects._emit(session, project, 'validation.waiting',
                    {'candidateId': candidate_id, 'checkerKey': key, 'reason': 'native_capacity_busy'})
            raise
        except asyncio.CancelledError:
            # A lost worker never reruns a possibly live isolated check blindly.
            raise
        except Exception:
            report = None
        with self.projects.transaction() as session:
            # Observations are retained even after cancellation; publication and acceptance are fenced below.
            check = session.get(ValidationCheck, (candidate_id, key))
            candidate = session.get(Candidate, candidate_id)
            if report is not None:
                for artifact_id in report.evidence_artifact_ids:
                    row = session.get(Artifact, artifact_id)
                    if (row is None or row.project_id != candidate.project_id
                            or row.details.get('runId') != candidate.run_id
                            or row.details.get('nativeJobId') != report.execution_id):
                        report = None
                        break
            check.status = report.status.value if report else CheckStatus.ERROR.value
            check.data = {**check.data, 'report': report.model_dump(mode='json') if report else None,
                          'finishedAt': self.clock().isoformat()}
        with self.projects.transaction() as session:
            project, candidate = self._candidate(session, scope, candidate_id)
            self.registry.checker(key)  # Revocation fences a checker that completed during IO.
            self.projects._emit(session, project, 'validation.observed',
                {'candidateId': candidate.id, 'checkerKey': key, 'status': check.status})

    def evidence(self, actor, project_id, candidate_id):
        with self.projects.transaction() as session:
            self.projects.authorized(session, actor, project_id)
            candidate = session.get(Candidate, candidate_id)
            if candidate is None or candidate.project_id != project_id:
                raise DomainError('Validation candidate not found', 'not_found', 404)
            return [{'checkerKey': row.checker_key, 'status': row.status, **row.data} for row in session.scalars(
                select(ValidationCheck).where(ValidationCheck.candidate_id == candidate_id))]

    def accept(self, actor, project_id, candidate_id, expected_revision, key):
        if execution_owner.get() is not None:
            raise DomainError('Engineering workers cannot accept their own release', 'independent_acceptance_required')

        def operation(session):
            project = self.projects.authorized(session, actor, project_id, write=True)
            candidate = session.get(Candidate, candidate_id)
            if candidate is None or candidate.project_id != project_id:
                raise DomainError('Validation candidate not found', 'not_found', 404)
            verification_id = candidate.data.get('verificationJobId')
            if verification_id:
                from kleos.projects.verification import VerificationJob
                verification = session.get(VerificationJob, verification_id)
                if verification is None or verification.status != 'completed':
                    raise DomainError('Verification has not completed or was cancelled', 'verification_incomplete')
            run = session.get(Objective, candidate.run_id)
            if run is None or run.status != 'succeeded':
                raise DomainError('Objective must finish before independent acceptance', 'run_inactive')
            from kleos.validation.policies import assert_run_coverage
            assert_run_coverage(self.registry, session, run, candidate.data['policy']['policy']['key'],
                                [ref['id'] for ref in candidate.data['outputs']])
            if project.revision != expected_revision or candidate.revision != project.context_revision:
                raise DomainError('Release candidate is stale', 'stale_context')
            if project.data.get('sourceManifest', {}) != candidate.data['baseline']:
                raise DomainError('Source baseline changed', 'source_closure_conflict')
            self._verify_subject(session, candidate)
            pins = self.registry.snapshot(candidate.data['policy']['policy']['key'])
            if pins != candidate.data['policy']:
                raise DomainError('Release policy or checker installation changed', 'checker_conflict')
            evidence = []
            for requirement in pins['policy']['required_checks']:
                check = session.get(ValidationCheck, (candidate_id, requirement['checker_key']))
                allowed = {CheckStatus.PASS.value}
                if requirement['allow_not_applicable']:
                    allowed.add(CheckStatus.NOT_APPLICABLE.value)
                if (check is None or check.status not in allowed or not check.data.get('independent')
                        or check.data.get('subjectHash') != candidate.subject_hash):
                    raise DomainError('Required independent checks have not passed', 'release_not_qualified')
                evidence.append({'checkerKey': check.checker_key, 'status': check.status, **check.data})
            prior = session.scalar(select(Release).where(Release.candidate_id == candidate_id))
            if prior:
                raise DomainError('Candidate was already accepted', 'release_conflict')
            release_id = str(uuid4())
            project.advance_revision()
            manifest = {'id': release_id, 'projectId': project.id, 'candidateId': candidate.id,
                'validatedRevision': candidate.revision, 'acceptedRevision': project.revision,
                'subjectHash': candidate.subject_hash, 'sources': candidate.data['sources'],
                'outputs': candidate.data['outputs'], 'policy': pins, 'checks': evidence,
                'acceptedBy': actor.user_id, 'acceptedAt': self.clock().isoformat(), 'kind': 'digital'}
            if 'interfaceAuthority' in candidate.data:
                manifest['interfaceAuthority'] = copy.deepcopy(candidate.data['interfaceAuthority'])
            manifest['manifestHash'] = fingerprint(manifest)
            session.add(Release(id=release_id, project_id=project.id, candidate_id=candidate_id,
                                revision=project.revision, data=manifest))
            project.data = {**project.data, 'sourceManifest': candidate.data['closure'], 'acceptedReleaseId': release_id}
            candidate.status = 'accepted'
            self.projects._emit(session, project, 'release.accepted',
                {'releaseId': release_id, 'manifestHash': manifest['manifestHash']})
            return {'projectId': project.id, 'releaseId': release_id, 'revision': project.revision}

        return self.projects._command(actor, key, {'operation': 'release.accept', 'projectId': project_id,
            'candidateId': candidate_id, 'expectedRevision': expected_revision}, operation)

    def release(self, actor, project_id, release_id):
        with self.projects.transaction() as session:
            self.projects.authorized(session, actor, project_id)
            row = session.get(Release, release_id)
            if row is None or row.project_id != project_id:
                raise DomainError('Release not found', 'not_found', 404)
            return row.data
