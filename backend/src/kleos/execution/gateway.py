"""Durable native actions, pinned inputs, and candidate-only publication."""
import asyncio
import base64
import copy
import json
import mimetypes
import re
import time
from pathlib import Path

from sqlalchemy import JSON, String, select
from sqlalchemy.orm import Mapped, mapped_column

from kleos.artifacts.service import Artifact
from kleos.contracts.models import DomainError
from kleos.execution import pcb_scaffold
from kleos.execution.native_runner import atomic_json, safe_file
from kleos.execution.pcb_contract import circuit_contract
from kleos.execution.protocol import (
    LANES,
    MAX_FILE,
    MAX_RESULT,
    MAX_SOURCES,
    NATIVE_MAX_SESSION_SECONDS,
    PROTOCOL,
    action_request,
    digest,
    fingerprint,
    output_path,
    source_path,
)
from kleos.projects.store import Base, Objective, Project


class NativeJob(Base):
    __tablename__ = 'kleos2_native_jobs'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str] = mapped_column(String(100))
    task_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))
    source_manifest: Mapped[dict] = mapped_column(JSON)
    active_action: Mapped[str | None] = mapped_column(String(128), nullable=True)


class NativeAction(Base):
    __tablename__ = 'kleos2_native_actions'
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))
    result: Mapped[dict] = mapped_column(JSON)


def export_path(lane, path):
    output_path(path)
    basename = path.removeprefix('outputs/')
    patterns = {
        'cad': r'(parts\.json|[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.(step|stl|svg))',
        'pcb': r'((erc|drc)\.(rpt|json)|routing\.json|pcb-project\.zip|circuit-contract\.json|netlist\.xml|routed\.kicad_(pcb|pro|dru)|gerbers/[A-Za-z0-9_.-]+\.(gbr|drl|gbrjob|map))',
        'firmware': r'firmware\.(elf|bin)',
        'application': r'companion-web\.zip',
    }
    if not re.fullmatch(patterns[lane], basename):
        raise ValueError('Native output is outside its fixed export contract')
    return path


def original_duration(request):
    values = [request[key] for key in ('durationSeconds', 'sessionSeconds') if key in request]
    if not values or any(not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= NATIVE_MAX_SESSION_SECONDS
                         for value in values):
        raise DomainError('Original native duration contract is missing or invalid', 'native_history_missing')
    return min(values)


def capacity_resources(request):
    # /run is a separate bounded journal tmpfs in addition to the job workspace.
    resources = dict(request['resources'])
    resources['scratchBytes'] += 128 * 1024**2
    return resources


def starter_paths(job):
    """Paths of the starter files the server generated for this job, each reproduced from its pinned contract."""
    if job.request.get('scaffold') != pcb_scaffold.VERSION or 'circuitContract' not in job.request:
        return set()
    starter = pcb_scaffold.scaffold(job.request['circuitContract'])
    return {item['path'] for item in job.request.get('sources', []) if not item.get('artifactId')
            and item['path'] in starter and digest(starter[item['path']].encode()) == item['sha256']}


def artifact_inputs(session, job):
    """The artifact-backed task inputs of a producer job, after verifying every other source.

    A source without an artifact is legitimate only when it can be reproduced: a starter file must equal the
    scaffold regenerated from the job's pinned circuit contract, and a resumed file must match what the job it
    continues recorded. Anything else means the producer's inputs are not what its plan authorized.
    """
    inputs, starter = [], None
    for item in job.request.get('sources', []):
        if item.get('artifactId'):
            inputs.append(item)
            continue
        path, checksum = item['path'], item['sha256']
        if job.request.get('scaffold') == pcb_scaffold.VERSION and 'circuitContract' in job.request:
            if starter is None:
                starter = pcb_scaffold.scaffold(job.request['circuitContract'])
            if path in starter and digest(starter[path].encode()) == checksum:
                continue
        prior = session.get(NativeJob, job.request['resumeOf']) if job.request.get('resumeOf') else None
        if prior is not None and path in job.request.get('resumedSourcePaths', []) and prior.source_manifest.get(path) == checksum:
            continue
        raise DomainError('Native producer input provenance changed', 'composition_input_unverified')
    return inputs


class NativeGateway:
    def __init__(self, projects, artifacts, settings, docker_client, run_repository, *, publisher=None, capacity=None):
        self.projects, self.artifacts, self.settings = projects, artifacts, settings
        self.docker, self.repository = docker_client, run_repository
        self.publisher = publisher or artifacts.publish_native
        self.capacity = capacity
        self.root = Path(settings.control_root).absolute()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError('Native control root cannot be a symlink')
        self.root.chmod(0o700)

    async def authorize(self, scope, task, arguments):
        await self.repository.assert_active(scope)
        resolved = await self.repository.resolve_artifacts(scope, task.input_artifact_ids)
        if task.capability.value not in LANES or task.capability.value not in self.settings.images:
            raise DomainError('No qualified native image is configured for this capability', 'tool_unavailable', 503)
        if scope.deadline_at.timestamp() <= time.time():
            raise DomainError('Native task deadline expired', 'deadline_exceeded')
        for path in task.allowed_paths:
            source_path(path)
        if 'path' in arguments:
            path = arguments['path']
            if isinstance(path, str) and path.startswith('outputs/'):
                output_path(path)
            else:
                source_path(path)
        if self.capacity is not None:
            if 'content' in arguments and arguments.get('path') not in task.allowed_paths:
                raise DomainError('Patch exceeds task authority', 'scope_conflict')
            # Tool authorization precedes the specialist's external-effect claim.
            # Waiting here creates no uncertain tool action and makes no model call.
            await self._admit(scope, self._job(scope, task, resolved))
        return resolved

    async def _admit(self, scope, job, *, composition=None):
        if self.capacity is None or job.status in {'finished', 'failed', 'cancelled'}:
            return
        while True:
            await self.repository.assert_active(scope)
            if composition is not None:
                composition.validate(scope, job.request['compositionId'])
            remaining = min(scope.deadline_at.timestamp(), job.request['deadline']) - time.time()
            if remaining <= 0:
                raise DomainError('Native capacity wait exceeded the original deadline', 'deadline_exceeded')
            if self.capacity.reserve('native:' + job.id, job.project_id, job.request_hash,
                    job.request.get('capacityResources', capacity_resources(job.request)),
                    details={'containerName': 'kleos-native-' + job.id,
                             'imageDigest': job.request['imageDigest'], 'protocol': PROTOCOL}):
                return
            await asyncio.sleep(min(.2, remaining))

    def _job(self, scope, task, resolved=()):
        ident = fingerprint({'tenant': scope.actor.tenant_id, 'project': scope.project_id,
                             'run': scope.run_id, 'task': task.id})
        task_hash = fingerprint(task.model_dump(mode='json'))
        with self.projects.transaction() as session:
            project = self.projects.authorized(session, scope.actor, scope.project_id, write=True)
            existing = session.get(NativeJob, ident)
            if existing:
                if existing.task_hash != task_hash:
                    raise DomainError('Native task contract changed during replay', 'task_conflict')
                return self._latest_attempt(session, scope, task, ident, existing)
            firmware_target = None
            if task.capability.value == 'firmware':
                firmware_target = project.data.get('interfaces', {}).get('firmware')
                if (not isinstance(firmware_target, dict) or set(firmware_target) != {'board'}
                        or firmware_target.get('board') not in self.settings.firmware_boards):
                    raise DomainError('Select an explicit firmware board with a qualified installation',
                                      'firmware_target_required')
            accepted_circuit = None
            peripheral_ids = None
            circuit_identity = None
            if task.capability.value == 'pcb':
                from kleos.projects.interfaces import accepted_circuit_identity
                circuit_identity = accepted_circuit_identity(session, project)
                peripheral_ids = circuit_identity['peripheralConceptIds']
                if circuit_identity['mode'] == 'imported_no_concept':
                    imported = {item['id']: item for item in circuit_identity['sourceArtifacts']}
                    if not set(imported).issubset(task.input_artifact_ids):
                        raise DomainError('Imported routing requires its complete approved source closure', 'imported_sources_required')
                    selected = [imported[ident] for ident in circuit_identity['importedSourceIds']]
                    if (not any(item['path'] == 'board.kicad_pcb' for item in selected)
                            or 'circuit.kicad_sch' not in {item['path'] for item in imported.values()}):
                        raise DomainError('Imported routing requires board.kicad_pcb and circuit.kicad_sch at the qualified source paths',
                                          'imported_layout_unsupported')
                accepted_circuit = circuit_contract(project.data.get('interfaces', {}).get('circuit'), peripheral_ids)
            sources, seen, total = [], set(), 0
            permitted = {ref.id: ref for ref in resolved}
            for artifact_id in task.input_artifact_ids:
                if artifact_id not in permitted:
                    raise DomainError('Native source was not resolved as a task input', 'artifact_scope_conflict')
                row = self.artifacts._authorized(session, scope.actor, artifact_id, scope.project_id)
                reference = permitted[artifact_id]
                if (reference.path, reference.sha256, reference.revision, reference.size, reference.media_type) != (
                        row.name, row.sha256, row.revision, row.size, row.media_type):
                    raise DomainError('Native input changed after resolution', 'artifact_scope_conflict')
                name = row.name
                if name.startswith('outputs/'):
                    name = 'inputs/' + row.id + '/' + Path(name).name
                name = source_path(name)
                if name in seen:
                    raise DomainError('Input artifacts contain ambiguous source paths', 'source_conflict')
                raw = self.artifacts.blobs.get(row.sha256)
                total += len(raw)
                if len(raw) > MAX_FILE or total > MAX_SOURCES or len(sources) >= 50:
                    raise DomainError('Native input closure exceeds its bound', 'artifact_limit')
                sources.append({'path': name, 'sha256': digest(raw), 'artifactId': row.id,
                                'contentBase64': base64.b64encode(raw).decode()})
                seen.add(name)
            scaffolded = False
            if accepted_circuit is not None and circuit_identity['mode'] == 'approved_concept':
                # The contract already names every part, pin and net, so the starter project is derived from it
                # mechanically rather than asked of a model; explicit task inputs win over a generated file.
                try:
                    starter = pcb_scaffold.scaffold(accepted_circuit)
                except (ValueError, KeyError):
                    starter = {}
                for path, text in starter.items():
                    if path in seen:
                        continue
                    raw = text.encode()
                    total += len(raw)
                    if len(raw) > MAX_FILE or total > MAX_SOURCES or len(sources) >= 50:
                        raise DomainError('Native input closure exceeds its bound', 'artifact_limit')
                    self.artifacts.blobs.put(raw)  # exact input bytes must survive, like every authorized input
                    sources.append({'path': path, 'sha256': digest(raw), 'contentBase64': base64.b64encode(raw).decode()})
                    seen.add(path)
                    scaffolded = True
            baselines = {}
            manifest = project.data.get('sourceManifest', {})
            for path in task.allowed_paths:
                old = session.get(Artifact, manifest[path]) if path in manifest else None
                baselines[path] = old.sha256 if old else None
            lane = task.capability.value
            limits = self.settings.application_resources if lane == 'application' else self.settings.resources
            request = {'protocol': PROTOCOL, 'jobId': ident, 'lane': lane,
                       'imageDigest': self.settings.images[lane], 'revision': scope.revision,
                       'resources': limits.wire(), 'maxActions': task.max_actions, 'sessionSeconds': self.settings.session_seconds,
                       'deadline': min(scope.deadline_at.timestamp(), time.time() + self.settings.session_seconds),
                       'sources': sources, 'allowedPaths': sorted(set(task.allowed_paths)),
                       'baselines': baselines,
                       'networkPolicy': ('docker-desktop-daemon-seccomp' if self.settings.docker_desktop_compatibility
                                         else 'in-process-seccomp')}
            if self.capacity is not None:
                request['capacityPoolId'] = self.capacity.pool_id
                request['capacityResources'] = capacity_resources(request)
            if firmware_target is not None:
                request.update(board=firmware_target['board'], firmwareTarget=dict(firmware_target),
                               firmwareTargetHash=fingerprint(firmware_target))
            if scaffolded:
                request['scaffold'] = pcb_scaffold.VERSION
            if accepted_circuit is not None:
                request.update(circuitContract=accepted_circuit, peripheralConceptIds=peripheral_ids,
                               circuitContractHash=fingerprint(accepted_circuit), acceptedCircuitIdentity=circuit_identity)
            job = NativeJob(id=ident, project_id=scope.project_id, run_id=scope.run_id, task_id=task.id,
                            task_hash=task_hash, request_hash=fingerprint(request), request=request,
                            status='pending', source_manifest={item['path']: item['sha256'] for item in sources},
                            active_action=None)
            session.add(job)
            return job

    @staticmethod
    def _attempt_ident(ident, attempt):
        return ident if attempt == 1 else fingerprint({'job': ident, 'attempt': attempt})

    def _latest_attempt(self, session, scope, task, ident, first):
        """The task's current native job; a closed one with a confirmed-stopped container is continued.

        Progress is never discarded: the continuation starts from the exact bytes every successful patch left in
        the immutable blob store, so a retry after a timeout or a block resumes the work instead of redoing it.
        """
        job, attempt = first, 1
        while (following := session.get(NativeJob, self._attempt_ident(ident, attempt + 1))) is not None:
            job, attempt = following, attempt + 1
        if job.status not in {'failed', 'cancelled'} or job.active_action is not None:
            return job
        proof = session.get(NativeAction, (job.id, '_termination'))
        if (proof is None or proof.status != 'completed' or proof.request_hash != job.request_hash
                or proof.result != {'kind': 'termination-proof', 'jobId': job.id, 'containerStopped': True}):
            return job
        if session.scalar(select(NativeAction.job_id).where(
                NativeAction.job_id == job.id, NativeAction.status != 'completed',
                NativeAction.action_id.not_in(('_dispatch', '_termination', '_cleanup'))).limit(1)) is not None:
            return job
        sources, total = [], 0
        original = {item['path']: item for item in job.request['sources']}
        for path, checksum in sorted(job.source_manifest.items()):
            source_path(path)
            raw = self.artifacts.blobs.get(checksum)
            total += len(raw)
            if total > MAX_SOURCES or len(sources) >= 50:
                raise DomainError('Native resume source closure exceeds bounds', 'artifact_limit')
            kept = original.get(path)
            sources.append(copy.deepcopy(kept) if kept is not None and kept['sha256'] == checksum else
                           {'path': path, 'sha256': checksum, 'contentBase64': base64.b64encode(raw).decode()})
        resumed = sorted(item['path'] for item in sources
                         if 'artifactId' not in item and item['path'] in task.allowed_paths)
        request = {**copy.deepcopy(job.request), 'jobId': self._attempt_ident(ident, attempt + 1),
                   'revision': scope.revision, 'sources': sources, 'resumeOf': job.id, 'resumeAttempt': attempt + 1,
                   'resumedSourcePaths': resumed, 'sessionSeconds': self.settings.session_seconds,
                   'deadline': min(scope.deadline_at.timestamp(), time.time() + self.settings.session_seconds)}
        continued = NativeJob(id=request['jobId'], project_id=job.project_id, run_id=job.run_id, task_id=job.task_id,
                              task_hash=job.task_hash, request_hash=fingerprint(request), request=request,
                              status='pending', source_manifest=dict(job.source_manifest), active_action=None)
        session.add(continued)
        return continued

    def _claim(self, scope, job, action):
        with self.projects.transaction() as session:
            self.projects.authorized(session, scope.actor, scope.project_id, write=True)
            current = session.get(NativeJob, job.id)
            existing = session.get(NativeAction, (job.id, action['actionId']))
            if existing:
                if existing.request_hash != fingerprint(action):
                    raise DomainError('Native action ID reused with different arguments', 'idempotency_conflict')
                if existing.status == 'completed':
                    return existing.result
            elif current.status in {'finished', 'failed', 'cancelled'}:
                raise DomainError('Native job is closed', 'native_closed')
            if current.active_action not in {None, action['actionId']}:
                raise DomainError('Another native action is still pending', 'native_busy')
            if time.time() >= job.request['deadline']:
                raise DomainError('Native session deadline expired', 'deadline_exceeded')
            if existing is None:
                session.add(NativeAction(job_id=job.id, action_id=action['actionId'],
                                         request_hash=fingerprint(action), status='pending', result={}))
            current.active_action, current.status = action['actionId'], 'running'
            return None

    def _directory(self, job):
        directory = self.root / job.id
        if directory.is_symlink():
            raise ValueError('Unsafe native job directory')
        directory.mkdir(mode=0o700, exist_ok=True)
        for name in ('actions', 'results'):
            path = directory / name
            if path.is_symlink():
                raise ValueError('Unsafe native control directory')
            path.mkdir(mode=0o700, exist_ok=True)
        return directory

    async def _result(self, scope, job, action, directory):
        filename = digest(action['actionId'].encode()) + '.json'
        path = directory / 'results' / filename
        while True:
            await self.repository.assert_active(scope)
            if not path.exists() and hasattr(self.docker, 'result'):
                remote = await self.docker.result(job.id, filename)
                if remote is not None:
                    atomic_json(path, remote)
            if path.exists():
                if path.stat().st_size > MAX_RESULT:
                    raise ValueError('Native result exceeds transfer limit')
                envelope = json.loads(safe_file(directory / 'results', filename, max_size=MAX_RESULT).read_bytes())
                expected = {'protocol': PROTOCOL, 'jobId': job.id, 'requestHash': job.request_hash,
                            'actionId': action['actionId'], 'actionHash': fingerprint(action)}
                if any(envelope.get(key) != value for key, value in expected.items()):
                    raise ValueError('Native result identity or input hash mismatch')
                (directory / ('ack-' + filename)).touch(mode=0o600)
                return envelope['response']
            if time.time() >= job.request['deadline']:
                raise TimeoutError('Native session exceeded its deadline')
            if not await self.docker.running(job.id):
                # Check once more after observing exit: finish may have written its result just before exit.
                if hasattr(self.docker, 'result'):
                    remote = await self.docker.result(job.id, filename)
                    if remote is not None:
                        atomic_json(path, remote)
                if path.exists():
                    continue
                raise RuntimeError('Native supervisor exited without a valid result')
            await asyncio.sleep(self.settings.poll_seconds)

    async def _publish(self, scope, task, job, response, *, composition=None):
        files, seen, source_names, total = [], set(), set(), 0
        resumed = set(job.request.get('resumedSourcePaths', []))
        # Starter files the server generated from the accepted contract belong to this task's authored closure: later
        # compilation needs them (library tables, symbols, footprints), and no earlier artifact holds them.
        generated = ({item['path'] for item in job.request['sources'] if 'artifactId' not in item}
                     if job.request.get('scaffold') else set())
        originals = {item['path']: item['sha256'] for item in job.request['sources'] if item['path'] not in resumed}
        result = response['result']
        if not isinstance(result.get('files'), list) or not 1 <= len(result['files']) <= 256:
            raise ValueError('Native finish requires a bounded file manifest')
        for item in result['files']:
            path, role = item['path'], item['role']
            if path in seen:
                raise ValueError('Duplicate native export')
            seen.add(path)
            raw = base64.b64decode(item['contentBase64'], validate=True)
            total += len(raw)
            if total > MAX_FILE or len(raw) != item['size'] or digest(raw) != item['sha256']:
                raise ValueError('Native export checksum or size mismatch')
            if role == 'source':
                source_path(path)
                source_names.add(path)
                if job.source_manifest.get(path) != digest(raw):
                    raise ValueError('Native finish differs from authorized source manifest')
                if path not in task.allowed_paths:
                    if originals.get(path) != digest(raw):
                        raise ValueError('Native source changed outside task authority')
                    if path not in generated:
                        continue  # an existing input is already an artifact of this project
            elif role == 'output':
                export_path(job.request['lane'], path)
            else:
                raise ValueError('Invalid native export role')
            files.append({'path': path, 'content': raw, 'role': role,
                          'expectedSha256': job.request['baselines'].get(path),
                          'mediaType': mimetypes.guess_type(path)[0] or 'application/octet-stream'})
        if source_names != set(job.source_manifest):
            raise ValueError('Native finish omitted source provenance')
        if not any(file['role'] == 'output' for file in files):
            raise ValueError('Native finish produced no outputs')
        provenance = {'nativeJobId': job.id, 'imageDigest': job.request['imageDigest'],
                      'requestHash': job.request_hash, 'inputs': originals, 'resources': job.request['resources']}
        if job.request.get('scaffold'):
            provenance['scaffold'] = job.request['scaffold']
        if job.request.get('resumeOf'):
            provenance.update(resumeOf=job.request['resumeOf'], resumeAttempt=job.request['resumeAttempt'])
        if job.request.get('rebuildOf'):
            provenance.update(rebuildOf=job.request['rebuildOf'],
                              rebuildRootJobId=job.request['rebuildRootJobId'],
                              sourceSnapshotHash=job.request['sourceSnapshotHash'])
        await self.repository.assert_active(scope)
        if 'compositionId' in job.request:
            if composition is None:
                raise DomainError('Compilation publication requires its server grant', 'composition_conflict')
            provenance.update(compositionId=job.request['compositionId'],
                              compositionTarget=job.request['compositionTarget'])
            refs = await self.artifacts.publish_compilation(scope, task, files, provenance, composition)
        else:
            if composition is not None:
                raise DomainError('Compilation cannot publish through an ordinary native job', 'composition_conflict')
            # The generated starter files are published with this task's sources, so they join its publication scope.
            published = task.model_copy(update={'allowed_paths': sorted({*task.allowed_paths, *generated})}) if generated else task
            refs = await self.publisher(scope, published, files, provenance)
        return [ref.model_dump(mode='json', by_alias=True) for ref in refs]

    async def invoke(self, scope, task, action, arguments):
        resolved = await self.authorize(scope, task, arguments)
        item = action_request({'actionId': arguments.get('actionId'), 'action': action,
                               'arguments': {k: v for k, v in arguments.items() if k != 'actionId'}})
        if action == 'patch' and item['arguments'].get('path') not in task.allowed_paths:
            raise DomainError('Patch exceeds task authority', 'scope_conflict')
        job = self._job(scope, task, resolved)
        return await self._invoke_job(scope, task, item, job)

    async def _invoke_job(self, scope, task, item, job, *, composition=None):
        action = item['action']
        identity = job.request.get('acceptedCircuitIdentity', {})
        if (action == 'patch' and identity.get('mode') == 'imported_no_concept'
                and item['arguments'].get('path') in {entry['path'] for entry in identity['sourceArtifacts']}):
            raise DomainError('Imported routing preserves approved source bytes; edits need a separately approved repair',
                              'imported_source_immutable')
        await self._admit(scope, job, composition=composition)
        if composition is not None:
            composition.validate(scope, job.request['compositionId'])
        cached = self._claim(scope, job, item)
        if cached is not None:
            return cached
        directory = self._directory(job)
        try:
            if action == 'patch':
                content = item['arguments'].get('content')
                if not isinstance(content, str) or len(content.encode()) > 1024 * 1024:
                    raise ValueError('Patch content exceeds bound')
                # Exact authorized input bytes survive failed builds and worker/container loss.
                self.artifacts.blobs.put(content.encode())
            request_path = directory / 'request.json'
            if request_path.exists():
                if fingerprint(json.loads(safe_file(directory, 'request.json').read_bytes())) != job.request_hash:
                    raise ValueError('Persisted native input changed')
            else:
                atomic_json(request_path, job.request)
            filename = digest(item['actionId'].encode()) + '.json'
            atomic_json(directory / 'actions' / filename, item)
            # Canonical JSON bytes and structural fingerprint use the same sorted serialization.
            daemon_root = self.settings.daemon_control_root or str(self.root)
            if not (directory / 'results' / filename).exists():
                if job.status == 'pending':
                    with self.projects.transaction() as session:
                        session.scalar(select(NativeJob).where(NativeJob.id == job.id).with_for_update())
                        if session.get(NativeAction, (job.id, '_dispatch')) is None:
                            session.add(NativeAction(job_id=job.id, action_id='_dispatch', request_hash=job.request_hash,
                                status='pending', result={'kind': 'dispatch-record', 'jobId': job.id}))
                async def remember_container(ident):
                    if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{64}', ident):
                        raise DomainError('Native immutable identity is missing', 'native_dispatch_uncertain')
                    with self.projects.transaction() as session:
                        session.scalar(select(NativeJob).where(NativeJob.id == job.id).with_for_update())
                        dispatch = session.get(NativeAction, (job.id, '_dispatch'))
                        if dispatch is None:
                            raise DomainError('Native dispatch receipt is missing', 'native_dispatch_uncertain')
                        prior = dispatch.result.get('containerId')
                        if prior is not None and prior != ident:
                            raise DomainError('Native container identity changed', 'native_contract_changed')
                        dispatch.result = {**dispatch.result, 'containerId': ident}

                await self.docker.ensure(job.id, job.request, job.request['imageDigest'],
                                         str(Path(daemon_root) / job.id), allow_create=job.status == 'pending',
                                         on_observed=remember_container)
                with self.projects.transaction() as session:
                    session.scalar(select(NativeJob).where(NativeJob.id == job.id).with_for_update())
                    dispatch = session.get(NativeAction, (job.id, '_dispatch'))
                    if dispatch is not None:
                        dispatch.status = 'completed'  # Owned container creation/start was observed.
            response = await self._result(scope, job, item, directory)
            if response.get('status') not in {'succeeded', 'failed'}:
                raise ValueError('Invalid native result status')
            failed = response['status'] == 'failed'
            if action == 'patch' and not failed:
                expected = item['arguments']
                observed = response.get('result', {})
                if (observed.get('path') != expected['path']
                        or observed.get('sha256') != digest(expected['content'].encode())
                        or job.source_manifest.get(expected['path']) != expected['expectedSha256']):
                    raise ValueError('Native patch acknowledgement differs from authorized input')
            artifacts = await self._publish(scope, task, job, response, composition=composition) if action == 'finish' and not failed else []
            result = {'summary': (response.get('error', 'Native action failed') if failed
                                  else response.get('result', {}).get('summary', f'Native {action} completed'))[:12000],
                      'artifacts': artifacts,
                      'observations': [{'name': 'native.' + action, 'status': 'ERROR' if failed else 'UNKNOWN',
                                        'details': ({'error': response.get('error', '')[:12000], 'executionSucceeded': False,
                                                     'candidateOnly': True} if failed else
                                                    {**{k: v for k, v in response['result'].items() if k != 'files'},
                                                     'executionSucceeded': True, 'candidateOnly': True}),
                                        'independent': False}], 'blockers': []}
            await self.repository.assert_active(scope)
            with self.projects.transaction() as session:
                self.projects.authorized(session, scope.actor, scope.project_id, write=True)
                record = session.get(NativeAction, (job.id, item['actionId']))
                record.status, record.result = 'completed', result
                current = session.get(NativeJob, job.id)
                current.active_action = None
                if action == 'patch' and not failed:
                    current.source_manifest = {**current.source_manifest,
                                               item['arguments']['path']: response['result']['sha256']}
                current.status = 'finished' if action == 'finish' and not failed else 'running'
            if action == 'finish' and not failed:
                try:
                    await self._confirm_termination(job.id)
                except Exception:
                    pass  # Reconciliation retries cleanup; the committed result remains valid.
            return result
        except BaseException:
            # Publication remains fenced even if termination cannot immediately reach Docker.
            (directory / 'cancel').touch(mode=0o600)
            with self.projects.transaction() as session:
                current = session.get(NativeJob, job.id)
                current.status = 'failed'
            try:
                await self._confirm_termination(job.id)
            except Exception:
                pass  # Durable failed job is discoverable by reconciliation.
            raise

    async def close_task(self, scope, task_id):
        """Release the native jobs of a task that ended without finishing.

        A blocked or failed task must not keep its container, and the shared capacity it reserves, until the
        session deadline. Its saved sources stay in the blob store, so a retry continues them on a new job.
        """
        with self.projects.transaction() as session:
            idle = [job.id for job in session.scalars(select(NativeJob).where(
                NativeJob.run_id == scope.run_id, NativeJob.task_id == task_id,
                NativeJob.status.in_({'pending', 'running'}), NativeJob.active_action.is_(None)))]
            for ident in idle:
                session.get(NativeJob, ident).status = 'cancelled'
        for ident in idle:
            with self.projects.transaction() as session:
                job = session.get(NativeJob, ident)
            (self._directory(job) / 'cancel').touch(mode=0o600)
            await self._confirm_termination(ident)

    def rebuild_snapshot(self, session, actor, job):
        """Server-only admission: exact trusted source bytes, never inferred from a lost container."""
        self.projects.authorized(session, actor, job.project_id, write=True)
        if (job.status not in {'finished', 'failed', 'cancelled'} or job.active_action is not None
                or session.scalar(select(NativeAction).where(NativeAction.job_id == job.id,
                                                            NativeAction.status != 'completed')) is not None):
            raise DomainError('Native job requires reconciliation before exact rebuild', 'native_uncertain')
        proof = session.get(NativeAction, (job.id, '_termination'))
        if (proof is None or proof.status != 'completed' or proof.request_hash != job.request_hash
                or proof.result != {'kind': 'termination-proof', 'jobId': job.id, 'containerStopped': True}):
            raise DomainError('Native container termination is not confirmed', 'native_uncertain')
        original_duration(job.request)
        if fingerprint(job.request) != job.request_hash or job.request.get('protocol') != PROTOCOL:
            raise DomainError('Original native contract failed its integrity check', 'native_contract_changed')
        sources, total = [], 0
        for path, checksum in sorted(job.source_manifest.items()):
            source_path(path)
            raw = self.artifacts.blobs.get(checksum)
            total += len(raw)
            if total > MAX_SOURCES or len(sources) >= 50:
                raise DomainError('Exact native source closure exceeds bounds', 'artifact_limit')
            sources.append({'path': path, 'sha256': checksum, 'contentBase64': base64.b64encode(raw).decode()})
        if not sources:
            raise DomainError('Native rebuild has no exact source closure', 'native_source_missing')
        snapshot = {'sourceJobId': job.id, 'sourceRequestHash': job.request_hash,
                    'sourceManifest': dict(job.source_manifest), 'originalRequest': copy.deepcopy(job.request),
                    'sources': sources}
        return {**snapshot, 'snapshotHash': fingerprint(snapshot)}

    async def invoke_rebuild(self, scope, task, snapshot, action, arguments):
        """Deterministic server coordinator only; never registered as a model tool."""
        await self.repository.assert_active(scope)
        if action not in {'build', 'render', 'finish'} or (action == 'render' and task.capability.value != 'cad'):
            raise DomainError('Exact rebuild cannot edit or select new sources', 'rebuild_scope_conflict')
        item = action_request({'actionId': arguments.get('actionId'), 'action': action,
                               'arguments': {k: v for k, v in arguments.items() if k != 'actionId'}})
        if not isinstance(snapshot, dict) or snapshot.get('snapshotHash') != fingerprint({
                k: v for k, v in snapshot.items() if k != 'snapshotHash'}):
            raise DomainError('Native rebuild snapshot changed', 'rebuild_scope_conflict')
        ident = fingerprint({'tenant': scope.actor.tenant_id, 'project': scope.project_id,
                             'run': scope.run_id, 'task': task.id})
        with self.projects.transaction() as session:
            self.projects.authorized(session, scope.actor, scope.project_id, write=True)
            run = session.get(Objective, scope.run_id)
            admission = run.data.get('nativeRebuild', {}) if run else {}
            grant = admission.get('grant', {})
            if admission.get('snapshot') != snapshot:
                raise DomainError('Native rebuild lacks its server-authorized snapshot', 'rebuild_scope_conflict')
            source_job = session.get(NativeJob, snapshot['sourceJobId'])
            if (source_job is None or source_job.project_id != scope.project_id
                    or source_job.request_hash != snapshot['sourceRequestHash']
                    or source_job.request != snapshot['originalRequest']
                    or source_job.source_manifest != snapshot['sourceManifest']):
                raise DomainError('Original native rebuild provenance changed', 'native_contract_changed')
            if self.rebuild_snapshot(session, scope.actor, source_job) != snapshot:
                raise DomainError('Native rebuild admission history changed', 'native_contract_changed')
            original = snapshot['originalRequest']
            required = 3 if original['lane'] == 'cad' else 2
            max_actions = min(original['maxActions'], grant.get('maxActions', 0), task.max_actions)
            duration = min(original_duration(original), grant.get('maxDurationSeconds', 0), 600)
            if (task.capability.value != original['lane'] or task.input_artifact_ids
                    or set(task.allowed_paths) != set(original['allowedPaths'])
                    or max_actions < required or duration < 1 or not grant.get('rootJobId')):
                raise DomainError('Native rebuild grant differs from its original tool contract', 'rebuild_scope_conflict')
            # Source bytes are checked again at dispatch, even though the grant snapshot is immutable.
            if {entry['path']: entry['sha256'] for entry in snapshot['sources']} != snapshot['sourceManifest']:
                raise DomainError('Native rebuild source manifest changed', 'native_source_missing')
            for source in snapshot['sources']:
                if self.artifacts.blobs.get(source['sha256']) != base64.b64decode(source['contentBase64'], validate=True):
                    raise DomainError('Native rebuild source content changed', 'native_source_missing')
            existing = session.get(NativeJob, ident)
            task_hash = fingerprint(task.model_dump(mode='json'))
            if existing:
                if existing.task_hash != task_hash or existing.request.get('sourceSnapshotHash') != snapshot['snapshotHash']:
                    raise DomainError('Native rebuild task changed during replay', 'task_conflict')
                job = existing
            else:
                request = {**copy.deepcopy(original), 'jobId': ident, 'revision': scope.revision,
                    'sources': copy.deepcopy(snapshot['sources']),
                    'deadline': min(scope.deadline_at.timestamp(), time.time() + duration),
                    'sessionSeconds': duration, 'maxActions': max_actions,
                    'rebuildOf': source_job.id, 'rebuildRootJobId': grant['rootJobId'],
                    'sourceSnapshotHash': snapshot['snapshotHash']}
                if self.capacity is not None:
                    request['capacityPoolId'] = self.capacity.pool_id
                    request['capacityResources'] = original.get('capacityResources', capacity_resources(request))
                job = NativeJob(id=ident, project_id=scope.project_id, run_id=scope.run_id, task_id=task.id,
                    task_hash=task_hash, request_hash=fingerprint(request), request=request, status='pending',
                    source_manifest=dict(snapshot['sourceManifest']), active_action=None)
                session.add(job)
        return await self._invoke_job(scope, task, item, job)

    async def _confirm_termination(self, job_id):
        with self.projects.transaction() as session:
            image = session.get(NativeJob, job_id).request['imageDigest']
            dispatch = session.get(NativeAction, (job_id, '_dispatch'))
            known_id = dispatch.result.get('containerId') if dispatch is not None else None
            prior_dispatch = dispatch is not None or session.scalar(select(NativeAction.job_id).where(
                NativeAction.job_id == job_id, NativeAction.action_id.not_in(('_dispatch', '_termination', '_cleanup'))).limit(1)) is not None
        observed = await self.docker.confirm_terminated(job_id, image, observed_id=known_id)
        if observed is None and prior_dispatch:
            raise DomainError('Native creation identity remains uncertain', 'native_dispatch_uncertain')
        with self.projects.transaction() as session:
            # Finish and maintenance can confirm the same container concurrently.
            # Serialize receipt insertion on its existing parent row, then recheck.
            job = session.scalar(select(NativeJob).where(NativeJob.id == job_id).with_for_update())
            dispatch = session.get(NativeAction, (job_id, '_dispatch'))
            if observed is not None:
                if not isinstance(observed, str) or not re.fullmatch(r'[a-f0-9]{64}', observed):
                    raise DomainError('Native termination identity is invalid', 'native_dispatch_uncertain')
                if dispatch is None:
                    dispatch = NativeAction(job_id=job_id, action_id='_dispatch', request_hash=job.request_hash,
                        status='completed', result={'kind': 'dispatch-record', 'jobId': job_id})
                    session.add(dispatch)
                if dispatch.result.get('containerId', observed) != observed:
                    raise DomainError('Native termination identity changed', 'native_contract_changed')
                dispatch.status = 'completed'
                dispatch.result = {**dispatch.result, 'containerId': observed}
            proof = session.get(NativeAction, (job_id, '_termination'))
            value = {'kind': 'termination-proof', 'jobId': job_id, 'containerStopped': True}
            if proof is None:
                session.add(NativeAction(job_id=job_id, action_id='_termination', request_hash=job.request_hash,
                                         status='completed', result=value))
            elif proof.status != 'completed' or proof.request_hash != job.request_hash or proof.result != value:
                raise DomainError('Native termination proof changed', 'native_contract_changed')

    async def _cleanup(self, job_id):
        with self.projects.transaction() as session:
            dispatch = session.get(NativeAction, (job_id, '_dispatch'))
            known_id = dispatch.result.get('containerId') if dispatch is not None else None
            if dispatch is not None and known_id is None:
                raise DomainError('Native cleanup identity is uncertain', 'native_dispatch_uncertain')
        await self.docker.cleanup(job_id, observed_id=known_id)
        with self.projects.transaction() as session:
            job = session.scalar(select(NativeJob).where(NativeJob.id == job_id).with_for_update())
            terminated = session.get(NativeAction, (job_id, '_termination'))
            expected = {'kind': 'termination-proof', 'jobId': job_id, 'containerStopped': True}
            if (terminated is None or terminated.status != 'completed' or terminated.request_hash != job.request_hash
                    or terminated.result != expected):
                raise DomainError('Native cleanup lacks termination proof', 'native_uncertain')
            value = {'kind': 'cleanup-proof', 'jobId': job_id, 'containerRemoved': True}
            receipt = session.get(NativeAction, (job_id, '_cleanup'))
            if receipt is None:
                session.add(NativeAction(job_id=job_id, action_id='_cleanup', request_hash=job.request_hash,
                                         status='completed', result=value))
            elif receipt.status != 'completed' or receipt.request_hash != job.request_hash or receipt.result != value:
                raise DomainError('Native cleanup proof changed', 'native_contract_changed')
            if self.capacity is not None:
                self.capacity.release('native:' + job.id, job.request_hash,
                    {'kind': 'container-removed', 'identity': 'kleos-native-' + job.id}, session=session)

    async def reconcile(self):
        """Terminate expired/terminal jobs without claiming graph or publication authority."""
        with self.projects.transaction() as session:
            def proof_exists(action_id, kind, field):
                return select(NativeAction.job_id).where(
                    NativeAction.job_id == NativeJob.id, NativeAction.action_id == action_id,
                    NativeAction.status == 'completed', NativeAction.request_hash == NativeJob.request_hash,
                    NativeAction.result['kind'].as_string() == kind,
                    NativeAction.result['jobId'].as_string() == NativeJob.id,
                    NativeAction.result[field].as_boolean().is_(True)).exists()
            cleaned = (proof_exists('_termination', 'termination-proof', 'containerStopped')
                       & proof_exists('_cleanup', 'cleanup-proof', 'containerRemoved')
                       & NativeJob.status.in_({'finished', 'failed', 'cancelled'}))
            jobs = list(session.scalars(select(NativeJob).where(~cleaned)))
            pending = []
            for job in jobs:
                run = session.get(Objective, job.run_id)
                project = session.get(Project, job.project_id)
                if (run is None or project is None or run.status not in {'pending', 'running', 'waiting'}
                        or project.context_revision != job.request['revision']):
                    if job.status not in {'finished', 'failed', 'cancelled'}:
                        job.status = 'cancelled'
                if job.status in {'finished', 'failed', 'cancelled'} or time.time() >= job.request['deadline']:
                    if job.status not in {'finished', 'failed', 'cancelled'}:
                        job.status = 'failed'
                    pending.append(job.id)
        failures = []
        for ident in pending:
            try:
                await self._confirm_termination(ident)
                await self._cleanup(ident)
            except Exception:
                failures.append(ident)
        return {'reconciled': len(pending) - len(failures), 'pending': failures}
