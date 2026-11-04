"""HTTP contract adapter. Authority and side effects belong to injected services."""
import asyncio
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import Field, ValidationError

from kleos.contracts.models import Actor, DomainError, WireModel


class Command(WireModel):
    type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=1)


class CommandAccepted(WireModel):
    command_id: str
    project_id: str
    revision: int = Field(ge=1)
    run_id: str | None = None


@dataclass(frozen=True)
class Cookie:
    name: str
    value: str = ""
    delete: bool = False
    secure: bool = True
    httponly: bool = True
    samesite: str = "lax"
    max_age: int | None = None


@dataclass(frozen=True)
class AuthResult:
    body: dict[str, Any]
    cookies: tuple[Cookie, ...] = ()
    status: int = 200


@dataclass(frozen=True)
class Content:
    data: bytes
    media_type: str = "application/octet-stream"
    filename: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TransportContext:
    client_host: str | None
    host: str | None
    origin: str | None
    forwarded: bool
    scheme: str


class Services(Protocol):
    """Implementations must check tenant/project access for every scoped operation.

    ``authenticate`` validates session/token and CSRF for mutations. Query/action
    are explicit named operations, never arbitrary commands or user-supplied code.
    Services must raise DomainError for unsupported operations; never invent success.
    Sync and async implementations are supported by this HTTP-only adapter.
    """
    def authenticate(self, headers: Mapping[str, str], cookies: Mapping[str, str], method: str) -> Actor: ...
    def auth(self, operation: str, body: dict, headers: Mapping[str, str], cookies: Mapping[str, str]) -> AuthResult: ...
    def query(self, actor: Actor, operation: str, params: dict) -> dict: ...
    def command(self, actor: Actor, body: dict, idempotency_key: str) -> dict: ...
    def action(self, actor: Actor, operation: str, params: dict, body: dict, idempotency_key: str | None) -> dict: ...
    def content(self, actor: Actor, operation: str, params: dict) -> Content: ...


async def resolve(value):
    return await value if inspect.isawaitable(value) else value


def parameters(request: Request, *, transport=False) -> dict:
    """Path identifiers override query parameters so authorization cannot be confused."""
    result = dict(request.query_params)
    result.pop('_transport', None)
    for key, value in request.path_params.items():
        pieces = key.split('_')
        result[pieces[0] + ''.join(piece.title() for piece in pieces[1:])] = value
    if transport:
        result['_transport'] = TransportContext(
            client_host=request.client.host if request.client else None,
            host=request.headers.get('host'), origin=request.headers.get('origin'),
            forwarded=any(key.lower() == 'forwarded' or key.lower().startswith('x-forwarded-')
                          for key in request.headers), scheme=request.url.scheme,
        )
    return result


async def json_body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DomainError("Request body must be JSON", "invalid_request", 422) from exc
    if not isinstance(value, dict):
        raise DomainError("Request body must be an object", "invalid_request", 422)
    return value


def idempotency_key(request: Request, *, required: bool = False) -> str | None:
    key = request.headers.get('Idempotency-Key')
    if (required and not key) or (key is not None and (not key.strip() or len(key) > 128)):
        raise DomainError("Provide a bounded Idempotency-Key", "invalid_idempotency_key", 422)
    return key


# Explicit public surface. Values are stable service operation IDs, not workflow stages.
QUERIES = {
    '/projects': 'projects', '/projects/{project_id}/workspace': 'workspace',
    '/runs/{run_id}': 'run', '/model-catalog': 'model_catalog', '/engineering-tools': 'engineering_tools',
    '/connectors': 'connectors', '/projects/{project_id}/engineering-preview': 'engineering_preview',
    '/projects/{project_id}/circuit-simulation': 'circuit_simulation',
    '/projects/{project_id}/agents': 'agents', '/projects/{project_id}/external-jobs': 'external_jobs',
    '/projects/{project_id}/external-jobs/{job_id}': 'external_job',
    '/projects/{project_id}/external-jobs/{job_id}/events': 'external_job_events',
    '/artifacts/{artifact_id}/download': 'artifact_download',
    '/invocations/{invocation_id}/transcript': 'invocation_transcript', '/native-jobs/{job_id}/logs': 'native_logs',
    '/printers/bambu/catalog': 'bambu_catalog', '/printers/bambu/local': 'bambu_local',
    '/printers/bambu/local/test-prints/{ident}': 'bambu_test_print',
    '/projects/{project_id}/print-preparations': 'print_preparations',
    '/projects/{project_id}/print-preparations/{ident}': 'print_preparation',
    '/projects/{project_id}/print-dispatches': 'print_dispatches',
    '/projects/{project_id}/print-dispatches/{ident}': 'print_dispatch',
    '/rentahuman/status': 'human_provider_status', '/rentahuman/humans': 'human_search',
    '/rentahuman/bounties': 'human_bounties', '/projects/{project_id}/human-task-drafts': 'human_task_drafts',
    '/auth/members': 'members', '/auth/invites': 'invites',
    '/projects/{project_id}/members': 'project_members', '/projects/{project_id}/events/history': 'event_history',
    '/projects/{project_id}/sources': 'sources',
}
ACTIONS = {
    ('DELETE', '/projects/{project_id}'): ('delete_project', False),
    ('POST', '/projects/{project_id}/objectives'): ('create_objective', True),
    ('POST', '/projects/{project_id}/reference-images'): ('upload_reference_image', False),
    ('POST', '/projects/{project_id}/artifacts'): ('upload_artifact', True),
    ('POST', '/projects/{project_id}/companion-preview'): ('companion_preview', False),
    ('POST', '/printers/bambu/local/discover'): ('bambu_discover', False),
    ('POST', '/printers/bambu/local/certificate'): ('bambu_certificate', False),
    ('POST', '/printers/bambu/local/connect'): ('bambu_connect', False),
    ('POST', '/printers/bambu/local/refresh'): ('bambu_refresh', False),
    ('DELETE', '/printers/bambu/local'): ('bambu_forget', False),
    ('POST', '/printers/bambu/local/test-prints'): ('bambu_prepare_test', False),
    ('POST', '/projects/{project_id}/print-preparations'): ('prepare_print', True),
    ('DELETE', '/projects/{project_id}/print-preparations/{ident}'): ('delete_print_preparation', False),
    ('POST', '/projects/{project_id}/print-preparations/{ident}/complete'): ('complete_print_preparation', True),
    ('POST', '/projects/{project_id}/print-dispatches'): ('create_print_dispatch', True),
    ('POST', '/projects/{project_id}/human-task-drafts'): ('create_human_draft', True),
    ('DELETE', '/projects/{project_id}/human-task-drafts/{ident}'): ('delete_human_draft', False),
    ('POST', '/projects/{project_id}/human-jobs/{job_id}/submissions'): ('submit_human_evidence', True),
    ('POST', '/projects/{project_id}/human-jobs/{job_id}/reviews'): ('review_human_evidence', True),
    ('POST', '/auth/invites'): ('create_invite', False),
    ('DELETE', '/auth/invites/{invite_id}'): ('delete_invite', False),
    ('POST', '/auth/members/{subject}/role'): ('change_role', False),
    ('DELETE', '/auth/members/{subject}'): ('remove_member', False),
    ('POST', '/auth/members/{subject}/reset-password'): ('reset_password', False),
    ('POST', '/projects/{project_id}/members'): ('add_project_member', False),
    ('DELETE', '/projects/{project_id}/members/{subject}'): ('remove_project_member', False),
    ('POST', '/projects/{project_id}/memory/query'): ('query_memory', False),
    ('POST', '/projects/{project_id}/sources'): ('create_source', True),
    ('DELETE', '/projects/{project_id}/sources/{source_id}'): ('delete_source', False),
}
for _name in ('send', 'cancel', 'reconcile'):
    ACTIONS['POST', '/projects/{project_id}/print-dispatches/{ident}/' + _name] = ('print_' + _name, False)
for _name in ('preview', 'link', 'sync'):
    ACTIONS['POST', '/projects/{project_id}/human-task-drafts/{ident}/' + _name] = ('human_draft_' + _name, False)
CONTENTS = {
    '/artifacts/{artifact_id}/content': 'artifact_content',
    '/releases/{release_id}/download': 'release_content',
    '/projects/{project_id}/reference-images/{image_id}/content': 'reference_image_content',
    '/printers/bambu/local/test-prints/{ident}/source': 'bambu_test_source',
    '/printers/bambu/local/test-prints/{ident}/plate': 'bambu_test_plate',
}


async def event_stream(request: Request, services: Services, cursor: int, poll_interval: float):
    while not await request.is_disconnected():
        try:
            actor = await resolve(services.authenticate(request.headers, request.cookies, 'GET'))
            page = await resolve(services.query(actor, 'events', {**parameters(request), 'after': cursor}))
            for event in page['events']:
                sequence = event.get('sequence')
                if not isinstance(sequence, int) or sequence <= cursor:
                    continue
                cursor = sequence
                yield f"id: {cursor}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
            yield ': heartbeat\n\n'
        except DomainError as error:
            if error.status in (401, 403):
                yield 'event: auth_expired\ndata: {}\n\n'
            else:
                yield 'event: stream_error\ndata: {}\n\n'
            return
        await asyncio.sleep(poll_interval)


def create_app(services: Services, *, poll_interval: float = 1.0) -> FastAPI:
    app = FastAPI(title='Kleos', version='2.0.0')
    app.state.services = services

    @app.exception_handler(DomainError)
    async def domain_error(_request, error):
        return JSONResponse({'message': error.message, 'code': error.code}, status_code=error.status)

    @app.exception_handler(ValidationError)
    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        return JSONResponse({'message': 'Invalid request parameters', 'code': 'invalid_request'}, status_code=422)

    @app.get('/api/v1/health')
    async def health():
        return {'status': 'ok', 'version': '2.0.0'}

    def auth_endpoint(operation):
        async def endpoint(request: Request):
            result = await resolve(services.auth(operation, await json_body(request), request.headers, request.cookies))
            if not isinstance(result, AuthResult):
                raise TypeError('Auth service must return AuthResult')
            response = JSONResponse(result.body, status_code=result.status)
            for cookie in result.cookies:
                if cookie.delete:
                    response.delete_cookie(cookie.name, secure=cookie.secure, httponly=cookie.httponly,
                                           samesite=cookie.samesite)
                else:
                    response.set_cookie(cookie.name, cookie.value, max_age=cookie.max_age, secure=cookie.secure,
                                        httponly=cookie.httponly, samesite=cookie.samesite)
            return response
        return endpoint

    for name in ('config', 'organizations', 'session', 'login', 'signup', 'setup', 'logout'):
        app.add_api_route('/api/v1/auth/' + name, auth_endpoint(name),
                         methods=['GET' if name in ('config', 'organizations', 'session') else 'POST'],
                         name='auth_' + name)

    @app.post('/api/v1/commands', status_code=202, response_model=CommandAccepted)
    async def command(body: Command, request: Request):
        actor = await resolve(services.authenticate(request.headers, request.cookies, request.method))
        result = await resolve(services.command(actor, body.model_dump(by_alias=True),
                                               idempotency_key(request, required=True)))
        return CommandAccepted.model_validate(result)

    def query_endpoint(operation):
        async def endpoint(request: Request):
            actor = await resolve(services.authenticate(request.headers, request.cookies, request.method))
            return await resolve(services.query(actor, operation, parameters(request,
                transport=operation.startswith('bambu_') or operation == 'connectors')))
        return endpoint

    for path, operation in QUERIES.items():
        app.add_api_route('/api/v1' + path, query_endpoint(operation), methods=['GET'], name=operation)

    def action_endpoint(operation, required):
        async def endpoint(request: Request):
            actor = await resolve(services.authenticate(request.headers, request.cookies, request.method))
            result = await resolve(services.action(actor, operation, parameters(request,
                transport=operation.startswith('bambu_') or operation == 'create_print_dispatch'), await json_body(request),
                                                  idempotency_key(request, required=required)))
            return result
        return endpoint

    for (method, path), (operation, required) in ACTIONS.items():
        app.add_api_route('/api/v1' + path, action_endpoint(operation, required), methods=[method], name=operation,
                          status_code=201 if operation in {'upload_artifact','upload_reference_image'}
                          else 202 if operation == 'delete_project' else 200)

    def content_endpoint(operation):
        async def endpoint(request: Request):
            actor = await resolve(services.authenticate(request.headers, request.cookies, request.method))
            value = await resolve(services.content(actor, operation,
                parameters(request, transport=operation.startswith('bambu_'))))
            headers = dict(value.headers)
            headers['Cache-Control'] = 'private, no-store'
            if value.filename:
                from urllib.parse import quote
                headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(value.filename, safe='')
            return Response(value.data, media_type=value.media_type, headers=headers)
        return endpoint

    for path, operation in CONTENTS.items():
        app.add_api_route('/api/v1' + path, content_endpoint(operation), methods=['GET'], name=operation)

    @app.get('/api/v1/projects/{project_id}/events')
    async def events(request: Request, after: int = 0):
        if after < 0:
            raise DomainError('Event cursor must be nonnegative', 'invalid_cursor', 422)
        actor = await resolve(services.authenticate(request.headers, request.cookies, request.method))
        # Fail authorization before sending HTTP200. Polls recheck membership/session.
        await resolve(services.query(actor, 'events', {**parameters(request), 'after': after}))
        return StreamingResponse(event_stream(request, services, after, poll_interval), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    return app
