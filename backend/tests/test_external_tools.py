"""Real scoped external adapters behind registered tools, with fixture HTTP only."""
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from kleos.artifacts.service import Artifact
from kleos.capabilities.external import ExternalJobs
from kleos.capabilities.external_tools import register_external_jobs
from kleos.capabilities.human_work import RentAHumanDiscovery
from kleos.capabilities.registry import CapabilityRegistry
from kleos.capabilities.sourcing import DigiKeyCatalog
from kleos.contracts.models import Actor, Capability, DomainError, TaskSpec
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Membership, Objective, Project, ProjectStore

OWNER = Actor(tenant_id='tenant', user_id='owner')
EDITOR = Actor(tenant_id='tenant', user_id='editor')


@pytest.fixture
def context(tmp_path):
    store = ProjectStore(f"sqlite:///{tmp_path / 'tools.db'}")
    store.initialize_for_tests()
    project = store.create(OWNER, 'Find the missing connector', 'create')['projectId']
    other = store.create(OWNER, 'Other context', 'other')['projectId']
    with store.transaction() as session:
        row = session.get(Project, project)
        row.data = {**row.data, 'sourceManifest': {'drawing.txt': 'drawing'}}
        session.add(Artifact(id='drawing', project_id=project, revision=1, name='drawing.txt', sha256='a'*64,
            size=10, media_type='text/plain', status='input', lane='research', details={}))
        session.add(Objective(id='run', project_id=project, revision=1, status='running', data={}))
        session.add(Objective(id='other-run', project_id=other, revision=1, status='running', data={}))
        session.add(Membership(project_id=project, user_id='editor', role='editor'))
    calls = []
    def respond(request):
        calls.append(request)
        assert request.method == 'GET'
        if request.url.host == 'api.digikey.com':
            assert request.headers['Authorization'] == 'Bearer private-catalog-token'
            return httpx.Response(200, json={'SearchLocaleUsed': {'Currency': 'USD'}, 'Product': {
                'ManufacturerProductNumber': 'CONNECTOR', 'ProductVariations': [{'DigiKeyProductNumber': 'ABC-ND',
                    'QuantityAvailableforPackageType': 100, 'MinimumOrderQuantity': 1,
                    'StandardPricing': [{'BreakQuantity': 1, 'UnitPrice': 2}]}]}})
        if request.url.path == '/api/humans':
            return httpx.Response(200, json={'success': True, 'humans': [{'id': 'human1', 'name': 'Inspector',
                'hourlyRate': 25, 'currency': 'USD', 'privateEmail': 'must-not-appear'}], 'hasMore': False})
        if request.url.path.endswith('/submissions'):
            return httpx.Response(200, json={'success': True, 'submissions': []})
        return httpx.Response(404)
    catalog = DigiKeyCatalog(client_id=SecretStr('client'), access_token=SecretStr('private-catalog-token'),
                            transport=httpx.MockTransport(respond))
    human = RentAHumanDiscovery(api_key=SecretStr('private-human-key'), transport=httpx.MockTransport(respond))
    providers = {('tenant', 'owner', 'digikey'): catalog, ('tenant', 'owner', 'rentahuman'): human}
    jobs = ExternalJobs(store, providers)
    repo, registry = GraphRepository(store), CapabilityRegistry()
    for capability, provider in [(Capability.SOURCING, catalog), (Capability.HUMAN_WORK, human)]:
        register_external_jobs(registry, jobs, repo, capability=capability, provider=provider.id,
            provider_version=provider.version, installation_hash='c'*64, qualified=True)
    tools = {tool.name: tool for tool in registry.tools(registry.snapshot([item['key'] for item in registry.catalog()]))}
    scope = RunScope(actor=OWNER, project_id=project, run_id='run', revision=1,
                     deadline_at=datetime.now(UTC) + timedelta(minutes=10))
    yield store, jobs, registry, tools, scope, calls, other
    store.engine.dispose()


def task(capability='sourcing', inputs=None):
    return TaskSpec(id='task-' + capability, objective='Find the specified connector or prepare a fit inspection',
                    capability=capability, input_artifact_ids=['drawing'] if inputs is None else inputs)


def create_args(capability='sourcing'):
    if capability == 'sourcing':
        return {'actionId': 'create', 'bom': [{'reference': 'J1', 'supplierPart': 'ABC-ND', 'quantity': 2}]}
    return {'actionId': 'create-human', 'workOrder': {'title': 'Inspect connector fit', 'procedure': 'Measure the supplied specimen',
            'evidenceRequired': ['photo', 'measurement'], 'specimen': 'drawing', 'skill': 'prototype assembly'}}


async def create(context, capability='sourcing'):
    result = await context[3][capability + '.create'].invoke(context[4], task(capability), create_args(capability))
    return result.observations[0].details['jobId']


@pytest.mark.asyncio
async def test_real_catalog_gets_are_scoped_and_durable_without_purchase(context):
    _, jobs, registry, tools, scope, calls, _ = context
    ident = await create(context)
    before = jobs.get(OWNER, ident)
    assert before['request']['ceilingMinorUnits'] == 0 and before['request']['currency'] == 'USD'
    assert before['request']['artifactBindings'] == [{'artifactId': 'drawing', 'sha256': 'a'*64, 'revision': 1}]
    assert before['presentation']['_contextRevision'] == 1
    quoted = await tools['sourcing.quote'].invoke(scope, task(), {'actionId': 'quote', 'jobId': ident})
    assert quoted.observations[0].status == 'PASS' and not quoted.observations[0].independent
    assert quoted.observations[0].details['result']['bindingQuote'] is False
    assert len(calls) == 1 and calls[0].url.host == 'api.digikey.com'
    saved = jobs.get(OWNER, ident)
    assert saved['preparations']['quote']['result'] == quoted.observations[0].details['result']
    assert saved['effectState'] == 'none' and not saved['physicalAcceptance']
    assert all(item['manifest']['effect'] != 'external_action' for item in registry.catalog())
    assert not any(name.endswith(('.purchase', '.hire', '.approve', '.link', '.submit')) for name in tools)
    assert 'private-catalog-token' not in quoted.model_dump_json()


@pytest.mark.asyncio
async def test_human_discovery_local_draft_and_waits_never_claim_hiring(context):
    _, jobs, _, tools, scope, calls, _ = context
    ident = await create(context, 'human_work')
    discovered = await tools['human_work.discover'].invoke(scope, task('human_work'), {'actionId': 'discover', 'jobId': ident})
    details = discovered.observations[0].details
    assert details['result']['hired'] is False and len(details['result']['people']) == 1
    assert 'must-not-appear' not in discovered.model_dump_json()
    drafted = await tools['human_work.draft'].invoke(scope, task('human_work'), {'actionId': 'draft', 'jobId': ident})
    assert drafted.observations[0].details['result']['submitted'] is False
    observed = await tools['human_work.observe'].invoke(scope, task('human_work'), {'actionId': 'observe', 'jobId': ident})
    assert observed.observations[0].status == 'UNKNOWN' and observed.blockers
    assert observed.observations[0].details['externalId'] is None
    assert len(calls) == 1 and jobs.get(OWNER, ident)['effectState'] == 'none'


@pytest.mark.asyncio
async def test_host_identity_replay_survives_policy_only_revision_but_not_context_change(context):
    store, jobs, _, tools, scope, calls, _ = context
    first = await create(context)
    with store.transaction() as session:
        session.get(Project, scope.project_id).advance_revision(context=False)
    assert await create(context) == first
    assert len(jobs.list(OWNER, scope.project_id)) == 1
    changed = create_args()
    changed['bom'][0]['quantity'] = 3
    with pytest.raises(DomainError, match='reused'):
        await tools['sourcing.create'].invoke(scope, task(), changed)
    await tools['sourcing.quote'].invoke(scope, task(), {'actionId': 'quote', 'jobId': first})
    with store.transaction() as session:
        session.get(Project, scope.project_id).advance_revision(context=True)
    with pytest.raises(DomainError, match='changed'):
        await tools['sourcing.quote'].invoke(scope, task(), {'actionId': 'stale', 'jobId': first})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_principal_project_input_and_provider_version_are_fenced(context):
    _, jobs, _, tools, scope, calls, other = context
    ident = await create(context)
    arguments = {'actionId': 'read', 'jobId': ident}
    with pytest.raises(DomainError, match='this account'):
        await tools['sourcing.quote'].invoke(scope.model_copy(update={'actor': EDITOR}), task(), arguments)
    jobs.providers[('tenant', 'editor', 'digikey')] = jobs.providers[('tenant', 'owner', 'digikey')]
    with pytest.raises(DomainError, match='outside'):
        await tools['sourcing.quote'].invoke(scope.model_copy(update={'actor': EDITOR}), task(), arguments)
    with pytest.raises(DomainError, match='outside'):
        await tools['sourcing.quote'].invoke(scope.model_copy(update={'project_id': other, 'run_id': 'other-run'}), task(), arguments)
    with pytest.raises(DomainError, match='outside'):
        await tools['sourcing.quote'].invoke(scope, task(inputs=[]), arguments)
    jobs.providers[('tenant', 'owner', 'digikey')].version = 'changed'
    with pytest.raises(DomainError, match='changed'):
        await tools['sourcing.quote'].invoke(scope, task(), arguments)
    assert not calls


@pytest.mark.asyncio
async def test_revocation_and_unknown_tool_fields_block_before_provider_io(context):
    _, _, registry, tools, scope, calls, _ = context
    ident = await create(context)
    with pytest.raises(ValidationError):
        await tools['sourcing.quote'].invoke(scope, task(), {'actionId': 'quote', 'jobId': ident, 'apiKey': 'injected'})
    with pytest.raises(ValidationError):
        await tools['sourcing.create'].invoke(scope, task(), {**create_args(), 'provider': 'other'})
    registry.revoke('sourcing.quote@1.0.0')
    with pytest.raises(DomainError, match='unavailable'):
        await tools['sourcing.quote'].invoke(scope, task(), {'actionId': 'quote', 'jobId': ident})
    assert not calls


@pytest.mark.asyncio
async def test_owned_existing_human_observation_retains_real_wait(context):
    _, jobs, _, tools, scope, calls, _ = context
    ident = await create(context, 'human_work')
    await jobs.observe_existing(OWNER, ident, 'existing-bounty', 'operator-link')
    observed = await tools['human_work.observe'].invoke(scope, task('human_work'), {'actionId': 'observe', 'jobId': ident})
    details = observed.observations[0].details
    assert details['status'] == 'waiting' and details['nextPollAt'] > details['observedAt']
    assert details['observation']['state'] == 'unknown' and details['providerClaimOnly']
    assert observed.blockers and len(calls) == 1  # Existing cooldown prevents another provider poll.


def test_registration_requires_explicit_scoped_readonly_installation(context):
    store, jobs, _, _, _, _, _ = context
    args = dict(capability=Capability.SOURCING, provider='digikey', provider_version='product-information-v4',
                installation_hash='a'*64, qualified=True)
    with pytest.raises(ValueError, match='read-only'):
        register_external_jobs(CapabilityRegistry(), jobs, GraphRepository(store), **args, operations=('purchase',))
    with pytest.raises(DomainError, match='No scoped'):
        register_external_jobs(CapabilityRegistry(), ExternalJobs(store), GraphRepository(store), **args)
    jobs.providers[('tenant', 'owner', 'digikey')].effect_capable = True
    with pytest.raises(DomainError, match='read-only'):
        register_external_jobs(CapabilityRegistry(), jobs, GraphRepository(store), **args)


@pytest.mark.asyncio
async def test_provider_failure_never_becomes_successful_tool_evidence(context):
    _, jobs, _, tools, scope, _, _ = context
    ident = await create(context)
    async def fail(_):
        raise RuntimeError('PRIVATE_PROVIDER_DIAGNOSTIC')
    jobs.providers[('tenant', 'owner', 'digikey')].quote = fail
    with pytest.raises(DomainError) as error:
        await tools['sourcing.quote'].invoke(scope, task(), {'actionId': 'fail', 'jobId': ident})
    assert error.value.code == 'provider_error'
    assert 'PRIVATE_PROVIDER_DIAGNOSTIC' not in str(error.value)
    assert 'quote' not in jobs.get(OWNER, ident)['preparations']


@pytest.mark.asyncio
async def test_cancelled_objective_cannot_issue_external_lookup(context):
    store, _, _, tools, scope, calls, _ = context
    ident = await create(context)
    with store.transaction() as session:
        session.get(Objective, scope.run_id).status = 'cancelled'
    with pytest.raises(DomainError, match='no longer active'):
        await tools['sourcing.quote'].invoke(scope, task(), {'actionId': 'after-cancel', 'jobId': ident})
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('state,check', [('failed', 'FAIL'), ('cancelled', 'UNKNOWN'), ('expired', 'UNKNOWN')])
async def test_unsuccessful_external_work_never_yields_pass(context, state, check):
    from kleos.capabilities.external import ExternalJob
    store, _, _, tools, scope, calls, _ = context
    ident = await create(context)
    with store.transaction() as session:
        session.get(ExternalJob, ident).status = state
    result = await tools['sourcing.observe'].invoke(scope, task(), {'actionId': 'observe', 'jobId': ident})
    assert result.observations[0].status == check and result.blockers
    assert result.observations[0].details['status'] == state and not calls
