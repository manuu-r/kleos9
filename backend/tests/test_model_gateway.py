import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, SecretStr
from sqlalchemy import select

from kleos.contracts.models import Actor, DomainError
from kleos.orchestration.engine import RunScope
from kleos.orchestration.models import ModelBudget, ModelConfiguration, ModelGateway, ModelLedger, ModelPolicy
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import ProjectStore


def configuration(key='text', provider='meta', **overrides):
    return ModelConfiguration(**(dict(key=key, provider=provider, protocol='openai_chat', model=f'{key}-pinned-model', version='1',
        base_url=f'https://{provider}.example/v1', qualified=True, max_input_tokens=32000,
        input_price_micro_usd_per_million=1000000, output_price_micro_usd_per_million=2000000) | overrides))


@pytest.fixture
def authority(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path / "models.db"}')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='user')
    project = store.create(actor, 'Enclosure with image reference', 'create')
    run = store.submit(actor, project['projectId'], 'Design enclosure', 1, 'run')
    scope = RunScope(actor=actor, project_id=project['projectId'], run_id=run['runId'], revision=run['revision'],
                     deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    yield store, scope
    store.engine.dispose()


def completion(**changes):
    return {'id': 'provider-receipt', 'choices': [{'finish_reason': 'stop',
        'message': {'role': 'assistant', 'content': 'Observed shape', 'reasoning_content': 'private internal text'}}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 20}, **changes}


def gateway(authority, handler, *, configurations=None, policy=None):
    store, scope = authority
    ledger = ModelLedger(store)
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    instance = ModelGateway(configurations or [configuration()], policy or ModelPolicy(text_model_key='text'),
        ledger, {'meta': SecretStr('test-secret-meta'), 'modal': SecretStr('test-secret-modal')}, client=client)
    return instance, scope, client


@pytest.mark.parametrize(('provider', 'expected_choice'), [('meta', 'auto'), ('modal', 'required')])
async def test_structured_output_tool_choice_follows_provider_support(authority, provider, expected_choice):
    requests = []

    class Shape(BaseModel):
        width: int

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion(choices=[{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'shape', 'type': 'function',
                'function': {'name': 'Shape', 'arguments': '{"width": 7}'}}]}}]))

    adapter, scope, client = gateway(authority, respond, configurations=[configuration(provider=provider)])
    async with client:
        result = await adapter.chat_model(scope).with_structured_output(Shape).bind(max_tokens=512).ainvoke('Measure')
    assert result.width == 7 and requests[0]['tool_choice'] == expected_choice
    # Only a provider that cannot force the call is told to answer through the function.
    assert (requests[0]['messages'][-1]['role'] == 'system') == (provider == 'meta')


async def test_real_http_request_structured_output_and_scoped_redacted_ledger(authority):
    requests = []

    class Shape(BaseModel):
        width: int

    def respond(request):
        requests.append(json.loads(request.content))
        assert request.url == 'https://meta.example/v1/chat/completions'
        assert request.headers['Authorization'] == 'Bearer test-secret-meta'
        return httpx.Response(200, json=completion(choices=[{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'shape', 'type': 'function',
                'function': {'name': 'Shape', 'arguments': '{"width": 42}'}}]}}]))

    adapter, scope, client = gateway(authority, respond)
    async with client:
        result = await adapter.chat_model(scope).with_structured_output(Shape).bind(max_tokens=512).ainvoke('Measure shape')
    assert result.width == 42
    assert requests[0]['tool_choice'] == 'auto' and requests[0]['max_tokens'] == 512
    assert requests[0]['tools'][0]['function']['name'] == 'Shape'
    records = adapter.ledger.records(scope.actor, scope.project_id, scope.run_id)
    assert records[0]['status'] == 'completed' and records[0]['committedMicroUsd'] == 140
    assert records[0]['usage'] == {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120}
    saved = json.dumps(records)
    assert all(value not in saved for value in ('Measure shape', 'test-secret', 'private internal'))
    with pytest.raises(DomainError, match='not found'):
        adapter.ledger.records(Actor(tenant_id='other', user_id='user'), scope.project_id, scope.run_id)


async def test_images_require_explicit_vision_model_and_reject_remote_sources(authority):
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion())
    adapter, scope, client = gateway(authority, respond,
        configurations=[configuration(), configuration('vision', vision=True)],
        policy=ModelPolicy(text_model_key='text', vision_model_key='vision'))
    image = HumanMessage(content=[{'type': 'text', 'text': 'Inspect sketch'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aW1hZ2U='}}])
    async with client:
        with pytest.raises(DomainError, match='cannot process images'):
            await adapter.chat_model(scope).ainvoke([image])
        await adapter.chat_model(scope, images=True).ainvoke([image])
        with pytest.raises(DomainError, match='inline'):
            await adapter.chat_model(scope, images=True).ainvoke([HumanMessage(content=[
                {'type': 'image_url', 'image_url': {'url': 'http://localhost/private'}}])])
    assert len(requests) == 1 and requests[0]['model'] == 'vision-pinned-model'


async def test_modal_429_uses_only_explicit_fallback_and_records_both_attempts(authority):
    calls = []
    def respond(request):
        calls.append(request.url.host)
        return httpx.Response(429, json={}) if len(calls) == 1 else httpx.Response(200, json=completion())
    adapter, scope, client = gateway(authority, respond,
        configurations=[configuration('modal', 'modal'), configuration()],
        policy=ModelPolicy(text_model_key='modal', modal_rate_limit_fallback_key='text'))
    async with client:
        await adapter.chat_model(scope).ainvoke('Shape')
    records = adapter.ledger.records(scope.actor, scope.project_id, scope.run_id)
    assert calls == ['modal.example', 'meta.example']
    assert records[0]['status'] == 'rate_limited' and records[0]['committedMicroUsd'] == 0
    assert records[1]['fallbackOf'] == records[0]['id']
    with authority[0].transaction() as session:
        assert session.get(ModelBudget, scope.run_id).calls == 2


@pytest.mark.parametrize('status', [429, 500, 401])
async def test_no_implicit_provider_retry_or_switch(authority, status):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={'error': {'message': 'Never persist private provider errors'}})
    adapter, scope, client = gateway(authority, respond)
    async with client:
        with pytest.raises(DomainError):
            await adapter.chat_model(scope).ainvoke('Shape')
    assert len(calls) == 1
    records = adapter.ledger.records(scope.actor, scope.project_id, scope.run_id)
    assert records[0]['status'] == {429: 'rate_limited', 500: 'provider_error', 401: 'provider_rejected'}[status]
    # A refusal before any generation (429, 4xx) is free; a 5xx may have consumed provider work and keeps its reservation.
    assert (records[0]['committedMicroUsd'] == 0) == (status != 500)
    assert 'Never persist' not in json.dumps(records)


async def test_budgets_and_uncertain_reservations_survive_gateway_restart(authority):
    calls = []
    def uncertain(request):
        calls.append(request)
        raise httpx.ReadTimeout('Sensitive request details', request=request)
    policy = ModelPolicy(text_model_key='text', max_calls=2, max_cost_micro_usd=50000)
    adapter, scope, client = gateway(authority, uncertain, policy=policy)
    async with client:
        with pytest.raises(DomainError, match='uncertain'):
            await adapter.chat_model(scope).ainvoke('Shape')
    recreated, _, client = gateway(authority, uncertain, policy=policy)
    async with client:
        with pytest.raises(DomainError, match='allowance exhausted'):
            await recreated.chat_model(scope).ainvoke('Shape again')
    assert len(calls) == 1
    assert recreated.ledger.records(scope.actor, scope.project_id, scope.run_id)[0]['status'] == 'uncertain'
    with authority[0].transaction() as session:
        budget = session.scalar(select(ModelBudget))
        assert budget.committed_cost == 40192 and budget.calls == 1


async def test_missing_usage_retains_reservation_and_policy_changes_cannot_reset_it(authority):
    adapter, scope, client = gateway(authority, lambda request: httpx.Response(200, json=completion(usage={})))
    async with client:
        await adapter.chat_model(scope).ainvoke('Shape')
    records = adapter.ledger.records(scope.actor, scope.project_id, scope.run_id)
    assert records[0]['status'] == 'usage_unknown' and records[0]['committedMicroUsd'] == 40192
    changed, _, client = gateway(authority, lambda request: pytest.fail('No request should run'),
        policy=ModelPolicy(text_model_key='text', max_calls=999))
    async with client:
        with pytest.raises(DomainError, match='pinned'):
            await changed.chat_model(scope).ainvoke('Try resetting budget')


async def test_cancellation_during_request_settles_usage_but_fences_return(authority):
    store, scope = authority
    def respond(request):
        GraphRepository(store).cancel(scope.actor, scope.project_id, scope.run_id)
        return httpx.Response(200, json=completion())
    adapter, _, client = gateway(authority, respond)
    async with client:
        with pytest.raises(DomainError, match='no longer active'):
            await adapter.chat_model(scope).ainvoke('Shape')
    assert adapter.ledger.records(scope.actor, scope.project_id, scope.run_id)[0]['status'] == 'completed'


async def test_incomplete_and_oversized_responses_are_not_success(authority):
    def respond(request):
        return httpx.Response(200, json=completion(choices=[{'finish_reason': 'length',
            'message': {'content': 'Partial shape'}}]))
    adapter, scope, client = gateway(authority, respond)
    async with client:
        with pytest.raises(DomainError, match='invalid response'):
            await adapter.chat_model(scope).ainvoke('Shape')
    assert adapter.ledger.records(scope.actor, scope.project_id, scope.run_id)[0]['status'] == 'invalid_response'
