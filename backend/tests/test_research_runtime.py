"""Unchanged HTTP command through actual planner/specialist/repository composition, mocked model only."""
import hashlib
import json

import httpx
import pytest
from alembic import command
from function_calls import function_call, provider_name
from pydantic import SecretStr
from test_interface_runtime import Locks, configured_runtime, model, persistence
from test_research_installation import settings as qualified_settings
from test_web_research import response as search_response

from kleos.infrastructure.runtime import Runtime, migration_config
from kleos.infrastructure.settings import Settings
from kleos.projects.store import Objective, Project


@pytest.mark.parametrize('fabricate', [False, True])
async def test_runtime_source_research_requires_actual_retrieval(tmp_path, fabricate):
    calls, source_ids = [], []
    def provider(request):
        body = json.loads(request.content)
        name = provider_name(body)
        calls.append(name)
        if name == 'PlanDecision':
            payload = {'done': True, 'summary': 'Research finished'} if calls.count(name) > 1 else {
                'tasks': [{'id': 'source-review', 'capability': 'research', 'objective': 'Inspect the load requirement',
                    'inputArtifactIds': source_ids, 'maxActions': 3}], 'summary': 'Inspect existing evidence'}
        elif name == 'Action':
            payload = {'action': 'finish', 'summary': 'Existing source declares load 15 N; independent testing is still needed.'}
            if not fabricate and calls.count(name) == 1:
                payload = {'action': 'tool', 'tool': 'research.project_sources',
                    'arguments': {'artifactIds': source_ids, 'query': '15 N'}}
        else:
            return httpx.Response(200, json={'id': 'memory', 'choices': [{'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': 'No reusable memory facts.'}}],
                'usage': {'prompt_tokens': 20, 'completion_tokens': 5}})
        name, payload = function_call(name, payload)
        return httpx.Response(200, json={'id': 'fixture', 'choices': [{'finish_reason': 'tool_calls',
            'message': {'role': 'assistant', 'tool_calls': [{'id': 'typed', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(payload)}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 25}})

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    runtime = configured_runtime(tmp_path, upstream)
    async with upstream, runtime.start(), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
        created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
            json={'type': 'project.create', 'payload': {'brief': 'Research the existing bracket load requirement'}})
        assert created.status_code == 202
        ident = created.json()['projectId']
        actor = runtime.access.authenticate({}, {}, 'GET')
        text = 'The existing bracket is required to support 15 N.'
        uploaded = runtime.artifacts.upload(actor, ident, {'expectedEngineeringRevision': 1,
            'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'file': {'path': 'bracket.txt', 'content': text, 'mediaType': 'text/plain'}}, 'source')
        source_ids.append(uploaded['artifact']['id'])
        workspace = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        blocked = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'web'}, json={
            'type': 'project.research', 'expectedRevision': workspace['revision'], 'payload': {'projectId': ident}})
        assert blocked.status_code == 503 and not calls
        requested = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'research'}, json={
            'type': 'project.research', 'expectedRevision': workspace['revision'],
            'payload': {'projectId': ident, 'scope': 'project_sources'}})
        assert requested.status_code == 202, requested.text
        assert await runtime.worker.process(requested.json()['runId'])
        with runtime.projects.transaction() as session:
            run = session.get(Objective, requested.json()['runId'])
            project = session.get(Project, ident)
            assert project.data['sourceManifest'] == {'bracket.txt': source_ids[0]}
            assert not project.data.get('acceptedReleaseId') and not project.data.get('approvedDesign')
            if fabricate:
                assert run.status == 'blocked'
                assert 'research' not in project.data['state']
            else:
                assert run.status == 'succeeded', run.data.get('blocker')
                evidence = project.data['state']['research']
                assert evidence['scope'] == 'project_sources' and evidence['feasibilityValidated'] is False
                assert text in evidence['observations'][0]['details']['entries'][0]['excerpt']
                assert 'bracket.txt' in project.data['state']['conversation'][-1]['text']
                assert all(pin['manifest']['capability'] == 'research' for pin in run.data['runtimePins']['tools'])


async def test_runtime_web_research_real_http_adapter_with_reviewed_fixture_only(tmp_path):
    model_calls, searches = [], []
    def provider(request):
        body = json.loads(request.content)
        name = provider_name(body)
        model_calls.append(name)
        if name == 'PlanDecision':
            payload = {'done': True, 'summary': 'Retrieved sources; physical feasibility remains unverified'} if model_calls.count(name) > 1 else {
                'tasks': [{'id': 'web-review', 'capability': 'research', 'objective': 'Search material manufacturer data',
                    'maxActions': 3}], 'summary': 'Search one relevant public question'}
        elif name == 'Action':
            payload = {'action': 'finish', 'summary': 'Manufacturer material source found; no physical approval.'}
            if model_calls.count(name) == 1:
                payload = {'action': 'tool', 'tool': 'research.web_search',
                    'arguments': {'query': 'manufacturer material data'}}
        else:
            return httpx.Response(200, json={'id': 'memory', 'choices': [{'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': 'No reusable memory facts.'}}],
                'usage': {'prompt_tokens': 20, 'completion_tokens': 5}})
        name, payload = function_call(name, payload)
        return httpx.Response(200, json={'id': 'fixture', 'choices': [{'finish_reason': 'tool_calls',
            'message': {'role': 'assistant', 'tool_calls': [{'id': 'typed', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(payload)}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 25}})

    def search(request):
        searches.append(request)
        assert request.method == 'GET' and request.url.host == 'search.example.org'
        assert request.url.params['q'] == 'manufacturer material data'
        return httpx.Response(200, json=search_response())

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    search_client = httpx.AsyncClient(transport=httpx.MockTransport(search))
    settings = Settings(environment='test', database_url=f'sqlite:///{tmp_path / "web-runtime.db"}',
        artifact_root=tmp_path / 'artifacts', native_control_root=tmp_path / 'native',
        slicer_control_root=tmp_path / 'slicer', checker_control_root=tmp_path / 'checker',
        session_secret=SecretStr('s'*40), development_no_login=True, model_catalog=[model('text')],
        text_model_key='text', meta_api_key=SecretStr('fixture-only'),
        web_search_installation=qualified_settings(tmp_path).web_search_installation)
    runtime = Runtime(settings, persistence_factory=persistence, worker_locks=Locks(),
        model_client=upstream, research_client=search_client)
    with runtime.projects.engine.begin() as connection:
        config = migration_config()
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
    async with upstream, search_client, runtime.start(), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
        created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
            json={'type': 'project.create', 'payload': {'brief': 'Research replacement bracket material data'}})
        ident = created.json()['projectId']
        requested = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'research'}, json={
            'type': 'project.research', 'expectedRevision': 1, 'payload': {'projectId': ident}})
        assert requested.status_code == 202, requested.text
        run_id = requested.json()['runId']
        assert await runtime.worker.process(run_id)
        assert not await runtime.worker.process(run_id)
        with runtime.projects.transaction() as session:
            run = session.get(Objective, run_id)
            assert run.status == 'succeeded', run.data.get('blocker')
            assert run.data['researchUsage']['requests'] == 1 and len(searches) == 1
            assert any(pin['manifest']['operation'] == 'web_search' for pin in run.data['runtimePins']['tools'])
            project = session.get(Project, ident)
            assert project.context_revision == 1 and project.revision == 2
            evidence = project.data['state']['research']
            assert evidence['scope'] == 'web' and evidence['feasibilityValidated'] is False
            assert 'https://manufacturer.example/material' in project.data['state']['conversation'][-1]['text']
            assert not project.data.get('acceptedReleaseId')
