"""Separate-origin, capability-bound static companion previews; no server code execution."""
import base64
import hashlib
import hmac
import html
import io
import ipaddress
import json
import re
import secrets
import stat
import struct
import threading
import time
import unicodedata
import zipfile
import zlib
from contextlib import contextmanager
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from kleos.access.service import Account, OrganizationMember
from kleos.api.companion_bootstrap import TRANSFORM_VERSION, preview_bootstrap
from kleos.api.previews import EngineeringPreviews
from kleos.artifacts.service import Artifact, NativePublication
from kleos.contracts.models import Actor, ArtifactRef, DomainError
from kleos.projects.store import fingerprint
from kleos.validation.service import _ref

MAX_ZIP = 32 * 1024**2
MAX_FILE = 32 * 1024**2
MAX_TOTAL = 128 * 1024**2
MAX_ENTRIES = 1024
MAX_HTML = 256 * 1024
_CENTRAL = struct.Struct('<4s6H3I5H2I')
_END = struct.Struct('<4s4H2IH')
_PERMISSIONS = ', '.join(name + '=()' for name in ('camera', 'microphone', 'geolocation', 'clipboard-read',
    'clipboard-write', 'payment', 'usb', 'serial', 'bluetooth', 'hid', 'midi', 'display-capture',
    'fullscreen', 'screen-wake-lock', 'accelerometer', 'gyroscope', 'magnetometer', 'xr-spatial-tracking'))
_TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
    '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
    '.json': 'application/json', '.wasm': 'application/wasm', '.png': 'image/png', '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif', '.ico': 'image/x-icon',
    '.svg': 'image/svg+xml', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf',
    '.otf': 'font/otf', '.bin': 'application/octet-stream', '.txt': 'text/plain; charset=utf-8'}


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
            or any(ord(c) < 33 for c in value)):
        raise ValueError('Use an explicit HTTP(S) origin without credentials or paths')
    host = parsed.hostname.lower().rstrip('.')
    if not host.isascii() or '%' in host:
        raise ValueError('Use canonical ASCII hostnames or IP addresses')
    try:
        address = ipaddress.ip_address(host)
        host = '[' + address.compressed + ']' if address.version == 6 else str(address)
    except ValueError:
        if (re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)', host.rsplit('.', 1)[-1])
                or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in host.split('.'))):
            raise ValueError('Use an unambiguous canonical hostname') from None
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80)
    if not 1 <= port <= 65535:
        raise ValueError('Origin port is outside its valid range')
    return parsed.scheme + '://' + host + (':' + str(port) if port != (443 if parsed.scheme == 'https' else 80) else '')


def _bad(message='The application bundle is not supported by the bounded preview host'):
    return DomainError(message, 'companion_bundle_invalid', 422)


def member_path(name):
    if (not isinstance(name, str) or not name or len(name) > 512 or name.startswith('/')
            or any(c in name for c in '\\:%?#') or unicodedata.normalize('NFC', name) != name
            or any(unicodedata.category(c) in {'Cc', 'Cf', 'Cs'} for c in name)
            or any(part in {'', '.', '..'} or part.endswith((' ', '.')) for part in name.split('/'))):
        raise _bad('Invalid or ambiguous preview member path')
    return name


class WebBundle:
    """Parse bounded ZIP metadata before allocating ZipInfo objects; read one asset at a time."""
    def __init__(self, raw):
        if not isinstance(raw, bytes) or len(raw) > MAX_ZIP:
            raise _bad()
        self.raw = raw
        try:
            end = raw.rfind(b'PK\x05\x06', max(0, len(raw) - 65557))
            if end < 0 or end + _END.size > len(raw):
                raise _bad()
            _, disk, cd_disk, disk_count, count, size, start, comment = _END.unpack_from(raw, end)
            if (disk or cd_disk or disk_count != count or not 1 <= count <= MAX_ENTRIES
                    or start + size != end or end + _END.size + comment != len(raw)
                    or not raw.startswith(b'PK\x03\x04')):
                raise _bad()
            cursor, total, seen, offsets, self.members = start, 0, {}, set(), {}
            while cursor < end:
                if len(seen) >= count or cursor + _CENTRAL.size > end:
                    raise _bad()
                fields = _CENTRAL.unpack_from(raw, cursor)
                sig, made, needed, flags, method, _, _, crc, compressed, length, names, extras, comments, volume, _, attrs, local = fields
                boundary = cursor + _CENTRAL.size + names + extras + comments
                if (sig != b'PK\x01\x02' or boundary > end or not names or needed >= 45 or volume
                        or flags & ~0x80e or method not in {0, 8} or length > MAX_FILE
                        or compressed > MAX_ZIP or length > max(1, compressed) * 200
                        or local in offsets or local + 30 > start or raw[local:local + 4] != b'PK\x03\x04'):
                    raise _bad()
                offsets.add(local)
                name_bytes = raw[cursor + _CENTRAL.size:cursor + _CENTRAL.size + names]
                name = name_bytes.decode('utf-8' if flags & 0x800 else 'cp437')
                directory = name.endswith('/')
                path = member_path(name[:-1] if directory else name)
                folded = path.casefold()
                kind = stat.S_IFMT(attrs >> 16) if made >> 8 == 3 else 0
                if (folded in seen or kind not in {0, stat.S_IFDIR if directory else stat.S_IFREG}
                        or attrs & 0x400 or (attrs & 0x10 and not directory) or (directory and length)):
                    raise _bad()
                seen[folded] = (path, directory)
                extra_cursor = cursor + _CENTRAL.size + names
                extra_end = extra_cursor + extras
                while extra_cursor < extra_end:
                    if extra_cursor + 4 > extra_end:
                        raise _bad()
                    tag, amount = struct.unpack_from('<HH', raw, extra_cursor)
                    extra_cursor += 4 + amount
                    if extra_cursor > extra_end or tag in {0x0001, 0x7075}:
                        raise _bad()
                total += length
                if total > MAX_TOTAL:
                    raise _bad()
                if not directory:
                    self.members[path] = (length, crc)
                cursor = boundary
            if cursor != end or len(seen) != count or 'index.html' not in self.members:
                raise _bad()
            prefixes = {}
            for path, _ in seen.values():
                parts = path.split('/')
                for n in range(1, len(parts) + 1):
                    prefix = '/'.join(parts[:n])
                    previous = prefixes.setdefault(prefix.casefold(), prefix)
                    if previous != prefix or (n < len(parts) and prefix in self.members):
                        raise _bad()
            if self.members['index.html'][0] > MAX_HTML:
                raise _bad()
            # Verify the entry document now; other members are checked when requested.
            self.read('index.html').decode('utf-8', errors='strict')
        except DomainError:
            raise
        except (ValueError, UnicodeError, struct.error, zipfile.BadZipFile, RuntimeError, zlib.error):
            raise _bad() from None

    def read(self, name):
        member_path(name)
        if name not in self.members:
            raise DomainError('Preview asset not found', 'not_found', 404)
        try:
            with zipfile.ZipFile(io.BytesIO(self.raw)) as archive:
                info = archive.getinfo(name)
                if (info.file_size, info.CRC) != self.members[name]:
                    raise _bad()
                with archive.open(info) as stream:
                    content = bytearray()
                    while chunk := stream.read(min(65536, MAX_FILE + 1 - len(content))):
                        content.extend(chunk)
                        if len(content) > MAX_FILE:
                            raise _bad()
                if len(content) != info.file_size:
                    raise _bad()
                return bytes(content)
        except DomainError:
            raise
        except (ValueError, KeyError, zipfile.BadZipFile, RuntimeError, zlib.error, EOFError):
            raise _bad() from None


DOCUMENT_VIEW_VERSION = 'ticket-base-anonymous-scripts-v2'


class _EntryDocument(HTMLParser):
    """Remove source bases and install the lease base before any asset URL is parsed."""
    def __init__(self, base):
        super().__init__(convert_charrefs=False)
        self.parts = ['<!doctype html><base href="' + html.escape(base, quote=True) + '">']

    def _start(self, tag, attrs, end):
        if tag.lower() == 'base':
            return
        if tag.lower() == 'script':
            # Classic script fetch options also govern dynamic module import credentials.
            # Re-encode parsed attribute values and replace every existing credentials mode.
            values = [(name, value) for name, value in attrs if name.lower() != 'crossorigin']
            values.append(('crossorigin', 'anonymous'))
            encoded = ''.join(' ' + name + ('="' + html.escape(value, quote=True) + '"'
                if value is not None else '') for name, value in values)
            self.parts.append('<script' + encoded + end)
        else:
            self.parts.append(self.get_starttag_text())

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, '>')

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, '/>')

    def handle_endtag(self, tag):
        if tag.lower() != 'base':
            self.parts.append('</' + tag + '>')

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append('&' + name + ';')

    def handle_charref(self, name):
        self.parts.append('&#' + name + ';')

    def handle_comment(self, data):
        self.parts.append('<!--' + data + '-->')


class CompanionPreviews:
    def __init__(self, projects, artifacts, *, secret, api_origin, frontend_origin, preview_origin,
                 enabled=False, ttl_seconds=300, clock=time.time):
        self.projects, self.artifacts, self.enabled = projects, artifacts, enabled
        self.selection = EngineeringPreviews(projects, artifacts)
        self.clock, self.ttl_seconds = clock, ttl_seconds
        self.api_origin, self.frontend_origin, self.preview_origin = map(origin, (api_origin, frontend_origin, preview_origin))
        if enabled and (len({self.api_origin, self.frontend_origin, self.preview_origin}) != 3
                or not isinstance(secret, str) or len(secret) < 32 or not 30 <= ttl_seconds <= 600):
            raise ValueError('Preview hosting requires distinct origins, a strong key and bounded leases')
        self.aad = ('kleos-companion-preview-v1|' + TRANSFORM_VERSION + '|' + DOCUMENT_VIEW_VERSION + '|' + self.api_origin + '|' + self.frontend_origin + '|' + self.preview_origin).encode()
        self.key = hmac.new((secret or '').encode(), b'kleos-companion-preview-key-v1', hashlib.sha256).digest()
        self.gate = threading.BoundedSemaphore(2)
        self.admission = threading.BoundedSemaphore(10)

    @contextmanager
    def _capacity(self, *, wait=False):
        # Metadata already holds the project lock and MUST stay nonblocking.
        # Asset/lease handlers call this before their transaction, so a bounded
        # queue lets a normal parallel bootstrap finish without lock inversion.
        admitted = self.admission.acquire(blocking=False) if wait else False
        if wait and not admitted:
            raise DomainError('Preview capacity is busy; try again shortly', 'preview_capacity', 503)
        acquired = False
        try:
            acquired = self.gate.acquire(timeout=5) if wait else self.gate.acquire(blocking=False)
            if not acquired:
                raise DomainError('Preview capacity is busy; try again shortly', 'preview_capacity', 503)
            yield
        finally:
            if acquired:
                self.gate.release()
            if admitted:
                self.admission.release()

    def _enabled(self):
        if not self.enabled:
            raise DomainError('Separate-origin companion previews are not enabled', 'companion_preview_unavailable', 503)

    def metadata(self, row, raw):
        if not self.enabled or row.lane != 'application' or row.name != 'outputs/companion-web.zip' or row.details.get('role') != 'output':
            return {'previewAvailable': False}
        if len(raw) != row.size or hashlib.sha256(raw).hexdigest() != row.sha256:
            raise DomainError('Application bundle content changed', 'artifact_conflict')
        with self._capacity():
            WebBundle(raw)
        return {'previewAvailable': True, 'previewArtifactId': row.id, 'previewSha256': row.sha256}

    def _bound(self, session, actor, project_id, artifact_id):
        project = self.projects.authorized(session, actor, project_id)
        membership = session.get(OrganizationMember, (actor.tenant_id, actor.user_id), with_for_update=True)
        if membership is None or session.get(Account, actor.user_id) is None:
            raise DomainError('Preview membership is no longer available', 'not_found', 404)
        selected, accepted = self.selection._selection(session, project)
        reference = next((ArtifactRef.model_validate(item) for item in selected if item['id'] == artifact_id), None)
        row = session.get(Artifact, artifact_id) if reference else None
        if (row is None or row.project_id != project.id or _ref(row) != reference or row.lane != 'application'
                or row.name != 'outputs/companion-web.zip' or row.details.get('role') != 'output'
                or row.details.get('origin') != 'native_candidate'):
            raise DomainError('Select a current published native web application bundle', 'companion_artifact_unavailable', 404)
        receipt = session.scalar(select(NativePublication).where(
            NativePublication.run_id == row.details.get('runId'), NativePublication.task_id == row.details.get('taskId'),
            NativePublication.job_id == row.details.get('nativeJobId')))
        if receipt is None or reference.model_dump(mode='json') not in receipt.references:
            raise DomainError('Application publication binding changed', 'artifact_conflict')
        binding = fingerprint({'ref': reference.model_dump(mode='json'), 'receipt': receipt.fingerprint,
            'receiptId': receipt.id, 'acceptedReleaseId': project.data.get('acceptedReleaseId') if accepted else None})
        return project, row, binding

    def issue(self, actor, project_id, body):
        self._enabled()
        if (not isinstance(body, dict) or set(body) != {'artifactId'} or not isinstance(body['artifactId'], str)
                or not 1 <= len(body['artifactId']) <= 80):
            raise DomainError('Select one web application artifact', 'invalid_request', 422)
        with self._capacity(wait=True), self.projects.transaction() as session:
            project, row, binding = self._bound(session, actor, project_id, body['artifactId'])
            raw = self.artifacts.blobs.get(row.sha256)
            if len(raw) != row.size:
                raise DomainError('Application bundle size changed', 'artifact_conflict')
            WebBundle(raw)
            now = int(self.clock())
            payload = {'actor': actor.model_dump(mode='json'), 'projectId': project.id, 'revision': project.context_revision,
                'artifactId': row.id, 'sha256': row.sha256, 'publicationHash': binding,
                'issuedAt': now, 'expiresAt': now + self.ttl_seconds}
            nonce = secrets.token_bytes(12)
            token = base64.urlsafe_b64encode(nonce + AESGCM(self.key).encrypt(nonce,
                json.dumps(payload, separators=(',', ':')).encode(), self.aad)).decode().rstrip('=')
            return {'url': self.preview_origin + '/preview/' + token + '/',
                'expiresAt': datetime.fromtimestamp(payload['expiresAt'], UTC).isoformat(),
                'artifactId': row.id, 'sha256': row.sha256, 'mode': 'isolated-web-build',
                'limitations': ['Interactive candidate web build; no engineering or physical acceptance.',
                    'Network, persistent storage, service workers, device APIs and external navigation are restricted.',
                    'Reviewed Flutter startup may use a versioned preview-only view with service-worker startup removed; stored files stay unchanged.',
                    'Browser compatibility and isolation require separate browser qualification.']}

    def _ticket(self, token):
        self._enabled()
        try:
            if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,4096}', token):
                raise ValueError()
            raw = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
            payload = json.loads(AESGCM(self.key).decrypt(raw[:12], raw[12:], self.aad))
            if (set(payload) != {'actor', 'projectId', 'revision', 'artifactId', 'sha256', 'publicationHash', 'issuedAt', 'expiresAt'}
                    or type(payload['issuedAt']) is not int or type(payload['expiresAt']) is not int
                    or not 0 < payload['expiresAt'] - payload['issuedAt'] <= self.ttl_seconds
                    or not payload['issuedAt'] <= self.clock() < payload['expiresAt']):
                raise ValueError()
            return payload, Actor.model_validate(payload['actor'])
        except (ValueError, TypeError, KeyError, InvalidTag, UnicodeError):
            raise DomainError('Preview link is invalid or expired', 'preview_link_invalid', 404) from None

    def serve(self, token, path):
        payload, actor = self._ticket(token)
        with self._capacity(wait=True), self.projects.transaction() as session:
            project, row, binding = self._bound(session, actor, payload['projectId'], payload['artifactId'])
            if (project.context_revision != payload['revision'] or row.sha256 != payload['sha256']
                    or binding != payload['publicationHash']):
                raise DomainError('Preview context or publication changed', 'preview_link_stale', 404)
            derived_headers = {}
            base = self.preview_origin + '/preview/' + token + '/files/'
            if path == '':
                content = ('<!doctype html><html><head><meta charset="utf-8"><title>Companion preview</title>'
                    '<style>html,body,iframe{margin:0;width:100%;height:100%;border:0;background:#09090b}</style></head>'
                    '<body><iframe title="Generated companion application" sandbox="allow-scripts" credentialless '
                    'referrerpolicy="no-referrer" src="' + html.escape(base + 'index.html', quote=True) + '"></iframe></body></html>').encode()
                mime = _TYPES['.html']
                csp = ("default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; frame-src " + base
                    + "; frame-ancestors " + self.frontend_origin + "; base-uri 'none'; form-action 'none'; sandbox allow-scripts")
            else:
                if not path.startswith('files/'):
                    raise DomainError('Preview asset not found', 'not_found', 404)
                name = member_path(path[6:])
                if (any(part.startswith('.') for part in name.split('/')) or name.endswith('.map')
                        or name.rsplit('/', 1)[-1] in {'flutter_service_worker.js', 'service-worker.js', 'sw.js'}):
                    raise DomainError('Preview asset is disabled', 'not_found', 404)
                raw = self.artifacts.blobs.get(row.sha256)
                if len(raw) != row.size:
                    raise DomainError('Application bundle size changed', 'artifact_conflict')
                content = WebBundle(raw).read(name)
                if name == 'flutter_bootstrap.js':
                    view = preview_bootstrap(content)
                    if view is not None:
                        content, derived_headers = view.content, view.headers()
                suffix = '.' + name.rsplit('.', 1)[-1].lower() if '.' in name else ''
                mime = _TYPES.get(suffix, 'application/octet-stream')
                if suffix == '.html':
                    if len(content) > MAX_HTML:
                        raise _bad()
                    parser = _EntryDocument(base)
                    parser.feed(content.decode('utf-8'))
                    content = ''.join(parser.parts).encode()
                csp = ("default-src 'none'; script-src " + base + " 'unsafe-inline' 'wasm-unsafe-eval'; "
                    "style-src " + base + " 'unsafe-inline'; img-src " + base + " data: blob:; font-src " + base
                    + " data:; connect-src " + base + "; media-src " + base + " blob:; base-uri " + base
                    + "; worker-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; sandbox allow-scripts")
            # Recheck expiry after bounded file IO; publication/membership remain row-locked.
            if self.clock() >= payload['expiresAt']:
                raise DomainError('Preview link expired', 'preview_link_invalid', 404)
            return content, mime, {**derived_headers, 'Content-Security-Policy': csp, 'Permissions-Policy': _PERMISSIONS,
                'Access-Control-Allow-Origin': 'null', 'Access-Control-Allow-Methods': 'GET, HEAD',
                'Cross-Origin-Resource-Policy': 'cross-origin', 'Cache-Control': 'no-store',
                'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff',
                'X-DNS-Prefetch-Control': 'off', 'Vary': 'Origin', 'Content-Type': mime}


def create_preview_app(service):
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.exception_handler(DomainError)
    async def failure(request, error):
        return JSONResponse({'code': error.code, 'message': error.message}, status_code=error.status,
            headers={'Cache-Control': 'no-store', 'Content-Security-Policy': "default-src 'none'; sandbox",
                'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'})

    @app.api_route('/preview/{token}/{path:path}', methods=['GET', 'HEAD'])
    def file(request: Request, token: str, path: str):
        try:
            requested_origin = origin(str(request.base_url))
        except ValueError:
            requested_origin = None
        if requested_origin != service.preview_origin:
            raise DomainError('Use the dedicated preview origin', 'preview_origin_invalid', 404)
        if request.headers.get('Service-Worker') or request.headers.get('Sec-Fetch-Dest') == 'serviceworker':
            raise DomainError('Service workers are disabled', 'not_found', 404)
        content, mime, headers = service.serve(token, path)
        return Response(content=b'' if request.method == 'HEAD' else content, media_type=mime, headers=headers)

    return app
