"""Composed existing-project journeys; model HTTP and native execution are fixtures.

The real native gateway/publication protocol runs, but FakeDocker's fixed writer
never executes supplied source code. Its bytes are candidates, not CAD evidence.
"""
import hashlib
import json

import httpx
import pytest
from sqlalchemy import select
from test_execution import FakeDocker
from test_interface_runtime import configured_runtime

from kleos.artifacts.service import Artifact
from kleos.capabilities.native import register_native
from kleos.contracts.models import Capability
from kleos.execution.docker import NativeSettings
from kleos.execution.gateway import NativeGateway, NativeJob
from kleos.projects.graph_repository import Effect
from kleos.projects.store import Objective, Project


@pytest.mark.parametrize('repair', [False, True], ids=['missing-part', 'component-repair'])
@pytest.mark.parametrize('expand_scope', [False, True], ids=['bounded-plan', 'reject-expanded-plan'])
async def test_existing_project_single_part_journey(tmp_path, repair, expand_scope):
    calls, inputs, baseline = [], {}, {}
    replacement = 'Fresh bracket candidate; inert fixture bytes, never executed.'
    objective = ('Redo only the mounting bracket from scratch, preserving the enclosure and board.' if repair else
                 'Create only the missing mounting bracket using the existing enclosure and board context.')

    def provider(request):
        body = json.loads(request.content)
        name = body.get('tools', [{}])[0].get('function', {}).get('name')
        calls.append((name, body))
        number = sum(kind == name for kind, _ in calls)
        if name == 'PlanDecision':
            packet = json.dumps(body['messages'])
            assert objective in packet
            assert all(row['id'] in packet and row['sha256'] in packet for row in inputs.values())
            payload = {'done': True, 'summary': 'The requested bracket candidate was produced; independent checking remains required.'} if number > 1 else {
                'tasks': [{'id': 'bracket-only', 'capability': 'cad', 'objective': objective,
                    'inputArtifactIds': [row['id'] for row in inputs.values()],
                    'allowedPaths': ['model.py', 'enclosure.py'] if expand_scope else ['model.py'],
                    'maxActions': 8}], 'summary': 'Only the bracket needs mechanical work.'}
        elif name == 'Action':
            if number > 1:
                assert 'Existing enclosure; mounting datum x=20 mm.' in json.dumps(body['messages'])
            actions = [
                {'action': 'tool', 'tool': 'cad.read', 'arguments': {'path': 'enclosure.py'}},
                {'action': 'tool', 'tool': 'cad.patch', 'arguments': {'path': 'model.py',
                    'content': replacement, 'expectedSha256': inputs.get('model.py', {}).get('sha256')}},
                {'action': 'tool', 'tool': 'cad.build', 'arguments': {}},
                {'action': 'tool', 'tool': 'cad.finish', 'arguments': {}},
                {'action': 'finish', 'summary': 'Bracket candidate only; unchanged context preserved. No independent geometry or physical validation.'},
            ]
            payload = actions[number - 1]
        else:
            return httpx.Response(200, json={'id': 'memory', 'choices': [{'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': 'No reusable memory facts.'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})
        return httpx.Response(200, json={'id': 'fixture', 'choices': [{'finish_reason': 'tool_calls',
            'message': {'role': 'assistant', 'tool_calls': [{'id': 'typed', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(payload)}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 25}})

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    runtime = configured_runtime(tmp_path, upstream)
    docker = FakeDocker()
    gateway = NativeGateway(runtime.projects, runtime.artifacts,
        NativeSettings(tmp_path / 'fixture-native', {'cad': 'sha256:' + 'a' * 64}, poll_seconds=.01),
        docker, runtime.repository)
    register_native(runtime.registry, gateway, capability=Capability.CAD, provider='fixture-native',
        provider_version='1.0.0', installation_hash='b' * 64, qualified=True,
        qualification_scope='Test-only fixed writer; no source execution or geometric validity claim.')
    async with upstream, runtime.start(), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
        response = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
            json={'type': 'project.create', 'payload': {'brief': objective}})
        assert response.status_code == 202, response.text
        project_id = response.json()['projectId']
        actor = runtime.access.authenticate({}, {}, 'POST')
        sources = {'enclosure.py': 'Existing enclosure; mounting datum x=20 mm.',
                   'pcb/board.kicad_pcb': 'Existing board; preserve its connector placement.'}
        if repair:
            sources['model.py'] = 'Old bracket source requiring replacement.'
        for path, content in sources.items():
            context = runtime.projects.context(actor, project_id)
            uploaded = runtime.artifacts.upload(actor, project_id, {'expectedEngineeringRevision': context.revision,
                'sha256': hashlib.sha256(content.encode()).hexdigest(),
                'file': {'path': path, 'content': content}}, 'upload-' + path)
            inputs[path] = uploaded['artifact']
            baseline[path] = uploaded['artifact']['id']
        workspace = (await client.get(f'/api/v1/projects/{project_id}/workspace')).json()
        envelope = {'type': 'project.repair' if repair else 'project.generate',
            'expectedRevision': workspace['revision'], 'payload': {'projectId': project_id,
                'objective': objective, 'allowedPaths': ['model.py']}}
        admitted = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'bracket'}, json=envelope)
        assert admitted.status_code == 202, admitted.text
        run_id = admitted.json()['runId']
        assert await runtime.worker.process(run_id)
        with runtime.projects.transaction() as session:
            run = session.get(Objective, run_id)
            project = session.get(Project, project_id)
            assert project.data['sourceManifest'] == baseline
            assert project.data.get('approvedDesign') is None and not project.data.get('acceptedReleaseId')
            assert not project.data.get('interfaces')
            all_runs = list(session.scalars(select(Objective).where(Objective.project_id == project_id)))
            assert len(all_runs) == 1 and run.data['kind'] == envelope['type']
            if repair:
                assert run.data['repairBaseline'] == baseline and run.data['repairAllowedPaths'] == ['model.py']
            else:
                assert run.data['editAllowedPaths'] == ['model.py']
            jobs = list(session.scalars(select(NativeJob).where(NativeJob.run_id == run_id)))
            if expand_scope:
                assert run.status in {'blocked', 'failed'}, run.data
                assert 'scope' in json.dumps(run.data.get('blocker', '')).lower(), run.data
                assert not jobs and docker.dispatches == 0
                assert not any(name == 'Action' for name, _ in calls)
            else:
                assert run.status == 'succeeded', run.data.get('blocker')
                assert len(jobs) == 1 and jobs[0].status == 'finished'
                assert jobs[0].request['allowedPaths'] == ['model.py']
                assert {row['path']: row['sha256'] for row in jobs[0].request['sources']} == {
                    path: row['sha256'] for path, row in inputs.items()}
                assert {task['capability'] for task in run.data['tasks']} == {'cad'}
                returned = run.data['results']['bracket-only']['artifacts']
                artifacts = [session.get(Artifact, value['id']) for value in returned]
                updated = next(row for row in artifacts if row.name == 'model.py')
                assert updated.sha256 == hashlib.sha256(replacement.encode()).hexdigest()
                assert all(row.status == 'candidate' for row in artifacts)
                assert not any(row.name in {'enclosure.py', 'pcb/board.kicad_pcb'} for row in artifacts)
                expected_closure = {**baseline, 'model.py': updated.id}
                assert all(row.details['sourceClosure'] == expected_closure for row in artifacts)
                effects = list(session.scalars(select(Effect).where(Effect.run_id == run_id)))
                assert effects and all(row.status == 'completed' for row in effects)
        for path, content in sources.items():
            assert runtime.artifacts.content(actor, inputs[path]['id'], project_id).data == content.encode()
        assert not {'CaptureResult', 'InterfaceProposal'} & {name for name, _ in calls}
        before = len(calls), docker.dispatches
        replay = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'bracket'}, json=envelope)
        assert replay.json() == admitted.json()
        assert not await runtime.worker.process(run_id)
        assert before == (len(calls), docker.dispatches)
