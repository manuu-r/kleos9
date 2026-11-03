"""Account installation gates with explicitly synthetic, test-only review records."""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from kleos.access.service import AccessService, AccessSettings
from kleos.capabilities.registry import CapabilityRegistry
from kleos.contracts.models import DomainError, TaskSpec
from kleos.infrastructure import external
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Objective, ProjectStore


@pytest.fixture
def context(tmp_path, monkeypatch):
    store = ProjectStore(f"sqlite:///{tmp_path / 'runtime.db'}")
    store.initialize_for_tests()
    access = AccessService(store, AccessSettings(secret_key='x'*32, environment='test', development_no_login=True))
    actor = access.authenticate({}, {}, 'GET')
    project = store.create(actor, 'Source a replacement connector', 'project')['projectId']
    with store.transaction() as session:
        session.add(Objective(id='run', project_id=project, revision=1, status='running', data={}))
    calls, constructed = [], []
    def respond(request):
        calls.append(request)
        if request.url.host == 'api.digikey.com':
            return httpx.Response(200, json={'SearchLocaleUsed': {'Currency': 'USD'}, 'Product': {
                'ManufacturerProductNumber': 'CONNECTOR', 'ProductVariations': [{'DigiKeyProductNumber': 'SKU-ND',
                    'QuantityAvailableforPackageType': 2, 'MinimumOrderQuantity': 1,
                    'StandardPricing': [{'BreakQuantity': 1, 'UnitPrice': 1.5}]}]}})
        return httpx.Response(200, json={'success': True, 'humans': [], 'hasMore': False})
    catalog_class, human_class = external.digikey.DigiKeyCatalog, external.rentahuman.RentAHumanDiscovery
    def catalog(**kwargs):
        constructed.append('digikey')
        return catalog_class(**kwargs, transport=httpx.MockTransport(respond))
    def human(**kwargs):
        constructed.append('rentahuman')
        return human_class(**kwargs, transport=httpx.MockTransport(respond))
    monkeypatch.setattr(external.digikey, 'DigiKeyCatalog', catalog)
    monkeypatch.setattr(external.rentahuman, 'RentAHumanDiscovery', human)
    settings = SimpleNamespace(environment='test', external_installations=[], external_credentials={},
                               session_secret=SecretStr('persistent-fixture-key-for-external-pins'))
    registry = CapabilityRegistry()
    yield settings, store, access, registry, GraphRepository(store), actor, project, calls, constructed, tmp_path
    store.engine.dispose()


def install(context, provider='digikey', **changes):
    settings, *_, tmp_path = context
    manifest = external.external_qualification_manifest(provider)
    report = {**manifest, 'sourceOverride': False, 'testOnly': True,
        'checks': {check: 'PASS' for check in manifest['requiredChecks']},
        'review': {'independent': True, 'reviewer': 'synthetic test reviewer, not a production attestation',
            'reference': 'test-only fixture', 'sourceHash': manifest['sourceHash'], 'reviewedAt': '2026-11-02T00:00:00Z'}}
    path = tmp_path / (provider + '-test-report.json')
    path.write_text(json.dumps(report))
    config = {'provider': provider, 'version': manifest['providerVersion'], 'tenantId': 'development',
              'userId': 'development', 'qualificationReference': str(path)}
    if provider == 'digikey':
        config.update(clientIdSecret='catalog_client_ref', accessTokenSecret='catalog_token_ref', accountIdSecret='catalog_account_ref')
        settings.external_credentials.update(catalog_client_ref=SecretStr('PRIVATE_CLIENT_VALUE'),
            catalog_token_ref=SecretStr('PRIVATE_TOKEN_VALUE'), catalog_account_ref=SecretStr('PRIVATE_ACCOUNT_VALUE'))
    else:
        config['apiKeySecret'] = 'human_key_ref'
        settings.external_credentials['human_key_ref'] = SecretStr('PRIVATE_HUMAN_VALUE')
    config.update(changes)
    settings.external_installations.append(config)
    return path, report


def build(context):
    return external.build_external(*context[:5])


@pytest.mark.asyncio
async def test_unconfigured_runtime_has_no_provider_or_registered_tool(context):
    runtime = build(context)
    assert not runtime.jobs.providers and not runtime.pins and not runtime.tool_keys
    assert context[3].catalog() == [] and not context[7] and not context[8]
    status = await runtime.facade.query(context[5], 'human_provider_status', {})
    assert status['accountConfigured'] is False
    await runtime.close()


@pytest.mark.asyncio
async def test_reviewed_fixture_composition_pins_sources_without_exposing_secrets(context):
    install(context)
    install(context, 'rentahuman')
    runtime = build(context)
    assert context[8] == ['digikey', 'rentahuman'] and not context[7]
    assert set(runtime.jobs.providers) == {('development', 'development', 'digikey'),
                                          ('development', 'development', 'rentahuman')}
    assert len(runtime.tool_keys) == 9
    encoded = json.dumps([runtime.pins, context[3].catalog()])
    for value in ('PRIVATE_CLIENT_VALUE', 'PRIVATE_TOKEN_VALUE', 'PRIVATE_ACCOUNT_VALUE', 'PRIVATE_HUMAN_VALUE',
                  'catalog_client_ref', 'catalog_token_ref', 'human_key_ref', 'catalog_account_ref'):
        assert value not in encoded
    assert all(pin['qualificationScope'] == external.SCOPE and pin['liveAccessVerified'] is False
               and pin['physicalActionsEnabled'] is False for pin in runtime.pins)
    assert all(row['manifest']['effect'] != 'external_action' for row in context[3].catalog())
    status = await runtime.facade.query(context[5], 'human_provider_status', {})
    assert status['accountConfigured'] and not status['livePostingEnabled']
    assert any('Live access has not been verified' in limit for limit in status['limitations'])
    await runtime.close()
    await runtime.close()
    assert all(provider.http._client.is_closed for provider in runtime.jobs.providers.values())


@pytest.mark.asyncio
async def test_runtime_registered_adapter_makes_only_scoped_mock_get(context):
    install(context)
    runtime = build(context)
    tools = {tool.name: tool for tool in context[3].tools(context[3].snapshot(runtime.tool_keys))}
    with context[1].transaction() as session:
        session.get(Objective, 'run').data = {'runtimePins': {'external': runtime.pins_for(context[5])}}
    scope = RunScope(actor=context[5], project_id=context[6], run_id='run', revision=1,
                     deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    task = TaskSpec(id='source', capability='sourcing', objective='Find one specified connector')
    draft = await tools['sourcing.create'].invoke(scope, task, {'actionId': 'create',
        'bom': [{'reference': 'J1', 'supplierPart': 'SKU-ND', 'quantity': 1}]})
    ident = draft.observations[0].details['jobId']
    quote = await tools['sourcing.quote'].invoke(scope, task, {'actionId': 'quote', 'jobId': ident})
    assert quote.observations[0].details['physicalAcceptance'] is False
    assert len(context[7]) == 1
    request = context[7][0]
    assert request.method == 'GET' and request.headers['Authorization'] == 'Bearer PRIVATE_TOKEN_VALUE'
    assert request.headers['X-DIGIKEY-Account-Id'] == 'PRIVATE_ACCOUNT_VALUE'
    await runtime.close()


@pytest.mark.parametrize('change', [{'version': 'unreviewed'}, {'baseUrl': 'https://other.invalid'},
                                   {'tenantId': ''}, {'userId': ''}, {'command': 'do-work'}])
def test_invalid_installation_cannot_construct_clients(context, change):
    install(context, **change)
    with pytest.raises(DomainError) as error:
        build(context)
    assert error.value.code == 'configuration_required' and not context[8]


@pytest.mark.parametrize('reference', ['missing', 'plaintext', 'empty'])
def test_missing_or_untyped_credentials_fail_without_echo(context, reference):
    install(context)
    credentials = context[0].external_credentials
    if reference == 'missing':
        credentials.pop('catalog_token_ref')
    elif reference == 'plaintext':
        credentials['catalog_token_ref'] = 'PRIVATE_NOT_A_SECRET_OBJECT'
    else:
        credentials['catalog_token_ref'] = SecretStr('')
    with pytest.raises(DomainError) as error:
        build(context)
    assert 'PRIVATE' not in str(error.value) and not context[8]


@pytest.mark.parametrize('change', ['missing', 'no_review', 'failed_check', 'changed_source', 'wrong_review_source',
                                   'live_claim', 'source_override', 'symlink', 'malformed'])
def test_unreviewed_or_mismatched_evidence_fails_closed(context, change):
    path, report = install(context)
    if change == 'missing':
        path.unlink()
    elif change == 'symlink':
        saved = path.with_suffix('.saved')
        path.rename(saved)
        path.symlink_to(saved)
    elif change == 'malformed':
        path.write_text('[]')
    else:
        if change == 'no_review':
            report.pop('review')
        elif change == 'failed_check':
            report['checks']['get_only'] = 'FAIL'
        elif change == 'changed_source':
            report['sourceHash'] = '0'*64
        elif change == 'wrong_review_source':
            report['review']['sourceHash'] = '0'*64
        elif change == 'live_claim':
            report['liveAccessVerified'] = True
        else:
            report['sourceOverride'] = True
        path.write_text(json.dumps(report))
    with pytest.raises(DomainError) as error:
        build(context)
    assert error.value.code == 'tool_unqualified' and not context[8]
    assert context[3].catalog() == []


def test_test_only_evidence_never_enables_development_or_production(context):
    install(context)
    for environment in ('development', 'production'):
        context[0].environment = environment
        with pytest.raises(DomainError, match='independently reviewed'):
            build(context)
    assert not context[8]


def test_all_accounts_validate_before_any_client_is_constructed(context):
    install(context)
    path, _ = install(context, 'rentahuman')
    path.unlink()
    with pytest.raises(DomainError):
        build(context)
    assert not context[8]


def test_duplicate_principal_is_rejected(context):
    install(context)
    context[0].external_installations.append(dict(context[0].external_installations[0]))
    with pytest.raises(DomainError) as error:
        build(context)
    assert error.value.code == 'configuration_required' and not context[8]


@pytest.mark.asyncio
async def test_explicit_public_human_installation_does_not_infer_owner_credentials(context):
    install(context, 'rentahuman', apiKeySecret=None)
    runtime = build(context)
    adapter = runtime.jobs.providers['development', 'development', 'rentahuman']
    assert not adapter.authenticated
    status = await runtime.facade.query(context[5], 'human_provider_status', {})
    assert 'public_search' in status['capabilities'] and 'owned_bounties' not in status['capabilities']
    await runtime.close()


def scope_and_task(context, runtime, *, run_id='run'):
    with context[1].transaction() as session:
        session.get(Objective, run_id).data = {'runtimePins': {'external': runtime.pins_for(context[5])}}
    scope = RunScope(actor=context[5], project_id=context[6], run_id=run_id, revision=1,
                     deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    task = TaskSpec(id='source', capability='sourcing', objective='Find one specified connector')
    return scope, task


async def create_job(tools, scope, task):
    result = await tools['sourcing.create'].invoke(scope, task, {'actionId': 'create',
        'bom': [{'reference': 'J1', 'supplierPart': 'SKU-ND', 'quantity': 1}]})
    return result.observations[0].details['jobId']


@pytest.mark.asyncio
async def test_adding_unrelated_account_preserves_existing_run_and_job_pins(context):
    from kleos.contracts.models import Actor
    install(context)
    first = build(context)
    scope, task = scope_and_task(context, first)
    snapshot = context[3].snapshot(first.tool_keys)
    tools = {tool.name: tool for tool in context[3].tools(snapshot)}
    job_id = await create_job(tools, scope, task)
    actor_pins = first.pins_for(context[5])
    global_pins = first.pins
    await first.close()
    install(context, tenantId='other-tenant', userId='other-user', accessTokenSecret='other_token_ref')
    context[0].external_credentials['other_token_ref'] = SecretStr('OTHER_ACCOUNT_TOKEN')
    registry = CapabilityRegistry()
    second = external.build_external(context[0], context[1], context[2], registry, context[4])
    assert second.pins == global_pins and second.pins_for(context[5]) == actor_pins
    assert second.pins_for(Actor(tenant_id='absent', user_id='absent')) == []
    assert second.tool_keys_for(Actor(tenant_id='absent', user_id='absent')) == []
    tools = {tool.name: tool for tool in registry.tools(snapshot)}
    assert await create_job(tools, scope, task) == job_id
    result = await tools['sourcing.quote'].invoke(scope, task, {'actionId': 'quote', 'jobId': job_id})
    assert result.observations[0].status == 'PASS'
    assert context[7][-1].headers['Authorization'] == 'Bearer PRIVATE_TOKEN_VALUE'
    await second.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['reference', 'value', 'account', 'server_key'])
async def test_account_binding_change_fences_existing_runs_and_jobs(context, change):
    install(context)
    first = build(context)
    scope, task = scope_and_task(context, first)
    snapshot = context[3].snapshot(first.tool_keys)
    tools = {tool.name: tool for tool in context[3].tools(snapshot)}
    job_id = await create_job(tools, scope, task)
    old_pin = first.pins_for(context[5])[0]['installationHash']
    await first.close()
    if change == 'reference':
        context[0].external_installations[0]['accessTokenSecret'] = 'replacement_token_ref'
        context[0].external_credentials['replacement_token_ref'] = SecretStr('PRIVATE_TOKEN_VALUE')
    elif change == 'value':
        context[0].external_credentials['catalog_token_ref'] = SecretStr('DIFFERENT_ACCOUNT_TOKEN')
    elif change == 'account':
        context[0].external_credentials['catalog_account_ref'] = SecretStr('DIFFERENT_ACCOUNT_ID')
    else:
        context[0].session_secret = SecretStr('different-persistent-server-key-for-pins')
    registry = CapabilityRegistry()
    second = external.build_external(context[0], context[1], context[2], registry, context[4])
    new_pin = second.pins_for(context[5])[0]['installationHash']
    assert old_pin != new_pin
    # Code/tool installation is unchanged; the account pin independently fences access.
    tools = {tool.name: tool for tool in registry.tools(snapshot)}
    with pytest.raises(DomainError) as error:
        await tools['sourcing.quote'].invoke(scope, task, {'actionId': 'quote', 'jobId': job_id})
    assert error.value.code == 'installation_conflict'
    with pytest.raises(DomainError) as error:
        await second.jobs.quote(context[5], job_id)
    assert error.value.code == 'provider_changed'
    assert second.jobs.get(context[5], job_id)['presentation']['_installationHash'] == old_pin
    assert not context[7]
    # An explicitly created new run can use the newly configured account.
    with context[1].transaction() as session:
        session.add(Objective(id='new-run', project_id=context[6], revision=1, status='running', data={}))
    new_scope, new_task = scope_and_task(context, second, run_id='new-run')
    new_id = await create_job(tools, new_scope, new_task)
    assert second.jobs.get(context[5], new_id)['presentation']['_installationHash'] == new_pin
    encoded = json.dumps(second.pins_for(context[5]))
    assert all(value not in encoded for value in ('PRIVATE_TOKEN_VALUE', 'catalog_token_ref',
        'replacement_token_ref', 'DIFFERENT_ACCOUNT_TOKEN', 'DIFFERENT_ACCOUNT_ID'))
    await second.close()


@pytest.mark.asyncio
async def test_missing_run_account_pin_cannot_create_or_lookup(context):
    install(context)
    runtime = build(context)
    tools = {tool.name: tool for tool in context[3].tools(context[3].snapshot(runtime.tool_keys))}
    scope = RunScope(actor=context[5], project_id=context[6], run_id='run', revision=1,
                     deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    task = TaskSpec(id='source', capability='sourcing', objective='Find one connector')
    with pytest.raises(DomainError) as error:
        await create_job(tools, scope, task)
    assert error.value.code == 'installation_conflict'
    assert runtime.jobs.list(context[5], context[6]) == [] and not context[7]
    await runtime.close()


@pytest.mark.asyncio
async def test_account_change_during_lookup_fences_observation_commit(context):
    install(context)
    runtime = build(context)
    scope, task = scope_and_task(context, runtime)
    tools = {tool.name: tool for tool in context[3].tools(context[3].snapshot(runtime.tool_keys))}
    job_id = await create_job(tools, scope, task)
    principal = ('development', 'development', 'digikey')
    provider = runtime.jobs.providers[principal]
    original = provider.quote
    async def changed(request):
        result = await original(request)
        runtime.jobs.installation_pins[principal] = '0'*64
        return result
    provider.quote = changed
    with pytest.raises(DomainError) as error:
        await runtime.jobs.quote(context[5], job_id)
    assert error.value.code == 'provider_changed'
    assert 'quote' not in runtime.jobs.get(context[5], job_id)['preparations']
    assert len(context[7]) == 1
    await runtime.close()
