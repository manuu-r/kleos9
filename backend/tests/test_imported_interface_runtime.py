"""Imported identity proposal through actual HTTP/gateway/worker with mock provider IO."""
import hashlib
import json

import httpx
from test_imported_interfaces import CIRCUIT, SOURCES
from test_interface_runtime import configured_runtime

from kleos.projects.interfaces import accepted_circuit_identity
from kleos.projects.store import Objective, Project


async def test_imported_board_proposal_actual_model_wire_then_explicit_approval(tmp_path):
    board_id = None
    calls = []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        name = body['tools'][0]['function']['name']
        if name == 'InterfaceProposal':
            payload = {'summary': 'Route this existing board without inventing a visual concept.',
                'interfaces': {'circuit': CIRCUIT}, 'circuitIdentity': {
                    'mode': 'imported_no_concept', 'sourceArtifactIds': [board_id]},
                'sourceIds': [board_id], 'limitations': ['Routing and engineering checks have not run.']}
            message = {'role': 'assistant', 'tool_calls': [{'id': 'proposal', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(payload)}}]}
            reason = 'tool_calls'
        else:
            message, reason = {'role': 'assistant', 'content': 'No new reusable facts.'}, 'stop'
        return httpx.Response(200, json={'id': 'fixture', 'choices': [{'finish_reason': reason, 'message': message}],
            'usage': {'prompt_tokens': 500, 'completion_tokens': 150}})
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    runtime = configured_runtime(tmp_path, upstream)
    async with upstream, runtime.start(), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
        created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'}, json={
            'type': 'project.create', 'payload': {'brief': 'Route an existing PCB; no new visual concept.', 'autoStart': False}})
        assert created.status_code == 202
        ident = created.json()['projectId']
        actor = runtime.access.authenticate({}, {}, 'GET')
        for index, (path, content) in enumerate(sorted(SOURCES.items())):
            revision = runtime.projects.context(actor, ident).revision
            uploaded = runtime.artifacts.upload(actor, ident, {'expectedEngineeringRevision': revision,
                'sha256': hashlib.sha256(content.encode()).hexdigest(),
                'file': {'path': path, 'content': content, 'mediaType': 'text/plain'}}, f'upload-{index}')
            if path == 'board.kicad_pcb':
                board_id = uploaded['artifact']['id']
        before = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        solved = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'solve'}, json={
            'type': 'project.solveInterfaces', 'expectedRevision': before['revision'],
            'payload': {'projectId': ident, 'objective': 'Route only the imported PCB while preserving exact board sources.'}})
        assert solved.status_code == 202, solved.text
        run_id = solved.json()['runId']
        assert await runtime.worker.process(run_id)
        with runtime.projects.transaction() as session:
            assert session.get(Objective, run_id).status == 'succeeded'
            assert 'acceptedCircuitIdentity' not in session.get(Project, ident).data
        proposal_calls = [body for body in calls if body['tools'][0]['function']['name'] == 'InterfaceProposal']
        assert len(proposal_calls) == 1
        assert not any(body['tools'][0]['function']['name'] == 'CaptureResult' for body in calls)
        packet = json.loads(proposal_calls[0]['messages'][1]['content'])
        board = next(source for source in packet['request']['importedSources'] if source['id'] == board_id)
        assert board['text'] == SOURCES['board.kicad_pcb'] and packet['approvedDesign'] is None
        pending = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        assert pending['project']['interfaces'] == {}
        assert board['sha256'] in pending['project']['state']['conversation'][-1]['text']
        approved = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'approve'}, json={
            'type': 'project.message', 'expectedRevision': pending['revision'], 'payload': {'projectId': ident,
                'text': 'approve interfaces', 'autoResume': True, 'referenceImageIds': []}})
        assert approved.status_code == 202, approved.text
        with runtime.projects.transaction() as session:
            project = runtime.projects.authorized(session, actor, ident)
            accepted = accepted_circuit_identity(session, project)
            assert accepted['mode'] == 'imported_no_concept' and len(accepted['sourceArtifacts']) == len(SOURCES)
            assert project.data.get('approvedDesign') is None and not project.data.get('acceptedReleaseId')
        generation = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'generate'}, json={
            'type': 'project.generate', 'expectedRevision': approved.json()['revision'], 'payload': {
                'projectId': ident, 'objective': 'Route this accepted imported PCB only. Preserve placement and source closure.'}})
        assert generation.status_code == 202, generation.text
        with runtime.projects.transaction() as session:
            project = runtime.projects.authorized(session, actor, ident)
            assert accepted_circuit_identity(session, project) == accepted
