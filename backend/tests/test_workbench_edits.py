"""Explicit requirement changes retain history and re-enter unapproved capture."""
import copy
import json

import httpx
import pytest
from sqlalchemy import select
from test_design import generate, result, run_scope
from test_interface_runtime import configured_runtime

from kleos.artifacts.service import Artifact, ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.design.service import DesignService
from kleos.contracts.models import Actor, DomainError
from kleos.projects.graph_repository import GraphRepository, execution_owner
from kleos.projects.store import Membership, Objective, Project, ProjectStore
from kleos.projects.workbench_edits import WorkbenchEdits, battery_requirement, user_requirement_snapshot
from kleos.validation.service import Release


def battery_body(ident, revision):
    return {'type': 'project.swapBattery', 'expectedRevision': revision, 'payload': {
        'projectId': ident, 'capacityMah': 1500, 'dimensionsMm': {'x': 30, 'y': 50, 'z': 5}}}


def test_battery_requirement_text_does_not_round_declared_geometry():
    text = battery_requirement({'capacityMah': 1234.56789, 'dimensionsMm': {'x': 30.123456789, 'y': 50, 'z': 5}})
    assert '1234.56789 mAh' in text and 'x=30.123456789' in text


@pytest.fixture
async def setup(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path}/edits.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    design = DesignService(store)
    created = design.create(actor, 'Battery-powered enclosure', 'create')
    await generate(design, actor, created, result())
    artifacts = ArtifactService(store, BlobStore(tmp_path / 'blobs'))
    yield store, actor, design, created, WorkbenchEdits(store, design), artifacts
    store.engine.dispose()


async def test_battery_revision_archives_authority_fences_old_work_and_preserves_files(setup):
    store, actor, design, created, edits, artifacts = setup
    ident = created['projectId']
    approved = design.confirm(actor, ident, created['revision'], 'confirm')
    data = b'unchanged project source'
    sha = artifacts.blobs.put(data)
    with store.transaction() as session:
        project = session.get(Project, ident)
        old_approval = copy.deepcopy(project.data['approvedDesign'])
        interfaces = {'circuit': {'fixture': 'previous explicitly approved circuit'}}
        project.data = {**project.data, 'sourceManifest': {'source.txt': 'source'},
            'interfaces': interfaces, 'acceptedInterfaces': {'fixture': 'approval record'},
            'acceptedCircuitIdentity': {'fixture': 'old circuit binding'}, 'acceptedReleaseId': 'release'}
        session.add(Artifact(id='source', project_id=ident, revision=project.context_revision, name='source.txt',
            sha256=sha, size=len(data), media_type='text/plain', status='candidate', lane='cad', details={'origin': 'user_upload'}))
        session.add(Release(id='release', project_id=ident, candidate_id='historical-candidate',
            revision=project.context_revision, data={'immutable': 'historical release fixture'}))
    old_scope = run_scope(design, actor, approved)
    body = battery_body(ident, approved['revision'])
    receipt = edits.command(actor, body, 'battery')
    assert edits.command(actor, body, 'battery') == receipt
    with pytest.raises(DomainError):
        await GraphRepository(store).assert_active(old_scope)
    with store.transaction() as session:
        project = session.get(Project, ident)
        assert project.context_revision == receipt['revision'] == approved['revision'] + 1
        assert project.data['engineeringRevision'] == receipt['revision']
        assert project.data['sourceManifest'] == {'source.txt': 'source'}
        assert session.get(Artifact, 'source').sha256 == sha
        assert session.get(Release, 'release').data == {'immutable': 'historical release fixture'}
        assert project.data['acceptedReleaseId'] is None and project.data['supersededReleaseIds'] == ['release']
        assert project.data['approvedDesign'] is None and project.data['approvedDesignHistory'] == [old_approval]
        assert project.data['interfaces'] == {} and project.data['acceptedCircuitIdentity'] is None
        history = project.data['workbenchEditHistory']
        assert len(history) == 1 and history[0]['interfaces'] == interfaces
        assert history[0]['acceptedReleaseId'] == 'release'
        assert not project.data['state']['confirmed'] and not project.data['state']['interfacesAgreed']
        assert project.data['state']['concept']['stale'] is True
        battery = project.data['requirements']['userEdits']['battery']
        assert battery['compatibilityStatus'] == 'UNKNOWN'
        assert session.get(Objective, approved['runId']).status == 'cancelled'
        assert session.get(Objective, receipt['runId']).data['kind'] == 'project.capture'
        assert len(list(session.scalars(select(Objective).where(Objective.status == 'pending')))) == 1
    assert artifacts.content(actor, 'source', project_id=ident).data == data


async def test_explicit_battery_reaches_capture_and_survives_user_confirmation(setup):
    store, actor, design, created, edits, _ = setup
    ident = created['projectId']
    first = design.confirm(actor, ident, created['revision'], 'confirm-original')
    receipt = edits.command(actor, battery_body(ident, first['revision']), 'battery')
    with store.transaction() as session:
        snapshot = user_requirement_snapshot(session.get(Project, ident))
    required = snapshot['requiredRequirements']
    with pytest.raises(DomainError, match='explicit user edits'):
        await generate(design, actor, receipt, result())
    # A failed generation receipt remains uncertain; submit a distinct explicit recapture.
    revised = design.capture(actor, ident, {'text': 'Use the declared battery dimensions'}, receipt['revision'], 'retry-capture')
    _, model, _ = await generate(design, actor, revised, result(requirements=['Separate lid and body', *required]))
    packet = json.loads(model.packets[0])
    assert packet['userEdits'] == snapshot
    approved = design.confirm(actor, ident, revised['revision'], 'confirm-revised')
    with store.transaction() as session:
        project = session.get(Project, ident)
        assert project.data['requirements']['userEdits'] == snapshot['values']
        assert project.data['approvedDesign']['version'] == 2
        assert project.data['approvedDesignHistory'][0]['version'] == 1
        assert project.data['requirements']['userEdits']['battery']['compatibilityStatus'] == 'UNKNOWN'
        assert project.context_revision == approved['revision']
        assert project.data['acceptedReleaseId'] is None


async def test_feature_toggle_uses_exact_candidate_and_restores_only_prior_explicit_exclusion(setup):
    store, actor, design, created, edits, _ = setup
    body = {'type': 'project.toggleFeature', 'expectedRevision': created['revision'],
            'payload': {'projectId': created['projectId'], 'name': 'Separate lid and body'}}
    receipt = edits.command(actor, body, 'drop')
    assert edits.command(actor, body, 'drop') == receipt
    with pytest.raises(DomainError, match='explicit user edits'):
        await generate(design, actor, receipt, result())
    recapture = design.capture(actor, created['projectId'], {'text': 'Respect exclusion'}, receipt['revision'], 'recapture')
    await generate(design, actor, recapture, result(requirements=[]))
    body['expectedRevision'] = recapture['revision']
    restored = edits.command(actor, body, 'restore')
    with store.transaction() as session:
        project = session.get(Project, created['projectId'])
        assert project.data['requirements']['userEdits']['features'] == {'Separate lid and body': True}
        assert len(project.data['workbenchEditHistory']) == 2
    await generate(design, actor, restored, result())


@pytest.mark.parametrize('change', ['unknown_feature', 'stale_revision', 'foreign', 'viewer', 'worker'])
def test_requirement_edits_fail_closed_without_queue_or_revision_change(setup, change):
    store, actor, _, created, edits, _ = setup
    body = battery_body(created['projectId'], created['revision'])
    token = None
    if change == 'unknown_feature':
        body.update(type='project.toggleFeature', payload={'projectId': created['projectId'], 'name': 'Invented feature'})
    elif change == 'stale_revision':
        body['expectedRevision'] -= 1
    elif change == 'foreign':
        actor = Actor(tenant_id='foreign', user_id=actor.user_id)
    elif change == 'viewer':
        with store.transaction() as session:
            session.get(Membership, (created['projectId'], actor.user_id)).role = 'viewer'
    else:
        token = execution_owner.set((created['runId'], 'worker'))
    try:
        with pytest.raises(DomainError):
            edits.command(actor, body, 'invalid')
    finally:
        if token:
            execution_owner.reset(token)
    with store.transaction() as session:
        project = session.get(Project, created['projectId'])
        assert project.revision == created['revision']
        assert not project.data.get('workbenchEditHistory')
        assert len(list(session.scalars(select(Objective)))) == 1


@pytest.mark.parametrize('value', [True, 0, -1, float('nan'), float('inf'), 1_000_001, 10**1000])
def test_battery_rejects_invalid_or_unbounded_numbers(setup, value):
    _, actor, _, created, edits, _ = setup
    body = battery_body(created['projectId'], created['revision'])
    body['payload']['capacityMah'] = value
    with pytest.raises(DomainError) as error:
        edits.command(actor, body, 'invalid')
    assert error.value.status == 422


async def test_actual_http_battery_command_and_worker_use_explicit_edit_without_autoaccept(tmp_path):
    captures = []
    battery = {'capacityMah': 1500, 'dimensionsMm': {'x': 30, 'y': 50, 'z': 5}}
    required = battery_requirement(battery)
    def respond(request):
        body = json.loads(request.content)
        name = body.get('tools', [{}])[0].get('function', {}).get('name')
        if name == 'CaptureResult':
            captures.append(json.loads(body['messages'][1]['content']))
            message = {'role': 'assistant', 'tool_calls': [{'id': 'capture', 'type': 'function', 'function': {
                'name': name, 'arguments': json.dumps(result(requirements=['Separate lid and body', required]))}}]}
            reason = 'tool_calls'
        else:
            message, reason = {'role': 'assistant', 'content': 'No reusable memory facts.'}, 'stop'
        return httpx.Response(200, json={'choices': [{'finish_reason': reason, 'message': message}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream:
        runtime = configured_runtime(tmp_path, upstream)
        async with runtime.start(), httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app),
                                                      base_url='http://localhost') as client:
            created = (await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
                json={'type': 'project.create', 'payload': {'brief': 'A battery-powered enclosure'}})).json()
            body = battery_body(created['projectId'], created['revision'])
            response = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'battery'}, json=body)
            assert response.status_code == 202, response.text
            assert await runtime.worker.process(response.json()['runId'])
            assert captures[0]['userEdits']['requiredRequirements'] == [required]
            view = (await client.get(f"/api/v1/projects/{created['projectId']}/workspace")).json()['project']
            assert view['requirements']['userEdits']['battery']['compatibilityStatus'] == 'UNKNOWN'
            assert view['acceptedReleaseId'] is None and view['state']['confirmed'] is False
            assert required in view['state']['proposal']['requirements']['items']
            with runtime.projects.transaction() as session:
                assert session.get(Objective, response.json()['runId']).status == 'succeeded'
