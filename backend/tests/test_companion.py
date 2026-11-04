import base64
import hashlib
import io
import stat
import struct
import zipfile
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
import pytest
from alembic import command
from pydantic import SecretStr
from sqlalchemy import select
from test_interface_runtime import Locks, persistence

from kleos.access.service import Account, OrganizationMember
from kleos.api.companion import CompanionPreviews, WebBundle, create_preview_app, origin
from kleos.artifacts.service import Artifact, ArtifactService, NativePublication
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError, TaskSpec
from kleos.infrastructure.runtime import Runtime, migration_config
from kleos.infrastructure.settings import Settings
from kleos.orchestration.engine import RunScope
from kleos.projects.store import Membership, Objective, Project, ProjectStore


def bundle(entries=None, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    entries = entries or {'index.html': b'<!doctype html><html><head><base href="/"></head><body><script src="main.dart.js"></script></body></html>',
        'main.dart.js': b'/* fixture only; never executed by these HTTP tests */',
        'canvaskit/canvaskit.wasm': b'\x00asm\x01\x00\x00\x00', 'assets/font.woff2': b'fixture-font',
        'flutter_service_worker.js': b'/* must not be served */'}
    with zipfile.ZipFile(output, 'w', compression=compression) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


async def publish(store, artifacts, actor, ident):
    submitted = store.submit(actor, ident, 'Build a companion application', 1, 'run')
    scope = RunScope(actor=actor, project_id=ident, run_id=submitted['runId'], revision=submitted['revision'],
        deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    task = TaskSpec(id='web', capability='application', objective='Build web application')
    raw = bundle()
    refs = await artifacts.publish_native(scope, task, [{'path': 'outputs/companion-web.zip', 'role': 'output',
        'content': raw, 'mediaType': 'application/zip'}],
        {'nativeJobId': 'fixture-native', 'requestHash': 'a'*64, 'imageDigest': 'sha256:' + 'b'*64})
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.status = 'succeeded'
        run.data = {**run.data, 'results': {'web': {'artifacts': [ref.model_dump(mode='json') for ref in refs]}}}
    return scope, refs[0], raw


@pytest.fixture
async def preview(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path}/companion.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    with store.transaction() as session:
        session.add(Account(id='owner', email='owner@example.test', name='Owner', password_hash='fixture-unusable'))
        session.add(OrganizationMember(tenant_id='tenant', user_id='owner', role='owner'))
    ident = store.create(actor, 'Companion app', 'create')['projectId']
    artifacts = ArtifactService(store, BlobStore(tmp_path / 'blobs'))
    scope, ref, raw = await publish(store, artifacts, actor, ident)
    now = [1000000000]
    service = CompanionPreviews(store, artifacts, secret='x'*40, api_origin='https://api.example.test',
        frontend_origin='https://workbench.example.test', preview_origin='https://preview.example.test',
        enabled=True, ttl_seconds=60, clock=lambda: now[0])
    yield store, artifacts, actor, scope, ref, raw, service, now
    store.engine.dispose()


def lease(env):
    return env[6].issue(env[2], env[3].project_id, {'artifactId': env[4].id})


def token(url):
    return urlsplit(url).path.split('/')[2]


async def test_actual_http_ticket_wrapper_base_assets_mime_and_opaque_cors(preview):
    _, _, actor, scope, ref, raw, service, _ = preview
    issued = lease(preview)
    assert issued['artifactId'] == ref.id and issued['sha256'] == hashlib.sha256(raw).hexdigest()
    assert issued['mode'] == 'isolated-web-build'
    encrypted = base64.urlsafe_b64decode(token(issued['url']) + '=' * (-len(token(issued['url'])) % 4))
    assert actor.user_id.encode() not in encrypted and scope.project_id.encode() not in encrypted
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_preview_app(service)),
            base_url=service.preview_origin) as client:
        wrapper = await client.get(issued['url'])
        assert wrapper.status_code == 200 and 'sandbox="allow-scripts"' in wrapper.text
        assert 'credentialless' in wrapper.text and 'allow-same-origin' not in wrapper.text
        assert 'frame-ancestors https://workbench.example.test' in wrapper.headers['Content-Security-Policy']
        assert "script-src 'none'" in wrapper.headers['Content-Security-Policy']
        index = await client.get(issued['url'] + 'files/index.html', headers={'Origin': 'null'})
        base = issued['url'] + 'files/'
        assert index.status_code == 200 and '<base href="' + base + '">' in index.text
        assert '<base href="/">' not in index.text
        assert index.headers['Access-Control-Allow-Origin'] == 'null'
        assert 'access-control-allow-credentials' not in index.headers and 'set-cookie' not in index.headers
        csp = index.headers['Content-Security-Policy']
        assert "worker-src 'none'" in csp and 'connect-src ' + base in csp
        assert "'wasm-unsafe-eval'" in csp and "'unsafe-eval'" not in csp
        for path, media in [('main.dart.js', 'text/javascript'), ('canvaskit/canvaskit.wasm', 'application/wasm'),
                ('assets/font.woff2', 'font/woff2')]:
            asset = await client.get(base + path, headers={'Origin': 'null'})
            assert asset.status_code == 200 and asset.headers['Content-Type'].startswith(media)
            assert asset.headers['Cache-Control'] == 'no-store'
            assert asset.headers['X-Content-Type-Options'] == 'nosniff'
        assert (await client.get(base + 'flutter_service_worker.js')).status_code == 404
        assert (await client.get(base + 'main.dart.js', headers={'Service-Worker': 'script'})).status_code == 404
        assert (await client.get('/api/v1/auth/session')).status_code == 404
        assert (await client.post(base + 'main.dart.js')).status_code == 405


@pytest.mark.parametrize('mutation', ['project_member', 'organization_member', 'account', 'context', 'publication',
    'publication_hash', 'cancelled_run', 'superseded_task', 'artifact_hash', 'blob_hash'])
async def test_each_file_request_revalidates_live_authority(preview, mutation):
    store, artifacts, actor, scope, ref, _, service, _ = preview
    issued = lease(preview)
    with store.transaction() as session:
        if mutation == 'project_member':
            session.delete(session.get(Membership, (scope.project_id, actor.user_id)))
        elif mutation == 'organization_member':
            session.delete(session.get(OrganizationMember, (actor.tenant_id, actor.user_id)))
        elif mutation == 'account':
            session.delete(session.get(Account, actor.user_id))
        elif mutation == 'context':
            session.get(Project, scope.project_id).advance_revision()
        elif mutation in {'publication', 'publication_hash'}:
            receipt = session.scalar(select(NativePublication))
            if mutation == 'publication':
                session.delete(receipt)
            else:
                receipt.fingerprint = 'c'*64
        elif mutation == 'cancelled_run':
            session.get(Objective, scope.run_id).status = 'cancelled'
        elif mutation == 'superseded_task':
            run = session.get(Objective, scope.run_id)
            run.data = {**run.data, 'superseded': {'web': 'another'}}
        elif mutation == 'artifact_hash':
            session.get(Artifact, ref.id).sha256 = 'f'*64
        else:
            artifacts.blobs._path(ref.sha256).write_bytes(b'corrupt')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_preview_app(service)),
            base_url=service.preview_origin) as client:
        response = await client.get(issued['url'] + 'files/main.dart.js')
    assert response.status_code in {404, 409, 500}
    assert token(issued['url']) not in response.text


async def test_expiry_tampering_foreign_origin_and_scope(preview):
    _, _, _, _, _, _, service, now = preview
    issued = lease(preview)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_preview_app(service)),
            base_url=service.preview_origin) as client:
        value = token(issued['url'])
        tampered = ('A' if value[0] != 'A' else 'B') + value[1:]
        assert (await client.get('/preview/' + tampered + '/')).status_code == 404
        assert (await client.get(issued['url'], headers={'Host': 'api.example.test'})).status_code == 404
        assert (await client.get(issued['url'], headers={'Host': 'bad;host.example.test'})).status_code == 404
        now[0] += 60
        assert (await client.get(issued['url'] + 'files/main.dart.js')).status_code == 404


def test_issue_requires_current_publication_and_actor(preview):
    _, _, actor, scope, ref, _, service, _ = preview
    with pytest.raises(DomainError):
        service.issue(Actor(tenant_id='another', user_id=actor.user_id), scope.project_id, {'artifactId': ref.id})
    with pytest.raises(DomainError):
        service.issue(actor, scope.project_id, {'artifactId': 'unpublished'})
    with pytest.raises(DomainError):
        service.issue(actor, scope.project_id, {'artifactId': ref.id, 'url': 'https://arbitrary.example'})


def test_capacity_gate_never_waits_under_an_existing_project_lock(preview):
    store, _, _, scope, ref, raw, service, _ = preview
    assert service.gate.acquire(False) and service.gate.acquire(False)
    try:
        with store.transaction() as session:
            store.authorized(session, scope.actor, scope.project_id)
            with pytest.raises(DomainError) as error:
                service.metadata(session.get(Artifact, ref.id), raw)
            assert error.value.code == 'preview_capacity'
    finally:
        service.gate.release()
        service.gate.release()


@pytest.mark.parametrize('path', ['../secret', '/index.html', 'a\\b', 'a//b', 'a/./b', 'a%2fb', 'a:b'])
def test_bundle_paths_are_rejected(path):
    with pytest.raises(DomainError):
        WebBundle(bundle({'index.html': b'<html></html>', path: b'unsafe'}))


@pytest.mark.parametrize('variant', ['links', 'bomb', 'many', 'zip64', 'count_mismatch', 'bad_crc', 'case', 'directory', 'nul'])
def test_bundle_bounds_and_ambiguities(variant):
    if variant == 'links':
        info = zipfile.ZipInfo('link')
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        raw = bundle({'index.html': b'<html></html>', info: b'../index.html'})
    elif variant == 'bomb':
        raw = bundle({'index.html': b'x' * 200000})
    elif variant == 'many':
        raw = bundle({'index.html': b'x', **{f'{n}.txt': b'x' for n in range(1024)}})
    elif variant == 'case':
        raw = bundle({'index.html': b'x', 'A/x': b'x', 'a/y': b'y'})
    elif variant == 'directory':
        raw = bundle({'index.html': b'x', 'a': b'x', 'a/b': b'y'})
    else:
        raw = bytearray(bundle({'index.html': b'<html></html>'}, zipfile.ZIP_STORED))
        central, end = raw.index(b'PK\x01\x02'), raw.index(b'PK\x05\x06')
        if variant == 'zip64':
            struct.pack_into('<H', raw, central + 6, 45)
        elif variant == 'count_mismatch':
            struct.pack_into('<H', raw, end + 10, 2)
        elif variant == 'nul':
            raw[central + 46] = 0
        else:
            raw[30 + len('index.html')] ^= 1
        raw = bytes(raw)
    with pytest.raises(DomainError):
        WebBundle(raw)


def test_origin_canonicalization_and_enabled_separation(preview):
    assert origin('https://EXAMPLE.test:443/') == 'https://example.test'
    assert origin('http://EXAMPLE.test:80') == 'http://example.test'
    assert origin('http://[0:0:0:0:0:0:0:1]:8004') == 'http://[::1]:8004'
    store, artifacts, *_ = preview
    with pytest.raises(ValueError):
        CompanionPreviews(store, artifacts, secret='x'*40, api_origin='https://same.test:443',
            frontend_origin='https://workbench.test', preview_origin='https://SAME.test', enabled=True)
    disabled = CompanionPreviews(store, artifacts, secret='', api_origin='http://api.test',
        frontend_origin='http://workbench.test', preview_origin='http://preview.test')
    with pytest.raises(DomainError) as error:
        disabled.issue(None, 'project', {'artifactId': 'anything'})
    assert error.value.code == 'companion_preview_unavailable'


@pytest.mark.parametrize('value', ['https://bücher.example', 'http://127.1:8004', 'http://0x7f000001:8004',
    'http://0177.0.0.1:8004', 'http://2130706433:8004', 'http://example.test:0', 'http://abc.1',
    'http://[::1%25eth0]:8004', 'https://host.example;script-src/'])
def test_noncanonical_browser_origin_aliases_are_rejected(value):
    with pytest.raises(ValueError):
        origin(value)


async def test_actual_runtime_issues_lease_and_serves_only_on_separate_app(tmp_path):
    settings = Settings(environment='test', database_url=f'sqlite:///{tmp_path / "runtime.db"}',
        artifact_root=tmp_path / 'blobs', native_control_root=tmp_path / 'native',
        slicer_control_root=tmp_path / 'slicer', checker_control_root=tmp_path / 'checker',
        session_secret=SecretStr('s'*40), development_no_login=True, model_catalog=[],
        api_origin='https://api.example.test', frontend_origin='https://workbench.example.test',
        preview_origin='https://preview.example.test', companion_preview_enabled=True)
    runtime = Runtime(settings, persistence_factory=persistence, worker_locks=Locks())
    with runtime.projects.engine.begin() as connection:
        config = migration_config()
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
    async with runtime.start(), httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app),
            base_url=settings.api_origin) as api, httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.preview_app),
            base_url=settings.preview_origin) as preview_client:
        created = await api.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
            json={'type': 'project.create', 'payload': {'brief': 'Preview a published companion application'}})
        assert created.status_code == 202
        ident = created.json()['projectId']
        actor = runtime.access.authenticate({}, {}, 'GET')
        _, ref, _ = await publish(runtime.projects, runtime.artifacts, actor, ident)
        metadata = await api.get(f'/api/v1/projects/{ident}/engineering-preview')
        assert metadata.status_code == 200, metadata.text
        application = metadata.json()['application']
        assert application['previewAvailable'] and application['previewArtifactId'] == ref.id
        assert application['previewSha256'] == ref.sha256
        issued = await api.post(f'/api/v1/projects/{ident}/companion-preview', json={'artifactId': ref.id})
        assert issued.status_code == 200, issued.text
        url = issued.json()['url']
        assert (await preview_client.get(url)).status_code == 200
        assert (await preview_client.get(url + 'files/main.dart.js')).status_code == 200
        assert (await api.get(urlsplit(url).path)).status_code == 404
        assert (await preview_client.get('/api/v1/auth/session')).status_code == 404
        with runtime.projects.transaction() as session:
            session.delete(session.get(OrganizationMember, (actor.tenant_id, actor.user_id)))
        assert (await preview_client.get(url + 'files/main.dart.js')).status_code == 404


async def test_trusted_browser_harness_serves_fixture_without_running_javascript(tmp_path):
    from companion_browser_fixture import PREVIEW, WORKBENCH, applications
    store, workbench, preview_app = await applications(tmp_path)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench), base_url=WORKBENCH) as client:
            page = await client.get('/')
            assert page.status_code == 200 and 'sandbox="allow-scripts"' in page.text
            url = page.text.split('src="', 1)[1].split('"', 1)[0]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=preview_app), base_url=PREVIEW) as client:
            assert (await client.get(url)).status_code == 200
            script = await client.get(url + 'files/main.dart.js')
            assert script.status_code == 200 and 'Loaded successfully' in script.text
            assert (await client.get(url + 'files/assets/data.json')).json()['fixture'] == 'relative asset loaded'
    finally:
        store.engine.dispose()
