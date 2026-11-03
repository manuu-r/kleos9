"""Preview derivation tests use an inert synthetic loader, never execute generated JS."""
import hashlib
import json

import httpx
import pytest
import test_companion as fixtures

from kleos.api import companion_bootstrap as bootstrap
from kleos.api.companion import create_preview_app
from kleos.contracts.models import DomainError


def source(monkeypatch):
    loader = b'/* reviewed inert unit-test loader */'
    monkeypatch.setattr(bootstrap, 'QUALIFIED_LOADER_SHA256', hashlib.sha256(loader).hexdigest())
    config = {'engineRevision': bootstrap.ENGINE_REVISION, 'builds': [{'compileTarget': 'dart2js',
        'renderer': 'canvaskit', 'mainJsPath': 'main.dart.js'}], 'useLocalCanvasKit': True}
    return loader + bootstrap.MARKER + json.dumps(config).encode() + b''';

_flutter.loader.load({
  serviceWorkerSettings: {
    serviceWorkerVersion: "1234567"
  }
});
'''


def test_exact_derivation_preserves_loader_build_config_and_original_bytes(monkeypatch):
    raw = source(monkeypatch)
    view = bootstrap.preview_bootstrap(raw)
    assert view.content.endswith(b'_flutter.loader.load();\n')
    assert view.content.split(b';\n\n_flutter.loader.load')[0] == raw.split(b';\n\n_flutter.loader.load')[0]
    assert view.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert view.view_sha256 == hashlib.sha256(view.content).hexdigest() != view.source_sha256
    assert view.headers()['X-Kleos-Preview-Transform'] == bootstrap.TRANSFORM_VERSION
    assert b'serviceWorkerSettings' in raw


@pytest.mark.parametrize('mutation', ['loader', 'config', 'suffix', 'extra_script', 'size'])
def test_unrecognized_service_worker_bootstrap_is_not_rewritten(monkeypatch, mutation):
    raw = source(monkeypatch)
    raw = {'loader': b'/* modified */' + raw,
        'config': raw.replace(b'"useLocalCanvasKit": true', b'"useLocalCanvasKit": false'),
        'suffix': raw.replace(b'"1234567"', b'getVersion()'),
        'extra_script': raw + b'evil();', 'size': b' ' * 65536 + raw}[mutation]
    with pytest.raises(DomainError) as error:
        bootstrap.preview_bootstrap(raw)
    assert error.value.code == 'preview_bootstrap_unsupported'


def test_custom_script_without_service_worker_remains_original():
    assert bootstrap.preview_bootstrap(b'/* custom no-worker entrypoint */') is None


async def test_http_transformation_declares_hashes_preserves_zip_and_binds_version(tmp_path, monkeypatch):
    raw = source(monkeypatch)
    original_bundle = fixtures.bundle
    monkeypatch.setattr(fixtures, 'bundle', lambda: original_bundle({'index.html': b'<script src="flutter_bootstrap.js"></script>',
        'flutter_bootstrap.js': raw}))
    generator = fixtures.preview.__wrapped__(tmp_path)
    env = await anext(generator)
    try:
        issued = fixtures.lease(env)
        service = env[6]
        assert bootstrap.TRANSFORM_VERSION.encode() in service.aad
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_preview_app(service)), base_url=service.preview_origin) as client:
            response = await client.get(issued['url'] + 'files/flutter_bootstrap.js')
            assert response.status_code == 200
            assert response.content == bootstrap.preview_bootstrap(raw).content
            assert response.headers['X-Kleos-Source-Sha256'] == hashlib.sha256(raw).hexdigest()
            assert response.headers['X-Kleos-View-Sha256'] == hashlib.sha256(response.content).hexdigest()
            assert "worker-src 'none'" in response.headers['Content-Security-Policy']
            assert 'allow-same-origin' not in response.headers['Content-Security-Policy']
            assert env[1].blobs.get(env[4].sha256) == env[5]
            service.aad += b'changed-transform-version'
            assert (await client.get(issued['url'] + 'files/flutter_bootstrap.js')).status_code == 404
    finally:
        await generator.aclose()


def test_html_script_view_enforces_anonymous_without_losing_attributes():
    from kleos.api.companion import _EntryDocument
    parser = _EntryDocument('https://preview.example.test/lease/files/')
    parser.feed('<base href="/"><script src="entry.js?a=1&amp;b=2" async crossorigin="use-credentials" '
        'CROSSORIGIN="use-credentials" data-note="&quot;quoted&quot;"></script><script>import("module.js")</script>')
    result = ''.join(parser.parts)
    assert 'use-credentials' not in result and result.count('crossorigin="anonymous"') == 2
    assert 'src="entry.js?a=1&amp;b=2" async' in result
    assert 'data-note="&quot;quoted&quot;"' in result
    assert '<script crossorigin="anonymous">import("module.js")</script>' in result


async def test_parallel_bootstrap_assets_wait_before_project_lock(tmp_path):
    import asyncio
    import threading
    generator = fixtures.preview.__wrapped__(tmp_path)
    env = await anext(generator)
    release, both_entered = threading.Event(), threading.Event()
    lock, active, maximum = threading.Lock(), [0], [0]
    service, blobs = env[6], env[1].blobs
    issued = fixtures.lease(env)
    original = blobs.get

    def bounded_get(sha):
        with lock:
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
            if active[0] == 2:
                both_entered.set()
        try:
            assert release.wait(5), 'test release timed out'
            return original(sha)
        finally:
            with lock:
                active[0] -= 1
    blobs.get = bounded_get
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_preview_app(service)), base_url=service.preview_origin) as client:
            tasks = [asyncio.create_task(client.get(issued['url'] + 'files/' + name)) for name in
                ('main.dart.js', 'canvaskit/canvaskit.wasm', 'assets/font.woff2')]
            assert await asyncio.to_thread(both_entered.wait, 3)
            release.set()
            responses = await asyncio.gather(*tasks)
            assert [response.status_code for response in responses] == [200, 200, 200]
            assert maximum[0] == 2
        for _ in range(10):
            assert service.admission.acquire(False)
        try:
            with pytest.raises(DomainError) as error:
                service.serve(fixtures.token(issued['url']), 'files/main.dart.js')
            assert error.value.code == 'preview_capacity'
        finally:
            for _ in range(10):
                service.admission.release()
    finally:
        release.set()
        blobs.get = original
        await generator.aclose()
