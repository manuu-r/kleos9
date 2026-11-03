"""Durable offline print preparation; no printer transport or physical authority."""
import asyncio
import base64
import hashlib
import io
import json
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from sqlalchemy import JSON, Float, Integer, String, func, select, text
from sqlalchemy.orm import Mapped, mapped_column

from kleos.artifacts.service import Artifact
from kleos.contracts.models import Actor, DomainError
from kleos.execution.security import daemon_socket_profile
from kleos.projects.store import Base, Membership, Objective, Project, fingerprint

PROTOCOL = 'kleos-slicer-v1'
MAX_BYTES = 32 * 1024**2
SLICER_RESOURCES = {'cpus': 2, 'memoryBytes': 4*1024**3, 'scratchBytes': 2*1024**3+64*1024**2, 'pids': 128}
BUNDLES = {
    'a1-mini-pla-04': ('N1', 'A1M', 'a43d98b86e6913c00fd56970f590b566783dcfe01d807fdaac320fcfd4394de4'),
    'a1-pla-04': ('N2S', 'A1', '18b43fe7f4e75d9ac00da4bf03853041b598eb00ad86ea02362160884aa5dd56'),
    'p1s-pla-04': ('C12', 'X1C', 'dc3c0fc0c438162dfdd569f9717878554c27ba47a785ac2ad521b4e31f55a00c'),
    # Bundle hash computed from the OrcaSlicer v2.4.2 profile files with the same flattening as `presets()`; the three
    # profiles above reproduce their pinned hashes from those files. Unqualified until the image is requalified.
    'x1c-pla-04': ('BL-P001', 'X1C', '0d4bf171031b85b96a57c4f6fb195fd48f5b043dea5c0e2204f38c6ed31af1ec'),
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class SlicingSettings:
    image: str
    control_root: Path
    qualified_profiles: tuple[str, ...] = ()
    docker_desktop: bool = False
    deployment: str = 'local'
    daemon_control_root: str | None = None

    def __post_init__(self):
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', self.image):
            raise ValueError('Slicer image must be an immutable image ID')
        if self.docker_desktop and self.deployment != 'local':
            raise ValueError('Docker Desktop socket policy is local-only')
        if any(profile not in BUNDLES for profile in self.qualified_profiles):
            raise ValueError('Unknown fixed slicer profile')

    def profile(self, ident):
        if ident not in self.qualified_profiles:
            raise DomainError('This slicer profile is not qualified for the configured image', 'capability_unavailable', 501)
        model, suffix, bundle = BUNDLES[ident]
        return {'printerModelId': model, 'nozzleMm': .4, 'material': 'PLA', 'bundleSha256': bundle,
                'image': self.image, 'filamentPreset': 'Bambu PLA Basic @BBL ' + suffix,
                'bedType': 'Textured PEI Plate', 'studioVersion': '2.8.2.61',
                'slicer': 'orca', 'slicerVersion': '2.4.2', 'qualified': True}


def validate_package(raw):
    """Independent bounded archive checks; tool output is still a digital candidate."""
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('Slicer package exceeds byte bounds')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if not infos or len(infos) > 128 or sum(info.file_size for info in infos) > 64 * 1024**2:
            raise ValueError('Slicer package expansion exceeds bounds')
        seen, gcodes = set(), []
        for info in infos:
            path = PurePosixPath(info.filename)
            if (info.filename in seen or path.is_absolute() or '\\' in info.filename
                    or any(p in {'', '.', '..'} for p in info.filename.rstrip('/').split('/'))
                    or info.file_size > MAX_BYTES or info.flag_bits & 1
                    or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Unsafe slicer archive member')
            seen.add(info.filename)
            if info.filename.endswith('.gcode'):
                gcodes.append(info)
        if len(gcodes) != 1 or not re.fullmatch(r'Metadata/plate_\d+\.gcode', gcodes[0].filename):
            raise ValueError('Expected one sliced plate')
        code = archive.read(gcodes[0]).decode('utf8')
        if '\0' in code or not re.search(r'^G[01] .*E[-+]?\d', code, re.MULTILINE):
            raise ValueError('Package has no extrusion path')
        if not any(name.endswith('.model') for name in seen):
            raise ValueError('Package lacks geometry')
        return {'gcodeBytes': len(code.encode()), 'packageBytes': len(raw), 'plateCount': 1,
                'archiveCheck': 'PASS', 'physicalAcceptance': False}


class DockerSlicingDriver:
    """Dedicated fixed protocol over the trusted Docker client; no model-selected commands."""
    def __init__(self, docker, settings):
        self.docker, self.settings = docker, settings

    async def start(self, job_id, source, extension, profile):
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('Invalid slicer job identity')
        settings, client = self.settings, self.docker.client
        info = await client.get('/info')
        info.raise_for_status()
        desktop = 'docker desktop' in info.json().get('OperatingSystem', '').lower()
        if desktop != settings.docker_desktop:
            raise RuntimeError('Docker Desktop requires the explicit local compatibility policy')
        metadata = await client.get('/images/' + profile['image'] + '/json')
        metadata.raise_for_status()
        metadata = metadata.json()
        if (metadata.get('Id') != profile['image'] or metadata.get('Config', {}).get('Labels', {}).get('kleos.slicer.protocol') != PROTOCOL):
            raise RuntimeError('Slicer image differs from its pinned protocol')
        request = {'jobId': job_id, 'source': base64.b64encode(source).decode(), 'sourceSha256': digest(source),
                   'extension': extension, 'image': profile['image'], 'profileId': profile['id'], 'bundleSha256': profile['bundleSha256']}
        root = settings.control_root.resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory = root / job_id
        if directory.exists():
            previous = (directory / 'request.json').read_bytes()
            if json.loads(previous) != request:
                raise RuntimeError('Slicer recovery request changed')
        else:
            directory.mkdir(mode=0o755)
            (directory / 'request.json').write_text(json.dumps(request))
            (directory / 'request.json').chmod(0o444)
        control = str(Path(settings.daemon_control_root) / job_id) if settings.daemon_control_root else str(directory)
        name = 'kleos-slicer-' + job_id
        inspected = await self.docker.inspect(name)
        seccomp = 'seccomp=' + json.dumps(daemon_socket_profile(), separators=(',', ':'))
        if inspected is None:
            inherited = {item.split('=', 1)[0] for item in metadata.get('Config', {}).get('Env', [])}
            env = [key + '=' for key in sorted(inherited - {'PATH', 'LANG', 'KLEOS_SLICER_ISOLATED'})]
            env += ['PATH=/usr/bin:/bin', 'LANG=C.UTF-8', 'KLEOS_SLICER_ISOLATED=1']
            spec = {'Image': profile['image'], 'User': '0:0', 'WorkingDir': '/',
                    'Entrypoint': ['/usr/bin/python3', '-I', '/opt/kleos-slicer/native_slicer.py'], 'Cmd': [],
                    'Env': env, 'Labels': {'kleos.slicer.job': job_id, 'kleos.slicer.protocol': PROTOCOL},
                    'NetworkDisabled': True, 'HostConfig': {
                        'ReadonlyRootfs': True, 'NetworkMode': 'none', 'IpcMode': 'private', 'Privileged': False,
                        'CapDrop': ['ALL'], 'CapAdd': ['CHOWN', 'SETUID', 'SETGID', 'KILL', 'DAC_OVERRIDE'],
                        'SecurityOpt': ['no-new-privileges:true', seccomp], 'Memory': 4 * 1024**3,
                        'MemorySwap': 4 * 1024**3, 'NanoCpus': 2_000_000_000, 'PidsLimit': 128,
                        'Mounts': [{'Type': 'bind', 'Source': control, 'Target': '/control', 'ReadOnly': True}],
                        'Tmpfs': {'/run': 'rw,nosuid,nodev,noexec,size=64m', '/tmp': 'rw,nosuid,nodev,size=2g,mode=1777'},
                        'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '1m', 'max-file': '1'}}}}
            response = await client.post('/containers/create', params={'name': name}, json=spec)
            if response.status_code != 409:
                response.raise_for_status()
            inspected = await self.docker.inspect(name)
        self._check(inspected, job_id, profile['image'])
        if inspected['State']['Status'] == 'created':
            response = await client.post('/containers/' + name + '/start')
            response.raise_for_status()
        elif not inspected['State'].get('Running'):
            raise RuntimeError('Slicer supervisor exited; preparation cannot be resumed')
        identity = inspected.get('Id')
        if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{64}', identity):
            raise DomainError('Slicer immutable container identity is missing', 'capacity_termination_unconfirmed')
        return identity

    @staticmethod
    def _check(info, job_id, image=None):
        if info is None or info.get('Config', {}).get('Labels', {}).get('kleos.slicer.job') != job_id:
            raise RuntimeError('Slicer container ownership mismatch')
        host = info.get('HostConfig', {})
        config = info.get('Config', {})
        mounts = host.get('Mounts', [])
        caps = {name.removeprefix('CAP_') for name in host.get('CapAdd', [])}
        if (config.get('User') != '0:0'
                or config.get('Entrypoint') != ['/usr/bin/python3', '-I', '/opt/kleos-slicer/native_slicer.py']
                or config.get('Labels', {}).get('kleos.slicer.protocol') != PROTOCOL
                or caps != {'CHOWN', 'SETUID', 'SETGID', 'KILL', 'DAC_OVERRIDE'}
                or host.get('PidsLimit') != 128 or host.get('Memory') != 4 * 1024**3
                or host.get('MemorySwap') != 4 * 1024**3 or host.get('NanoCpus') != 2_000_000_000
                or len(mounts) != 1 or mounts[0].get('Target') != '/control' or not mounts[0].get('ReadOnly')
                or mounts[0].get('Type') != 'bind' or host.get('IpcMode') != 'private'):
            raise RuntimeError('Slicer resource, identity or mount policy changed')
        for variable in config.get('Env', []):
            key, _, value = variable.partition('=')
            if value and key not in {'PATH', 'LANG', 'KLEOS_SLICER_ISOLATED'}:
                raise RuntimeError('Slicer inherited an unexpected environment value')
        if (image is not None and info.get('Image') != image or not host.get('ReadonlyRootfs')
                or host.get('NetworkMode') != 'none' or host.get('Privileged')
                or 'ALL' not in host.get('CapDrop', [])
                or 'seccomp=' + json.dumps(daemon_socket_profile(), separators=(',', ':')) not in host.get('SecurityOpt', [])):
            raise RuntimeError('Slicer container isolation changed')

    async def result(self, job_id):
        client = self.docker.client
        inspected = await self.docker.inspect('kleos-slicer-' + job_id)
        self._check(inspected, job_id)
        if not inspected['State'].get('Running'):
            raise RuntimeError('Slicer supervisor exited without a result')
        response = await client.post('/containers/kleos-slicer-' + job_id + '/exec', json={
            'AttachStdout': True, 'AttachStderr': False, 'Tty': False, 'User': '0:0',
            'Cmd': ['/usr/bin/python3', '-I', '/opt/kleos-slicer/native_slicer.py', 'read-result']})
        response.raise_for_status()
        ident = response.json()['Id']
        raw, output = bytearray(), bytearray()
        async with client.stream('POST', '/exec/' + ident + '/start', json={'Detach': False, 'Tty': False}) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                while len(raw) >= 8:
                    length = int.from_bytes(raw[4:8], 'big')
                    if raw[0] != 1 or length > MAX_BYTES * 2:
                        raise RuntimeError('Invalid slicer transfer frame')
                    if len(raw) < length + 8:
                        break
                    output.extend(raw[8:length + 8])
                    del raw[:length + 8]
                    if len(output) > MAX_BYTES * 2:
                        raise RuntimeError('Slicer result exceeds bounds')
                if len(raw) > MAX_BYTES * 2 + 8:
                    raise RuntimeError('Slicer transfer exceeds bounds')
        status = await client.get('/exec/' + ident + '/json')
        status.raise_for_status()
        if status.json().get('ExitCode') == 44:
            return None
        if status.json().get('ExitCode') != 0 or status.json().get('Running') or raw:
            raise RuntimeError('Slicer result reader failed: exit=' + str(status.json().get('ExitCode')) + ', running=' + str(status.json().get('Running')) + ', partial=' + str(bool(raw)))
        return json.loads(output)

    async def cleanup(self, job_id, *, acknowledged=False, observed_id=None):
        if observed_id is not None and not re.fullmatch(r'[a-f0-9]{64}', observed_id):
            raise DomainError('Slicer recorded identity is invalid', 'capacity_termination_unconfirmed')
        info = await self.docker.inspect('kleos-slicer-' + job_id)
        if info is None and observed_id is not None:
            info = await self.docker.inspect(observed_id)
        if info:
            self._check(info, job_id)
            container_id = info.get('Id')
            if not isinstance(container_id, str) or not re.fullmatch(r'[a-f0-9]{64}', container_id):
                raise DomainError('Slicer immutable container identity is missing', 'capacity_termination_unconfirmed')
            if observed_id is not None and observed_id != container_id:
                raise DomainError('Slicer immutable container identity changed', 'capacity_termination_unconfirmed')
            observed_id = container_id
            response = await self.docker.client.delete('/containers/' + container_id, params={'force': 'true', 'v': 'true'})
            if response.status_code != 404:
                response.raise_for_status()
        if info is None and (not acknowledged or observed_id is None):
            raise DomainError('Slicer create outcome is uncertain; retain capacity for reconciliation', 'effect_uncertain')
        by_id = await self.docker.inspect(observed_id)
        by_name = await self.docker.inspect('kleos-slicer-' + job_id)
        if by_id is not None or by_name is not None:
            raise DomainError('Slicer removal is not confirmed', 'capacity_termination_unconfirmed')
        return {'kind': 'container-removed', 'identity': 'kleos-slicer-' + job_id}


class PrintPreparation(Base):
    __tablename__ = 'kleos2_print_preparations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    creator_id: Mapped[str] = mapped_column(String(100))
    engineering_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    source_artifact_id: Mapped[str] = mapped_column(String(36))
    source_sha256: Mapped[str] = mapped_column(String(64))
    profile_id: Mapped[str] = mapped_column(String(100))
    profile: Mapped[dict] = mapped_column(JSON)
    prepared_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    prepared_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    measurements: Mapped[dict] = mapped_column(JSON)
    generation: Mapped[int] = mapped_column(Integer)
    container_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)


class PrintDispatch(Base):
    __tablename__ = 'kleos2_print_dispatches'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    preparation_id: Mapped[str] = mapped_column(String(36))
    prepared_sha256: Mapped[str] = mapped_column(String(64))
    printer_serial: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[float] = mapped_column(Float)


def preparation_view(row):
    return {'id': row.id, 'projectId': row.project_id, 'engineeringRevision': row.engineering_revision,
            'status': {'ready': 'ready_for_operator', 'stale': 'failed'}.get(row.status, row.status), 'sourceArtifactId': row.source_artifact_id, 'sourceSha256': row.source_sha256,
            'profileId': row.profile_id, 'profile': row.profile, 'preparedArtifactId': row.prepared_artifact_id,
            'preparedSha256': row.prepared_sha256, 'measurements': {**{key: value for key, value in row.measurements.items() if not key.startswith('_')}, **({'error': row.error} if row.error else {})},
            'physicalAcceptance': False, 'automaticNativePrepared': row.status == 'ready'}


def dispatch_view(row):
    return {'id': row.id, 'projectId': row.project_id, 'preparationId': row.preparation_id, 'status': row.status,
            'preparedSha256': row.prepared_sha256, 'printerSerial': row.printer_serial,
            'filename': 'prepared.3mf', 'message': row.message, 'physicalAcceptance': False}


def current_source(session, project, source, expected_hash=None):
    """Artifact revisions are native context revisions, not the UI engineering epoch."""
    if source is None or source.project_id != project.id or (expected_hash and source.sha256 != expected_hash):
        return False
    if source.id in project.data.get('sourceManifest', {}).values():
        return True
    if source.details.get('origin') == 'native_candidate':
        run = session.get(Objective, source.details.get('runId'))
        if (run is not None and run.project_id == project.id and run.revision == project.context_revision
                and source.revision == run.revision and run.status not in {'cancelled', 'failed'}):
            return True
    release_id = project.data.get('acceptedReleaseId')
    if release_id:
        from kleos.validation.service import Release
        release = session.get(Release, release_id)
        if release is not None and release.project_id == project.id:
            for reference in release.data.get('sources', []) + release.data.get('outputs', []):
                if reference.get('id') == source.id and reference.get('sha256') == source.sha256:
                    return True
    return False


class PrintPreparations:
    def __init__(self, projects, blobs, settings=None, driver=None, *, clock=time.time, capacity=None):
        self.capacity = capacity
        self.projects, self.blobs, self.settings, self.driver, self.clock = projects, blobs, settings, driver, clock

    def _worker_authority(self, session, project, row):
        """The durable creator owns admission/publication; cleanup needs no user authority."""
        if project is None or project.tenant_id != row.tenant_id:
            raise DomainError('Preparation project is unavailable', 'not_found', 404)
        actor = Actor(tenant_id=row.tenant_id, user_id=row.creator_id)
        purpose = project.data.get('internalPurpose')
        if purpose is None:
            self.projects.authorized(session, actor, project.id, write=True)
        else:
            # Only the dedicated server-created Bambu fixture bypasses generic
            # project visibility, never account or organization authority.
            if (purpose != 'bambu_test_print' or project.owner_id != row.creator_id
                    or project.data.get('deletion') is not None):
                raise DomainError('Internal preparation scope changed', 'forbidden', 403)
            self.projects.authorize_account(session, actor, write=True)
            if self.projects.require_account_authority:
                from kleos.access.service import OrganizationMember
                org = session.get(OrganizationMember, (actor.tenant_id, actor.user_id))
                if org.role not in {'owner', 'admin'}:
                    raise DomainError('Local test preparation requires administrator access', 'forbidden', 403)
        member = session.get(Membership, (project.id, row.creator_id),
                             with_for_update=True, populate_existing=True)
        if member is None or member.role not in ({'owner'} if purpose else {'owner', 'editor'}):
            raise DomainError('Preparation creator no longer has project write access', 'forbidden', 403)

    def _row(self, session, actor, project_id, ident, *, dispatch=False, write=False):
        project = self.projects.authorized(session, actor, project_id, write=write)
        row = session.get(PrintDispatch if dispatch else PrintPreparation, ident)
        if row is None or row.project_id != project.id or row.tenant_id != actor.tenant_id:
            raise DomainError('Print job not found', 'not_found', 404)
        return project, row

    def list(self, actor, project_id, *, dispatch=False):
        with self.projects.transaction() as session:
            self.projects.authorized(session, actor, project_id)
            model = PrintDispatch if dispatch else PrintPreparation
            rows = session.scalars(select(model).where(model.project_id == project_id).order_by(model.created_at))
            return [(dispatch_view if dispatch else preparation_view)(row) for row in rows]

    def get(self, actor, project_id, ident, *, dispatch=False):
        with self.projects.transaction() as session:
            _, row = self._row(session, actor, project_id, ident, dispatch=dispatch)
            return (dispatch_view if dispatch else preparation_view)(row)

    def create(self, actor, project_id, body, key):
        def operation(session):
            project = self.projects.authorized(session, actor, project_id, write=True)
            if project.data.get('engineeringRevision', 1) != body['expectedEngineeringRevision']:
                raise DomainError('Engineering context changed', 'revision_conflict')
            if body.get('releaseId') and body['releaseId'] != project.data.get('acceptedReleaseId'):
                raise DomainError('Release is not accepted for this project', 'revision_conflict')
            source = session.get(Artifact, body['artifactId'])
            if not current_source(session, project, source):
                raise DomainError('Current project artifact not found', 'not_found', 404)
            if Path(source.name).suffix.lower() not in {'.stl', '.obj'}:
                raise DomainError('Preparation supports STL and triangulated OBJ; export other formats to STL first', 'unsupported_format', 422)
            if self.settings is None or self.driver is None:
                raise DomainError('No qualified slicer is configured', 'capability_unavailable', 501)
            profile = self.settings.profile(body['profileId'])
            pending = session.scalar(select(func.count()).select_from(PrintPreparation).where(
                PrintPreparation.project_id == project_id, PrintPreparation.status.in_(['queued', 'running'])))
            if pending >= 16:
                raise DomainError('Too many pending print preparations', 'capacity_exceeded', 429)
            row = PrintPreparation(id=str(uuid4()), tenant_id=actor.tenant_id, project_id=project_id,
                creator_id=actor.user_id, engineering_revision=body['expectedEngineeringRevision'], status='queued',
                source_artifact_id=source.id, source_sha256=source.sha256, profile_id=body['profileId'], profile=profile,
                measurements={}, generation=0, created_at=self.clock())
            session.add(row)
            self.projects._emit(session, project, 'print.preparation.created', preparation_view(row))
            return {'projectId': project_id, 'preparation': preparation_view(row)}
        return self.projects._command(actor, key, {'operation': 'print.prepare', 'projectId': project_id, **body}, operation)

    def cancel(self, actor, project_id, ident, key):
        def operation(session):
            project, row = self._row(session, actor, project_id, ident, write=True)
            row.status, row.generation = 'cancelled', row.generation + 1
            self.projects._emit(session, project, 'print.preparation.cancelled', preparation_view(row))
            return {'projectId': project_id, 'preparation': preparation_view(row)}
        return self.projects._command(actor, key, {'operation': 'print.cancelPreparation', 'projectId': project_id, 'id': ident}, operation)

    async def process_next(self, *, max_running=1):
        """Worker entry: claim transactionally, then start deterministic isolated job."""
        if self.driver is None:
            return False
        if type(max_running) is not int or not 1 <= max_running <= 4:
            raise ValueError('Slicer concurrency must be between one and four')
        with self.projects.transaction() as session:
            if session.get_bind().dialect.name == 'postgresql':
                session.execute(text('SELECT pg_advisory_xact_lock(73628, 12)'))
            active = session.scalar(select(func.count()).select_from(PrintPreparation).where(
                PrintPreparation.container_id.is_not(None)))
            if active >= max_running:
                return False
            candidate = session.execute(select(PrintPreparation.id, PrintPreparation.project_id)
                .where(PrintPreparation.status == 'queued').order_by(PrintPreparation.created_at).limit(1)).first()
            if candidate is None:
                return False
            project = session.get(Project, candidate.project_id, with_for_update=True)
            row = session.get(PrintPreparation, candidate.id, with_for_update=True)
            if row.status != 'queued':
                return True
            try:
                self._worker_authority(session, project, row)
            except DomainError:
                row.status, row.generation, row.error = 'cancelled', row.generation+1, 'Preparation authority was revoked'
                return True
            source = session.get(Artifact, row.source_artifact_id)
            if (project.data.get('engineeringRevision', 1) != row.engineering_revision
                    or not current_source(session, project, source, row.source_sha256)):
                row.status, row.error = 'stale', 'Source is no longer current'
                return True
            job_id = uuid4().hex
            if self.capacity is not None and not self.capacity.reserve(
                    'slicer:'+job_id, row.project_id, self._capacity_hash(row, job_id), SLICER_RESOURCES,
                    session=session, details={'containerName': 'kleos-slicer-'+job_id,
                        'imageDigest': row.profile['image'], 'protocol': PROTOCOL}):
                return False
            row.status, row.generation = 'running', row.generation + 1
            ident, generation = row.id, row.generation
            row.container_id = job_id
            row.measurements = {**row.measurements, '_dispatchAcknowledged': False}
            job_id = row.container_id
            source = session.get(Artifact, row.source_artifact_id)
            extension, source_hash, profile = Path(source.name).suffix.lower()[1:], row.source_sha256, {**row.profile, 'id': row.profile_id}
        try:
            observed_id = await self.driver.start(job_id, self.blobs.get(source_hash), extension, profile)
            with self.projects.transaction() as session:
                row = session.get(PrintPreparation, ident, with_for_update=True)
                if row.container_id == job_id:
                    row.measurements = {**row.measurements, '_dispatchAcknowledged': True,
                                        '_containerId': observed_id if isinstance(observed_id, str) and re.fullmatch(r'[a-f0-9]{64}', observed_id) else None}
        except Exception:
            self._fail(ident, generation, 'Isolated slicer could not start')
        return True

    @staticmethod
    def _capacity_hash(row, job_id):
        return fingerprint({'jobId': job_id, 'preparationId': row.id, 'sourceSha256': row.source_sha256,
                            'profile': row.profile, 'resources': SLICER_RESOURCES})

    def _fail(self, ident, generation, message):
        with self.projects.transaction() as session:
            row = session.get(PrintPreparation, ident, with_for_update=True)
            if row and row.status == 'running' and row.generation == generation:
                row.status, row.error = 'failed', message

    async def reconcile(self):
        if self.driver is None:
            return 0
        with self.projects.transaction() as session:
            rows = [(r.id, r.generation, r.container_id, r.created_at) for r in session.scalars(
                select(PrintPreparation).where(PrintPreparation.container_id.is_not(None)).order_by(PrintPreparation.created_at).limit(16))]
        completed = 0
        for ident, generation, job_id, created in rows:
            with self.projects.transaction() as session:
                current = session.get(PrintPreparation, ident)
                terminal = current.status != 'running'
                acknowledged = current.measurements.get('_dispatchAcknowledged', False)
                observed_id = current.measurements.get('_containerId')
            if terminal:
                try:
                    proof = await self.driver.cleanup(job_id, acknowledged=acknowledged, observed_id=observed_id)
                    with self.projects.transaction() as session:
                        current = session.get(PrintPreparation, ident, with_for_update=True)
                        if current.container_id != job_id:
                            continue
                        if self.capacity is not None:
                            self.capacity.release('slicer:'+job_id, self._capacity_hash(current, job_id), proof, session=session)
                        current.container_id = None
                except Exception:
                    # Quarantine this uncertain reservation without starving other cleanup.
                    with self.projects.transaction() as session:
                        current = session.get(PrintPreparation, ident, with_for_update=True)
                        if current.container_id == job_id:
                            current.error = 'Slicer cleanup is uncertain; compute capacity remains reserved'
                continue
            try:
                result = await self.driver.result(job_id)
                if result is None:
                    if self.clock() - created > 900:
                        self._fail(ident, generation, 'Slicer deadline exceeded')
                    continue
                with self.projects.transaction() as session:
                    project_id = session.scalar(select(PrintPreparation.project_id).where(PrintPreparation.id == ident))
                    project = session.get(Project, project_id, with_for_update=True)
                    row = session.get(PrintPreparation, ident, with_for_update=True)
                    if row.status != 'running' or row.generation != generation:
                        continue
                    self._worker_authority(session, project, row)
                    if (result.get('protocol') != PROTOCOL or result.get('jobId') != job_id
                            or result.get('sourceSha256') != row.source_sha256
                            or result.get('bundleSha256') != row.profile['bundleSha256']
                            or result.get('image') != row.profile['image']):
                        raise ValueError('Slicer result provenance mismatch')
                    if result.get('status') != 'ready':
                        raise ValueError('Slicer rejected the part')
                    package = base64.b64decode(result['package'], validate=True)
                    package_hash = digest(package)
                    if package_hash != result.get('preparedSha256'):
                        raise ValueError('Slicer package digest mismatch')
                    measures = validate_package(package)
                    if (project.data.get('engineeringRevision', 1) != row.engineering_revision
                            or not current_source(session, project, session.get(Artifact, row.source_artifact_id), row.source_sha256)):
                        row.status, row.error = 'stale', 'Engineering context changed during preparation'
                        continue
                    self.blobs.put(package)
                    artifact = Artifact(id=str(uuid4()), project_id=row.project_id, revision=row.engineering_revision,
                        name='prepared-' + row.id + '.3mf', sha256=package_hash, size=len(package), media_type='model/3mf',
                        status='candidate', lane='fabrication', details={'origin': PROTOCOL, 'preparationId': row.id,
                            'sourceSha256': row.source_sha256, 'profile': row.profile, 'physicalAcceptance': False})
                    session.add(artifact)
                    row.status, row.prepared_artifact_id, row.prepared_sha256 = 'ready', artifact.id, package_hash
                    candidate = result.get('measurements', {})
                    if not isinstance(candidate, dict) or len(json.dumps(candidate, allow_nan=False)) > 8192:
                        raise ValueError('Invalid slicer measurements')
                    row.measurements = {**candidate, **measures, '_dispatchAcknowledged': True,
                                        '_containerId': row.measurements.get('_containerId')}
                    self.projects._emit(session, project, 'print.preparation.ready', preparation_view(row))
                    completed += 1
            except (Exception, asyncio.CancelledError) as error:
                if isinstance(error, asyncio.CancelledError):
                    raise
                self._fail(ident, generation, 'Slicer output could not be verified')
        return completed

    def dispatch(self, actor, project_id, body, key, *, printer_serial='unpaired', decision=None):
        def operation(session):
            project, preparation = self._row(session, actor, project_id, body['preparationId'], write=True)
            if (preparation.status != 'ready' or preparation.prepared_sha256 != body['preparedSha256']
                    or preparation.engineering_revision != body['expectedEngineeringRevision']
                    or project.data.get('engineeringRevision', 1) != preparation.engineering_revision
                    or not current_source(session, project, session.get(Artifact, preparation.source_artifact_id), preparation.source_sha256)):
                raise DomainError('Print preparation is not current', 'revision_conflict')
            row = PrintDispatch(id=str(uuid4()), tenant_id=actor.tenant_id, project_id=project_id,
                preparation_id=preparation.id, prepared_sha256=preparation.prepared_sha256, printer_serial=printer_serial,
                status=decision[0] if decision else 'blocked',
                message=decision[1] if decision else 'Printer upload and start are blocked by server policy.',
                created_at=self.clock())
            session.add(row)
            self.projects._emit(session, project, 'print.dispatch.' + row.status, dispatch_view(row))
            return {'projectId': project_id, 'dispatch': dispatch_view(row)}
        return self.projects._command(actor, key, {'operation': 'print.dispatch', 'projectId': project_id, **body}, operation)


def qualify_slicing(installation, control_root):
    """Load operator-owned exact-image evidence; frontend claims cannot qualify a tool."""
    if not isinstance(installation, dict) or set(installation) - {'image', 'report', 'dockerDesktop', 'deployment', 'daemonControlRoot'}:
        raise ValueError('Invalid slicer installation configuration')
    image = installation.get('image')
    path = Path(installation.get('report', ''))
    if not path.is_file() or path.stat().st_size > 128 * 1024:
        raise ValueError('Slicer qualification report is unavailable or oversized')
    report = json.loads(path.read_bytes())
    from kleos.execution.security import BASELINE_SHA256
    if (report.get('image') != image or report.get('sourceOverride') is not False
            or report.get('runnerSha256') != digest(Path(__file__).with_name('native_slicer.py').read_bytes())
            or report.get('securityBaselineSha256') != BASELINE_SHA256
            or report.get('environment') != 'local-docker-desktop-amd64'
            or report.get('rejections') != {'open-mesh': 'PASS', 'modified-preset': 'PASS'}
            or installation.get('dockerDesktop') is not True or installation.get('deployment', 'local') != 'local'):
        raise ValueError('Slicer image, source or execution environment lacks matching qualification')
    qualified = []
    for ident, evidence in report.get('profiles', {}).items():
        if ident not in BUNDLES or evidence.get('status') != 'PASS':
            continue
        isolation = evidence.get('measurements', {}).get('isolation', {})
        if (evidence.get('bundleSha256') != BUNDLES[ident][2]
                or evidence.get('checks', {}).get('archiveCheck') != 'PASS'
                or evidence.get('checks', {}).get('plateCount') != 1
                or isolation != {'uid': 10001, 'effectiveCapabilities': 0, 'trustedControlWritable': False,
                                 'socketPolicy': 'unix-only', 'noNewPrivileges': True}):
            raise ValueError('Slicer profile evidence does not satisfy the fixed contract')
        qualified.append(ident)
    if not qualified:
        raise ValueError('No slicer profiles passed qualification')
    return SlicingSettings(image, Path(control_root), tuple(qualified), True, 'local', installation.get('daemonControlRoot'))
