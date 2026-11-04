"""Real API/runtime/worker/gateway composition; HTTP providers and graph storage are fixtures."""
import base64
import hashlib
import io
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from alembic import command
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import select

from kleos.infrastructure.runtime import Runtime, migration_config
from kleos.infrastructure.settings import Settings
from kleos.orchestration.models import ModelAttempt
from kleos.projects.store import Objective, Project


@asynccontextmanager
async def persistence(url, *, setup):
    assert not setup
    yield InMemorySaver(), InMemoryStore()


class Locks:
    def execute(self, _):
        pass

    @asynccontextmanager
    async def hold(self, _):
        yield self


def model(key, vision=False):
    return {'key': key, 'model': key + '-pinned', 'provider': 'meta', 'protocol': 'openai_chat',
        'version': '1', 'baseUrl': 'https://fixture.example/v1', 'qualified': True,
        'vision': vision, 'maxInputTokens': 50000, 'inputPriceMicroUsdPerMillion': 1000000,
        'outputPriceMicroUsdPerMillion': 2000000}


def configured_runtime(tmp_path, upstream):
    settings = Settings(environment='test', database_url=f'sqlite:///{tmp_path / "runtime.db"}',
        artifact_root=tmp_path / 'artifacts', native_control_root=tmp_path / 'native',
        slicer_control_root=tmp_path / 'slicer', checker_control_root=tmp_path / 'checker',
        session_secret=SecretStr('s'*40), development_no_login=True,
        model_catalog=[model('text'), model('vision', True)], text_model_key='text', vision_model_key='vision',
        meta_api_key=SecretStr('fixture-not-a-real-key'))
    runtime = Runtime(settings, persistence_factory=persistence, worker_locks=Locks(), model_client=upstream)
    with runtime.projects.engine.begin() as connection:
        config = migration_config()
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
    return runtime


@pytest.mark.parametrize('image', [False, True])
async def test_runtime_proposes_from_imported_context_and_existing_message_approves(tmp_path, image):
    calls = []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        name = body['tools'][0]['function']['name']
        if name == 'InterfaceProposal':
            payload = {'summary': 'Keep the imported board target; no circuit redesign is requested.',
                'interfaces': {'firmware': {'board': 'nrf52840dk/nrf52840'}},
                'limitations': ['Firmware behavior and physical hardware remain unchecked.'], 'sourceIds': ['brief']}
            message = {'role': 'assistant', 'tool_calls': [{'id': 'proposal', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(payload)}}]}
            reason = 'tool_calls'
        else:
            # Real LangMem extraction observes no new reusable facts in this mocked response.
            message, reason = {'role': 'assistant', 'content': 'No additional reusable facts.'}, 'stop'
        return httpx.Response(200, json={'id': 'fixture-response', 'choices': [{'finish_reason': reason,
            'message': message}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 25}})

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    runtime = configured_runtime(tmp_path, upstream)
    async with upstream, runtime.start(), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
        created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
            json={'type': 'project.create', 'payload': {'brief': 'Use existing project board nrf52840dk/nrf52840; firmware only.'}})
        assert created.status_code == 202
        ident = created.json()['projectId']
        actor = runtime.access.authenticate({}, {}, 'GET')
        content = 'Existing firmware target: nrf52840dk/nrf52840.'
        uploaded = runtime.artifacts.upload(actor, ident, {'expectedEngineeringRevision': 1,
            'sha256': hashlib.sha256(content.encode()).hexdigest(),
            'file': {'path': 'board.txt', 'content': content, 'mediaType': 'text/plain'}}, 'source')
        if image:
            raw = io.BytesIO()
            Image.new('RGB', (5, 5), 'red').save(raw, format='PNG')
            runtime.artifacts.reference_image(actor, ident, {'name': 'board.png',
                'contentBase64': base64.b64encode(raw.getvalue()).decode()})
        workspace = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        solved = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'solve'}, json={
            'type': 'project.solveInterfaces', 'expectedRevision': workspace['revision'], 'payload': {'projectId': ident}})
        assert solved.status_code == 202, solved.text
        run_id = solved.json()['runId']
        assert await runtime.worker.process(run_id)
        with runtime.projects.transaction() as session:
            run = session.get(Objective, run_id)
            assert run.status == 'succeeded', run.data.get('blocker')
            assert run.data['memoryStatus'] == 'recorded'
            assert session.get(Project, ident).data['interfaces'] == {}
            assert len(list(session.scalars(select(ModelAttempt).where(ModelAttempt.run_id == run_id)))) == len(calls)
        proposals = [body for body in calls if body['tools'][0]['function']['name'] == 'InterfaceProposal']
        assert len(proposals) == 1 and proposals[0]['model'] == ('vision-pinned' if image else 'text-pinned')
        body = proposals[0]['messages'][1]['content']
        encoded = body[0]['text'] if image else body
        assert json.loads(encoded)['request']['importedSources'][0]['text'] == content
        if image:
            assert any(part['type'] == 'image_url' for part in body)
        review = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        assert review['project']['status'] == 'review'
        assert review['project']['state']['interfaceProposal']['status'] == 'pending'
        assert 'approve interfaces' in review['project']['state']['conversation'][-1]['text']
        assert review['project']['interfaces'] == {}
        count = len(calls)
        accepted = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'approve'}, json={
            'type': 'project.message', 'expectedRevision': review['revision'],
            'payload': {'projectId': ident, 'text': 'approve interfaces', 'autoResume': True,
                'clientMessageId': 'ui-confirmation', 'referenceImageIds': []}})
        assert accepted.status_code == 202, accepted.text
        again = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'approve'}, json={
            'type': 'project.message', 'expectedRevision': review['revision'],
            'payload': {'projectId': ident, 'text': 'approve interfaces', 'autoResume': True,
                'clientMessageId': 'ui-confirmation', 'referenceImageIds': []}})
        assert again.json() == accepted.json() and len(calls) == count
        with runtime.projects.transaction() as session:
            project = session.get(Project, ident)
            assert project.data['interfaces'] == {'firmware': {'board': 'nrf52840dk/nrf52840'}}
            assert project.data['sourceManifest'] == {'board.txt': uploaded['artifact']['id']}
            assert project.data.get('approvedDesign') is None and not project.data.get('acceptedReleaseId')
            assert project.data['acceptedInterfaces']['engineeringValidated'] is False


@pytest.mark.parametrize('peripheral', [False, True])
async def test_capture_confirm_chooses_concrete_prerequisite_and_admits_generation(tmp_path, peripheral):
    calls = []
    def provider(request):
        body = json.loads(request.content)
        name = body['tools'][0]['function']['name']
        calls.append(name)
        if name == 'CaptureResult':
            part = {'id': 'connector' if peripheral else 'body', 'label': 'Connector' if peripheral else 'Body',
                'role': 'connector' if peripheral else 'shell', 'shape': 'box',
                'size': {'x': 2, 'y': 2, 'z': 1}, 'position': {'x': 0, 'y': 0, 'z': 0},
                'explode': {'x': 0, 'y': 0, 'z': 0}, 'color': '#697080'}
            if peripheral:
                part['circuitRole'] = 'peripheral'
            payload = {'message': 'One separately identified requested part for review.', 'readyToReview': True,
                'requirements': ['Preserve the requested part'], 'scene': {'schemaVersion': 'concept-scene-v2',
                    'title': 'Requested part', 'summary': 'Illustration only', 'deviceKind': 'part', 'parts': [part]}}
        elif name == 'InterfaceProposal':
            payload = {'summary': 'Map the approved connector to J1; review before generation.', 'interfaces': {
                'circuit': {'references': [{'reference': 'J1', 'value': 'Power',
                    'footprint': 'Connector:PinHeader_1x02', 'pins': {'1': 'VCC', '2': 'GND'}}],
                    'conceptReferences': {'connector': 'J1'}, 'constraints': {'maxWidthMm': 30., 'maxHeightMm': 20.,
                        'minTrackWidthMm': .3, 'minClearanceMm': .2, 'copperLayers': ['F.Cu', 'B.Cu']}}},
                'sourceIds': ['approvedDesign'], 'limitations': ['Electrical behavior remains unchecked.']}
        else:
            return httpx.Response(200, json={'id': 'memory', 'choices': [{'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': 'No reusable facts.'}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 10}})
        return httpx.Response(200, json={'id': 'fixture', 'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'tool_calls': [{'id': 'call', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(payload)}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 100}})
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    runtime = configured_runtime(tmp_path, upstream)
    async with upstream, runtime.start(), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
        create = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'capture'}, json={
            'type': 'project.create', 'payload': {'brief': 'Design this connector' if peripheral else 'Design a simple body',
                'autoStart': True}})
        assert create.status_code == 202, create.text
        ident = create.json()['projectId']
        assert await runtime.worker.process(create.json()['runId'])
        captured = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        if peripheral:
            with runtime.projects.transaction() as session:
                project = session.get(Project, ident)
                project.data = {**project.data, 'interfaces': {'circuit': {'priorConceptContract': True}},
                    'interfaceApprovalHistory': [{'proposalId': 'old-approved-selection'}]}
        confirm = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'confirm'}, json={
            'type': 'project.confirm', 'expectedRevision': captured['revision'], 'payload': {'projectId': ident}})
        assert confirm.status_code == 202, confirm.text
        with runtime.projects.transaction() as session:
            run = session.get(Objective, confirm.json()['runId'])
            assert run.data['kind'] == ('project.solveInterfaces' if peripheral else 'objective')
            project = session.get(Project, ident)
            assert project.data['interfaces']['designIntent']['referenceOnly'] is True
            assert project.data['interfaces']['designIntent']['units'] == 'relative-illustration'
            assert not project.data['interfaces']['designIntent']['explodeAffectsManufacturing']
            assert 'circuit' not in project.data['interfaces']
            if peripheral:
                assert project.data['interfaceApprovalHistory'] == [{'proposalId': 'old-approved-selection'}]
        if peripheral:
            assert await runtime.worker.process(confirm.json()['runId'])
            proposed = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
            assert proposed['project']['state']['interfaceProposal']['status'] == 'pending'
            premature = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'premature-generate'}, json={
                'type': 'project.generate', 'expectedRevision': proposed['revision'], 'payload': {'projectId': ident}})
            assert premature.status_code == 409 and premature.json()['code'] == 'approval_required'
            approval = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'accept-interfaces'}, json={
                'type': 'project.message', 'expectedRevision': proposed['revision'], 'payload': {
                    'projectId': ident, 'text': 'approve interfaces', 'autoResume': True, 'referenceImageIds': []}})
            assert approval.status_code == 202, approval.text
            generate = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'generate'}, json={
                'type': 'project.generate', 'expectedRevision': approval.json()['revision'], 'payload': {'projectId': ident}})
            assert generate.status_code == 202, generate.text
            with runtime.projects.transaction() as session:
                assert session.get(Objective, generate.json()['runId']).data['kind'] == 'project.generate'
                assert session.get(Project, ident).data['interfaces']['circuit']['conceptReferences'] == {'connector': 'J1'}
        else:
            # Mechanical confirmation already admitted its dynamic generation objective.
            assert 'InterfaceProposal' not in calls
            assert (await client.get(f'/api/v1/projects/{ident}/workspace')).json()['project']['interfaces']
        with runtime.projects.transaction() as session:
            assert all(run.status != 'cancelled' for run in session.scalars(select(Objective)))
