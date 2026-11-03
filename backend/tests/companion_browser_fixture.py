"""Explicit trusted browser-contract fixture; no generated application or model calls.

Run manually from backend: .venv/bin/python tests/companion_browser_fixture.py
Opens no browser itself. Stops both local fixture servers on Ctrl-C.
"""
import argparse
import asyncio
import html
import io
import json
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse

from kleos.access.service import Account, OrganizationMember
from kleos.api.companion import CompanionPreviews, create_preview_app
from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, TaskSpec
from kleos.orchestration.engine import RunScope
from kleos.projects.store import Objective, ProjectStore

WORKBENCH = 'http://127.0.0.1:18792'
PREVIEW = 'http://127.0.0.1:18793'
API = 'http://127.0.0.1:18791'


def trusted_bundle(*, adversarial=False):
    files = {'index.html': '''<!doctype html><html><head><base href="/"><title>Trusted preview fixture</title>
<style>body{background:white;color:black;font:16px sans-serif;margin:24px}button{padding:8px 16px}</style></head>
<body><h1>Trusted preview fixture</h1><p id="status">Loading</p><pre id="checks"></pre>
<button id="increment">Increment</button><p id="counter">Count: 0</p>
<button id="negative">Run local network checks</button><button id="navigation">Attempt local external navigation</button>
<script src="main.dart.js"></script></body></html>'''.encode(),
        'main.dart.js': b'''(async () => {
const checks = {origin: self.origin};
try { localStorage.getItem('fixture'); checks.storage = 'unexpected access'; }
catch (_) { checks.storage = 'blocked'; }
try { checks.serviceWorker = navigator.serviceWorker ? 'unexpected access' : 'unavailable'; }
catch (_) { checks.serviceWorker = 'blocked'; }
try {
  const data = await (await fetch('assets/data.json', {credentials: 'omit'})).json();
  checks.relativeAsset = data.fixture;
  await WebAssembly.compileStreaming(fetch('canvaskit/fixture.wasm', {credentials: 'omit'}));
  checks.wasm = 'compiled';
  document.getElementById('status').textContent = 'Loaded successfully';
} catch (error) { document.getElementById('status').textContent = 'Load failed: ' + error.name; }
document.getElementById('checks').textContent = JSON.stringify(checks, null, 2);
let count = 0;
document.getElementById('increment').onclick = () => {
  document.getElementById('counter').textContent = 'Count: ' + (++count);
};
})();''', 'assets/data.json': b'{"fixture":"relative asset loaded"}',
        'canvaskit/fixture.wasm': b'\x00asm\x01\x00\x00\x00'}
    if adversarial:
        files['main.dart.js'] += b'''
const localTrap = 'http://127.0.0.1:18791/attempt/';
const negative = {scope: 'trusted-local-negative-fixture'};
function reportNegative() {
  document.getElementById('checks').textContent = JSON.stringify(negative, null, 2);
  window.top.postMessage({kind:'kleos-trusted-browser-fixture', observations:negative}, '*');
}
document.getElementById('negative').onclick = async () => {
  try { await fetch(localTrap+'fetch', {credentials:'omit'}); negative.fetch='unexpected success'; }
  catch (_) { negative.fetch='blocked'; }
  const image = new Image(); image.src = localTrap+'image'; document.body.appendChild(image);
  const script = document.createElement('script'); script.src=localTrap+'script'; document.body.appendChild(script);
  const child = document.createElement('iframe'); child.src=localTrap+'frame'; document.body.appendChild(child);
  const form = document.createElement('form'); form.action=localTrap+'form'; form.method='POST';
  document.body.appendChild(form); try { form.submit(); } catch (_) {}
  try { navigator.sendBeacon(localTrap+'beacon', 'fixture'); negative.beacon='attempted'; }
  catch (_) { negative.beacon='blocked'; }
  try { new WebSocket('ws://127.0.0.1:18791/attempt/websocket'); negative.websocket='unexpected construction'; }
  catch (_) { negative.websocket='blocked'; }
  try { new Worker(new URL('worker.js', document.baseURI)); negative.worker='unexpected construction'; }
  catch (_) { negative.worker='blocked'; }
  try { await navigator.serviceWorker.register(new URL('worker.js', document.baseURI)); negative.serviceWorker='unexpected success'; }
  catch (_) { negative.serviceWorker='blocked'; }
  negative.image='attempted'; negative.script='attempted'; negative.frame='attempted'; negative.form='attempted';
  reportNegative();
};
document.getElementById('navigation').onclick = () => {
  negative.navigation='attempted'; reportNegative();
  try { location.href = localTrap+'navigation'; } catch (_) { negative.navigation='blocked'; reportNegative(); }
};
'''
        files['worker.js'] = b'fetch("http://127.0.0.1:18791/attempt/worker", {credentials:"omit"});'
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


async def applications(root, *, adversarial=False):
    store = ProjectStore(f'sqlite:///{root / "fixture.db"}')
    store.initialize_for_tests()
    actor = Actor(tenant_id='browser-fixture', user_id='browser-fixture')
    with store.transaction() as session:
        session.add(Account(id=actor.user_id, email='browser-fixture@example.test', name='Fixture', password_hash='not-loginable'))
        session.add(OrganizationMember(tenant_id=actor.tenant_id, user_id=actor.user_id, role='owner'))
    project = store.create(actor, 'Trusted static browser fixture, not generated engineering evidence', 'create')
    submitted = store.submit(actor, project['projectId'], 'Publish trusted fixture bytes', 1, 'fixture')
    scope = RunScope(actor=actor, project_id=project['projectId'], run_id=submitted['runId'], revision=submitted['revision'],
        deadline_at=datetime.now(UTC) + timedelta(minutes=10))
    artifacts = ArtifactService(store, BlobStore(root / 'blobs'))
    task = TaskSpec(id='fixture', capability='application', objective='Trusted static browser fixture')
    refs = await artifacts.publish_native(scope, task, [{'path': 'outputs/companion-web.zip', 'role': 'output',
        'content': trusted_bundle(adversarial=adversarial), 'mediaType': 'application/zip'}],
        {'nativeJobId': 'trusted-fixture-only', 'requestHash': 'a'*64, 'imageDigest': 'sha256:' + 'b'*64})
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.status = 'succeeded'
        run.data = {**run.data, 'results': {'fixture': {'artifacts': [ref.model_dump(mode='json') for ref in refs]}}}
    service = CompanionPreviews(store, artifacts, secret='trusted-local-fixture-only-key-1234567890',
        api_origin=API, frontend_origin=WORKBENCH, preview_origin=PREVIEW, enabled=True, ttl_seconds=600)
    workbench = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    trap = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    observations = []
    workbench.state.trap_app = trap
    workbench.state.trap_observations = observations

    @trap.api_route('/attempt/{kind}', methods=['GET', 'POST', 'OPTIONS'])
    async def receive(request: Request, kind: str):
        # Deliberately retain no body, URL parameters, cookies or capability strings.
        observations.append({'kind': kind[:40], 'method': request.method})
        return JSONResponse({'received': True}, headers={'Access-Control-Allow-Origin': '*'})

    @trap.websocket('/attempt/websocket')
    async def websocket(socket: WebSocket):
        observations.append({'kind': 'websocket', 'method': 'WEBSOCKET'})
        await socket.accept()
        await socket.close()

    @workbench.get('/observations')
    def observed():
        return {'scope': 'trusted-local-negative-fixture', 'received': observations}

    @workbench.get('/')
    def page():
        lease = service.issue(actor, scope.project_id, {'artifactId': refs[0].id})
        # Mirrors the unchanged widget's sandbox, credentialless and referrer settings.
        monitor = '''<h2>Local trap requests (must remain empty)</h2><pre id="trap">[]</pre>
<h2>Fixture-reported attempts (not independent evidence)</h2><pre id="attempts">No attempts yet</pre>
<script>
setInterval(async () => { document.getElementById('trap').textContent =
  JSON.stringify(await (await fetch('/observations')).json(), null, 2); }, 500);
window.addEventListener('message', (event) => {
  if (event.origin === 'null' && event.data && event.data.kind === 'kleos-trusted-browser-fixture')
    document.getElementById('attempts').textContent = JSON.stringify(event.data.observations, null, 2);
});
</script>''' if adversarial else ''
        return HTMLResponse('<!doctype html><html><body><h1>Browser contract fixture (not Flutter qualification)</h1>'
            '<iframe title="Built companion app" sandbox="allow-scripts" credentialless referrerpolicy="no-referrer" '
            'style="width:800px;height:500px;border:1px solid #999" src="' + html.escape(lease['url'], quote=True)
            + '"></iframe>' + monitor + '</body></html>', headers={'Cache-Control': 'no-store'})
    return store, workbench, create_preview_app(service)


async def main(adversarial=False):
    root = Path(tempfile.mkdtemp(prefix='kleos-companion-browser-fixture-'))
    store, workbench, preview = await applications(root, adversarial=adversarial)
    bindings = [(workbench, 18792), (preview, 18793)]
    if adversarial:
        bindings.append((workbench.state.trap_app, 18791))
    servers = [uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, access_log=False, log_level='warning',
        proxy_headers=False)) for app, port in bindings]
    print(json.dumps({'frontendUrl': WORKBENCH, 'previewOrigin': PREVIEW, 'fixtureOnly': True,
        'adversarial': adversarial}), flush=True)
    try:
        await asyncio.gather(*(server.serve() for server in servers))
    finally:
        store.engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adversarial', action='store_true', help='Enable local-only negative probes and loopback trap server')
    args = parser.parse_args()
    asyncio.run(main(args.adversarial))
