"""Independent PCB checker transport; no candidate code is executed by the control plane."""
import asyncio
import json
import re
import time
from datetime import UTC, datetime

from kleos.contracts.models import DomainError
from kleos.execution.security import daemon_socket_profile
from kleos.projects.store import fingerprint
from kleos.validation.pcb_check import PROTOCOL
from kleos.validation.service import CheckerAdmissionDeferred


def checker_spec(image, directory, execution_id, inherited_keys=()):
    """Fixed checker entry point; no source files, commands, credentials or writable host mounts."""
    return {
        'Image': image, 'Entrypoint': ['/usr/bin/timeout', '--signal=KILL', '175', '/usr/bin/python3', '-I', '/opt/kleos-pcb-checker/pcb_check.py'],
        'Cmd': [], 'User': '10001:10001', 'WorkingDir': '/tmp',
        'Env': [key+'=' for key in sorted(set(inherited_keys)-{'PATH', 'LANG', 'HOME', 'HOSTNAME'})]
               + ['PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin', 'LANG=C.UTF-8',
                  'HOME=/tmp', 'HOSTNAME=kleos-pcb-checker'],
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


class IsolatedPcbProcess:
    """One read-only check; caller owns durable check intent and uncertain recovery."""
    def __init__(self, docker, image, *, timeout=180, deployment='local', capacity=None):
        if not re.fullmatch('sha256:[a-f0-9]{64}', image) or timeout != 180:
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
        name = 'kleos-pcb-check-'+execution_id
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

