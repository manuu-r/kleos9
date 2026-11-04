"""Control-plane Docker API only; the socket is never mounted into native jobs."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from kleos.execution.protocol import MAX_RESULT, NATIVE_MAX_SESSION_SECONDS, PROTOCOL
from kleos.execution.security import daemon_socket_profile


@dataclass(frozen=True)
class Resources:
    cpus: int = 2
    memory_bytes: int = 4 * 1024**3
    pids: int = 128
    scratch_bytes: int = 2 * 1024**3

    def __post_init__(self):
        if not (1 <= self.cpus <= 8 and 512 * 1024**2 <= self.memory_bytes <= 16 * 1024**3
                and 32 <= self.pids <= 512 and 256 * 1024**2 <= self.scratch_bytes <= 16 * 1024**3):
            raise ValueError('Native resources are outside deployment bounds')

    def wire(self):
        return {'cpus': self.cpus, 'memoryBytes': self.memory_bytes,
                'pids': self.pids, 'scratchBytes': self.scratch_bytes}


@dataclass(frozen=True)
class NativeSettings:
    control_root: Path
    images: dict[str, str]
    # Absolute daemon-side path corresponding to control_root; never supplied by an agent.
    daemon_control_root: str | None = None
    resources: Resources = field(default_factory=Resources)
    application_resources: Resources = field(default_factory=lambda: Resources(
        memory_bytes=6 * 1024**3, pids=256, scratch_bytes=4 * 1024**3))
    poll_seconds: float = .2
    session_seconds: int = 600
    docker_desktop_compatibility: bool = False
    deployment: str = "local"
    firmware_boards: tuple[str, ...] = ()

    def __post_init__(self):
        if self.docker_desktop_compatibility and self.deployment != "local":
            raise ValueError("Docker Desktop compatibility is local-only")
        if (len(set(self.firmware_boards)) != len(self.firmware_boards)
                or any(board not in {'nrf52840dk/nrf52840', 'native_sim'} for board in self.firmware_boards)):
            raise ValueError('Firmware board allowlist contains an unsupported target')
        if any(not re.fullmatch(r'sha256:[a-f0-9]{64}', image) for image in self.images.values()):
            raise ValueError('Native images must be pinned immutable Docker image IDs')
        if not 1 <= self.session_seconds <= NATIVE_MAX_SESSION_SECONDS or not .01 <= self.poll_seconds <= 5:
            raise ValueError('Native time bounds are invalid')


def container_spec(job_id, request, image, control_directory):
    limits = request['resources']
    mounts = [{'Type': 'bind', 'Source': str(control_directory), 'Target': '/control', 'ReadOnly': True,
               'BindOptions': {'Propagation': 'rprivate'}}]
    tmpfs = {'/run': 'rw,nosuid,nodev,noexec,size=128m',
             '/tmp': f"rw,nosuid,nodev,size={limits['scratchBytes']},mode=1777"}
    if request['lane'] == 'application':
        tmpfs['/tmp'] += ',exec'  # Private Flutter/Dart binaries execute only inside this bounded mount.
    spec = {
        'Image': image, 'Entrypoint': ['python', '-I', '/opt/kleos-native/bootstrap.py'], 'Cmd': [],
        'User': '0:0', 'WorkingDir': '/', 'Env': ['KLEOS_NATIVE_SANDBOX=1'],
        'Labels': {'kleos.native.protocol': PROTOCOL, 'kleos.native.job': job_id},
        'NetworkDisabled': True, 'AttachStdout': False, 'AttachStderr': False,
        'HostConfig': {'ReadonlyRootfs': True, 'NetworkMode': 'none', 'IpcMode': 'private',
                       'CapDrop': ['ALL'], 'CapAdd': ['CHOWN', 'SETUID', 'SETGID', 'KILL', 'DAC_OVERRIDE'],
                       'SecurityOpt': ['no-new-privileges:true'], 'Privileged': False,
                       'Memory': limits['memoryBytes'], 'MemorySwap': limits['memoryBytes'],
                       'NanoCpus': limits['cpus'] * 1_000_000_000, 'PidsLimit': limits['pids'],
                       'OomKillDisable': False, 'AutoRemove': False, 'Mounts': mounts, 'Tmpfs': tmpfs,
                       'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '1m', 'max-file': '1'}}},
    }

    if request.get('networkPolicy') == 'docker-desktop-daemon-seccomp':
        spec['HostConfig']['SecurityOpt'].append('seccomp=' + json.dumps(daemon_socket_profile(), separators=(',', ':')))
        spec['Env'].append('KLEOS_NATIVE_DOCKER_DESKTOP=1')
    return spec


class DockerClient:
    """Injectable asynchronous Docker HTTP client. Never accepts a command from model input."""
    def __init__(self, socket='/var/run/docker.sock', *, client=None):
        self.client = client or httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=socket), base_url='http://docker/v1.45', timeout=20)

    async def inspect(self, name):
        result = await self.client.get('/containers/' + name + '/json')
        if result.status_code == 404:
            return None
        result.raise_for_status()
        return result.json()

    async def ensure(self, job_id, request, image, control_directory, *, allow_create=True, on_observed=None):
        name = 'kleos-native-' + job_id
        compatibility = request.get('networkPolicy') == 'docker-desktop-daemon-seccomp'
        if compatibility:
            daemon = await self.client.get('/info')
            daemon.raise_for_status()
            if 'docker desktop' not in daemon.json().get('OperatingSystem', '').lower():
                raise RuntimeError('Compatibility policy requires a verified local Docker Desktop daemon')
        inspected = await self.inspect(name)
        if inspected is None:
            if not allow_create:
                raise RuntimeError('Native container is missing after dispatch; reconcile before retry')
            result = await self.client.get('/images/' + image + '/json')
            result.raise_for_status()
            metadata = result.json()
            if (metadata.get('Id') != image
                    or metadata.get('Config', {}).get('Labels', {}).get('kleos.native.protocol') != PROTOCOL):
                raise RuntimeError('Native image is not the pinned rewritten runtime')
            spec = container_spec(job_id, request, image, control_directory)
            inherited = {item.split('=', 1)[0] for item in metadata.get('Config', {}).get('Env', [])}
            spec['Env'] = [key + '=' for key in sorted(inherited - {'PATH', 'LANG', 'KLEOS_NATIVE_SANDBOX', 'KLEOS_NATIVE_DOCKER_DESKTOP'})]
            spec['Env'] += ['PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin', 'LANG=C.UTF-8',
                            'KLEOS_NATIVE_SANDBOX=1', 'KLEOS_NATIVE_DOCKER_DESKTOP=' + ('1' if compatibility else '')]
            result = await self.client.post('/containers/create', params={'name': name}, json=spec)
            if result.status_code != 409:
                result.raise_for_status()
                created_id = result.json().get('Id')
                if not isinstance(created_id, str) or not re.fullmatch(r'[a-f0-9]{64}', created_id):
                    raise RuntimeError('Native immutable container identity is missing')
                if on_observed is not None:
                    await on_observed(created_id)
            inspected = await self.inspect(name)
        if inspected is None or inspected.get('Config', {}).get('Labels', {}).get('kleos.native.job') != job_id:
            raise RuntimeError('Native container ownership mismatch')
        if inspected.get('Image') != image:
            raise RuntimeError('Native container image changed')
        for variable in inspected.get('Config', {}).get('Env', []):
            key, _, value = variable.partition('=')
            if value and key not in {'PATH', 'LANG', 'KLEOS_NATIVE_SANDBOX', 'KLEOS_NATIVE_DOCKER_DESKTOP'}:
                raise RuntimeError('Native supervisor inherited an unexpected environment value')
        host = inspected.get('HostConfig', {})
        if (not host.get('ReadonlyRootfs') or host.get('NetworkMode') != 'none'
                or host.get('Privileged') or 'ALL' not in host.get('CapDrop', [])):
            raise RuntimeError('Native container isolation differs from its contract')
        if compatibility:
            expected_profile = 'seccomp=' + json.dumps(daemon_socket_profile(), separators=(',', ':'))
            if expected_profile not in host.get('SecurityOpt', []):
                raise RuntimeError('Docker Desktop syscall profile changed')
        ident = inspected.get('Id')
        if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{64}', ident):
            raise RuntimeError('Native immutable container identity is missing')
        if on_observed is not None:
            await on_observed(ident)
        state = inspected['State']
        if state.get('Status') == 'created':
            result = await self.client.post('/containers/' + ident + '/start')
            result.raise_for_status()
        elif not state.get('Running'):
            # Never restart a supervisor whose generated working state may have been lost.
            raise RuntimeError('Native session exited; a new explicitly planned task is required')
        return ident

    async def result(self, job_id, filename):
        if not re.fullmatch(r'[a-f0-9]{64}\.json', filename):
            raise ValueError('Invalid native result filename')
        response = await self.client.post('/containers/kleos-native-' + job_id + '/exec', json={
            'AttachStdout': True, 'AttachStderr': False, 'Tty': False, 'User': '0:0',
            'Cmd': ['python', '-I', '/opt/kleos-native/result_reader.py', filename]})
        if response.status_code in {404, 409}:
            return None
        response.raise_for_status()
        ident = response.json()['Id']
        raw, output = bytearray(), bytearray()
        async with self.client.stream('POST', '/exec/' + ident + '/start',
                                      json={'Detach': False, 'Tty': False}) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                while len(raw) >= 8:
                    length = int.from_bytes(raw[4:8], 'big')
                    if raw[0] != 1 or length > MAX_RESULT:
                        raise RuntimeError('Invalid native result transfer frame')
                    if len(raw) < 8 + length:
                        break
                    output.extend(raw[8:8 + length])
                    del raw[:8 + length]
                    if len(output) > MAX_RESULT:
                        raise RuntimeError('Native result exceeds transfer bound')
                if len(raw) > MAX_RESULT + 8:
                    raise RuntimeError('Native result transfer exceeds bound')
        inspected = await self.client.get('/exec/' + ident + '/json')
        inspected.raise_for_status()
        status = inspected.json()
        if status.get('ExitCode') == 44:
            return None
        if status.get('Running') or status.get('ExitCode') != 0 or raw:
            raise RuntimeError('Native result reader failed')
        return json.loads(output)

    async def running(self, job_id):
        inspected = await self.inspect('kleos-native-' + job_id)
        return bool(inspected and inspected['State'].get('Running'))

    async def stop(self, job_id):
        result = await self.client.post('/containers/kleos-native-' + job_id + '/kill')
        if result.status_code not in {404, 409}:
            result.raise_for_status()

    async def confirm_terminated(self, job_id, image, *, observed_id=None):
        """Stop the verified owned container by immutable ID, then prove it is stopped/absent."""
        if observed_id is not None and not re.fullmatch(r'[a-f0-9]{64}', observed_id):
            raise RuntimeError('Native immutable container identity is invalid')
        inspected = await self.inspect(observed_id or 'kleos-native-' + job_id)
        if inspected is None:
            return observed_id  # Exact ID absence is proof; name-only absence is not.
        labels = inspected.get('Config', {}).get('Labels', {})
        if (labels.get('kleos.native.job') != job_id or labels.get('kleos.native.protocol') != PROTOCOL
                or inspected.get('Image') != image or not re.fullmatch(r'[a-f0-9]{64}', inspected.get('Id', ''))
                or (observed_id is not None and inspected['Id'] != observed_id)):
            raise RuntimeError('Native termination ownership or image mismatch')
        ident = inspected['Id']
        result = await self.client.post('/containers/' + ident + '/kill')
        if result.status_code not in {404, 409}:
            result.raise_for_status()
        remaining = await self.inspect(ident)
        if remaining is not None and (remaining.get('Id') != ident or remaining['State'].get('Running')):
            raise RuntimeError('Native container termination remains uncertain')
        return ident

    async def cleanup(self, job_id, *, observed_id=None):
        name = 'kleos-native-' + job_id
        if observed_id is not None and not re.fullmatch(r'[a-f0-9]{64}', observed_id):
            raise RuntimeError('Native immutable container identity is invalid')
        inspected = await self.inspect(observed_id or name)
        if inspected:
            labels = inspected.get('Config', {}).get('Labels', {})
            if (labels.get('kleos.native.job') != job_id or labels.get('kleos.native.protocol') != PROTOCOL
                    or not re.fullmatch(r'[a-f0-9]{64}', inspected.get('Id', ''))
                    or (observed_id is not None and inspected['Id'] != observed_id)):
                raise RuntimeError('Native cleanup ownership mismatch')
            result = await self.client.delete('/containers/' + inspected['Id'], params={'force': 'true', 'v': 'true'})
            if result.status_code != 404:
                result.raise_for_status()
            if await self.inspect(inspected['Id']) is not None:
                raise RuntimeError('Native container removal remains uncertain')
        if await self.inspect(name) is not None:
            raise RuntimeError('Native container name remains present after cleanup')
        result = await self.client.delete('/volumes/kleos-native-scratch-' + job_id)
        if result.status_code != 404:
            result.raise_for_status()

    async def close(self):
        await self.client.aclose()
