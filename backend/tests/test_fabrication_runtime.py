"""Runtime composition and worker lifecycle with no Docker/provider/printer calls."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_slicing import prepared  # noqa: F401

from kleos.access.service import AccessService, AccessSettings
from kleos.api import create_app
from kleos.api.services import ApplicationServices
from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.fabrication import BambuConnections, slicing
from kleos.contracts.models import DomainError
from kleos.infrastructure.fabrication import FabricationRuntime, build_fabrication
from kleos.projects.store import ProjectStore

REPORT = Path(slicing.__file__).parent / 'qualification/slicer-qualified-2026-11-02.json'
IMAGE = json.loads(REPORT.read_text())['image']


class Docker:
    instances = []
    def __init__(self, *, socket):
        self.socket, self.closed = socket, 0
        self.instances.append(self)
        self.client = object()
    async def close(self):
        self.closed += 1


@pytest.fixture
def composition(tmp_path):
    Docker.instances.clear()
    store = ProjectStore(f"sqlite:///{tmp_path / 'runtime.db'}")
    store.initialize_for_tests()
    access = AccessService(store, AccessSettings(secret_key='x'*32, environment='test', development_no_login=True))
    actor = access.authenticate({}, {}, 'GET')
    project = store.create(actor, 'Prepare replacement shell', 'create')['projectId']
    settings = SimpleNamespace(environment='test', docker_socket='/fixture/no-network.sock',
        slicer_installation=None, slicer_control_root=tmp_path / 'slicing', bambu_local_enabled=False,
        frontend_origin='http://localhost:5180', preview_origin='http://localhost:8004')
    artifacts = ArtifactService(store, BlobStore(tmp_path / 'blobs'))
    connections = BambuConnections(enabled=False)
    yield settings, store, artifacts, access, connections, actor, project
    store.engine.dispose()


def build(composition):
    return build_fabrication(*composition[:5], docker_factory=Docker)


def enable(composition):
    composition[0].slicer_installation = {'image': IMAGE, 'report': str(REPORT), 'dockerDesktop': True}


@pytest.mark.asyncio
async def test_disabled_runtime_opens_no_client_and_serves_empty_views(composition):
    runtime = build(composition)
    assert not runtime.enabled and not Docker.instances
    assert runtime.bambu.preparations is runtime.preparations
    with TestClient(create_app(ApplicationServices(composition[1], composition[3],
                         fabrication=runtime.facade, bambu=runtime.bambu))) as client:
        result = client.get(f'/api/v1/projects/{composition[6]}/print-preparations')
        assert result.status_code == 200 and result.json() == {'preparations': []}
        catalog = client.get('/api/v1/printers/bambu/catalog').json()
        assert catalog['profiles'] == [] and not catalog['localPrintingEnabled']
    with pytest.raises(DomainError, match='No qualified'):
        await runtime.cycle()
    await runtime.close()


@pytest.mark.asyncio
async def test_qualified_factory_wires_one_client_and_profiles_without_enabling_print(composition):
    enable(composition)
    runtime = build(composition)
    assert runtime.enabled and len(Docker.instances) == 1
    assert runtime.preparations.settings.image == IMAGE
    assert runtime.facade.bambu is runtime.bambu
    assert runtime.preparations.driver.docker is Docker.instances[0]
    catalog = runtime.bambu.query(composition[5], 'bambu_catalog', {})
    assert {p['printerModelId'] for p in catalog['profiles']} == {'N1', 'N2S', 'C12'}
    assert all(p['automaticPreparation'] and p['qualified'] for p in catalog['profiles'])
    assert not catalog['localPrintingEnabled'] and not catalog['directPrintModelIds']
    await runtime.close()
    await runtime.close()
    assert Docker.instances[0].closed == 1
    with pytest.raises(RuntimeError, match='closed'):
        await runtime.cycle()


@pytest.mark.parametrize('change', [{'image': 'sha256:' + 'a'*64}, {'report': '/private/unavailable/report'},
                                   {'dockerDesktop': False}, {'qualified': True}])
def test_invalid_installation_fails_before_docker_without_exposing_values(composition, change):
    enable(composition)
    composition[0].slicer_installation.update(change)
    with pytest.raises(DomainError) as error:
        build(composition)
    assert error.value.code == 'tool_unqualified'
    assert 'private' not in str(error.value) and not Docker.instances


def test_local_report_cannot_enable_production(composition):
    enable(composition)
    composition[0].environment = 'production'
    with pytest.raises(DomainError, match='local development only'):
        build_fabrication(*composition[:5])
    assert not Docker.instances


@pytest.mark.asyncio
async def test_worker_orders_recovery_before_claim_and_preserves_active_jobs_on_stop():
    stop = asyncio.Event()
    events = []
    class Preparations:
        settings, driver = object(), object()
        async def reconcile(self):
            events.append('reconcile')
            return 2
        async def process_next(self, *, max_running):
            assert max_running == 1
            events.append('claim')
            stop.set()
            return True
    docker = Docker(socket='fixture')
    runtime = FabricationRuntime(Preparations(), object(), object(), docker)
    await runtime.run(stop, poll_seconds=.1)
    assert events == ['reconcile', 'claim'] and docker.closed == 1


@pytest.mark.asyncio
async def test_worker_cancellation_closes_client_without_destroying_jobs():
    entered = asyncio.Event()
    class Preparations:
        settings, driver = object(), object()
        async def reconcile(self):
            entered.set()
            await asyncio.Event().wait()
    docker = Docker(socket='fixture')
    runtime = FabricationRuntime(Preparations(), object(), object(), docker)
    task = asyncio.create_task(runtime.run(poll_seconds=.1))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert docker.closed == 1


@pytest.mark.asyncio
async def test_worker_error_log_is_fixed_and_stop_is_respected(caplog):
    stop = asyncio.Event()
    class Preparations:
        settings, driver = object(), object()
        async def reconcile(self):
            stop.set()
            raise RuntimeError('PRIVATE_TRANSPORT_VALUE')
    docker = Docker(socket='fixture')
    await FabricationRuntime(Preparations(), object(), object(), docker).run(stop, poll_seconds=.1)
    assert 'durable jobs retained' in caplog.text and 'PRIVATE_TRANSPORT_VALUE' not in caplog.text
    assert docker.closed == 1


@pytest.mark.asyncio
async def test_global_worker_capacity_counts_unreconciled_containers(prepared):  # noqa: F811
    from test_slicing import ACTOR
    service, project, body, driver = prepared
    service.create(ACTOR, project, body, 'one')
    service.create(ACTOR, project, body, 'two')
    assert await service.process_next(max_running=1)
    assert not await service.process_next(max_running=1)
    await service.reconcile()
    # A finished container still occupies its resource slot until cleanup is confirmed.
    assert not await service.process_next(max_running=1)
    await service.reconcile()
    assert await service.process_next(max_running=1)
    assert len(driver.started) == 2 and len(driver.cleaned) == 1
