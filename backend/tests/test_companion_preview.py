import io
import json
import stat
import struct
import time
import zipfile
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, select
from test_control_plane import create

import kleos.companion_preview as preview_module
from kleos.auth import Principal
from kleos.companion_preview import CompanionArchive, CompanionPreviews, create_preview_app
from kleos.config import Settings
from kleos.contracts import DomainError
from kleos.db import (
    Artifact,
    Attempt,
    Event,
    Membership,
    NativeJob,
    Project,
    ProjectMembership,
    Run,
    Task,
    now,
)
from kleos.storage import digest

BASE_FILES = {
    'index.html': b'<!doctype html><html><head><base href="/"></head><body>Real build<script src="flutter_bootstrap.js"></script></body></html>',
    'main.dart.js': b'window.compiledFixture = true;',
    'flutter_bootstrap.js': b'window.bootstrapFixture = true;',
    'assets/data.json': b'{"fixture":true}',
}


def bundle(extra=None, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=compression) as archive:
        for name, content in [*BASE_FILES.items(), *(extra or [])]:
            archive.writestr(name, content)
    return output.getvalue()


@pytest.fixture
def companion(platform):
    platform.settings.session_key = SecretStr('preview-test-key-only')
    platform.settings.companion_preview_signing_key = SecretStr('preview-fixture-dedicated-key-32-characters')
    platform.settings.companion_preview_base_url = 'http://preview.example.test'
    project_id = create(platform, 'A compiled companion preview')['projectId']
    content = bundle()
    key = f'previews/{project_id}/companion-web.zip'
    platform.store.put(key, content, 'application/zip')
    with platform.db.transaction() as session:
        session.add(Run(id='preview-run', tenant_id='org-a', project_id=project_id, command_id='fixture',
                        kind='project.generate', revision=1, status='succeeded'))
        session.add(Task(id='preview-task', tenant_id='org-a', project_id=project_id, run_id='preview-run',
                         lane='application', kind='project.generate', revision=1, status='succeeded'))
        session.add(Attempt(id='preview-attempt', tenant_id='org-a', task_id='preview-task', number=1, fence=1,
                            policy_revision=1, provider='google', model='fixture', status='succeeded', context={}))
        session.add(NativeJob(id='preview-job', tenant_id='org-a', task_id='preview-task', attempt_id='preview-attempt',
                             purpose='build', status='succeeded', token_hash='fixture', expires_at=now() + timedelta(hours=1),
                             request={'revision': 1, 'inputArtifacts': []}))
        session.add(Artifact(id='preview-artifact', tenant_id='org-a', project_id=project_id,
            task_id='preview-task', attempt_id='preview-attempt', revision=1, lane='application',
            name='native/companion-web.zip', key=key, sha256=digest(content), size=len(content),
            media_type='application/zip', status='candidate', provenance={'nativeJobId': 'preview-job'}))
    service = CompanionPreviews(platform.db, platform.settings, platform.store)
    with TestClient(create_preview_app(platform.settings, platform.db, platform.store)) as client:
        yield platform, project_id, service, client, content, key


def open_preview(companion, principal=None):
    platform, project, service, client, _, _ = companion
    minted = service.mint(principal or platform.principal, project, 'preview-artifact')
    path = urlsplit(minted['url']).path
    ticket = path.split('/')[2]
    return minted, path, ticket


def test_preview_serves_real_compiled_assets_and_changes_only_response_base(companion):
    platform, _, service, client, original, key = companion
    minted, path, ticket = open_preview(companion)
    assert minted['mode'] == 'isolated-web-build' and minted['sha256'] == digest(original)
    wrapper = client.get(path)
    assert wrapper.status_code == 200
    assert 'sandbox="allow-scripts"' in wrapper.text
    assert 'allow-same-origin' not in wrapper.text
    index = client.get(path.removesuffix('view') + 'index.html')
    assert index.status_code == 200 and 'Real build' in index.text
    assert '<base href="http://preview.example.test/p/' + ticket + '/">' in index.text
    assert index.headers['X-Kleos-Preview-Transform'] == 'base-url-only; source-bundle-unchanged'
    assert client.get(path.removesuffix('view') + 'main.dart.js').content == BASE_FILES['main.dart.js']
    assert client.get(path.removesuffix('view') + 'assets/data.json').json() == {'fixture': True}
    assert platform.store.get(key) == original
    assert CompanionArchive(platform.store.get(key)).read('index.html') == BASE_FILES['index.html']
    archive, expected_hash = service.authorize(ticket)
    assert expected_hash == digest(original) and archive.read('main.dart.js') == BASE_FILES['main.dart.js']


def test_preview_headers_disable_credentials_devices_external_resources_and_caching(companion):
    _, _, _, client, _, _ = companion
    _, path, _ = open_preview(companion)
    wrapper = client.get(path)
    asset_path = path.removesuffix('view') + 'main.dart.js'
    asset = client.get(asset_path, headers={'Origin': 'null'})
    for response in (wrapper, asset):
        assert 'no-store' in response.headers['Cache-Control']
        assert response.headers['Referrer-Policy'] == 'no-referrer'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert 'Access-Control-Allow-Credentials' not in response.headers
        assert response.headers['Access-Control-Allow-Origin'] == 'null'
        assert 'camera=()' in response.headers['Permissions-Policy']
        assert 'microphone=()' in response.headers['Permissions-Policy']
        assert 'usb=()' in response.headers['Permissions-Policy']
        assert 'Set-Cookie' not in response.headers
        csp = response.headers['Content-Security-Policy']
        assert "default-src 'none'" in csp and "form-action 'none'" in csp
        assert 'sandbox allow-scripts' in csp and 'allow-same-origin' not in csp
        assert 'https:' not in csp and 'http://evil' not in csp
    assert "frame-src 'none'" in asset.headers['Content-Security-Policy']
    assert "object-src 'none'" in asset.headers['Content-Security-Policy']
    assert 'frame-src http://preview.example.test/p/' in wrapper.headers['Content-Security-Policy']
    assert asset.headers['Content-Type'].startswith('application/javascript')
    head = client.head(asset_path)
    assert head.status_code == 200 and head.content == b''
    assert head.headers['Content-Length'] == asset.headers['Content-Length']
    options = client.options(asset_path, headers={'Origin': 'null', 'Access-Control-Request-Method': 'GET'})
    assert options.status_code == 204 and options.headers['Access-Control-Allow-Origin'] == 'null'


def test_preview_tickets_are_scoped_tamper_resistant_expiring_and_never_audited_as_urls(companion, monkeypatch):
    platform, project, service, client, _, _ = companion
    minted, path, ticket = open_preview(companion)
    assert client.get(path.replace(ticket, ticket + 'x')).status_code == 403
    assert client.get('/p/not-a-signed-ticket/view').status_code == 403
    assert client.get(path.removesuffix('view') + 'missing.js').status_code == 404
    with platform.db.transaction() as session:
        events = session.scalars(select(Event).where(Event.project_id == project,
                                                    Event.type == 'audit.companionPreviewOpened')).all()
        assert len(events) == 1
        serialized = json.dumps(events[0].data)
        assert ticket not in serialized and minted['url'] not in serialized
        assert events[0].data['artifactId'] == 'preview-artifact'
    original_time = time.time
    monkeypatch.setattr(preview_module.time, 'time', lambda: original_time() + 601)
    # The signed creation timestamp and explicit expiry both enforce a short lifetime.
    assert client.get(path).status_code == 403


@pytest.mark.parametrize('mutation', ['membership', 'deleted', 'revision', 'stale', 'job_failed', 'manifest_hash'])
def test_cached_assets_recheck_current_authority_for_every_request(companion, mutation):
    platform, project_id, _, client, _, _ = companion
    _, path, _ = open_preview(companion)
    assert client.get(path).status_code == 200  # warm the immutable archive cache
    with platform.db.transaction() as session:
        if mutation == 'membership':
            session.execute(delete(Membership).where(Membership.subject == platform.principal.subject))
        elif mutation == 'deleted':
            session.get(Project, project_id).deleted_at = now()
        elif mutation == 'revision':
            session.get(Project, project_id).engineering_revision += 1
        elif mutation == 'stale':
            session.get(Artifact, 'preview-artifact').status = 'stale'
        elif mutation == 'job_failed':
            session.get(NativeJob, 'preview-job').status = 'failed'
        elif mutation == 'manifest_hash':
            session.get(Artifact, 'preview-artifact').sha256 = 'changed'
    assert client.get(path.removesuffix('view') + 'main.dart.js').status_code == 403
    assert client.options(path.removesuffix('view') + 'main.dart.js').status_code == 403


def test_project_membership_revocation_invalidates_existing_ticket(companion):
    platform, project_id, _, client, _, _ = companion
    with platform.db.transaction() as session:
        session.add(Membership(subject='project-member', tenant_id='org-a', role='member'))
        session.add(ProjectMembership(subject='project-member', project_id=project_id, role='viewer'))
    _, path, _ = open_preview(companion, Principal('project-member', 'org-a', 'member'))
    assert client.get(path).status_code == 200
    with platform.db.transaction() as session:
        session.execute(delete(ProjectMembership).where(ProjectMembership.subject == 'project-member'))
    assert client.get(path).status_code == 403


def test_preview_mint_rejects_other_tenants_unbuilt_files_and_corrupted_immutable_bundle(companion, monkeypatch):
    platform, project, service, _, _, key = companion
    with pytest.raises(DomainError):
        service.mint(Principal('other', 'org-b', 'owner'), project, 'preview-artifact')
    with platform.db.transaction() as session:
        artifact = session.get(Artifact, 'preview-artifact')
        artifact.provenance = {}
    with pytest.raises(DomainError, match='successful current'):
        service.mint(platform.principal, project, 'preview-artifact')
    with platform.db.transaction() as session:
        session.get(Artifact, 'preview-artifact').provenance = {'nativeJobId': 'preview-job'}
    monkeypatch.setattr(platform.store, 'get_limited', lambda *args: b'corrupted')
    with pytest.raises(DomainError, match='unavailable or invalid'):
        service.mint(platform.principal, project, 'preview-artifact')


@pytest.mark.parametrize('name', ['/escape', '../escape', 'a/../escape', 'a\\escape', 'a//escape', 'a:escape', 'a\nescape'])
def test_zip_member_paths_cannot_traverse_or_normalize_to_other_files(name):
    with pytest.raises(ValueError):
        CompanionArchive(bundle([(name, b'bad')]))


def test_zip_rejects_duplicates_symlinks_encryption_and_unsupported_compression():
    with pytest.warns(UserWarning, match='Duplicate'):
        duplicate = bundle([('index.html', b'duplicate')])
    with pytest.raises(ValueError):
        CompanionArchive(duplicate)
    symlink = zipfile.ZipInfo('assets/link')
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError):
        CompanionArchive(bundle([(symlink, b'../../outside')]))
    encrypted = bytearray(bundle())
    central = encrypted.index(b'PK\x01\x02')
    flags = struct.unpack_from('<H', encrypted, central + 8)[0]
    struct.pack_into('<H', encrypted, central + 8, flags | 1)
    with pytest.raises(ValueError):
        CompanionArchive(bytes(encrypted))
    with pytest.raises(ValueError):
        CompanionArchive(bundle(compression=zipfile.ZIP_BZIP2))


@pytest.mark.parametrize('ceiling,value', [('MAX_ENTRIES', 2), ('MAX_BUNDLE_BYTES', 8),
    ('MAX_ENTRY_BYTES', 16), ('MAX_UNPACKED_BYTES', 32)])
def test_zip_rejects_bombs_before_decompressing_entries(monkeypatch, ceiling, value):
    content = bundle([('assets/bomb', b'x' * 1000)])
    monkeypatch.setattr(preview_module, ceiling, value)
    def never_decompress(*args, **kwargs):
        pytest.fail('Rejected archive must not decompress entries')
    monkeypatch.setattr(zipfile.ZipFile, 'open', never_decompress)
    with pytest.raises(ValueError):
        CompanionArchive(content)


def test_preview_requires_an_isolated_origin_and_cloud_https():
    for address in ('http://localhost:5180', 'http://localhost:8000', 'https://user:password@preview.example',
                    'https://preview.example/path', 'https://preview.example?token=secret'):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, companion_preview_base_url=address)
    with pytest.raises(ValidationError, match='require HTTPS'):
        Settings(_env_file=None, environment='production', companion_preview_base_url='http://preview.example')


def test_html_rewrite_cannot_amplify_an_oversized_entry_before_decompression(monkeypatch):
    monkeypatch.setitem(BASE_FILES, 'index.html', b'<base>' * 200_000)
    content = bundle()

    def never_decompress(*args, **kwargs):
        pytest.fail('Oversized HTML must be rejected before decompression or URL rewriting')

    monkeypatch.setattr(zipfile.ZipFile, 'open', never_decompress)
    with pytest.raises(ValueError, match='HTML entry'):
        CompanionArchive(content)


def test_authenticated_mint_api_does_not_enable_null_origin_access_to_control_api(companion):
    platform, project_id, _, preview_client, _, _ = companion
    endpoint = f'/api/v1/projects/{project_id}/companion-preview'
    minted = platform.client.post(endpoint, json={'artifactId': 'preview-artifact'})
    assert minted.status_code == 200
    assert minted.json()['artifactId'] == 'preview-artifact'
    assert preview_client.get(urlsplit(minted.json()['url']).path).status_code == 200
    assert platform.client.get(urlsplit(minted.json()['url']).path).status_code == 404
    preflight = platform.client.options(endpoint, headers={'Origin': 'null',
        'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type'})
    assert preflight.status_code == 400
    assert 'Access-Control-Allow-Origin' not in preflight.headers
    platform.client.headers.pop('Authorization')
    assert platform.client.post(endpoint, json={'artifactId': 'preview-artifact'}).status_code == 401
    assert preview_client.get('/docs').status_code == 404
    assert preview_client.get('/openapi.json').status_code == 404
