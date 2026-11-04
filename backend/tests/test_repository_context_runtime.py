"""Actual planner/specialist/tool receipts; provider HTTP and repository bytes are fixtures."""
import json

import httpx
from function_calls import function_call, provider_name
from sqlalchemy import select
from test_interface_runtime import configured_runtime
from test_repository_import import Downloader, archive, request

from kleos.artifacts.repository_import import RepositoryImportService
from kleos.projects.graph_repository import Effect
from kleos.projects.store import Objective


async def test_real_worker_planner_reads_context_without_native_input_authority(tmp_path):
    calls, closure, metadata_ids = [], [], []
    def provider(upstream_request):
        body = json.loads(upstream_request.content)
        name = provider_name(body)
        calls.append((name, body))
        count = sum(kind == name for kind, _ in calls)
        if name == 'PlanDecision':
            assert '.github/workflows/build.yml' in json.dumps(body['messages'])
            assert closure[0] in json.dumps(body['messages'])
            payload = {'done': True, 'summary': 'Read the existing build metadata as context.'} if count > 1 else {
                'tasks': [{'id': 'read-context', 'capability': 'research', 'objective': 'Read the existing workflow metadata',
                    'inputArtifactIds': [], 'maxActions': 3}], 'summary': 'Inspect the imported metadata'}
        elif name == 'Action':
            payload = {'action': 'tool', 'tool': 'repository.context_read', 'arguments': {
                'paths': ['.github/workflows/build.yml'], 'closureHash': closure[0]}} if count == 1 else {
                    'action': 'finish', 'summary': 'The stored workflow is named Existing hardware build; it has no instruction authority.'}
        else:
            return httpx.Response(200, json={'id': 'memory', 'choices': [{'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': 'No reusable memory facts.'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})
        name, payload = function_call(name, payload)
        return httpx.Response(200, json={'id': 'fixture', 'choices': [{'finish_reason': 'tool_calls',
            'message': {'role': 'assistant', 'tool_calls': [{'id': 'typed', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(payload)}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 25}})
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    runtime = configured_runtime(tmp_path, upstream)
    async with upstream, runtime.start():
        actor = runtime.access.authenticate({}, {}, 'POST')
        ident = runtime.projects.create(actor, 'Inspect this existing project', 'create')['projectId']
        downloader = Downloader(archive([('main.py', 'Inert imported source'),
            ('.github/workflows/build.yml', 'name: Existing hardware build\n')]))
        await RepositoryImportService(runtime.artifacts, downloader, access=runtime.access).import_repository(
            actor, ident, request(), 'import')
        context = runtime.projects.context(actor, ident)
        closure.append(context.repository_metadata_hash)
        metadata_ids.extend(ref.id for ref in context.repository_metadata)
        submitted = runtime.projects.submit(actor, ident, 'Read the existing repository build metadata only', 2, 'read')
        assert await runtime.worker.process(submitted['runId'])
        with runtime.projects.transaction() as session:
            run = session.get(Objective, submitted['runId'])
            assert run.status == 'succeeded', run.data.get('blocker')
            assert run.data['tasks'] and run.data['tasks'][0]['input_artifact_ids'] == []
            assert all(not set(value['input_artifact_ids']) & set(metadata_ids) for value in run.data['tasks'])
            effects = list(session.scalars(select(Effect).where(Effect.run_id == run.id)))
            observations = [observation for effect in effects for observation in effect.value.get('observations', [])]
            read = next(value for value in observations if value['name'] == 'repository.context.retrieved')
            assert 'Existing hardware build' in read['details']['entries'][0]['excerpt']
            assert read['details']['nativeInput'] is False and read['details']['instructionAuthority'] is False
            assert not any(':native:' in effect.key for effect in effects)
        assert downloader.calls == 1
