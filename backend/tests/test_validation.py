import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from kleos.artifacts.service import Artifact, ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError, TaskSpec
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository, execution_owner
from kleos.projects.store import Objective, Project, ProjectStore
from kleos.validation.service import (
    Candidate,
    CheckerConfiguration,
    CheckerRegistry,
    CheckReport,
    CheckRequirement,
    ReleasePolicy,
    ValidationCheck,
    ValidationService,
)


@pytest.fixture
async def setup(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path / "validation.db"}')
    store.initialize_for_tests()
    blobs = BlobStore(tmp_path / 'blobs')
    actor = Actor(tenant_id='tenant', user_id='owner')
    project = store.create(actor, 'Repair only the bracket', 'create')
    baseline = {}
    with store.transaction() as session:
        p = session.get(Project, project['projectId'])
        for artifact_id, name, content in [('old-part', 'part.py', b'old bracket'), ('kept', 'other.py', b'unchanged')]:
            digest = blobs.put(content)
            session.add(Artifact(id=artifact_id, project_id=p.id, revision=1, name=name, sha256=digest,
                size=len(content), media_type='text/plain', status='input', lane='cad', details={'origin': 'user_upload'}))
            baseline[name] = artifact_id
        p.data = {**p.data, 'sourceManifest': baseline}
    submitted = store.submit(actor, project['projectId'], 'Replace bracket', 1, 'run')
    scope = RunScope(actor=actor, project_id=project['projectId'], run_id=submitted['runId'],
                    revision=submitted['revision'], deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    task = TaskSpec(id='part', objective='Repair only bracket', capability='cad', allowed_paths=['part.py'])
    repo = GraphRepository(store)
    await repo.publish_plan(scope, tasks=[task], superseded={}, turn=0, summary='Repair', question=None)
    artifacts = await ArtifactService(store, blobs).publish_native(scope, task, [
        {'path': 'part.py', 'role': 'source', 'content': b'new bracket',
         'expectedSha256': hashlib.sha256(b'old bracket').hexdigest()},
        {'path': 'outputs/bracket.step', 'role': 'output', 'content': b'fixture STEP candidate'}],
        {'nativeJobId': 'generator-job', 'imageDigest': 'a' * 64, 'requestHash': 'b' * 64})
    yield store, scope, artifacts, baseline
    store.engine.dispose()


def registry(callback, *, allow_na=False):
    result = CheckerRegistry()
    result.register(CheckerConfiguration(key='geometry', version='1', installation_hash='c' * 64,
        qualified=True), callback)
    result.register_policy(ReleasePolicy(key='digital', version='1', required_checks=(
        CheckRequirement(checker_key='geometry', allow_not_applicable=allow_na),)))
    return result


def finish(store, scope):
    with store.transaction() as session:
        session.get(Objective, scope.run_id).status = 'succeeded'


async def test_independent_partial_release_preserves_unchanged_sources_and_manifest(setup):
    store, scope, artifacts, baseline = setup
    calls = []
    async def check(scope, subject):
        calls.append(subject)
        assert {item.path for item in subject.sources} == {'part.py', 'other.py'}
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='checker-job',
                           summary='Fixture measured', measurements={'fixtureCount': 1})
    service = ValidationService(store, registry(check))
    ids = [ref.id for ref in artifacts]
    candidate = service.prepare(scope, ids, 'digital', 'prepare')
    assert service.prepare(scope, ids, 'digital', 'prepare') == candidate
    evidence = await service.run(scope, candidate)
    await service.run(scope, candidate)
    assert len(calls) == 1 and evidence[0]['status'] == 'PASS' and evidence[0]['independent']
    finish(store, scope)
    accepted = service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept')
    assert service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept') == accepted
    manifest = service.release(scope.actor, scope.project_id, accepted['releaseId'])
    assert manifest['kind'] == 'digital' and manifest['validatedRevision'] == scope.revision
    assert len(manifest['manifestHash']) == 64
    with store.transaction() as session:
        project = session.get(Project, scope.project_id)
        assert project.data['sourceManifest'] == {'other.py': baseline['other.py'], 'part.py': artifacts[0].id}
        assert session.get(Artifact, 'kept').sha256 == hashlib.sha256(b'unchanged').hexdigest()
        assert session.get(Candidate, candidate).status == 'accepted'


@pytest.mark.parametrize('status', ['FAIL', 'UNKNOWN', 'ERROR', 'NOT_APPLICABLE'])
async def test_nonpassing_evidence_remains_distinct_and_blocks_acceptance(setup, status):
    store, scope, artifacts, _ = setup
    async def check(scope, subject):
        return CheckReport(subject_hash=subject.subject_hash, status=status, execution_id='check', summary=status)
    service = ValidationService(store, registry(check))
    candidate = service.prepare(scope, [item.id for item in artifacts], 'digital', 'prepare')
    assert (await service.run(scope, candidate))[0]['status'] == status
    finish(store, scope)
    with pytest.raises(DomainError, match='have not passed'):
        service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept')


async def test_not_applicable_requires_explicit_server_policy(setup):
    store, scope, artifacts, _ = setup
    async def check(scope, subject):
        return CheckReport(subject_hash=subject.subject_hash, status='NOT_APPLICABLE', execution_id='check',
                           summary='This qualified check does not apply to this fixture')
    service = ValidationService(store, registry(check, allow_na=True))
    candidate = service.prepare(scope, [item.id for item in artifacts], 'digital', 'prepare')
    await service.run(scope, candidate)
    finish(store, scope)
    assert service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept')['releaseId']


@pytest.mark.parametrize('problem', ['wrong_subject', 'generator_job', 'exception'])
async def test_generator_or_unbound_checks_cannot_become_independent_evidence(setup, problem):
    store, scope, artifacts, _ = setup
    async def check(scope, subject):
        if problem == 'exception':
            raise RuntimeError('Provider secret must not be recorded')
        return CheckReport(subject_hash='f' * 64 if problem == 'wrong_subject' else subject.subject_hash,
            status='PASS', execution_id='generator-job' if problem == 'generator_job' else 'check', summary='Claim')
    service = ValidationService(store, registry(check))
    candidate = service.prepare(scope, [item.id for item in artifacts], 'digital', 'prepare')
    evidence = await service.run(scope, candidate)
    assert evidence[0]['status'] == 'ERROR' and evidence[0]['report'] is None
    finish(store, scope)
    with pytest.raises(DomainError, match='have not passed'):
        service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept')


async def test_model_task_success_is_not_release_evidence_and_worker_cannot_accept(setup):
    store, scope, artifacts, _ = setup
    async def check(scope, subject):
        pytest.fail('No checker invoked in this test')
    service = ValidationService(store, registry(check))
    candidate = service.prepare(scope, [item.id for item in artifacts], 'digital', 'prepare')
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'results': {'part': {'status': 'succeeded', 'observations': [
            {'status': 'PASS', 'independent': True}]}}}
    finish(store, scope)
    with pytest.raises(DomainError, match='have not passed'):
        service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept')
    token = execution_owner.set((scope.run_id, 'worker'))
    try:
        with pytest.raises(DomainError, match='cannot accept'):
            service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept')
    finally:
        execution_owner.reset(token)


async def test_scope_source_closure_and_cross_tenant_access_are_enforced(setup):
    store, scope, artifacts, _ = setup
    async def check(*args):
        pytest.fail('Scope errors must precede checks')
    service = ValidationService(store, registry(check))
    with pytest.raises(DomainError, match='source closure'):
        service.prepare(scope, [artifacts[1].id], 'digital', 'missing-source')
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        tasks = [dict(item) for item in run.data['tasks']]
        tasks[0]['allowedPaths'] = []
        run.data = {**run.data, 'tasks': tasks}
    with pytest.raises(DomainError, match='outside task scope'):
        service.prepare(scope, [item.id for item in artifacts], 'digital', 'scope')
    foreign = scope.model_copy(update={'actor': Actor(tenant_id='other', user_id='owner')})
    with pytest.raises(DomainError, match='not found'):
        service.prepare(foreign, [item.id for item in artifacts], 'digital', 'foreign')


async def test_cancellation_retains_observation_but_fences_release(setup):
    store, scope, artifacts, _ = setup
    async def check(scope, subject):
        GraphRepository(store).cancel(scope.actor, scope.project_id, scope.run_id)
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='check', summary='Measured')
    service = ValidationService(store, registry(check))
    candidate = service.prepare(scope, [item.id for item in artifacts], 'digital', 'prepare')
    with pytest.raises(DomainError, match='no longer active'):
        await service.run(scope, candidate)
    assert service.evidence(scope.actor, scope.project_id, candidate)[0]['status'] == 'PASS'
    with pytest.raises(DomainError, match='must finish'):
        service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept')


async def test_revocation_and_changed_artifacts_invalidate_evidence(setup):
    store, scope, artifacts, _ = setup
    async def check(scope, subject):
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='check', summary='Measured')
    registrations = registry(check)
    service = ValidationService(store, registrations)
    candidate = service.prepare(scope, [item.id for item in artifacts], 'digital', 'prepare')
    await service.run(scope, candidate)
    finish(store, scope)
    with store.transaction() as session:
        row = session.get(Artifact, artifacts[0].id)
        row.sha256 = 'f' * 64
    with pytest.raises(DomainError, match='subject changed'):
        service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept')
    with store.transaction() as session:
        session.get(Artifact, artifacts[0].id).sha256 = artifacts[0].sha256
    registrations.revoke('geometry')
    with pytest.raises(DomainError, match='unavailable'):
        service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept')


async def test_uncertain_running_checker_is_not_repeated(setup):
    store, scope, artifacts, _ = setup
    async def check(*args):
        pytest.fail('Unknown isolated execution cannot be repeated')
    service = ValidationService(store, registry(check))
    candidate = service.prepare(scope, [item.id for item in artifacts], 'digital', 'prepare')
    with store.transaction() as session:
        session.add(ValidationCheck(candidate_id=candidate, checker_key='geometry', status='running', data={}))
    with pytest.raises(DomainError, match='reconciliation'):
        await service.run(scope, candidate)


def test_unqualified_checker_and_physical_policy_cannot_be_enabled():
    with pytest.raises(DomainError, match='qualified'):
        CheckerRegistry().register(CheckerConfiguration(key='check', version='1', installation_hash='a' * 64), None)
    with pytest.raises(ValidationError):
        ReleasePolicy(key='print', version='1', kind='physical', required_checks=(CheckRequirement(checker_key='check'),))


async def test_project_revision_change_invalidates_previous_passing_checks(setup):
    store, scope, artifacts, _ = setup
    async def check(scope, subject):
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='check', summary='Measured')
    service = ValidationService(store, registry(check))
    candidate = service.prepare(scope, [item.id for item in artifacts], 'digital', 'prepare')
    await service.run(scope, candidate)
    finish(store, scope)
    with store.transaction() as session:
        session.get(Project, scope.project_id).advance_revision()
    with pytest.raises(DomainError, match='stale'):
        service.accept(scope.actor, scope.project_id, candidate, scope.revision + 1, 'accept')
    with pytest.raises(DomainError, match='not found'):
        service.evidence(Actor(tenant_id='another', user_id='owner'), scope.project_id, candidate)
