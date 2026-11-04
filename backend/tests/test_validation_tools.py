import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from kleos.artifacts.service import Artifact, ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.registry import CapabilityRegistry
from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.infrastructure.validation import configure_validation
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Project, ProjectStore
from kleos.validation.cad import IsolatedCadProcess


@pytest.fixture
async def fixture(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path}/state.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='member')
    created = store.create(actor, 'Check CAD exports', 'create')
    submitted = store.submit(actor, created['projectId'], 'Validate exports', 1, 'submit')
    scope = RunScope(actor=actor, project_id=created['projectId'], run_id=submitted['runId'],
                     revision=submitted['revision'], deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    repository = GraphRepository(store)
    artifacts = ArtifactService(store, BlobStore(tmp_path/'blobs'))
    producer = TaskSpec(id='generator', objective='Make bracket', capability='cad', allowed_paths=['model.py'])
    await repository.publish_plan(scope, tasks=[producer], superseded={}, turn=0, summary='CAD', question=None)
    refs = await artifacts.publish_native(scope, producer, [
        {'path': 'model.py', 'role': 'source', 'content': b'candidate source never executed', 'expectedSha256': None},
        {'path': 'outputs/base.step', 'role': 'output', 'content': b'fixture export'},
        {'path': 'outputs/base.stl', 'role': 'output', 'content': b'fixture mesh'},
        {'path': 'outputs/base.svg', 'role': 'output', 'content': b'<svg/>'}],
        {'nativeJobId': 'generator-job', 'imageDigest': 'a'*64, 'requestHash': 'b'*64})
    await repository.publish(scope, TaskResult(task_id=producer.id, status='succeeded', summary='Candidate only', artifacts=refs))
    evidence = Path(__file__).parents[1]/'src/kleos/validation/qualification/cad-check-exact-image.json'
    settings = SimpleNamespace(environment='test', checker_control_root=tmp_path/'checks', docker_socket='/unused',
                               cad_checker_installation={'imageDigest': json.loads(evidence.read_text())['imageId'],
                                                         'qualificationReference': str(evidence)})
    yield store, scope, artifacts, refs, settings
    store.engine.dispose()


def validator_task(refs, criteria=None):
    return TaskSpec(id='verify', objective='Check only exports', capability='validation',
                    input_artifact_ids=[ref.id for ref in refs],
                    acceptance_criteria=['cad.export-integrity'] if criteria is None else criteria)


def test_no_install_does_not_advertise_success_or_tools(tmp_path):
    capabilities = CapabilityRegistry()
    wiring = configure_validation(SimpleNamespace(cad_checker_installation=None), None, None, capabilities)
    assert not wiring.tool_keys and not capabilities.available() and wiring.docker is None
    with pytest.raises(DomainError, match='unavailable'):
        wiring.registry.snapshot('cad.export-integrity')


async def test_helper_records_independent_scoped_check_and_replay_without_accept_tool(fixture, monkeypatch):
    store, scope, artifacts, refs, settings = fixture
    requests = []

    async def fake_run(self, directory, execution_id):
        packet = json.loads((directory/'request.json').read_text())
        requests.append(packet)
        assert len(packet['files']) == 2
        assert not (directory/refs[0].sha256).exists()
        return {'protocol': packet['protocol'], 'subjectHash': packet['subjectHash'], 'executionId': execution_id,
                'status': 'PASS', 'measurements': {'base': {'fixtureOnly': True}}}

    monkeypatch.setattr(IsolatedCadProcess, 'run', fake_run)
    with store.transaction() as session:
        project = session.get(Project, scope.project_id)
        project.data = {**project.data, 'interfaces': {'validation': {'cad': {
            'units': 'mm', 'constraints': [{'part': 'outputs/base', 'metric': 'extent_x_mm', 'expected': 24, 'tolerance': .1}]}}}}
    capabilities = CapabilityRegistry()
    wiring = configure_validation(settings, store, artifacts, capabilities, docker=SimpleNamespace())
    tools = capabilities.tools(capabilities.catalog())
    assert [tool.name for tool in tools] == ['validation.check']
    task = validator_task(refs[:3])
    args = {'artifactIds': task.input_artifact_ids, 'requestKey': 'same-check'}
    result = await tools[0].invoke(scope, task, args)
    repeated = await tools[0].invoke(scope, task, args)
    assert len(requests) == 1 and repeated == result
    assert requests[0]['constraints'][0]['expected'] == 24
    assert not result.blockers
    assert result.observations[0].independent and result.observations[0].status.value == 'PASS'
    candidate = result.observations[0].details['candidateId']
    with pytest.raises(DomainError, match='Objective must finish'):
        wiring.service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept')


async def test_tool_rejects_unassigned_and_unchecked_export_types(fixture):
    store, scope, artifacts, refs, settings = fixture
    capabilities = CapabilityRegistry()
    configure_validation(settings, store, artifacts, capabilities, docker=SimpleNamespace())
    tool = capabilities.tools(capabilities.catalog())[0]
    with pytest.raises(DomainError, match='exact assigned'):
        await tool.authorize(scope, validator_task(refs[:2]), {'artifactIds': [refs[2].id], 'requestKey': 'x'})
    with pytest.raises(DomainError, match='paired STEP/STL'):
        await tool.authorize(scope, validator_task(refs), {'artifactIds': [ref.id for ref in refs], 'requestKey': 'x'})
    with pytest.raises(ValueError):
        await tool.authorize(scope, validator_task(refs), {'artifactIds': [refs[1].id], 'requestKey': 'x', 'accept': True})
    with store.transaction() as session:
        session.get(Artifact, refs[1].id).lane = 'pcb'
    with pytest.raises(DomainError, match='covered digital outputs'):
        await tool.authorize(scope, validator_task(refs), {'artifactIds': [refs[1].id], 'requestKey': 'different-lane'})


async def test_broad_task_claims_stay_unknown_even_if_export_check_passes(fixture, monkeypatch):
    store, scope, artifacts, refs, settings = fixture

    async def fake_run(self, directory, execution_id):
        packet = json.loads((directory/'request.json').read_text())
        assert packet['constraints'] == []
        return {'protocol': packet['protocol'], 'subjectHash': packet['subjectHash'], 'executionId': execution_id,
                'status': 'PASS', 'measurements': {}}

    monkeypatch.setattr(IsolatedCadProcess, 'run', fake_run)
    capabilities = CapabilityRegistry()
    configure_validation(settings, store, artifacts, capabilities, docker=SimpleNamespace())
    tool = capabilities.tools(capabilities.catalog())[0]
    task = validator_task(refs[:3], ['safe for repeated physical use'])
    result = await tool.invoke(scope, task, {'artifactIds': task.input_artifact_ids, 'requestKey': 'limited'})
    assert result.observations[-1].status.value == 'UNKNOWN'
    assert result.blockers == ['unsupported.task-criteria:UNKNOWN']


async def test_production_cannot_enable_local_checker(fixture):
    store, _, artifacts, _, settings = fixture
    settings.environment = 'production'
    with pytest.raises(DomainError, match='local deployment'):
        configure_validation(settings, store, artifacts, CapabilityRegistry(), docker=SimpleNamespace())
