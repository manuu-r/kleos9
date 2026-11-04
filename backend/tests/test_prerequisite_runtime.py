"""Same-objective interface approval with real API/worker, inert model/native IO."""
import base64
import copy
import io
import json

import httpx
import pytest
from function_calls import function_call, provider_name
from PIL import Image
from sqlalchemy import select
from test_execution import FakeDocker
from test_interface_runtime import configured_runtime

from kleos.capabilities.native import register_native
from kleos.contracts.models import Capability
from kleos.execution.docker import NativeSettings
from kleos.execution.gateway import NativeGateway, NativeJob
from kleos.orchestration.models import ModelBudget
from kleos.projects.store import Objective, Project

BOARD = 'nrf52840dk/nrf52840'


@pytest.mark.parametrize("image", [False, True])
async def test_firmware_objective_approves_and_resumes_same_budget_and_thread(tmp_path, monkeypatch, image):
    calls = []
    def provider(request):
        body = json.loads(request.content)
        name = provider_name(body)
        calls.append(name)
        count = calls.count(name)
        if name == 'PlanDecision':
            payload = {'done': True} if count > 1 else {'tasks': [{'id': 'firmware', 'capability': 'firmware',
                'objective': 'Build firmware for the explicitly requested board', 'allowedPaths': ['main.c'], 'maxActions': 5}]}
        elif name == 'InterfaceProposal':
            assert body['model'] == ('vision-pinned' if image else 'text-pinned')
            assert ('data:image/png;base64,' in json.dumps(body['messages'])) is image
            payload = {'summary': 'Use the requested development board; device behavior remains unverified.',
                'interfaces': {'firmware': {'board': BOARD}}, 'sourceIds': ['brief']}
        elif name == 'Action':
            payload = [
                {'action': 'tool', 'tool': 'firmware.patch', 'arguments': {'path': 'main.c',
                    'content': 'Inert source fixture', 'expectedSha256': None}},
                {'action': 'tool', 'tool': 'firmware.build', 'arguments': {}},
                {'action': 'tool', 'tool': 'firmware.finish', 'arguments': {}},
                {'action': 'finish', 'summary': 'Candidate exports, no device validation.'},
            ][count - 1]
        else:
            return httpx.Response(200, json={'id': 'memory', 'choices': [{'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': 'No reusable facts.'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})
        name, payload = function_call(name, payload)
        return httpx.Response(200, json={'id': 'fixture', 'choices': [{'finish_reason': 'tool_calls',
            'message': {'role': 'assistant', 'tool_calls': [{'id': 'typed', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(payload)}}]}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 25}})

    def writer(command, work, timeout, limits, application):
        # Never execute the supplied command or source. Fixed bytes test protocol only.
        target = work / 'build/zephyr'
        target.mkdir(parents=True, exist_ok=True)
        for suffix in ('elf', 'bin'):
            (target / ('zephyr.' + suffix)).write_bytes(b'Inert firmware export fixture')
        return 'No device or compiler verification'
    monkeypatch.setattr('test_execution.fixture_writer', writer)
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    runtime = configured_runtime(tmp_path, upstream)
    docker = FakeDocker()
    image_digest = 'sha256:' + 'a'*64
    gateway = NativeGateway(runtime.projects, runtime.artifacts,
        NativeSettings(tmp_path/'fixture-native', {'firmware': image_digest}, firmware_boards=(BOARD,), poll_seconds=.01),
        docker, runtime.repository)
    runtime.native_gateway = gateway
    runtime.native_pins = [{'capability': 'firmware', 'firmwareBoard': BOARD, 'imageDigest': image_digest,
                           'qualificationScope': 'Inert test writer only'}]
    register_native(runtime.registry, gateway, capability=Capability.FIRMWARE, provider='fixture',
        provider_version='1', installation_hash='b'*64, qualified=True,
        qualification_scope='Inert fixture only')
    async with upstream, runtime.start(), httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app),
            base_url='http://local') as client:
        created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
            json={'type': 'project.create', 'payload': {'brief': 'Build firmware only for '+BOARD}})
        assert created.status_code == 202, created.text
        project_id = created.json()['projectId']
        if image:
            raw = io.BytesIO()
            Image.new('RGB', (4, 4), 'blue').save(raw, format='PNG')
            actor = runtime.access.authenticate({}, {}, 'POST')
            runtime.artifacts.reference_image(actor, project_id, {'name': 'reference.png',
                'contentBase64': base64.b64encode(raw.getvalue()).decode()})
        workspace = (await client.get(f'/api/v1/projects/{project_id}/workspace')).json()
        admitted = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'objective'},
            json={'type': 'project.generate', 'expectedRevision': workspace['revision'],
                  'payload': {'projectId': project_id, 'objective': 'Firmware only for '+BOARD, 'allowedPaths': ['main.c']}})
        assert admitted.status_code == 202, admitted.text
        run_id = admitted.json()['runId']
        assert await runtime.worker.process(run_id)
        with runtime.projects.transaction() as session:
            run = session.get(Objective, run_id)
            assert run.status == 'waiting', run.data.get('blocker')
            assert run.data['question']['kind'] == 'interface_prerequisite'
            original = copy.deepcopy(run.data)
            budget = session.get(ModelBudget, run_id)
            initial_calls, policy_hash = budget.calls, budget.policy_hash
            assert run.data['activeBudget']['lease'] is None
            assert session.scalar(select(NativeJob)) is None
        assert calls == ['PlanDecision', 'InterfaceProposal'] and docker.dispatches == 0
        workspace = (await client.get(f'/api/v1/projects/{project_id}/workspace')).json()
        envelope = {'type': 'project.message', 'expectedRevision': workspace['revision'],
                    'payload': {'projectId': project_id, 'text': 'approve interfaces'}}
        approved = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'approve'}, json=envelope)
        assert approved.status_code == 202, approved.text
        assert approved.json()['runId'] == run_id
        assert await runtime.worker.process(run_id)
        with runtime.projects.transaction() as session:
            run = session.get(Objective, run_id)
            project = session.get(Project, project_id)
            assert run.status == 'succeeded', run.data.get('blocker')
            assert project.data['interfaces']['firmware']['board'] == BOARD
            assert run.revision == project.context_revision > original['scope']['revision']
            assert {k:v for k,v in run.data['scope'].items() if k != 'revision'} == {
                k:v for k,v in original['scope'].items() if k != 'revision'}
            assert run.data['runtimePins'] == original['runtimePins']
            assert run.data['activeBudget']['limitMs'] == original['activeBudget']['limitMs']
            assert run.data['activeBudget']['committedMs'] >= original['activeBudget']['committedMs']
            budget = session.get(ModelBudget, run_id)
            assert budget.policy_hash == policy_hash and budget.calls > initial_calls
            assert len(list(session.scalars(select(Objective)))) == 1
            assert not project.data.get('acceptedReleaseId')
            jobs = list(session.scalars(select(NativeJob)))
            assert len(jobs) == 1 and jobs[0].request['firmwareTarget'] == {'board': BOARD}
        before = len(calls), docker.dispatches
        replay = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'approve'}, json=envelope)
        assert replay.json() == approved.json()
        assert not await runtime.worker.process(run_id)
        assert (len(calls), docker.dispatches) == before
