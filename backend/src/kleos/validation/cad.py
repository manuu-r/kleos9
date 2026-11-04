"""Pinned independent CAD export checking in a separate, credential-free container."""
import asyncio
import hashlib
import json
import re
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import Field

from kleos.contracts.models import DomainError, WireModel
from kleos.execution.security import daemon_socket_profile
from kleos.projects.store import fingerprint
from kleos.validation.native_check import PROTOCOL
from kleos.validation.service import CheckerAdmissionDeferred, CheckerConfiguration, CheckReport


class CadConstraint(WireModel):
    part: str = Field(pattern=r'^[a-zA-Z0-9_/-]{1,200}$')
    metric: Literal['extent_x_mm', 'extent_y_mm', 'extent_z_mm', 'volume_mm3']
    expected: float = Field(ge=0, le=1e12, allow_inf_nan=False)
    tolerance: float = Field(ge=0, le=1e6, allow_inf_nan=False)


class CadRequirements(WireModel):
    """Supplied by trusted policy from accepted requirements, never generated source."""
    units: Literal['mm'] = 'mm'
    constraints: list[CadConstraint] = Field(default_factory=list, max_length=128)
    unsupported_requirements: list[str] = Field(default_factory=list, max_length=128)


def checker_spec(image, directory, execution_id, inherited_keys=()):
    """Fixed checker entry point; no source files, commands, credentials or writable host mounts."""
    return {
        'Image': image, 'Entrypoint': ['python', '-I', '/opt/kleos-checker/native_check.py'],
        'Cmd': [], 'User': '10001:10001', 'WorkingDir': '/tmp',
        'Env': [key+'=' for key in sorted(set(inherited_keys)-{'PATH', 'LANG'})]
               + ['PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin', 'LANG=C.UTF-8'],
        'Labels': {'kleos.checker.protocol': PROTOCOL, 'kleos.checker.execution': execution_id},
        'NetworkDisabled': True, 'AttachStdout': True, 'AttachStderr': False,
        'HostConfig': {
            'ReadonlyRootfs': True, 'NetworkMode': 'none', 'IpcMode': 'private',
            'CapDrop': ['ALL'], 'CapAdd': [], 'Privileged': False,
            'SecurityOpt': ['no-new-privileges:true', 'seccomp='+json.dumps(daemon_socket_profile(), separators=(',', ':'))],
            'Memory': 2*1024**3, 'MemorySwap': 2*1024**3, 'NanoCpus': 2_000_000_000,
            'PidsLimit': 64, 'OomKillDisable': False, 'AutoRemove': False,
            'Mounts': [{'Type': 'bind', 'Source': str(directory), 'Target': '/input', 'ReadOnly': True,
                        'BindOptions': {'Propagation': 'rprivate'}}],
            'Tmpfs': {'/tmp': 'rw,nosuid,nodev,noexec,size=256m,mode=1777'},
            'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '256k', 'max-file': '1'}},
        },
    }


def decode_logs(raw):
    if len(raw) > 256*1024:
        raise DomainError('Checker output exceeded its limit', 'checker_output')
    output, offset = bytearray(), 0
    while offset < len(raw):
        if len(raw)-offset < 8 or raw[offset] not in (1, 2) or raw[offset+1:offset+4] != b'\0\0\0':
            raise DomainError('Checker output framing is invalid', 'checker_output')
        length = int.from_bytes(raw[offset+4:offset+8], 'big')
        if offset+8+length > len(raw):
            raise DomainError('Checker output is incomplete', 'checker_output')
        if raw[offset] == 1:
            output.extend(raw[offset+8:offset+8+length])
        offset += 8+length
    return json.loads(output)


CHECKER_RESOURCES = {'cpus': 2, 'memoryBytes': 2*1024**3, 'scratchBytes': 256*1024**2, 'pids': 64}


class IsolatedCadProcess:
    """One read-only check; caller owns durable check intent and uncertain recovery."""
    def __init__(self, docker, image, *, timeout=120, deployment='local', capacity=None):
        if not re.fullmatch('sha256:[a-f0-9]{64}', image) or not 1 <= timeout <= 300:
            raise ValueError('Checker image and time bounds must be pinned')
        # Current qualification is the local Docker Desktop execution path only.
        if deployment != 'local':
            raise ValueError('This checker execution policy is qualified only for local deployment')
        self.docker, self.image, self.timeout, self.capacity = docker, image, timeout, capacity

    async def run(self, directory, execution_id, *, project_id=None, assert_active=None, deadline_at=None):
        client = self.docker.client
        info = await client.get('/info')
        info.raise_for_status()
        if 'docker desktop' not in info.json().get('OperatingSystem', '').lower():
            raise DomainError('Checker requires its qualified Docker Desktop execution policy', 'checker_unavailable')
        image = await client.get('/images/'+self.image+'/json')
        image.raise_for_status()
        metadata = image.json()
        if (metadata.get('Id') != self.image
                or metadata.get('Config', {}).get('Labels', {}).get('kleos.checker.protocol') != PROTOCOL):
            raise DomainError('Checker image identity mismatch', 'checker_unavailable')
        keys = [item.split('=', 1)[0] for item in metadata.get('Config', {}).get('Env', [])]
        spec = checker_spec(self.image, directory, execution_id, keys)
        name = 'kleos-cad-check-'+execution_id
        created = False
        container_id = None
        request_hash = fingerprint({'image': self.image, 'executionId': execution_id,
                                    'resources': CHECKER_RESOURCES})
        budget = self.timeout if deadline_at is None else min(self.timeout, (deadline_at-datetime.now(UTC)).total_seconds())
        deadline = time.monotonic()+max(0, budget)
        if self.capacity is not None:
            admission_deadline = min(deadline, time.monotonic()+1)
            while True:
                if assert_active is not None:
                    await assert_active()
                if time.monotonic() >= deadline:
                    raise DomainError('Independent checker capacity deadline exceeded', 'deadline_exceeded')
                if self.capacity.reserve('checker:'+execution_id, project_id, request_hash, CHECKER_RESOURCES,
                        details={'containerName': name, 'imageDigest': self.image, 'protocol': PROTOCOL}):
                    break
                if time.monotonic() >= admission_deadline:
                    raise CheckerAdmissionDeferred('Native capacity is busy; no checker dispatch attempted')
                await asyncio.sleep(min(.2, max(0, admission_deadline-time.monotonic())))
        attempted = False
        try:
            async with asyncio.timeout(max(0, deadline-time.monotonic())):
                if assert_active is not None:
                    await assert_active()
                attempted = True
                response = await client.post('/containers/create', params={'name': name}, json=spec)
                response.raise_for_status()
                created = True
                identity = response.json().get('Id')
                if isinstance(identity, str) and re.fullmatch(r'[a-f0-9]{64}', identity):
                    container_id = identity
                response = await client.post('/containers/'+name+'/start')
                response.raise_for_status()
                while True:
                    state = await client.get('/containers/'+name+'/json')
                    state.raise_for_status()
                    state = state.json()['State']
                    if not state['Running']:
                        if state.get('ExitCode') != 0 or state.get('OOMKilled'):
                            raise DomainError('Independent checker process failed', 'checker_failed')
                        break
                    await asyncio.sleep(.1)
                # Stream bounds protect against a misconfigured image or oversized parser output.
                async with client.stream('GET', '/containers/'+name+'/logs', params={'stdout': 1, 'stderr': 0}) as logs:
                    logs.raise_for_status()
                    raw = bytearray()
                    async for chunk in logs.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 256*1024:
                            raise DomainError('Checker output exceeded its limit', 'checker_output')
                return decode_logs(raw)
        finally:
            # A lost create response may still have started a container. Check its exact
            # server-owned identity before cleanup; absence after uncertain create is not proof.
            inspect = await client.get('/containers/'+name+'/json')
            if inspect.status_code == 404 and container_id is not None:
                inspect = await client.get('/containers/'+container_id+'/json')
            observed = inspect.status_code != 404
            if observed:
                inspect.raise_for_status()
                metadata = inspect.json()
                labels = metadata.get('Config', {}).get('Labels', {})
                if (metadata.get('Image') != self.image or labels.get('kleos.checker.protocol') != PROTOCOL
                        or labels.get('kleos.checker.execution') != execution_id):
                    raise DomainError('Checker cleanup identity mismatch', 'capacity_termination_unconfirmed')
                observed_id = metadata.get('Id')
                if not isinstance(observed_id, str) or not re.fullmatch(r'[a-f0-9]{64}', observed_id):
                    raise DomainError('Checker immutable container identity is missing', 'capacity_termination_unconfirmed')
                if container_id is not None and container_id != observed_id:
                    raise DomainError('Checker immutable container identity changed', 'capacity_termination_unconfirmed')
                container_id = observed_id
                removed = await client.delete('/containers/'+container_id, params={'force': 1, 'v': 1})
                if removed.status_code != 404:
                    removed.raise_for_status()
                by_id = await client.get('/containers/'+container_id+'/json')
                inspect = await client.get('/containers/'+name+'/json')
                if by_id.status_code != 404 or inspect.status_code != 404:
                    raise DomainError('Checker removal is not confirmed', 'capacity_termination_unconfirmed')
            if self.capacity is not None and (observed or (created and container_id is not None) or not attempted):
                self.capacity.release('checker:'+execution_id, request_hash,
                    {'kind': 'container-removed', 'identity': name})


class CadChecker:
    def __init__(self, repository, blobs, process, control_root, requirements_loader, *, daemon_root=None):
        self.repository, self.blobs, self.process = repository, blobs, process
        self.root = Path(control_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.daemon_root = Path(daemon_root).resolve() if daemon_root else self.root
        self.requirements_loader = requirements_loader

    async def invoke(self, scope, subject):
        if subject.project_id != scope.project_id or subject.revision != scope.revision:
            raise DomainError('Checker subject scope mismatch', 'scope_conflict')
        refs = subject.sources + subject.outputs
        resolved = await self.repository.resolve_artifacts(scope, [ref.id for ref in refs])
        if resolved != refs:
            raise DomainError('Checker artifact binding changed', 'revision_conflict')
        requirements = CadRequirements.model_validate(await self.requirements_loader(scope, subject))
        if any(len(item) > 500 for item in requirements.unsupported_requirements):
            raise DomainError('Checker requirements exceed their limit', 'checker_input')
        execution_id = 'cad-check-'+uuid4().hex
        request = {'protocol': PROTOCOL, 'executionId': execution_id, 'subjectHash': subject.subject_hash,
                   **requirements.model_dump(by_alias=True), 'files': []}
        directory = Path(tempfile.mkdtemp(prefix='cad-check-', dir=self.root))
        directory.chmod(0o755)
        total = 0
        try:
            for ref in subject.outputs:
                path = PurePosixPath(ref.path)
                if path.suffix.lower() not in ('.step', '.stp', '.stl'):
                    continue
                part = str(path.with_suffix(''))
                if not re.fullmatch(r'[a-zA-Z0-9_/-]{1,200}', part):
                    raise DomainError('Unsupported CAD export path', 'checker_input')
                content = self.blobs.get(ref.sha256)
                total += len(content)
                if len(content) != ref.size or total > 128*1024**2:
                    raise DomainError('Checker input byte limit or digest binding failed', 'checker_input')
                target = directory/ref.sha256
                target.write_bytes(content)
                target.chmod(0o644)
                request['files'].append({'artifactId': ref.id, 'part': part,
                                         'kind': 'stl' if path.suffix.lower() == '.stl' else 'step',
                                         'sha256': ref.sha256, 'size': ref.size})
            if not request['files']:
                return CheckReport(subject_hash=subject.subject_hash, execution_id=execution_id,
                                   status='NOT_APPLICABLE', summary='No CAD exports in this candidate')
            (directory/'request.json').write_text(json.dumps(request, allow_nan=False))
            (directory/'request.json').chmod(0o644)
            kwargs = {'project_id': scope.project_id, 'assert_active': lambda: self.repository.assert_active(scope),
                      'deadline_at': scope.deadline_at} if getattr(self.process, 'capacity', None) is not None else {}
            result = await self.process.run(self.daemon_root/directory.name, execution_id, **kwargs)
            await self.repository.assert_active(scope)
            if (result.get('protocol') != PROTOCOL or result.get('subjectHash') != subject.subject_hash
                    or result.get('executionId') != execution_id):
                raise DomainError('Independent checker result binding mismatch', 'checker_output')
            status = result.get('status')
            if status not in {'PASS', 'FAIL', 'UNKNOWN', 'ERROR'}:
                raise DomainError('Independent checker status invalid', 'checker_output')
            return CheckReport(subject_hash=subject.subject_hash, execution_id=execution_id, status=status,
                               summary=result.get('reason') or 'Independent CAD export integrity and explicitly scoped numeric checks; no physical acceptance',
                               measurements={'parts': result.get('measurements', {}),
                                             'unsupportedRequirements': result.get('unsupportedRequirements', []),
                                             'inputBindings': request['files'], 'constraints': request['constraints'],
                                             'units': 'mm', 'physicalAcceptance': False})
        finally:
            shutil.rmtree(directory)


def install_cad_checker(registry, adapter, evidence_path):
    """Explicit registration only. Release-policy selection is the server integrator's decision."""
    evidence = json.loads(Path(evidence_path).read_text())
    expected_cases = {'valid_exports': 'PASS', 'dimension_mismatch': 'FAIL', 'digest_mismatch': 'FAIL',
                      'unsupported_requirement': 'UNKNOWN', 'wrong_units': 'UNKNOWN', 'mesh_mismatch': 'FAIL'}
    if (evidence.get('qualified') is not True or evidence.get('imageId') != adapter.process.image
            or evidence.get('protocol') != PROTOCOL or evidence.get('sourceOverride') is not False
            or evidence.get('deployment') != 'local'
            or evidence.get('networkPolicy') != 'docker-desktop-daemon-seccomp'
            or evidence.get('containerChecksPassed') is not True
            or evidence.get('daemonSeccompSha256') != hashlib.sha256(json.dumps(daemon_socket_profile(), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            or evidence.get('checkerSourceSha256') != hashlib.sha256(Path(__file__).with_name('native_check.py').read_bytes()).hexdigest()
            or set(evidence.get('passedCases', [])) != set(expected_cases)
            or {case.get('case'): case.get('result', {}).get('status') for case in evidence.get('cases', [])} != expected_cases
            or len(evidence.get('containers', [])) != len(expected_cases)):
        raise DomainError('Independent CAD checker qualification does not match installation', 'checker_unqualified')
    config = CheckerConfiguration(key='cad.export-integrity', version='1',
                                  installation_hash=adapter.process.image.removeprefix('sha256:'),
                                  qualified=True, timeout_seconds=adapter.process.timeout)
    registry.register(config, adapter.invoke)
    return config
