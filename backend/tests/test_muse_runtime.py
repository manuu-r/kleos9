"""Actual capture graph and spend ledger with documented Muse profiles; no live IO."""
import base64
import io
import json
import os

import httpx
import pytest
from PIL import Image
from test_design import result, run_scope
from test_runtime import migrate, runtime_actor, settings, test_persistence

from kleos.contracts.models import DomainError
from kleos.infrastructure.runtime import Runtime
from kleos.orchestration.models import ModelBudget, ModelLedger
from kleos.projects.store import Objective

META_MODEL = 'muse-spark-1.3'
MODAL_MODEL = 'meta-models/Muse-Spark-Open-816B-A42B'


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    # Never inherit workstation credentials, profiles, endpoints or deployment policy.
    for name in tuple(os.environ):
        if name.startswith(('KLEOS_', 'KLEOS_')):
            monkeypatch.delenv(name)
    monkeypatch.setenv('KLEOS_META_API_KEY', 'mock-meta-credential')
    monkeypatch.setenv('KLEOS_MODAL_LLM_KEY', 'mock-modal-credential')
    monkeypatch.setenv('KLEOS_MODAL_LLM_URL', 'https://fixture.modal.run/v1/')


def capture_runtime(tmp_path, client, *, provider='meta', fallback=None):
    configured = settings(tmp_path, _env_file=None, model_provider=provider,
        modal_rate_limit_fallback_key=fallback)
    assert configured.environment == 'test'
    runtime = Runtime(configured, persistence_factory=test_persistence,
        worker_locks=object(), model_client=client)
    migrate(runtime)
    actor = runtime_actor(runtime)
    project_id = runtime.projects.create(actor, 'Separate enclosure and lid from sketch', 'create')['projectId']
    raw = io.BytesIO()
    Image.new('RGB', (4, 4), 'blue').save(raw, format='PNG')
    image = runtime.artifacts.reference_image(actor, project_id,
        {'name': 'sketch.png', 'contentBase64': base64.b64encode(raw.getvalue()).decode()})
    current = runtime.projects.context(actor, project_id)
    receipt = runtime.design.capture(actor, project_id, {'referenceImageIds': [image['id']]},
        current.revision, 'capture')
    return runtime, run_scope(runtime.design, actor, receipt)


def capture_response():
    return httpx.Response(200, json={'id': 'mock-muse-request', 'choices': [{
        'finish_reason': 'tool_calls', 'message': {'role': 'assistant',
            'reasoning_content': 'private reasoning must not persist',
            'tool_calls': [{'id': 'capture', 'type': 'function', 'function': {
                'name': 'CaptureResult', 'arguments': json.dumps(result())}}]}}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})


def assert_capture_wire(request, provider):
    body = json.loads(request.content)
    assert str(request.url) == ('https://api.meta.ai/v1/chat/completions' if provider == 'meta'
                               else 'https://fixture.modal.run/v1/chat/completions')
    assert request.headers['authorization'] == f'Bearer mock-{provider}-credential'
    assert body['model'] == (META_MODEL if provider == 'meta' else MODAL_MODEL)
    assert body['tools'][0]['function']['name'] == 'CaptureResult'
    assert body['tools'][0]['function']['parameters']['properties']['scene']
    assert body['max_tokens'] == 8192 and body['stream'] is False
    images = [part for message in body['messages'] if isinstance(message['content'], list)
              for part in message['content'] if part['type'] == 'image_url']
    assert len(images) == 1
    assert images[0]['image_url']['url'].startswith('data:image/png;base64,')
    assert base64.b64decode(images[0]['image_url']['url'].split(',', 1)[1]).startswith(b'\x89PNG')
    return body


def ledger(runtime, scope):
    records = ModelLedger(runtime.projects).records(scope.actor, scope.project_id, scope.run_id)
    with runtime.projects.transaction() as session:
        budget = session.get(ModelBudget, scope.run_id)
        run = session.get(Objective, scope.run_id)
        stored = json.dumps({'records': records, 'run': run.data})
        assert 'mock-meta-credential' not in stored and 'mock-modal-credential' not in stored
        assert 'private reasoning must not persist' not in stored
        assert 'data:image/' not in stored
        return records, budget.calls, budget.committed_cost


@pytest.mark.parametrize('provider', ['meta', 'modal'])
async def test_legacy_credentials_default_muse_capture_images_and_typed_output(tmp_path, provider):
    calls = []

    def respond(request):
        calls.append(assert_capture_wire(request, provider))
        return capture_response()

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime, scope = capture_runtime(tmp_path, client, provider=provider)
        async with runtime.start():
            completed = await runtime.engine_factory(scope).start(scope, 'Design from sketch')
            assert completed['done']
            assert len(calls) == 1
            observation = completed['results']['capture']['observations'][0]
            assert observation['status'] == 'UNKNOWN' and observation['independent'] is False
            assert observation['details']['engineeringValidated'] is False
            assert len(observation['details']['candidate']['scene']['parts']) == 2
            records, count, cost = ledger(runtime, scope)
            assert count == 1 and cost == 210
            assert records[0]['status'] == 'completed' and records[0]['fallbackOf'] is None
            assert records[0]['usage'] == {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120}
            assert records[0]['model']['model'] == calls[0]['model']


@pytest.mark.parametrize('fallback_status', [200, 429])
async def test_explicit_modal_429_fallback_is_single_and_preserves_usage_history(tmp_path, fallback_status):
    calls = []

    def respond(request):
        provider = 'modal' if not calls else 'meta'
        calls.append(assert_capture_wire(request, provider))
        if provider == 'modal' or fallback_status == 429:
            return httpx.Response(429, json={'error': {'message': 'mock rate limit'}})
        return capture_response()

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime, scope = capture_runtime(tmp_path, client, provider='modal', fallback='meta-muse-spark')
        async with runtime.start():
            engine = runtime.engine_factory(scope)
            if fallback_status == 200:
                assert (await engine.start(scope, 'Design from sketch'))['done']
            else:
                with pytest.raises(DomainError) as failure:
                    await engine.start(scope, 'Design from sketch')
                assert failure.value.code == 'model_rate_limited'
            assert len(calls) == 2
            assert calls[0]['messages'] == calls[1]['messages']
            assert calls[0]['tools'] == calls[1]['tools']
            records, count, cost = ledger(runtime, scope)
            primary = next(item for item in records if item['fallbackOf'] is None)
            fallback = next(item for item in records if item['fallbackOf'] is not None)
            assert fallback['fallbackOf'] == primary['id']
            assert primary['status'] == 'rate_limited' and primary['committedMicroUsd'] == 0
            assert count == 2 and cost == (210 if fallback_status == 200 else 0)
            assert fallback['status'] == ('completed' if fallback_status == 200 else 'rate_limited')


@pytest.mark.parametrize(('provider', 'status', 'fallback'), [
    ('modal', 429, None), ('modal', 503, 'meta-muse-spark'),
    ('modal', 401, 'meta-muse-spark'), ('meta', 429, 'modal-muse-spark-open'),
])
async def test_capture_never_switches_without_matching_explicit_modal_rate_limit_policy(
        tmp_path, provider, status, fallback):
    calls = []

    def respond(request):
        calls.append(assert_capture_wire(request, provider))
        return httpx.Response(status, json={'error': {'message': 'mock provider rejection'}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime, scope = capture_runtime(tmp_path, client, provider=provider, fallback=fallback)
        async with runtime.start():
            with pytest.raises(DomainError) as failure:
                await runtime.engine_factory(scope).start(scope, 'Design from sketch')
            assert failure.value.code == ('model_rate_limited' if status == 429 else 'model_provider_error')
            assert len(calls) == 1
            records, count, cost = ledger(runtime, scope)
            assert count == 1 and records[0]['fallbackOf'] is None
            if status == 429:
                assert cost == 0 and records[0]['status'] == 'rate_limited'
            else:
                assert cost == records[0]['reservedMicroUsd'] > 0
                assert records[0]['status'] == 'provider_error'
