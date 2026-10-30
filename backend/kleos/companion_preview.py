"""Private, short-lived views of real compiled companion bundles.

The separate static server never executes generated code. Browser code runs in
an opaque sandbox nested inside a trusted navigation-constrained wrapper.
Original immutable bundles remain unchanged for release packaging.
"""
import html
import io
import mimetypes
import re
import stat
import time
import zipfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import Field
from sqlalchemy import select

from .auth import resolve_membership
from .contracts import DomainError, WireModel
from .db import Artifact, Database, NativeJob
from .domain import emit, get_project
from .storage import ArtifactStore, digest

TICKET_SECONDS = 600
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_ENTRY_BYTES = 32 * 1024 * 1024
MAX_HTML_BYTES = 1024 * 1024
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 2000


class CompanionPreviewRequest(WireModel):
    artifact_id: str = Field(min_length=1, max_length=80)


class CompanionPreviewView(WireModel):
    url: str
    expires_at: str
    artifact_id: str
    sha256: str
    mode: str = 'isolated-web-build'
    limitations: list[str]


def safe_member(name):
    path = PurePosixPath(name)
    if (not name or len(name) > 300 or path.is_absolute() or '..' in path.parts
            or '\\' in name or ':' in name or any(ord(char) < 32 for char in name)
            or path.as_posix() != name.rstrip('/')):
        raise ValueError('Unsafe companion bundle member')
    return name.rstrip('/')


class CompanionArchive:
    def __init__(self, content):
        if len(content) > MAX_BUNDLE_BYTES:
            raise ValueError('Companion bundle exceeds its compressed ceiling')
        self.content = content
        self.members = {}
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                raise ValueError('Companion bundle has too many entries')
            total = 0
            for item in entries:
                name = safe_member(item.filename)
                mode = (item.external_attr >> 16) & 0xffff
                if (name in self.members or stat.S_ISLNK(mode) or item.flag_bits & 1
                        or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                        or item.file_size > MAX_ENTRY_BYTES):
                    raise ValueError('Companion bundle has ambiguous or unsupported entries')
                if mimetypes.guess_type(name)[0] == 'text/html' and item.file_size > MAX_HTML_BYTES:
                    raise ValueError('Companion HTML entry exceeds its preview ceiling')
                total += item.file_size
                if total > MAX_UNPACKED_BYTES:
                    raise ValueError('Companion bundle exceeds its unpacked ceiling')
                self.members[name] = item
            if not all(name in self.members for name in ('index.html', 'main.dart.js', 'flutter_bootstrap.js')):
                raise ValueError('Companion bundle lacks required compiled Flutter files')

    def read(self, name):
        item = self.members.get(safe_member(name))
        if item is None or item.is_dir():
            raise DomainError('Preview file not found', 404, 'not_found')
        with zipfile.ZipFile(io.BytesIO(self.content)) as archive, archive.open(item) as stream:
            content = stream.read(MAX_ENTRY_BYTES + 1)
        if len(content) > MAX_ENTRY_BYTES or len(content) != item.file_size:
            raise ValueError('Companion member failed its size check')
        return content


def built_companion(session, project, artifact_id):
    artifact = session.scalar(select(Artifact).where(
        Artifact.id == artifact_id, Artifact.project_id == project.id,
        Artifact.tenant_id == project.tenant_id, Artifact.revision == project.engineering_revision,
        Artifact.lane == 'application', Artifact.status.in_(['candidate', 'accepted'])))
    job_id = artifact.provenance.get('nativeJobId') if artifact and isinstance(artifact.provenance, dict) else None
    job = session.get(NativeJob, job_id) if job_id else None
    if (not artifact or not artifact.name.endswith('/companion-web.zip') or artifact.size > MAX_BUNDLE_BYTES
            or not job or job.tenant_id != project.tenant_id or job.status != 'succeeded'
            or job.purpose != 'build' or job.attempt_id != artifact.attempt_id):
        raise DomainError('A successful current companion web build is required', 409, 'preview_unavailable')
    return artifact


class CompanionPreviews:
    def __init__(self, database, settings, store):
        self.database, self.settings, self.store = database, settings, store
        self.cache = OrderedDict()

    def signer(self):
        key = self.settings.companion_preview_signing_key
        if not key or not self.settings.companion_preview_base_url:
            raise DomainError('The isolated companion preview service is not configured', 503, 'preview_unconfigured')
        return URLSafeTimedSerializer(key.get_secret_value(), salt='companion-preview-v1')

    def archive(self, artifact):
        key = (artifact.tenant_id, artifact.id, artifact.sha256)
        if key not in self.cache:
            content = self.store.get_limited(artifact.key, MAX_BUNDLE_BYTES)
            if len(content) != artifact.size or digest(content) != artifact.sha256:
                raise ValueError('Companion bundle failed its immutable manifest check')
            self.cache[key] = CompanionArchive(content)
            while len(self.cache) > 4:
                self.cache.popitem(last=False)
        return self.cache[key]

    def mint(self, principal, project_id, artifact_id):
        signer = self.signer()
        with self.database.transaction() as session:
            project = get_project(session, project_id, principal)
            artifact = built_companion(session, project, artifact_id)
            try:
                self.archive(artifact)
            except (ValueError, OSError, zipfile.BadZipFile) as exc:
                raise DomainError('The compiled companion bundle is unavailable or invalid', 409,
                                  'preview_unavailable') from exc
            expires = int(time.time()) + TICKET_SECONDS
            token = signer.dumps({'subject': principal.subject, 'tenantId': principal.tenant_id,
                'projectId': project.id, 'revision': project.engineering_revision,
                'artifactId': artifact.id, 'sha256': artifact.sha256, 'expires': expires})
            # Record access without capturing the bearer ticket or URL.
            emit(session, project, 'audit.companionPreviewOpened', {'actor': principal.subject,
                 'artifactId': artifact.id, 'sha256': artifact.sha256})
            return {'url': self.settings.companion_preview_base_url.rstrip('/') + '/p/' + token + '/view',
                    'expiresAt': datetime.fromtimestamp(expires, UTC).isoformat(),
                    'artifactId': artifact.id, 'sha256': artifact.sha256, 'mode': 'isolated-web-build',
                    'limitations': ['This is the real compiled web build; runtime features depend on its implementation.',
                                    'Device permissions, external resource/API fetches, forms, and popups are restricted.',
                                    'Preview access expires after ten minutes and grants no release approval.']}

    def authorize(self, ticket):
        try:
            claim = self.signer().loads(ticket, max_age=TICKET_SECONDS)
            if not isinstance(claim, dict) or not isinstance(claim.get('expires'), int) or claim['expires'] <= time.time():
                raise ValueError('Expired ticket')
            with self.database.transaction(read_only=True) as session:
                principal = resolve_membership(session, claim['subject'], claim['tenantId'])
                project = get_project(session, claim['projectId'], principal, lock=False)
                artifact = built_companion(session, project, claim['artifactId'])
                if project.engineering_revision != claim['revision'] or artifact.sha256 != claim['sha256']:
                    raise ValueError('Superseded preview')
                return self.archive(artifact), artifact.sha256
        except (BadSignature, DomainError, KeyError, TypeError, ValueError, OSError, zipfile.BadZipFile) as exc:
            raise DomainError('Preview expired or unavailable; reopen it from the project', 403,
                              'preview_unavailable') from exc


def create_preview_app(settings, database=None, storage=None):
    """Serve on a dedicated origin; never mount this on the authenticated API."""
    db = database or Database(settings.database_url)
    service = CompanionPreviews(db, settings, storage or ArtifactStore(settings))

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.cache.clear()
        if database is None:
            db.close()

    app = FastAPI(title='Kleos isolated companion preview', docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.previews = service
    origin = (settings.companion_preview_base_url or '').rstrip('/')
    parents = []
    for candidate in [settings.frontend_url, *settings.cors_origins]:
        parsed = urlsplit(candidate)
        if parsed.scheme in {'http', 'https'} and parsed.netloc:
            parents.append(parsed.scheme + '://' + parsed.netloc)
    ancestors = ' '.join(sorted(set(parents))) or "'none'"

    @app.exception_handler(DomainError)
    async def error(request, exc):
        return JSONResponse(status_code=exc.status, content={'message': exc.message, 'code': exc.code},
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    @app.api_route('/p/{ticket}/{name:path}', methods=['GET', 'HEAD', 'OPTIONS'])
    def asset(request: Request, ticket: str, name: str):
        archive, archive_hash = service.authorize(ticket)
        prefix = origin + '/p/' + ticket + '/'
        headers = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                   'Referrer-Policy': 'no-referrer', 'Access-Control-Allow-Origin': 'null',
                   'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
                   'Cross-Origin-Resource-Policy': 'cross-origin',
                   'Cross-Origin-Opener-Policy': 'same-origin',
                   'Origin-Agent-Cluster': '?1',
                   'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), usb=(), bluetooth=(), payment=()',
                   'X-Kleos-Source-Sha256': archive_hash}
        if request.method == 'OPTIONS':
            return Response(status_code=204, headers=headers)
        if name == 'view':
            # The generated inner frame cannot navigate outside this ticket's
            # path, even via location assignment, because this trusted parent
            # applies frame-src and both frames have distinct opaque origins.
            headers['Content-Security-Policy'] = (
                "default-src 'none'; style-src 'unsafe-inline'; frame-src " + prefix + "; "
                "base-uri 'none'; form-action 'none'; sandbox allow-scripts; frame-ancestors " + ancestors)
            body = ('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<style>html,body,iframe{width:100%;height:100%;margin:0;border:0;display:block;overflow:hidden}'
                    '</style></head><body><iframe title="Built companion app" sandbox="allow-scripts" '
                    'referrerpolicy="no-referrer" allow="camera \'none\'; microphone \'none\'; geolocation \'none\'" '
                    'src="' + html.escape(prefix + 'index.html', quote=True) + '"></iframe></body></html>').encode()
            media = 'text/html'
        else:
            try:
                body = archive.read(name or 'index.html')
            except (ValueError, OSError, zipfile.BadZipFile) as exc:
                raise DomainError('Preview file unavailable', 404, 'not_found') from exc
            # Explicit origin/path sources work with opaque sandbox origins.
            headers['Content-Security-Policy'] = (
                "default-src 'none'; script-src " + prefix + " 'unsafe-inline' 'wasm-unsafe-eval'; "
                "style-src 'unsafe-inline' " + prefix + "; img-src " + prefix + " data: blob:; "
                "font-src " + prefix + " data:; connect-src " + prefix + "; worker-src blob: " + prefix + "; "
                "frame-src 'none'; object-src 'none'; base-uri " + prefix + "; form-action 'none'; "
                "sandbox allow-scripts")
            media = {'.js': 'application/javascript', '.mjs': 'application/javascript',
                     '.wasm': 'application/wasm', '.json': 'application/json', '.ttf': 'font/ttf',
                     '.woff2': 'font/woff2'}.get(PurePosixPath(name).suffix,
                                               mimetypes.guess_type(name)[0] or 'application/octet-stream')
            if media == 'text/html':
                if len(body) > MAX_HTML_BYTES:
                    raise DomainError('Preview HTML entry exceeds its supported size', 409)
                try:
                    document = body.decode('utf-8')
                except UnicodeError as exc:
                    raise DomainError('Preview entrypoint is not UTF-8', 409) from exc
                base = '<base href="' + html.escape(prefix, quote=True) + '">'
                document, count = re.subn(r'<base\b[^>]*>', lambda _: base, document, count=1,
                                          flags=re.IGNORECASE)
                if not count:
                    document = base + document
                body = document.encode()
                headers['X-Kleos-Preview-Transform'] = 'base-url-only; source-bundle-unchanged'
        headers['Content-Length'] = str(len(body))
        return Response(content=b'' if request.method == 'HEAD' else body, media_type=media, headers=headers)

    return app
