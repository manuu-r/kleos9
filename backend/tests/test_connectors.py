"""Workbench connector DTO, installed-tool projection and account/local boundaries."""
import json
from types import SimpleNamespace

import httpx
import pytest
from test_external_runtime import build, context, install
from test_runtime import installed_registry, migrate, settings, test_persistence

from kleos.access.service import OrganizationMember
from kleos.api.app import create_app
from kleos.api.bambu import BambuFacade
from kleos.api.connectors import Connectors
from kleos.api.services import ApplicationServices
from kleos.capabilities.fabrication.models import ConnectionState
from kleos.capabilities.registry import ToolManifest
from kleos.capabilities.services import SearchInput
from kleos.contracts.models import Actor, DomainError
from kleos.infrastructure.runtime import Runtime

# Imported fixture deliberately creates only synthetic reviewed reports and mocked HTTP.
assert context


def add_member(store, actor, role='member'):
    with store.transaction() as session:
        session.add(OrganizationMember(tenant_id=actor.tenant_id, user_id=actor.user_id, role=role))


async def test_unconfigured_actual_runtime_connectors_returns_empty_not_501(tmp_path):
    runtime = Runtime(settings(tmp_path, _env_file=None, model_catalog=[], meta_api_key=None,
        modal_api_key=None), persistence_factory=test_persistence, worker_locks=object())
    migrate(runtime)
    async with runtime.start():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url='http://localhost') as client:
            response = await client.get('/api/v1/connectors')
    assert response.status_code == 200
    rows = response.json()['connectors']
    assert [row['id'] for row in rows] == ['kleos-research']
    assert rows[0]['manifest']['capabilities'] == ['research.context_read', 'research.source_search']


async def test_external_connectors_are_exact_principal_readonly_and_redacted(context):
    install(context)
    install(context, 'rentahuman')
    external = build(context)
    _, store, access, registry, _, actor, _, requests, *_ = context
    facade = Connectors(access, registry, external=external)
    rows = facade.query(actor, {})['connectors']
    assert {row['id'] for row in rows} == {'digikey', 'rentahuman'}
    for row in rows:
        assert row['enabled'] is True
        assert row['manifest']['mode'] == 'read_only'
        assert row['manifest']['transport'] == 'rest'
        assert row['manifest']['endpoint'] is None
        assert row['manifest']['supportsCancellation'] is False
        assert any('not been verified' in item for item in row['manifest']['disclosure'])
    human = next(row for row in rows if row['id'] == 'rentahuman')
    assert all(cap.startswith('human.') for cap in human['manifest']['capabilities'])
    encoded = json.dumps(rows)
    for forbidden in ['PRIVATE_', '_ref', 'installationHash', 'qualificationReference',
                      'api_key', 'access_token', 'accountId', 'tenantId', 'userId']:
        assert forbidden not in encoded
    for other in [Actor(tenant_id=actor.tenant_id, user_id='other'),
                  Actor(tenant_id='other', user_id=actor.user_id)]:
        add_member(store, other)
        assert facade.query(other, {}) == {'connectors': []}
    with pytest.raises(DomainError) as failure:
        facade.query(Actor(tenant_id='foreign', user_id='outsider'), {})
    assert failure.value.status == 403
    assert requests == []
    principal = (actor.tenant_id, actor.user_id, 'digikey')
    removed = external.jobs.providers.pop(principal)
    assert [row['id'] for row in facade.query(actor, {})['connectors']] == ['rentahuman']
    external.jobs.providers[principal] = removed
    await external.close()


async def test_revoked_tools_and_effectful_operations_are_not_advertised(context):
    install(context)
    external = build(context)
    _, _, access, registry, _, actor, *_ = context
    facade = Connectors(access, registry, external=external)
    assert facade.query(actor, {})['connectors']
    for entry in registry.catalog():
        registry.revoke(entry['key'])
    async def unavailable(*args):
        raise AssertionError('Listing cannot invoke a tool')
    registry.register(ToolManifest(id='sourcing.purchase', version='1.0.0', capability='sourcing',
        operation='purchase', provider='digikey', provider_version=external.pins[0]['providerVersion'],
        installation_hash='a'*64, transport='http', effect='external_action', qualified=True,
        description='Must not expose purchasing'), SearchInput, unavailable, unavailable)
    assert facade.query(actor, {}) == {'connectors': []}
    await external.close()


async def test_tool_rows_match_flutter_manifest_and_slicer_is_actual_installation(context):
    _, _, access, _, _, actor, *_ = context
    registry = installed_registry()
    async def unavailable(*args):
        raise AssertionError('Listing cannot invoke native work')
    for operation in ['build', 'read', 'patch']:
        registry.register(ToolManifest(id=f'cad.{operation}', version='1.0.0', capability='cad',
            operation=operation, provider='native', provider_version='fixture-1',
            installation_hash='a'*64, transport='native', qualified=True,
            description='Private qualification path must not leak /private/fixture'),
            SearchInput, unavailable, unavailable)
    fabrication = SimpleNamespace(enabled=False)
    facade = Connectors(access, registry, fabrication=fabrication)
    rows = facade.query(actor, {})['connectors']
    assert len(rows) == 2
    tool = next(row for row in rows if row['id'] == 'native-cad')
    assert tool['manifest']['capabilities'] == ['mechanics.build', 'mechanics.patch', 'mechanics.read']
    assert tool['manifest']['transport'] == 'builtin'
    assert '/private/' not in json.dumps(rows)
    fabrication.enabled = True
    slicer = next(row for row in facade.query(actor, {})['connectors'] if row['id'] == 'orcaslicer')
    assert slicer['manifest']['capabilities'] == ['engineering.fabrication.prepare']
    assert 'blocked' in ' '.join(slicer['manifest']['disclosure'])
    assert set(slicer) == {'id', 'name', 'enabled', 'manifest'}
    assert set(slicer['manifest']) == {'name', 'version', 'transport', 'capabilities', 'mode',
        'endpoint', 'supportsIdempotency', 'supportsCancellation', 'disclosure'}


class Printer:
    enabled = True

    def __init__(self):
        self.reads = []
        self.view = ConnectionState(enabled=True, paired=True, connected=True, status='connected',
            address='192.168.0.99', serial='PRIVATE-SERIAL', model_id='a1-mini',
            observation={'credential': 'PRIVATE-SECRET', 'kind': 'bambu'})

    def state(self, local):
        self.reads.append(local.actor)
        return self.view


def printer_app(context):
    _, store, access, registry, _, actor, *_ = context
    printer = Printer()
    bambu = BambuFacade(access, printer, SimpleNamespace(environment='test', bambu_local_enabled=True,
        frontend_origin='http://localhost:5180', preview_origin='http://localhost:8004'))
    facade = Connectors(access, registry, bambu=bambu)
    services = ApplicationServices(store, access, connectors=facade)
    return create_app(services), printer, actor, facade


async def test_bambu_status_uses_actual_request_transport_and_allowlisted_status(context):
    app, printer, actor, _ = printer_app(context)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)),
                                 base_url='http://localhost') as client:
        response = await client.get('/api/v1/connectors', headers={'Origin': 'http://localhost:5180'})
        row = response.json()['connectors'][0]
        assert response.status_code == 200 and printer.reads == [actor]
        assert row['enabled'] is True and row['manifest']['mode'] == 'read_only'
        assert row['telemetry'] == {'kind': 'bambu', 'connected': True, 'stale': False,
            'profile': {'modelId': 'a1-mini', 'physicalAcceptance': False}}
        assert 'PRIVATE' not in response.text and '192.168' not in response.text
        printer.view = printer.view.model_copy(update={'connected': False, 'status': 'stale'})
        stale = (await client.get('/api/v1/connectors')).json()['connectors'][0]
        assert stale['enabled'] is True and stale['telemetry']['stale'] is True
        assert stale['telemetry']['connected'] is False


@pytest.mark.parametrize('headers', [
    {'X-Forwarded-For': '127.0.0.1'}, {'Origin': 'http://localhost:8004'}, {'Host': 'remote.example'},
])
async def test_bambu_nonlocal_or_preview_requests_never_read_printer(context, headers):
    app, printer, _, _ = printer_app(context)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)),
                                 base_url='http://localhost') as client:
        response = await client.get('/api/v1/connectors?_transport=forged', headers=headers)
    assert response.status_code == 200 and response.json() == {'connectors': []}
    assert printer.reads == []


async def test_bambu_nonadmin_never_reads_paired_metadata(context):
    from kleos.api.app import TransportContext
    _, store, _, _, _, actor, *_ = context
    _, printer, _, facade = printer_app(context)
    other = Actor(tenant_id=actor.tenant_id, user_id='viewer')
    add_member(store, other, 'viewer')
    response = facade.query(other, {'_transport': TransportContext(client_host='127.0.0.1',
        host='localhost', origin='http://localhost:5180', forwarded=False, scheme='http')})
    assert response == {'connectors': []} and printer.reads == []
