"""Fixed MCP container control and bounded Docker attach stream; no shell API."""
import asyncio
import hashlib
import json
import re
from contextlib import asynccontextmanager

import httpx

from kleos.contracts.models import DomainError
from kleos.execution.security import daemon_socket_profile

PROTOCOL = 'kleos-mcp-inspection-v1'
RESOURCES = {'cpus': 1, 'memoryBytes': 512 * 1024**2, 'scratchBytes': 256 * 1024**2, 'pids': 64}


def image_id(value):
    return isinstance(value, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', value)


def container_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise DomainError('MCP container identity is invalid', 'mcp_container_conflict')
    return value


def labels(job_id, request_hash):
    return {'kleos.mcp.protocol': PROTOCOL, 'kleos.mcp.job': job_id, 'kleos.mcp.request': request_hash}


def profile():
    return 'seccomp=' + json.dumps(daemon_socket_profile(), separators=(',', ':'))


def spec(job_id, request_hash, installation, directory):
    return {'Image': installation.image, 'Entrypoint': ['/usr/bin/timeout', '--signal=KILL',
        str(installation.timeout_seconds), '/opt/venv/bin/python', '-I', '/opt/kleos-mcp/server.py'],
        'Cmd': [], 'User': '10001:10001', 'WorkingDir': '/tmp', 'Env': [],
        'Labels': labels(job_id, request_hash), 'NetworkDisabled': True, 'OpenStdin': True,
        'StdinOnce': False, 'AttachStdin': True, 'AttachStdout': True, 'AttachStderr': True, 'Tty': False,
        'HostConfig': {'ReadonlyRootfs': True, 'NetworkMode': 'none', 'IpcMode': 'private',
            'CapDrop': ['ALL'], 'CapAdd': [], 'SecurityOpt': ['no-new-privileges:true', profile()],
            'Privileged': False, 'Memory': RESOURCES['memoryBytes'], 'MemorySwap': RESOURCES['memoryBytes'],
            'NanoCpus': RESOURCES['cpus'] * 1000000000, 'PidsLimit': RESOURCES['pids'],
            'OomKillDisable': False, 'AutoRemove': False,
            'Mounts': [{'Type': 'bind', 'Source': str(directory), 'Target': '/inputs', 'ReadOnly': True,
                        'BindOptions': {'Propagation': 'rprivate'}}],
            'Tmpfs': {'/tmp': f"rw,nosuid,nodev,noexec,size={RESOURCES['scratchBytes']},mode=1777"},
            'LogConfig': {'Type': 'none'}}}


class AttachChannel:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.remaining = self.kind = self.total = self.stderr = self.sent = 0

    async def send(self, content):
        self.sent += len(content)
        if len(content) > 262144 or self.sent > 1048576:
            raise DomainError('MCP stdin exceeds its bound', 'mcp_input_limit')
        self.writer.write(content)
        await self.writer.drain()

    async def receive(self, max_bytes):
        if not 1 <= max_bytes <= 16384:
            raise ValueError('Invalid MCP channel read bound')
        try:
            while True:
                if not self.remaining:
                    header = await self.reader.readexactly(8)
                    self.kind, self.remaining = header[0], int.from_bytes(header[4:], 'big')
                    if (header[1:4] != b'\0\0\0' or self.kind not in {1, 2}
                            or not 1 <= self.remaining <= 1048576):
                        raise DomainError('Invalid Docker MCP stream frame', 'mcp_transport_error')
                content = await self.reader.readexactly(min(max_bytes, self.remaining))
                self.remaining -= len(content)
                self.total += len(content)
                if self.total > 2097152:
                    raise DomainError('Docker MCP stream exceeds its bound', 'mcp_output_limit')
                if self.kind == 1:
                    return content
                self.stderr += len(content)
                if self.stderr > 65536:
                    raise DomainError('MCP diagnostic output exceeds its bound', 'mcp_output_limit')
                # Never record uncontrolled application stderr or credentials.
        except asyncio.IncompleteReadError:
            return b''


class DockerTransport:
    def __init__(self, *, socket='/var/run/docker.sock', client=None, connector=None):
        self.socket = socket
        self.client = client or httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=socket),
            base_url='http://docker/v1.45', timeout=10)
        self.connector = connector or asyncio.open_unix_connection

    async def close(self):
        await self.client.aclose()

    async def daemon_identity(self):
        response = await self.client.get('/info')
        response.raise_for_status()
        ident = response.json().get('ID')
        if not isinstance(ident, str) or not 1 <= len(ident) <= 128:
            raise DomainError('Docker daemon identity is unavailable', 'mcp_daemon_conflict')
        return ident

    async def inspect(self, ident):
        if not re.fullmatch(r'(?:[a-f0-9]{64}|kleos-mcp-[a-f0-9]{32})', ident):
            raise DomainError('Invalid MCP container identifier', 'mcp_container_conflict')
        response = await self.client.get(f'/containers/{ident}/json')
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    @staticmethod
    def owned(value, job_id, request_hash, image):
        actual = value.get('Config', {}).get('Labels', {})
        if (value.get('Image') != image or any(actual.get(key) != expected
                for key, expected in labels(job_id, request_hash).items())):
            raise DomainError('MCP container ownership changed', 'mcp_container_conflict')
        return container_id(value.get('Id'))

    async def preflight(self, installation):
        image = await self.client.get(f'/images/{installation.image}/json')
        image.raise_for_status()
        image = image.json()
        if (image.get('Id') != installation.image or
                image.get('Config', {}).get('Labels', {}).get('kleos.mcp.protocol') != PROTOCOL):
            raise DomainError('MCP image differs from its qualified runtime', 'mcp_installation_conflict')
        inherited = image.get('Config', {}).get('Env', [])
        if any(not isinstance(item, str) or '=' not in item for item in inherited):
            raise DomainError('Invalid MCP image environment', 'mcp_installation_conflict')
        return {'environmentKeys': sorted({item.split('=', 1)[0] for item in inherited}),
                'daemonId': await self.daemon_identity()}

    async def create(self, job_id, request_hash, installation, directory, *, image_env, daemon_id):
        if await self.daemon_identity() != daemon_id:
            raise DomainError('Docker daemon changed before MCP dispatch', 'mcp_daemon_conflict')
        body = spec(job_id, request_hash, installation, directory)
        # Blank inherited values; set only nonsecret deterministic runtime values.
        body['Env'] = [key + '=' for key in sorted(set(image_env)
                                                - {'PATH', 'LANG', 'QWEN_MM_NATIVE_MODE', 'QWEN_MM_AUTOLAUNCH'})]
        body['Env'] += ['PATH=/opt/venv/bin:/usr/bin:/bin', 'LANG=C.UTF-8',
                        'QWEN_MM_NATIVE_MODE=1', 'QWEN_MM_AUTOLAUNCH=0']
        response = await self.client.post('/containers/create', params={'name': 'kleos-mcp-' + job_id}, json=body)
        if response.status_code == 409:
            raise DomainError('MCP dispatch identity already exists; reconcile first', 'effect_uncertain')
        response.raise_for_status()
        return container_id(response.json().get('Id'))

    async def start(self, ident, job_id, request_hash, installation, directory):
        observed = await self.inspect(container_id(ident))
        if observed is None or self.owned(observed, job_id, request_hash, installation.image) != ident:
            raise DomainError('MCP container disappeared before dispatch', 'mcp_container_conflict')
        expected = spec(job_id, request_hash, installation, directory)
        config, host = observed.get('Config', {}), observed.get('HostConfig', {})
        if (any(config.get(key) != expected[key] for key in ('User', 'Entrypoint', 'WorkingDir', 'Tty', 'OpenStdin'))
                or config.get('Cmd') not in (None, [])):
            raise DomainError('MCP command contract changed', 'mcp_container_conflict')
        for key, value in expected['HostConfig'].items():
            if key in {'Mounts', 'LogConfig'}:
                continue
            actual = host.get(key)
            if key == 'CapAdd' and actual is None:
                actual = []
            if actual != value:
                raise DomainError('MCP resource/isolation contract changed', 'mcp_container_conflict')
        mounts = observed.get('Mounts', [])
        if (len(mounts) != 1 or mounts[0].get('Source') != str(directory)
                or mounts[0].get('Destination') != '/inputs' or mounts[0].get('RW') is not False):
            raise DomainError('MCP source mount changed', 'mcp_container_conflict')
        permitted = {'PATH', 'LANG', 'QWEN_MM_NATIVE_MODE', 'QWEN_MM_AUTOLAUNCH'}
        if any(item.partition('=')[2] and item.partition('=')[0] not in permitted
               for item in config.get('Env', [])):
            raise DomainError('MCP inherited an unexpected environment value', 'mcp_container_conflict')
        if observed.get('State', {}).get('Status') != 'created':
            raise DomainError('MCP session cannot be restarted', 'effect_uncertain')
        response = await self.client.post(f'/containers/{ident}/start')
        response.raise_for_status()

    @asynccontextmanager
    async def attach(self, ident):
        container_id(ident)
        reader, writer = await self.connector(self.socket, limit=8192)
        try:
            request = (f'POST /v1.45/containers/{ident}/attach?stream=1&stdin=1&stdout=1&stderr=1 HTTP/1.1\r\n'
                       'Host: docker\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Length: 0\r\n\r\n')
            writer.write(request.encode('ascii'))
            await writer.drain()
            headers = await reader.readuntil(b'\r\n\r\n')
            if len(headers) > 8192 or not headers.startswith(b'HTTP/1.1 101 '):
                raise DomainError('Docker MCP attach upgrade failed', 'mcp_transport_error')
            yield AttachChannel(reader, writer)
        finally:
            writer.close()
            await writer.wait_closed()

    async def remove(self, job_id, request_hash, image, observed_id=None):
        """Authority-independent cleanup, always verify exact ownership before kill."""
        target = container_id(observed_id) if observed_id else 'kleos-mcp-' + job_id
        observed = await self.inspect(target)
        if observed is None:
            # Name absence after an unacknowledged create cannot prove no late create.
            if observed_id is None:
                raise DomainError('MCP dispatch outcome requires reconciliation', 'mcp_termination_uncertain')
            if await self.inspect('kleos-mcp-' + job_id) is not None:
                raise DomainError('MCP container name was reused', 'mcp_container_conflict')
            return {'kind': 'container-removed', 'identity': observed_id}
        ident = self.owned(observed, job_id, request_hash, image)
        if observed_id and ident != observed_id:
            raise DomainError('MCP immutable container identity changed', 'mcp_container_conflict')
        response = await self.client.delete(f'/containers/{ident}', params={'force': 'true', 'v': 'true'})
        if response.status_code not in {204, 404}:
            response.raise_for_status()
        if (await self.inspect(ident) is not None or
                await self.inspect('kleos-mcp-' + job_id) is not None):
            raise DomainError('MCP container removal is unconfirmed', 'mcp_termination_uncertain')
        return {'kind': 'container-removed', 'identity': ident}


def seccomp_hash():
    return hashlib.sha256(profile().removeprefix('seccomp=').encode()).hexdigest()
