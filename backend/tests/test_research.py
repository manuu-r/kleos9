import hashlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
import test_validation
from sqlalchemy import select
from test_validation import CheckReport
from test_validation import registry as checker_registry
from test_verification import complete

from kleos.api.research import ResearchFacade
from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.registry import CapabilityRegistry
from kleos.capabilities.research import ProjectSourceSearch, register_project_sources
from kleos.contracts.models import Actor, DomainError, Observation, TaskResult, TaskSpec
from kleos.orchestration.engine import RunScope
from kleos.projects.research import ResearchRepository, ResearchService
from kleos.projects.store import Membership, Objective, Project, ProjectStore
from kleos.projects.verification import VerificationService

validation_fixture = test_validation.setup


@pytest.fixture
def research(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path}/research.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    ident = store.create(actor, 'Assess a replacement instrument bracket', 'create')['projectId']
    artifacts = ArtifactService(store, BlobStore(tmp_path / 'blobs'))
    content = 'Material: aluminum. Required load: 15 N. Confirm support temperature.'
    artifacts.upload(actor, ident, {'expectedEngineeringRevision': 1,
        'sha256': hashlib.sha256(content.encode()).hexdigest(),
        'file': {'path': 'spec.txt', 'content': content, 'mediaType': 'text/plain'}}, 'upload')
    with store.transaction() as session:
        project = session.get(Project, ident)
        project.data = {**project.data, 'approvedDesign': {'shape': 'bracket'},
            'interfaces': {'mechanical': {'loadN': 15}}, 'referenceImageIds': ['preserved-image-id'],
            'state': {'conversation': [{'role': 'user', 'text': 'Keep the original mounting holes'}]},
            'modelPolicy': {'provider': 'meta'}}
    repo = ResearchRepository(store)
    registry = CapabilityRegistry()
    keys = register_project_sources(registry, repo, artifacts)
    service = ResearchService(store, registry)
    yield store, actor, ident, artifacts, repo, registry, keys, service
    store.engine.dispose()


def row(env):
    with env[0].transaction() as session:
        return session.get(Project, env[2])


def begin(env):
    store, actor, ident, artifacts, repo, registry, keys, service = env
    receipt = service.submit(actor, ident, row(env).revision, 'research', scope='project_sources')
    scope = RunScope(actor=actor, project_id=ident, run_id=receipt['runId'], revision=row(env).context_revision,
        deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    task = TaskSpec(id='inspect', objective='Inspect the declared load and evidence gaps', capability='research',
        input_artifact_ids=list(row(env).data['sourceManifest'].values()))
    return scope, task


def test_default_web_is_unavailable_not_silently_source_only(research):
    store, actor, ident, _, _, registry, _, service = research
    before = deepcopy(row(research).data)
    with pytest.raises(DomainError) as error:
        ResearchFacade(service).command(actor, {'type': 'project.research',
            'payload': {'projectId': ident}, 'expectedRevision': row(research).revision}, 'web')
    assert error.value.code == 'web_research_unavailable'
    assert row(research).data == before
    with store.transaction() as session:
        assert not list(session.scalars(select(Objective)))
    assert registry.available() == ['research']


def test_explicit_source_admission_preserves_context_and_pins(research):
    store, actor, ident, _, _, registry, keys, service = research
    before = deepcopy(row(research))
    receipt = service.submit(actor, ident, before.revision, 'research', scope='project_sources')
    assert service.submit(actor, ident, before.revision, 'research', scope='project_sources') == receipt
    after = row(research)
    assert after.context_revision == before.context_revision and after.revision == before.revision + 1
    for key in ('sourceManifest', 'approvedDesign', 'requirements', 'interfaces', 'referenceImageIds', 'modelPolicy'):
        assert after.data[key] == before.data[key]
    assert after.data['state']['conversation'] == before.data['state']['conversation']
    with store.transaction() as session:
        run = session.get(Objective, receipt['runId'])
        assert run.revision == before.context_revision
        assert run.data['researchRequest']['tools'] == registry.snapshot(keys)
        assert run.data['researchRequest']['scope'] == 'project_sources'
        assert run.data['modelPolicySnapshot'] == before.data['modelPolicy']
    with pytest.raises(DomainError) as error:
        service.submit(actor, ident, after.revision, 'second', scope='project_sources')
    assert error.value.code == 'run_conflict'


async def test_actual_source_read_receipt_and_candidate_publication(research):
    store, actor, ident, artifacts, repo, registry, keys, _ = research
    before = deepcopy(row(research).data)
    scope, task = begin(research)
    tool = registry.tools(registry.snapshot(keys))[0]
    args = {'artifactIds': task.input_artifact_ids, 'query': '15 N', 'maxChars': 128}

    async def invoke():
        return (await tool.invoke(scope, task, args)).model_dump(mode='json')

    tool_result = await repo.run_once(scope, task.id + ':tool:0', invoke)
    observed = Observation.model_validate(tool_result['observations'][0])
    assert observed.details['entries'][0]['excerpt'].startswith('Material: aluminum')
    assert observed.details['entries'][0]['artifact']['sha256'] == hashlib.sha256(
        artifacts.content(actor, task.input_artifact_ids[0]).data).hexdigest()
    result = TaskResult(task_id=task.id, status='succeeded', summary='The source specifies 15 N; testing remains needed.',
        observations=[observed])
    await repo.publish(scope, result)
    await repo.assert_complete(scope, {task.id: result.model_dump(mode='json')})
    await repo.assert_complete(scope, {task.id: result.model_dump(mode='json')})
    project = row(research)
    assert project.data['state']['research']['status'] == 'candidate'
    assert project.data['state']['research']['feasibilityValidated'] is False
    for name in ('approvedDesign', 'interfaces', 'sourceManifest', 'referenceImageIds'):
        assert project.data[name] == before[name]


async def test_fabricated_model_observation_cannot_complete(research):
    _, _, _, _, repo, *_ = research
    scope, task = begin(research)
    result = TaskResult(task_id=task.id, status='succeeded', summary='Feasible', observations=[
        Observation(name='research.web.retrieved', status='PASS', details={'sources': [{'url': 'https://example.com'}]})])
    with pytest.raises(DomainError) as error:
        await repo.publish(scope, result)
    assert error.value.code == 'research_evidence_missing'
    with pytest.raises(DomainError):
        await repo.assert_complete(scope, {})


async def test_source_receipt_does_not_complete_web_scope(research):
    store, _, _, _, repo, registry, keys, _ = research
    scope, task = begin(research)
    tool = registry.tools(registry.snapshot(keys))[0]
    result = await tool.invoke(scope, task, {'artifactIds': task.input_artifact_ids})
    await repo.run_once(scope, task.id + ':tool:0', lambda: _value(result.model_dump(mode='json')))
    saved = TaskResult(task_id=task.id, status='succeeded', summary='Inspected source', observations=result.observations)
    await repo.publish(scope, saved)
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'researchRequest': {**run.data['researchRequest'], 'scope': 'web'}}
    with pytest.raises(DomainError) as error:
        await repo.assert_complete(scope, {task.id: saved.model_dump(mode='json')})
    assert error.value.code == 'research_evidence_missing'


async def _value(value):
    return value


async def test_sources_require_exact_current_task_scope_and_permission(research):
    store, actor, ident, artifacts, repo, *_ = research
    scope, task = begin(research)
    adapter = ProjectSourceSearch(repo, artifacts)
    with pytest.raises(DomainError) as error:
        await adapter.invoke(scope, task.model_copy(update={'input_artifact_ids': []}),
            {'artifactIds': task.input_artifact_ids})
    assert error.value.code == 'artifact_scope_conflict'
    with store.transaction() as session:
        session.get(Membership, (ident, actor.user_id)).role = 'viewer'
    with pytest.raises(DomainError) as error:
        await adapter.invoke(scope, task, {'artifactIds': task.input_artifact_ids})
    assert error.value.code == 'forbidden'


async def test_cancel_fences_source_read_and_completion(research):
    _, actor, ident, artifacts, repo, *_ = research
    scope, task = begin(research)
    repo.cancel(actor, ident, scope.run_id)
    with pytest.raises(DomainError):
        await ProjectSourceSearch(repo, artifacts).invoke(scope, task, {'artifactIds': task.input_artifact_ids})
    with pytest.raises(DomainError):
        await repo.assert_complete(scope, {})


def test_admission_reauthorizes_replays_and_checks_revision(research):
    store, actor, ident, _, _, _, _, service = research
    revision = row(research).revision
    with pytest.raises(DomainError) as error:
        service.submit(actor, ident, revision - 1, 'stale', scope='project_sources')
    assert error.value.code == 'revision_conflict'
    service.submit(actor, ident, revision, 'once', scope='project_sources')
    with store.transaction() as session:
        session.delete(session.get(Membership, (ident, actor.user_id)))
    with pytest.raises(DomainError):
        service.submit(actor, ident, revision, 'once', scope='project_sources')


async def test_read_only_research_preserves_prior_candidate_verification(validation_fixture, tmp_path):
    store, prior, artifacts, baseline = complete(validation_fixture)
    registry = CapabilityRegistry()
    register_project_sources(registry, ResearchRepository(store), ArtifactService(store, BlobStore(tmp_path / 'unused')))
    receipt = ResearchService(store, registry).submit(prior.actor, prior.project_id,
        prior.revision, 'research-existing', scope='project_sources')
    with store.transaction() as session:
        project = session.get(Project, prior.project_id)
        assert project.context_revision == prior.revision and project.data['sourceManifest'] == baseline
        assert session.get(Objective, prior.run_id).revision == project.context_revision
        session.get(Objective, receipt['runId']).status = 'cancelled'
        ui_revision = project.revision
    async def check(scope, subject):
        assert scope.run_id == prior.run_id
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='independent-fixture',
            summary='Existing candidate fixture independently checked')
    verification = VerificationService(store, checker_registry(check))
    queued = verification.submit(prior.actor, prior.project_id, ui_revision, 'verify-existing',
        policy_key='digital', run_id=prior.run_id)
    assert await verification.process_next()
    assert verification.get(prior.actor, prior.project_id, queued['verificationId'])['status'] == 'completed'
