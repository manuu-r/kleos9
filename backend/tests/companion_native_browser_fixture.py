"""Host preserved exact-image Flutter candidates locally; never build or execute them on host."""
import argparse
import asyncio
import html
import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from kleos.access.service import Account, OrganizationMember
from kleos.api.companion import CompanionPreviews, WebBundle, create_preview_app
from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor
from kleos.execution.gateway import NativeJob
from kleos.execution.protocol import digest, fingerprint
from kleos.execution.web_bundle import FONTS, LICENSE_SHA256, PROVENANCE
from kleos.projects.store import Objective, ProjectStore

IMAGE = 'sha256:7c5aedd853f6be70c0d629a90134297fdf94716213cf32b3e2a743339776ea8d'
FONT_IMAGE = 'sha256:572fd5f8d64fd6d7a1bed7c955ee33c8223529a43fc0dedd8e53dcc81328e315'
SUPPORTED_IMAGES = frozenset({IMAGE, FONT_IMAGE})
SOURCES = '6f99742b69481daf142a12af0d07ac06d8c6a0dafe14fa8e9d59a6e295987c4d'
WORKBENCH = 'http://127.0.0.1:18794'
PREVIEW = 'http://127.0.0.1:18795'


def inspect_evidence(root):
    report = json.loads((root / 'evidence.json').read_bytes())
    sources = json.loads((root / 'fixture-sources.json').read_bytes())
    if (report.get('qualified') is not True or report.get('sourceOverride') is not False
            or report.get('imageId') not in SUPPORTED_IMAGES or report.get('fixtureSourcesSha256') != SOURCES
            or fingerprint(sources) != SOURCES or report.get('finalCapacity', {}).get('jobs') != 0):
        raise ValueError('Exact-image fixture qualification/provenance is missing')
    refs = [ref for ref in report['artifacts'] if ref['path'] == 'outputs/companion-web.zip']
    if len(refs) != 1:
        raise ValueError('Expected one exact published Flutter bundle')
    reference = refs[0]
    raw = (root / 'companion-web.zip').read_bytes()
    if digest(raw) != reference['sha256'] or len(raw) != reference['size']:
        raise ValueError('Preserved Flutter bundle hash/size mismatch')
    if BlobStore(root / 'blobs').get(reference['sha256']) != raw:
        raise ValueError('Published immutable artifact differs from preserved bundle')
    if report['imageId'] == FONT_IMAGE:
        bundle = WebBundle(raw)
        packaged = json.loads(bundle.read(PROVENANCE))
        if report.get('sourceClosureUnchanged') is not True or packaged != report.get('fontPackaging'):
            raise ValueError('Bundled font qualification source/provenance is missing')
        if packaged.get('mode') != 'pinned-sdk-default' or len(packaged.get('fonts', [])) != len(FONTS):
            raise ValueError('Unexpected bundled font contract')
        for item, expected in zip(packaged['fonts'], FONTS, strict=True):
            if digest(bundle.read(item['path'])) != item['sha256'] or item['sha256'] != expected[3]:
                raise ValueError('Bundled font digest differs')
        if digest(bundle.read(packaged['license']['path'])) != LICENSE_SHA256:
            raise ValueError('Bundled font license differs')
    return report, sources, reference


def applications(root):
    report, sources, ref = inspect_evidence(root)
    projects = ProjectStore(f'sqlite:///{root / "state.db"}')
    actor = Actor(tenant_id='qualification', user_id='fixture')
    with projects.transaction() as session:
        jobs = list(session.scalars(select(NativeJob)))
        if len(jobs) != 1:
            raise ValueError('Unexpected native qualification job closure')
        job = jobs[0]
        if (job.request['imageDigest'] != report['imageId'] or job.status != 'finished'
                or job.source_manifest != {path: digest(content.encode()) for path, content in sources.items()}):
            raise ValueError('Native source/image/finish authority does not match evidence')
        run = session.get(Objective, job.run_id)
        if run is None or run.project_id != job.project_id:
            raise ValueError('Qualification run is missing')
        # Only the isolated qualification ledger is adapted to expose its actual finished candidates.
        # This is not a release, model decision, physical acceptance or production state mutation.
        run.status = 'succeeded'
        run.data = {**run.data, 'results': {job.task_id: {'artifacts': report['artifacts']}}}
        if session.get(Account, actor.user_id) is None:
            session.add(Account(id=actor.user_id, email='fixture@example.test', name='Offline qualification', password_hash='not-loginable'))
        if session.get(OrganizationMember, (actor.tenant_id, actor.user_id)) is None:
            session.add(OrganizationMember(tenant_id=actor.tenant_id, user_id=actor.user_id, role='owner'))
        project_id = job.project_id
    service = CompanionPreviews(projects, ArtifactService(projects, BlobStore(root / 'blobs')),
        secret='trusted-native-browser-fixture-only-1234567890', api_origin='http://127.0.0.1:18791',
        frontend_origin=WORKBENCH, preview_origin=PREVIEW, enabled=True, ttl_seconds=600)
    workbench = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    preview_app = create_preview_app(service)
    observed = []

    @preview_app.middleware('http')
    async def observe(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path.split('/files/', 1)[-1] if '/files/' in request.url.path else 'wrapper'
        if path in {'wrapper', 'index.html', 'flutter_bootstrap.js', 'main.dart.js',
                'canvaskit/chromium/canvaskit.js', 'canvaskit/chromium/canvaskit.wasm', 'assets/FontManifest.json'}:
            observed.append({'asset': path, 'origin': 'null' if request.headers.get('Origin') == 'null' else 'other-or-absent',
                'mode': request.headers.get('Sec-Fetch-Mode', '')[:20],
                'destination': request.headers.get('Sec-Fetch-Dest', '')[:20], 'status': response.status_code,
                'mime': response.headers.get('Content-Type'), 'allowOrigin': response.headers.get('Access-Control-Allow-Origin')})
            del observed[:-64]
        return response

    @workbench.get('/request-observations')
    def request_observations():
        return {'scope': 'trusted-local-diagnostic-only', 'requests': observed}


    @workbench.get('/')
    def page():
        lease = service.issue(actor, project_id, {'artifactId': ref['id']})
        return HTMLResponse('<!doctype html><html><body style="background:white;color:black">'
            '<h1>Exact-image compiled Flutter candidate</h1><p>Browser qualification in progress; not an accepted release.</p>'
            '<iframe title="Built companion app" sandbox="allow-scripts" credentialless referrerpolicy="no-referrer" '
            'style="width:1000px;height:700px;border:1px solid #999" src="' + html.escape(lease['url'], quote=True)
            + '"></iframe></body></html>', headers={'Cache-Control': 'no-store'})
    return projects, workbench, preview_app, {'imageId': report['imageId'], 'fixtureSourcesSha256': SOURCES,
        'artifactSha256': ref['sha256'], 'artifactSize': ref['size'], 'frontendUrl': WORKBENCH,
        'previewOrigin': PREVIEW, 'browserQualified': False, 'nativeRunId': job.run_id}


async def main(root):
    projects, workbench, preview, provenance = applications(root.resolve())
    print(json.dumps(provenance), flush=True)
    servers = [uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, access_log=False,
        log_level='warning', proxy_headers=False)) for app, port in ((workbench, 18794), (preview, 18795))]
    try:
        await asyncio.gather(*(server.serve() for server in servers))
    finally:
        projects.engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    asyncio.run(main(parser.parse_args().work))
