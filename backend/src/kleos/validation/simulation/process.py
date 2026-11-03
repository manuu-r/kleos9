"""Control-plane transport for the isolated ngspice image: one fixed container per compiled deck.

The container has no network, no credentials, a read-only root, dropped capabilities, bounded CPU,
memory, processes and scratch, and sees only one read-only generated deck. Cleanup never trusts a
lost create response: the exact container identity and image are verified before removal, and
capacity is released only with removal proof.
"""
import asyncio
import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx

from kleos.projects.store import fingerprint
from kleos.validation.simulation.plan import Deck
from kleos.validation.simulation.results import MAX_FILE_BYTES, DeckOutput, SimulationDataError
from kleos.validation.simulation.run import ExecutorUnavailable

PROTOCOL = 'kleos-circuit-sim-v1'
IMAGE_PATTERN = re.compile(r'sha256:[a-f0-9]{64}')
RESOURCES = {'cpus': 1, 'memoryBytes': 1024**3, 'scratchBytes': 64 * 1024**2, 'pids': 64}
MAX_LOG_BYTES = 2 * MAX_FILE_BYTES + 64 * 1024
TIMEOUT_SECONDS = 25


def sim_spec(image, directory, execution_id):
    """Fixed entry point; the only input is one read-only compiler-generated deck."""
    return {
        'Image': image, 'Cmd': [], 'User': '10001:10001', 'WorkingDir': '/tmp',
        'Env': ['PATH=/usr/local/bin:/usr/bin:/bin', 'LANG=C.UTF-8', 'HOME=/tmp', 'HOSTNAME=kleos-circuit-sim'],
        'Labels': {'kleos.checker.protocol': PROTOCOL, 'kleos.checker.execution': execution_id},
        'NetworkDisabled': True, 'AttachStdout': True, 'AttachStderr': False,
        'HostConfig': {
            'ReadonlyRootfs': True, 'NetworkMode': 'none', 'IpcMode': 'private', 'CapDrop': ['ALL'], 'CapAdd': [],
            'Privileged': False, 'SecurityOpt': ['no-new-privileges:true'],
            'Memory': RESOURCES['memoryBytes'], 'MemorySwap': RESOURCES['memoryBytes'], 'NanoCpus': 1_000_000_000,
            'PidsLimit': RESOURCES['pids'], 'OomKillDisable': False, 'AutoRemove': False,
            'Mounts': [{'Type': 'bind', 'Source': str(directory), 'Target': '/input', 'ReadOnly': True,
                        'BindOptions': {'Propagation': 'rprivate'}}],
            'Tmpfs': {'/tmp': 'rw,nosuid,nodev,noexec,size=64m,mode=1777'},
            'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '8m', 'max-file': '1'}}}}


def decode_stdout(raw):
    output, offset = bytearray(), 0
    while offset < len(raw):
        if len(raw) - offset < 8 or raw[offset] not in (1, 2) or raw[offset + 1:offset + 4] != b'\0\0\0':
            raise SimulationDataError('Solver container output framing is invalid')
        length = int.from_bytes(raw[offset + 4:offset + 8], 'big')
        if offset + 8 + length > len(raw):
            raise SimulationDataError('Solver container output is incomplete')
        if raw[offset] == 1:
            output.extend(raw[offset + 8:offset + 8 + length])
        offset += 8 + length
    try:
        document = json.loads(output)
    except ValueError:
        raise SimulationDataError('Solver container output is not valid JSON') from None
    if (not isinstance(document, dict) or document.get('protocol') != PROTOCOL
            or any(not isinstance(document.get(key), str) for key in ('banner', 'op', 'tran'))):
        raise SimulationDataError('Solver container output has the wrong protocol')
    return document


class IsolatedNgspice:
    """DeckExecutor backed by a pinned local image. The image digest is the only installation input."""

    def __init__(self, docker, image, control_root, *, capacity=None):
        if not isinstance(image, str) or not IMAGE_PATTERN.fullmatch(image):
            raise ValueError('The simulation image must be pinned by its sha256 image ID')
        self.docker, self.image, self.capacity = docker, image, capacity
        self.root = Path(control_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    async def _verify_image(self, client):
        try:
            response = await client.get('/images/' + self.image + '/json')
        except httpx.HTTPError:
            raise ExecutorUnavailable('The container runtime is unavailable.') from None
        if response.status_code == 404:
            raise ExecutorUnavailable('The pinned simulation image is not installed.')
        response.raise_for_status()
        metadata = response.json()
        if metadata.get('Id') != self.image or metadata.get('Config', {}).get('Labels', {}).get(
                'kleos.checker.protocol') != PROTOCOL:
            raise ExecutorUnavailable('The simulation image identity does not match its pin.')

    async def run(self, deck: Deck, project_id=None):
        client = self.docker.client
        await self._verify_image(client)
        execution_id = uuid4().hex
        name = 'kleos-circuit-sim-' + execution_id
        key = 'checker:sim-' + execution_id
        request_hash = fingerprint({'image': self.image, 'executionId': execution_id, 'resources': RESOURCES})
        if self.capacity is not None:
            if project_id is None or not self.capacity.reserve(key, project_id, request_hash, RESOURCES,
                    details={'containerName': name, 'imageDigest': self.image, 'protocol': PROTOCOL}):
                raise ExecutorUnavailable('Native capacity is busy; the simulation was not started.')
        directory = Path(tempfile.mkdtemp(dir=self.root, prefix='sim-'))
        container_id, attempted = None, False
        try:
            os.chmod(directory, 0o755)
            target = directory / 'deck.cir'
            target.write_text(deck.text)
            os.chmod(target, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            deadline = time.monotonic() + TIMEOUT_SECONDS
            try:
                async with asyncio.timeout(TIMEOUT_SECONDS):
                    attempted = True
                    created = await client.post('/containers/create', params={'name': name},
                                                json=sim_spec(self.image, directory, execution_id))
                    created.raise_for_status()
                    identity = created.json().get('Id')
                    if isinstance(identity, str) and re.fullmatch(r'[a-f0-9]{64}', identity):
                        container_id = identity
                    (await client.post('/containers/' + name + '/start')).raise_for_status()
                    while True:
                        state = await client.get('/containers/' + name + '/json')
                        state.raise_for_status()
                        state = state.json()['State']
                        if not state['Running']:
                            if state.get('ExitCode') != 0 or state.get('OOMKilled'):
                                raise SimulationDataError('The solver container failed or exceeded its limits')
                            break
                        await asyncio.sleep(.05)
                    raw = bytearray()
                    async with client.stream('GET', '/containers/' + name + '/logs',
                                             params={'stdout': 1, 'stderr': 0}) as logs:
                        logs.raise_for_status()
                        async for chunk in logs.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > MAX_LOG_BYTES:
                                raise SimulationDataError('Solver container output exceeded its bound')
                document = decode_stdout(raw)
            except TimeoutError:
                raise SimulationDataError('The solver container exceeded its deadline') from None
            except httpx.HTTPError:
                raise ExecutorUnavailable('The container runtime became unavailable.') from None
            assert time.monotonic() <= deadline + 1
            return DeckOutput(document['op'], document['tran']), document['banner']
        finally:
            removed = await self._remove(client, name, container_id, execution_id)
            if self.capacity is not None and (removed or not attempted):
                self.capacity.release(key, request_hash, {'kind': 'container-removed', 'identity': name})
            for item in directory.glob('*'):
                item.unlink(missing_ok=True)
            directory.rmdir()

    async def _remove(self, client, name, container_id, execution_id):
        """Verify exact identity, force-remove, and prove absence. Returns True when removal is proven."""
        inspect = await client.get('/containers/' + name + '/json')
        if inspect.status_code == 404 and container_id is not None:
            inspect = await client.get('/containers/' + container_id + '/json')
        if inspect.status_code == 404:
            return container_id is None  # absence after an uncertain create is not proof
        inspect.raise_for_status()
        metadata = inspect.json()
        labels = metadata.get('Config', {}).get('Labels', {})
        observed = metadata.get('Id')
        if (metadata.get('Image') != self.image or labels.get('kleos.checker.execution') != execution_id
                or not isinstance(observed, str) or not re.fullmatch(r'[a-f0-9]{64}', observed)
                or (container_id is not None and container_id != observed)):
            raise SimulationDataError('Solver container cleanup identity mismatch; capacity retained')
        deleted = await client.delete('/containers/' + observed, params={'force': 1, 'v': 1})
        if deleted.status_code != 404:
            deleted.raise_for_status()
        gone = (await client.get('/containers/' + observed + '/json')).status_code == 404
        if not gone:
            raise SimulationDataError('Solver container removal is not confirmed; capacity retained')
        return True
