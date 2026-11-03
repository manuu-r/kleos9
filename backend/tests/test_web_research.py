import gzip
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from kleos.capabilities.registry import CapabilityRegistry
from kleos.capabilities.web_research import SearxngGateway, SearxngInstallation, register_searxng
from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import Effect
from kleos.projects.research import ResearchRepository, ResearchService
from kleos.projects.store import Objective, Project, ProjectStore


def installation(**changes):
    return SearxngInstallation(**({'endpoint': 'https://search.example.org/search', 'allowed_host': 'search.example.org',
        'version': 'pinned-test-fixture', 'billing': 'self_hosted_no_query_fees', 'engines': ('wikipedia',),
        'qualified': True, 'max_requests_per_run': 2} | changes))


def response(**changes):
    return {'results': [{'title': '<b>Manufacturer material data</b>', 'url': 'https://manufacturer.example/material',
        'content': 'The published <em>test method</em> applies to a specified sample.'}], **changes}


@pytest.fixture
def authority(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path}/web.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    ident = store.create(actor, 'Research bracket materials', 'create')['projectId']
    yield store, actor, ident, ResearchRepository(store)
    store.engine.dispose()


def setup(authority, handler, **settings):
    store, actor, ident, repository = authority
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = SearxngGateway(installation(**settings), repository, client=client)
    registry = CapabilityRegistry()
    keys = register_searxng(registry, gateway)
    receipt = ResearchService(store, registry).submit(actor, ident, 1, 'research')
    scope = RunScope(actor=actor, project_id=ident, run_id=receipt['runId'], revision=1,
        deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    task = TaskSpec(id='research-material', capability='research', objective='Find manufacturer material data')
    return client, gateway, registry.tools(registry.snapshot(keys))[0], scope, task


async def test_http_search_is_bounded_fixed_get_cited_and_replays(authority):
    calls = []
    def server(request):
        calls.append(request)
        assert request.method == 'GET' and request.url.host == 'search.example.org'
        assert request.url.path == '/search'
        assert dict(request.url.params) == {'q': 'manufacturer bracket material', 'format': 'json',
            'pageno': '1', 'engines': 'wikipedia'}
        assert 'Authorization' not in request.headers
        return httpx.Response(200, json=response(private_debug='secret-not-retained'))
    client, gateway, tool, scope, task = setup(authority, server)
    args = {'actionId': 'stable-turn', 'query': 'manufacturer bracket material'}
    async with client:
        result = await tool.invoke(scope, task, args)
        replay = await tool.invoke(scope, task, args)
        assert replay == result and len(calls) == 1
        with pytest.raises(DomainError) as error:
            await tool.invoke(scope, task, {**args, 'query': 'different request'})
        assert error.value.code == 'idempotency_conflict'
    check = result.observations[0]
    assert check.status == 'PASS' and not check.independent
    assert check.details['sources'][0]['title'] == 'Manufacturer material data'
    assert check.details['sourcePagesFetched'] is False and check.details['feasibilityValidated'] is False
    store, _, ident, repo = authority
    saved = TaskResult(task_id=task.id, status='succeeded', summary='Found material data; tests still required.',
        observations=result.observations)
    await repo.publish(scope, saved)
    await repo.assert_complete(scope, {task.id: saved.model_dump(mode='json')})
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert run.data['researchUsage']['requests'] == 1
        effects = list(session.scalars(select(Effect)))
        assert 'secret-not-retained' not in json.dumps([effect.value for effect in effects])
        assert session.get(Project, ident).data['state']['research']['status'] == 'candidate'


@pytest.mark.parametrize('payload,code', [
    ({'results': []}, 'research_no_sources'),
    (response(unresponsive_engines=[['wikipedia', 'timeout']]), 'research_search_partial'),
    ({'results': [{'title': 'Unsafe', 'url': 'file:///secret', 'content': 'secret'}]}, 'research_no_sources')])
async def test_absent_or_partial_evidence_never_claims_complete(authority, payload, code):
    client, _, tool, scope, task = setup(authority, lambda _: httpx.Response(200, json=payload))
    async with client:
        result = await tool.invoke(scope, task, {'actionId': 'first', 'query': 'data'})
    assert code in result.blockers
    forged = TaskResult(task_id=task.id, status='succeeded', summary='Done', observations=result.observations)
    with pytest.raises(DomainError):
        await authority[3].publish(scope, forged)


@pytest.mark.parametrize('mode', ['redirect', 'error', 'oversized', 'invalid', 'timeout', 'compressed'])
async def test_errors_keep_reservation_and_unknown_effect_does_not_repeat(authority, mode):
    calls = []
    def server(request):
        calls.append(request)
        if mode == 'timeout':
            raise httpx.ReadTimeout('DO NOT LEAK query/private URL')
        if mode == 'redirect':
            return httpx.Response(302, headers={'Location': 'http://169.254.169.254/'})
        if mode == 'error':
            return httpx.Response(403, text='private debug')
        if mode == 'oversized':
            return httpx.Response(200, content=b'x' * 5000)
        if mode == 'compressed':
            # Streaming response avoids httpx's eager fixture decompression.
            return httpx.Response(200, headers={'Content-Encoding': 'gzip'},
                stream=httpx.ByteStream(gzip.compress(b'x' * 1048576)))
        return httpx.Response(200, json={'private': 'invalid'})
    client, _, tool, scope, task = setup(authority, server, max_response_bytes=4096)
    async with client:
        for _ in range(2):
            with pytest.raises(DomainError) as error:
                await tool.invoke(scope, task, {'actionId': 'same', 'query': 'data'})
            assert 'private' not in str(error.value) and '169.254' not in str(error.value)
    assert len(calls) == 1
    with authority[0].transaction() as session:
        assert session.get(Objective, scope.run_id).data['researchUsage']['requests'] == 1


async def test_request_budget_and_source_only_authority(authority):
    calls = []
    def server(request):
        calls.append(request)
        return httpx.Response(200, json=response())
    client, _, tool, scope, task = setup(authority, server, max_requests_per_run=1)
    async with client:
        await tool.invoke(scope, task, {'actionId': 'one', 'query': 'data'})
        with pytest.raises(DomainError) as error:
            await tool.invoke(scope, task, {'actionId': 'two', 'query': 'data'})
        assert error.value.code == 'research_budget_exhausted'
        with authority[0].transaction() as session:
            run = session.get(Objective, scope.run_id)
            run.data = {**run.data, 'researchRequest': {**run.data['researchRequest'], 'scope': 'project_sources'}}
        with pytest.raises(DomainError) as error:
            await tool.invoke(scope, task, {'actionId': 'three', 'query': 'data'})
        assert error.value.code == 'research_scope_conflict'
    assert len(calls) == 1


@pytest.mark.parametrize('query', ['!paid data', '!! http://internal', 'data\nprivate', '   '])
async def test_query_cannot_select_other_engines_or_redirect(authority, query):
    client, _, tool, scope, task = setup(authority, lambda _: pytest.fail('must not make HTTP request'))
    async with client:
        with pytest.raises(ValidationError):
            await tool.invoke(scope, task, {'actionId': 'bad', 'query': query})


@pytest.mark.parametrize('url', ['http://search.example.org/search', 'https://user:secret@search.example.org/search',
    'https://localhost/search', 'https://169.254.169.254/search', 'https://search.example.org/arbitrary',
    'https://search.example.org/search?key=secret', 'https://127.1/search', 'https://0x7f000001/search'])
def test_installation_rejects_unsafe_or_arbitrary_endpoints(url):
    with pytest.raises(ValidationError):
        installation(endpoint=url, allowed_host=urlsplit(url).hostname)


def test_unqualified_installation_not_registered(authority):
    gateway = SearxngGateway(installation(qualified=False), authority[3])
    with pytest.raises(DomainError):
        register_searxng(CapabilityRegistry(), gateway)


async def test_cancellation_during_http_discards_evidence_and_retains_allowance(authority):
    scope_ref = []
    def server(_):
        store, actor, ident, repo = authority
        repo.cancel(actor, ident, scope_ref[0].run_id)
        return httpx.Response(200, json=response())
    client, _, tool, scope, task = setup(authority, server)
    scope_ref.append(scope)
    async with client:
        with pytest.raises(DomainError):
            await tool.invoke(scope, task, {'actionId': 'cancelled', 'query': 'data'})
    with authority[0].transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert run.status == 'cancelled' and run.data['researchUsage']['requests'] == 1
        assert not run.data.get('results')
