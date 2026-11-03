"""Fixed tile API and real durable queue with fake slicer transport; no hardware."""
import asyncio
import base64
import hashlib
import struct
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import httpx
import pytest
import test_bambu_api
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from test_bambu_api import BASE, contract_keys
from test_runtime import migrate, settings, test_persistence
from test_slicing import Driver

from kleos.access.service import OrganizationMember
from kleos.api import create_app
from kleos.api.app import TransportContext
from kleos.api.bambu_test_print import BambuTestPrintFacade, tile_stl
from kleos.api.services import ApplicationServices
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.fabrication.slicing import (
    BUNDLES,
    PrintPreparation,
    PrintPreparations,
    SlicingSettings,
)
from kleos.contracts.models import Actor, DomainError
from kleos.infrastructure.runtime import Runtime
from kleos.projects.store import Project

api = test_bambu_api.api


@pytest.fixture
def test_api(api, tmp_path):
    _, _, bambu, calls, store = api
    driver = Driver()
    jobs = PrintPreparations(store, BlobStore(tmp_path/'test-blobs'),
        SlicingSettings('sha256:'+'a'*64, tmp_path/'control', tuple(BUNDLES)), driver)
    bambu.preparations = jobs
    facade = BambuTestPrintFacade(bambu, jobs)
    app = create_app(ApplicationServices(store, bambu.access, bambu=bambu, bambu_test_print=facade))
    with TestClient(app, base_url='http://localhost:8000', client=('127.0.0.1', 54001)) as client:
        yield client, facade, jobs, driver, calls, store


def prepare(env, **changes):
    return env[0].post(BASE+'/local/test-prints', json={'profileId': 'a1-mini-pla-04', 'printerModelId': 'N1', **changes})


def test_fixed_fixture_exact_geometry_and_frozen_enqueue_contract(test_api):
    client, _, _, driver, calls, store = test_api
    raw = tile_stl()
    assert len(raw) == 684 and struct.unpack('<I', raw[80:84])[0] == 12
    points = [struct.unpack('<12fH', raw[start:start+50])[3:12] for start in range(84, len(raw), 50)]
    vertices = [tuple(p[i:i+3]) for p in points for i in (0, 3, 6)]
    assert [max(p[i] for p in vertices)-min(p[i] for p in vertices) for i in range(3)] == [10, 10, 2]
    result = prepare(test_api)
    assert result.status_code == 200, result.text
    body = result.json()
    contract_keys('BambuTestPrintView', body)
    contract_keys('BambuProfile', body['profile'])
    assert body['status'] == 'slicing' and body['sourceSha256'] == hashlib.sha256(raw).hexdigest()
    assert body['packageSha256'] is None and body['estimatedSeconds'] is None and not body['physicalAcceptance']
    assert prepare(test_api).json() == body
    with store.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Project)) == 1
        assert session.scalar(select(func.count()).select_from(PrintPreparation)) == 1
    endpoint = BASE+'/local/test-prints/'+body['id']
    source = client.get(endpoint+'/source')
    assert source.status_code == 200 and source.content == raw
    assert client.get(endpoint+'/plate').status_code == 409
    assert not driver.started and not calls
    profiles = client.get(BASE+'/catalog').json()['profiles']
    assert len(profiles) == 3 and all(p['testPreparation'] for p in profiles)
    assert client.get('/api/v1/projects').json()['projects'] == []


async def test_existing_worker_completes_and_download_survives_facade_restart(test_api):
    client, facade, jobs, driver, calls, store = test_api
    body = prepare(test_api).json()
    endpoint = BASE+'/local/test-prints/'+body['id']
    assert await jobs.process_next()
    assert driver.started[0][1] == tile_stl() and driver.started[0][2] == 'stl'
    expected = base64.b64decode(driver.results[driver.started[0][0]]['package'])
    assert await jobs.reconcile() == 1
    ready = client.get(endpoint).json()
    assert ready['status'] == 'ready' and ready['packageSha256'] == hashlib.sha256(expected).hexdigest()
    assert not ready['physicalAcceptance'] and not calls
    restarted = BambuTestPrintFacade(facade.bambu, jobs)
    with TestClient(create_app(ApplicationServices(store, facade.bambu.access, bambu=facade.bambu, bambu_test_print=restarted)),
            base_url='http://localhost:8000', client=('127.0.0.1', 54002)) as again:
        assert again.get(endpoint).json() == ready
        downloaded = again.get(endpoint+'/plate')
        assert downloaded.status_code == 200 and downloaded.content == expected
        assert downloaded.headers['content-type'] == 'application/zip'
    assert prepare(test_api).json() == ready and len(driver.started) == 1


@pytest.mark.parametrize('changes', [{'printerModelId': 'C12'}, {'profileId': 'unqualified'},
    {'image': 'attacker:latest'}, {'source': 'arbitrary geometry'}, {'authorizePrint': True}])
def test_client_cannot_override_geometry_profile_or_dispatch_policy(test_api, changes):
    assert prepare(test_api, **changes).status_code in {422, 501}
    assert not test_api[3].started and not test_api[4]


@pytest.mark.parametrize('header', [{'X-Forwarded-For': '127.0.0.1'}, {'Host': 'evil.example'},
    {'Origin': 'https://preview.invalid'}])
def test_loopback_authority_applies_to_create_status_and_download(test_api, header):
    client = test_api[0]
    body = prepare(test_api).json()
    endpoint = BASE+'/local/test-prints/'+body['id']
    assert client.post(BASE+'/local/test-prints', json={'profileId': 'a1-mini-pla-04', 'printerModelId': 'N1'}, headers=header).status_code == 403
    for suffix in ('', '/source', '/plate'):
        assert client.get(endpoint+suffix, headers=header).status_code == 403


def test_revoked_admin_blocks_receipt_replay_and_download(test_api):
    client, _, _, _, calls, store = test_api
    body = prepare(test_api).json()
    with store.transaction() as session:
        session.get(OrganizationMember, ('development', 'development')).role = 'member'
    assert prepare(test_api).status_code == 403
    assert client.get(BASE+'/local/test-prints/'+body['id']+'/source').status_code == 403
    assert not calls


async def test_cancelled_and_uncertain_jobs_stay_failed_without_silent_retry(test_api):
    client, facade, jobs, driver, _, store = test_api
    body = prepare(test_api).json()
    with store.transaction() as session:
        row = session.get(PrintPreparation, body['id'])
        row.status = 'cancelled'
    assert not await jobs.process_next()
    assert prepare(test_api).json()['status'] == 'failed'
    actor = facade.bambu.access.authenticate({}, {}, 'GET')
    assert client.get('/api/v1/projects/'+facade._project_id(actor)+'/workspace').status_code == 404
    retry = client.post(BASE+'/local/test-prints', json={'profileId': 'a1-mini-pla-04', 'printerModelId': 'N1'},
        headers={'Idempotency-Key': 'explicit-new-attempt'}).json()
    async def uncertain(*args):
        raise RuntimeError('Private native detail')
    driver.start = uncertain
    assert await jobs.process_next()
    failed = client.get(BASE+'/local/test-prints/'+retry['id']).json()
    assert failed['status'] == 'failed' and 'Private' not in str(failed)
    with store.transaction() as session:
        assert session.get(PrintPreparation, retry['id']).container_id is not None
    assert not await jobs.process_next()


def test_unconfigured_slicer_and_tampered_source_fail_closed(test_api):
    client, _, jobs, _, _, store = test_api
    body = prepare(test_api).json()
    jobs.settings = None
    assert prepare(test_api).status_code == 501
    assert client.get(BASE+'/local/test-prints/'+body['id']+'/source').status_code == 200
    with store.transaction() as session:
        project = session.get(Project, session.get(PrintPreparation, body['id']).project_id)
        project.data = {**project.data, 'sourceManifest': {}}
    assert client.get(BASE+'/local/test-prints/'+body['id']+'/source').status_code == 409


def test_concurrent_distinct_requests_share_one_fixture_project(test_api, monkeypatch):
    _, facade, _, _, _, store = test_api
    actor = facade.bambu.access.authenticate({}, {}, 'GET')
    transport = {'_transport': TransportContext('127.0.0.1', 'localhost:8000', None, False, 'http')}
    original, barrier, lock, calls = store._command, Barrier(2), Lock(), []
    def command(actor, key, body, operation):
        with lock:
            calls.append(key)
            first = len(calls) <= 2
        def synchronized(session):
            if first:
                barrier.wait(timeout=5)
            return operation(session)
        return original(actor, key, body, synchronized)
    monkeypatch.setattr(store, '_command', command)
    def create(index):
        return asyncio.run(facade.action(actor, 'bambu_prepare_test', transport,
            {'profileId': 'a1-mini-pla-04', 'printerModelId': 'N1'}, 'concurrent-'+str(index)))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, (1, 2)))
    assert len({item['id'] for item in results}) == 2
    with store.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Project)) == 1
        assert session.scalar(select(func.count()).select_from(PrintPreparation)) == 2


async def test_other_principal_with_own_fixture_cannot_read_or_download(test_api):
    _, facade, _, _, _, store = test_api
    victim = prepare(test_api).json()
    other = Actor(tenant_id='development', user_id='other-admin')
    with store.transaction() as session:
        session.add(OrganizationMember(tenant_id=other.tenant_id, user_id=other.user_id, role='admin'))
    params = {'_transport': TransportContext('127.0.0.1', 'localhost:8000', None, False, 'http')}
    own = await facade.action(other, 'bambu_prepare_test', params, {'profileId': 'a1-mini-pla-04', 'printerModelId': 'N1'})
    assert own['id'] != victim['id']
    for operation in ('bambu_test_print', 'bambu_test_source', 'bambu_test_plate'):
        with pytest.raises(DomainError) as error:
            method = facade.query if operation == 'bambu_test_print' else facade.content
            method(other, operation, {**params, 'ident': victim['id']})
        assert error.value.code == 'not_found'


async def test_actual_runtime_routes_worker_and_private_downloads(tmp_path):
    configured = settings(tmp_path, bambu_local_enabled=True,
        printer_encryption_key=SecretStr(base64.b64encode(b'k'*32).decode()))
    runtime = Runtime(configured, persistence_factory=test_persistence, worker_locks=object())
    migrate(runtime)
    # Explicit test transport; do not initialize Docker or run native software.
    jobs = runtime.fabrication_runtime.preparations
    jobs.settings = SlicingSettings('sha256:'+'a'*64, tmp_path/'fixture-control', tuple(BUNDLES))
    jobs.driver = Driver()
    jobs.capacity = None
    async with runtime.start():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app, client=('127.0.0.1', 54500)),
                base_url='http://localhost:8000') as client:
            response = await client.post(BASE+'/local/test-prints', json={'profileId': 'a1-mini-pla-04', 'printerModelId': 'N1'})
            assert response.status_code == 200, response.text
            endpoint = BASE+'/local/test-prints/'+response.json()['id']
            assert (await client.get(endpoint+'/source')).content == tile_stl()
            assert await jobs.process_next()
            assert await jobs.reconcile() == 1
            ready = (await client.get(endpoint)).json()
            assert ready['status'] == 'ready'
            downloaded = await client.get(endpoint+'/plate')
            assert hashlib.sha256(downloaded.content).hexdigest() == ready['packageSha256']
            assert (await client.get('/api/v1/projects')).json()['projects'] == []
