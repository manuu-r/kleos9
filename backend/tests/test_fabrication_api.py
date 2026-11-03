"""Frozen preparation contract plus scope, idempotency and physical-policy checks."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_slicing import Driver, cube

from kleos.access.service import AccessService, AccessSettings
from kleos.api import create_app
from kleos.api.fabrication import ACTIONS, QUERIES, FabricationFacade
from kleos.api.services import ApplicationServices
from kleos.artifacts.service import Artifact
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.fabrication.slicing import BUNDLES, PrintPreparations, SlicingSettings
from kleos.projects.store import ProjectStore

BASE = '/api/v1'


class Services(ApplicationServices):
    # The integrator owns production composition; this exercises the actual HTTP router.
    def __init__(self, *args, fabrication, **kwargs):
        super().__init__(*args, **kwargs)
        self.fabrication = fabrication
    def query(self, actor, operation, params):
        if operation in QUERIES:
            return self.fabrication.query(actor, operation, params)
        return super().query(actor, operation, params)
    def action(self, actor, operation, params, body, idempotency_key):
        if operation in ACTIONS:
            return self.fabrication.action(actor, operation, params, body, idempotency_key)
        return super().action(actor, operation, params, body, idempotency_key)


@pytest.fixture
def api(tmp_path):
    store = ProjectStore(f"sqlite:///{tmp_path / 'api.db'}")
    store.initialize_for_tests()
    access = AccessService(store, AccessSettings(secret_key='x'*32, environment='test', development_no_login=True))
    actor = access.authenticate({}, {}, 'GET')
    project = store.create(actor, 'Replacement enclosure', 'create')['projectId']
    other = store.create(actor, 'Other project', 'other')['projectId']
    blobs = BlobStore(tmp_path / 'blobs')
    source = cube()
    with store.transaction() as session:
        from kleos.projects.store import Project
        record = session.get(Project, project)
        record.data = {**record.data, 'sourceManifest': {'part.obj': 'source'}}
        session.add(Artifact(id='source', project_id=project, revision=1, name='part.obj', sha256=blobs.put(source),
            size=len(source), media_type='model/obj', status='input', lane='cad', details={}))
    jobs = PrintPreparations(store, blobs, SlicingSettings('sha256:'+'a'*64, tmp_path / 'control', tuple(BUNDLES)), Driver())
    facade = FabricationFacade(jobs)
    with TestClient(create_app(Services(store, access, fabrication=facade))) as client:
        yield client, jobs, actor, project, other
    store.engine.dispose()


def create(api, **changes):
    response = api[0].post(f'{BASE}/projects/{api[3]}/print-preparations', headers={'Idempotency-Key': 'prepare'},
        json={'artifactId': 'source', 'profileId': 'a1-mini-pla-04', 'expectedEngineeringRevision': 1, **changes})
    return response


def check_schema(name, value):
    schema = json.loads((Path(__file__).parents[1] / 'contracts/openapi.json').read_text())['components']['schemas'][name]
    assert set(schema.get('required', [])) <= set(value)
    assert set(value) <= set(schema['properties'])


def test_contract_idempotency_scope_and_listing(api):
    response = create(api)
    assert response.status_code == 200, response.text
    view = response.json()
    check_schema('PrintPreparationView', view)
    check_schema('BambuProfile', view['profile'])
    assert view['status'] == 'queued' and view['physicalAcceptance'] is False
    assert create(api).json() == view
    assert create(api, profileId='p1s-pla-04').status_code == 409
    assert api[0].get(f'{BASE}/projects/{api[3]}/print-preparations').json() == {'preparations': [view]}
    assert api[0].get(f'{BASE}/projects/{api[4]}/print-preparations/{view["id"]}').status_code == 404
    assert api[0].delete(f'{BASE}/projects/{api[4]}/print-preparations/{view["id"]}').status_code == 404


@pytest.mark.asyncio
async def test_native_completion_and_dispatch_never_grant_physical_authority(api):
    client, jobs, _, project, _ = api
    view = create(api).json()
    endpoint = f'{BASE}/projects/{project}/print-preparations/{view["id"]}'
    spoof = client.post(endpoint + '/complete', headers={'Idempotency-Key': 'complete'},
        json={'artifactId': 'source', 'sha256': 'a'*64, 'bundleSha256': 'b'*64, 'measurements': {'physicalAcceptance': True}})
    assert spoof.status_code == 409
    await jobs.process_next()
    await jobs.reconcile()
    ready = client.get(endpoint).json()
    assert ready['status'] == 'ready_for_operator' and ready['automaticNativePrepared']
    response = client.post(f'{BASE}/projects/{project}/print-dispatches', headers={'Idempotency-Key': 'dispatch'},
        json={'preparationId': view['id'], 'preparedSha256': ready['preparedSha256'], 'expectedEngineeringRevision': 1,
              'authorizePrint': True, 'externalSpoolConfirmed': True})
    assert response.status_code == 200, response.text
    dispatch = response.json()
    check_schema('BambuDispatchView', dispatch)
    assert dispatch['status'] == 'blocked' and not dispatch['physicalAcceptance']
    url = f'{BASE}/projects/{project}/print-dispatches/{dispatch["id"]}'
    assert client.post(url + '/send', json={}).status_code == 403
    assert client.post(url + '/reconcile', json={}).json() == dispatch
    assert client.post(url + '/cancel', json={}).json() == dispatch


@pytest.mark.parametrize('changes', [{'releaseId': 'unaccepted'}, {'image': 'evil:latest'},
                                    {'profileId': 'unqualified'}, {'expectedEngineeringRevision': 3}])
def test_caller_cannot_override_scope_or_tool_installation(api, changes):
    assert create(api, **changes).status_code in {409, 422, 501}


def test_unconfigured_service_explicit_and_cancellation_fenced(api):
    view = create(api).json()
    response = api[0].delete(f'{BASE}/projects/{api[3]}/print-preparations/{view["id"]}')
    assert response.json()['status'] == 'cancelled'
    api[1].settings = None
    response = api[0].post(f'{BASE}/projects/{api[3]}/print-preparations', headers={'Idempotency-Key': 'disabled'},
        json={'artifactId': 'source', 'profileId': 'a1-mini-pla-04', 'expectedEngineeringRevision': 1})
    assert response.status_code == 501
