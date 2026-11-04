"""HTTP boundary checks with explicit fake services, never paid providers."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from kleos.api import AuthResult, Content, Cookie, create_app
from kleos.api.app import event_stream
from kleos.contracts.models import Actor, DomainError


class FakeServices:
    def __init__(self):
        self.calls = []
        self.receipts = {}

    def authenticate(self, headers, cookies, method):
        if headers.get('authorization') != 'Bearer fixture':
            raise DomainError('Sign in', 'unauthorized', 401)
        return Actor(tenant_id=headers.get('x-organization-id', 'org'), user_id='user')

    def auth(self, operation, body, headers, cookies):
        if operation == 'config':
            return AuthResult({'loginRequired': True})
        if operation == 'login':
            return AuthResult({'subject': 'user'}, cookies=(Cookie('session', 'test-session'),))
        raise DomainError('Unsupported fixture operation', 'unsupported', 501)

    def query(self, actor, operation, params):
        self.calls.append((operation, params, actor.tenant_id))
        if actor.tenant_id != 'org' or params.get('projectId', 'p') != 'p':
            raise DomainError('Not found', 'not_found', 404)
        if operation == 'events':
            return {'events': [{'id': 'e1', 'sequence': 1, 'type': 'task.updated', 'data': {},
                                'traceId': 't', 'createdAt': '2026-11-02T00:00:00Z'}]}
        if operation == 'workspace':
            return {'project': {'id': 'p'}, 'revision': 1, 'eventCursor': 1,
                    'runs': [], 'artifacts': [], 'checks': [], 'trainingOptedIn': False}
        raise DomainError('Capability unavailable', 'unsupported', 501)

    def command(self, actor, body, idempotency_key):
        self.calls.append(('command', body, actor.tenant_id))
        self.receipts.setdefault(idempotency_key, {'commandId': 'c', 'projectId': 'p', 'revision': 1, 'runId': 'r'})
        return self.receipts[idempotency_key]

    async def action(self, actor, operation, params, body, idempotency_key):
        self.calls.append((operation, params, actor.tenant_id, body, idempotency_key))
        if operation == 'create_objective':
            return {'objectiveId': 'o', 'projectId': params['projectId'], 'status': 'queued'}
        raise DomainError('Capability unavailable', 'unsupported', 501)

    def content(self, actor, operation, params):
        if actor.tenant_id != 'org':
            raise DomainError('Not found', 'not_found', 404)
        self.calls.append((operation, params, actor.tenant_id))
        return Content(b'fixture-bytes', filename='part.stl', media_type='model/stl')


@pytest.fixture
def api():
    services = FakeServices()
    return TestClient(create_app(services)), services


AUTH = {'Authorization': 'Bearer fixture'}


def test_command_receipt_shape_idempotency_and_expected_revision_forwarding(api):
    client, services = api
    headers = {**AUTH, 'Idempotency-Key': 'same'}
    body = {'type': 'project.message', 'payload': {'projectId': 'p', 'text': 'Make bracket'}, 'expectedRevision': 7}
    first = client.post('/api/v1/commands', json=body, headers=headers)
    second = client.post('/api/v1/commands', json=body, headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json() == {'commandId': 'c', 'projectId': 'p', 'revision': 1, 'runId': 'r'}
    assert services.calls[0][1] == body
    assert len(services.receipts) == 1


@pytest.mark.parametrize('body', [{}, {'type': ''}, {'type': 'project.message', 'expectedRevision': 0}])
def test_invalid_command_never_reaches_services(api, body):
    client, services = api
    response = client.post('/api/v1/commands', json=body, headers={**AUTH, 'Idempotency-Key': 'key'})
    assert response.status_code == 422
    assert response.json()['code'] == 'invalid_request'
    assert not services.calls


def test_mutation_requires_idempotency_key(api):
    client, services = api
    response = client.post('/api/v1/commands', json={'type': 'project.create'}, headers=AUTH)
    assert response.status_code == 422 and not services.calls


def test_authentication_and_project_authorization_are_enforced(api):
    client, services = api
    assert client.get('/api/v1/projects/p/workspace').status_code == 401
    assert client.get('/api/v1/projects/other/workspace', headers=AUTH).status_code == 404
    assert client.get('/api/v1/projects/p/workspace', headers={**AUTH, 'X-Organization-Id': 'other'}).status_code == 404
    response = client.get('/api/v1/projects/p/workspace?projectId=other&includeAgentState=true', headers=AUTH)
    assert response.status_code == 200
    assert services.calls[-1][1] == {'projectId': 'p', 'includeAgentState': 'true'}


def test_auth_cookie_is_http_only_and_secure(api):
    response = api[0].post('/api/v1/auth/login', json={'email': 'example@example.invalid', 'password': 'fixture'})
    assert response.status_code == 200
    assert 'HttpOnly' in response.headers['set-cookie'] and 'Secure' in response.headers['set-cookie']


def test_objective_routes_to_async_service_without_fixed_stage(api):
    client, services = api
    body = {'objective': 'Design just the missing bracket', 'expectedRevision': 1}
    response = client.post('/api/v1/projects/p/objectives', headers={**AUTH, 'Idempotency-Key': 'objective'}, json=body)
    assert response.status_code == 200 and response.json()['objectiveId'] == 'o'
    assert services.calls[-1] == ('create_objective', {'projectId': 'p'}, 'org', body, 'objective')


def test_unavailable_capabilities_are_explicit_not_successful_fixtures(api):
    response = api[0].post('/api/v1/printers/bambu/local/discover', headers=AUTH, json={})
    assert response.status_code == 501 and response.json()['code'] == 'unsupported'


def test_private_binary_content_is_authorized_and_not_cached(api):
    client, services = api
    assert client.get('/api/v1/artifacts/a/content').status_code == 401
    response = client.get('/api/v1/artifacts/a/content', headers=AUTH)
    assert response.content == b'fixture-bytes' and response.headers['content-type'] == 'model/stl'
    assert response.headers['cache-control'] == 'private, no-store'
    assert services.calls[-1] == ('artifact_content', {'artifactId': 'a'}, 'org')


def test_bad_json_is_a_bounded_public_error(api):
    response = api[0].post('/api/v1/printers/bambu/local/connect', headers=AUTH, content='secret invalid body')
    assert response.status_code == 422 and 'secret' not in response.text


@pytest.mark.asyncio
async def test_event_stream_resumes_cursor_and_rechecks_authority():
    class Request:
        headers = Headers(AUTH)
        cookies = {}
        query_params = {}
        path_params = {'project_id': 'p'}
        async def is_disconnected(self):
            return False
    services = FakeServices()
    iterator = event_stream(Request(), services, 0, 0)
    event = await anext(iterator)
    assert event.startswith('id: 1\ndata: ') and json.loads(event.split('data: ')[1])['sequence'] == 1
    assert await anext(iterator) == ': heartbeat\n\n'
    services.authenticate = lambda *_: (_ for _ in ()).throw(DomainError('Expired', 'unauthorized', 401))
    assert await anext(iterator) == 'event: auth_expired\ndata: {}\n\n'
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)


def test_every_frozen_frontend_http_operation_has_a_route(api):
    inventory = Path(__file__).parent / 'contracts/frontend-obligations.json'
    obligations = json.loads(inventory.read_text())['operations']
    routes = {(method, route.path) for route in api[0].app.routes for method in getattr(route, 'methods', [])}
    for operation in obligations:
        if operation['frontendSources']:
            assert (operation['method'], operation['path']) in routes


def test_service_validation_errors_do_not_echo_private_payloads():
    from pydantic import BaseModel

    class Input(BaseModel):
        port: int

    class BadInput(FakeServices):
        def action(self, *args):
            return Input.model_validate({'port': 'private-lan-access-code'})

    client = TestClient(create_app(BadInput()))
    response = client.post('/api/v1/printers/bambu/local/connect', headers=AUTH, json={})
    assert response.status_code == 422
    assert response.json() == {'message': 'Invalid request parameters', 'code': 'invalid_request'}
    assert 'private-lan-access-code' not in response.text
