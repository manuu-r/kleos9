import asyncio
import hashlib
import io
import json
import tarfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import test_mcp_bridge as fixtures
from sqlalchemy import select
from test_mcp_bridge import FixtureRunner, arguments, config, report

from kleos.capabilities.mcp_bridge.bridge import ArtifactInspector, StagedInput
from kleos.capabilities.mcp_bridge.docker import AttachChannel, DockerTransport, labels, spec
from kleos.capabilities.mcp_bridge.runner import DockerRunner, McpSession, _stage
from kleos.contracts.models import DomainError
from kleos.execution.capacity import CapacityService
from kleos.projects.graph_repository import Effect

authority = fixtures.authority


class FixtureDocker:
    def __init__(self, authority):
        self.repo, self.artifacts, self.scope, self.task = authority
        self.protocol = FixtureRunner()
        self.creates = self.removes = 0
        self.fail_preflight = self.fail_create = self.fail_remove = False
        self.after_start = None
        self.daemon_id = 'fixture-daemon'

    async def daemon_identity(self):
        return self.daemon_id

    async def preflight(self, installation):
        if self.fail_preflight:
            raise DomainError('Exact image unavailable', 'mcp_installation_conflict')
        return {'environmentKeys': [], 'daemonId': self.daemon_id}

    async def create(self, ident, request_hash, installation, directory, *, image_env, daemon_id):
        self.creates += 1
        self.directory, self.installation = Path(directory), installation
        self.ident, self.hash = ident, request_hash
        if self.fail_create:
            raise RuntimeError('Create acknowledgement lost')
        return 'a' * 64

    @asynccontextmanager
    async def attach(self, ident):
        refs = await self.repo.resolve_artifacts(self.scope, self.task.input_artifact_ids)
        inputs = tuple(StagedInput(ref, f'inputs/{index:03}', (self.directory / f'{index:03}').read_bytes())
                       for index, ref in enumerate(refs))
        async with self.protocol.open(self.scope, self.task, self.installation, inputs,
                                      action_id='fixture') as channel:
            yield channel

    async def start(self, *args):
        if self.after_start:
            self.after_start()

    async def remove(self, ident, request_hash, image, observed):
        self.removes += 1
        assert (ident, request_hash, image) == (self.ident, self.hash, self.installation.image)
        if self.fail_remove:
            raise DomainError('Termination unconfirmed', 'mcp_termination_uncertain')
        return {'kind': 'container-removed', 'identity': 'a' * 64}


def setup(authority, tmp_path):
    repo, artifacts, _, _ = authority
    capacity, docker = CapacityService(repo.store), FixtureDocker(authority)
    runner = DockerRunner(repo, capacity, docker, control_root=tmp_path / 'mcp')
    inspector = ArtifactInspector(config(), report(config()), environment='test', repository=repo,
                                 artifacts=artifacts, runner=runner)
    return inspector, runner, capacity, docker


async def test_busy_capacity_waits_in_same_inspector_effect_then_releases(authority, tmp_path):
    repo, _, scope, task = authority
    inspector, runner, capacity, docker = setup(authority, tmp_path)
    maximum = {key: value for key, value in capacity.limits.wire().items() if key != 'maxJobs'}
    capacity.reserve('checker:busy', scope.project_id, 'b' * 64, maximum)
    work = asyncio.create_task(inspector.invoke(scope, task, arguments(task)))
    await asyncio.sleep(.25)
    assert not work.done() and docker.creates == 0
    with repo.store.transaction() as session:
        assert session.scalar(select(Effect)).status == 'claimed'
        assert session.scalar(select(McpSession)) is None
    capacity.release('checker:busy', 'b' * 64, {'kind': 'container-removed', 'identity': 'fixture-busy'})
    result = await work
    assert result.observations[0].details['records'][0]['text'].startswith('Existing enclosure')
    assert docker.creates == docker.removes == docker.protocol.calls == 1
    assert capacity.usage()['jobs'] == 0
    with repo.store.transaction() as session:
        row = session.scalar(select(McpSession))
        assert row.status == 'removed' and row.container_id == 'a' * 64
        ident = row.id
    assert await runner.reconcile(ident) == {'kind': 'container-removed', 'identity': 'a' * 64}
    assert await inspector.invoke(scope, task, arguments(task)) == result
    assert docker.creates == 1


async def test_preflight_rejection_leaves_no_capacity_dispatch_or_effect_claim(authority, tmp_path):
    repo, _, scope, task = authority
    inspector, _, capacity, docker = setup(authority, tmp_path)
    docker.fail_preflight = True
    with pytest.raises(DomainError) as failure:
        await inspector.invoke(scope, task, arguments(task))
    assert failure.value.code == 'mcp_installation_conflict'
    assert capacity.usage()['jobs'] == docker.creates == 0
    with repo.store.transaction() as session:
        assert session.scalar(select(McpSession)) is None
        assert session.scalar(select(Effect)) is None
    docker.fail_preflight = False
    assert not (await inspector.invoke(scope, task, arguments(task))).blockers


async def test_unknown_create_retains_capacity_until_independent_cleanup_and_never_reruns(authority, tmp_path):
    repo, _, scope, task = authority
    inspector, runner, capacity, docker = setup(authority, tmp_path)
    docker.fail_create = docker.fail_remove = True
    with pytest.raises(DomainError):
        await inspector.invoke(scope, task, arguments(task))
    assert capacity.usage()['jobs'] == 1
    with repo.store.transaction() as session:
        row = session.scalar(select(McpSession))
        ident = row.id
        assert row.status == 'dispatching' and row.container_id is None
    repo.cancel(scope.actor, scope.project_id, scope.run_id)
    docker.fail_remove = False
    await runner.reconcile(ident)
    assert capacity.usage()['jobs'] == 0
    assert docker.creates == 1


async def test_cancellation_after_native_start_still_removes_and_releases(authority, tmp_path):
    repo, _, scope, task = authority
    inspector, _, capacity, docker = setup(authority, tmp_path)
    docker.after_start = lambda: repo.cancel(scope.actor, scope.project_id, scope.run_id)
    with pytest.raises(DomainError):
        await inspector.invoke(scope, task, arguments(task))
    assert docker.creates == docker.removes == 1 and capacity.usage()['jobs'] == 0
    assert docker.protocol.calls == 0


def test_staging_is_idempotent_bounded_and_rejects_changed_or_symlinked_bytes(tmp_path):
    files = {'000': b'exact source', 'request.json': b'{}'}
    staged = _stage(tmp_path / 'root', 'a' * 32, files)
    assert _stage(tmp_path / 'root', 'a' * 32, files) == staged
    with pytest.raises(DomainError):
        _stage(tmp_path / 'root', 'a' * 32, files | {'000': b'changed'})
    staged.chmod(0o755)
    (staged / '000').unlink()
    (staged / '000').symlink_to(tmp_path / 'secret')
    with pytest.raises((DomainError, OSError)):
        _stage(tmp_path / 'root', 'a' * 32, files)


def test_container_contract_has_independent_watchdog_and_no_host_authority(tmp_path):
    value = spec('a' * 32, 'b' * 64, config(), tmp_path)
    assert value['Entrypoint'][:3] == ['/usr/bin/timeout', '--signal=KILL', '30']
    assert value['User'] == '10001:10001' and value['HostConfig']['NetworkMode'] == 'none'
    assert value['HostConfig']['ReadonlyRootfs'] and value['HostConfig']['CapDrop'] == ['ALL']
    assert value['Env'] == [] and value['HostConfig']['LogConfig'] == {'Type': 'none'}
    assert value['HostConfig']['Mounts'][0]['ReadOnly']
    assert all('docker.sock' not in mount['Source'] for mount in value['HostConfig']['Mounts'])


@pytest.mark.parametrize('existing_id', [False, True])
async def test_cleanup_rejects_reused_container_name_even_when_original_id_absent(existing_id):
    ident, job = 'a' * 64, 'b' * 32

    async def handle(request):
        if request.url.path.endswith('/' + ident + '/json'):
            return httpx.Response(404)
        return httpx.Response(200, json={'Id': 'c' * 64, 'Image': config().image,
            'Config': {'Labels': labels(job, 'd' * 64)}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://docker/v1.45') as client:
        transport = DockerTransport(client=client)
        with pytest.raises(DomainError):
            await transport.remove(job, 'd' * 64, config().image, ident if existing_id else None)


async def test_attach_frames_are_fragmented_bounded_and_stderr_is_discarded():
    reader = asyncio.StreamReader()
    payload = b'{"safe":true}\n'
    for kind, content in [(2, b'secret diagnostic'), (1, payload)]:
        reader.feed_data(bytes([kind, 0, 0, 0]) + len(content).to_bytes(4, 'big') + content)
    reader.feed_eof()
    channel = AttachChannel(reader, None)
    output = b''
    while chunk := await channel.receive(3):
        output += chunk
    assert output == payload
    invalid = asyncio.StreamReader()
    invalid.feed_data(b'\x01\0\0\0' + (1048577).to_bytes(4, 'big'))
    with pytest.raises(DomainError):
        await AttachChannel(invalid, None).receive(16)


async def test_image_preflight_rejects_wrong_runtime_without_create():
    calls = []

    async def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={'Id': config().image, 'Config': {'Labels': {}}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://docker/v1.45') as client:
        with pytest.raises(DomainError):
            await DockerTransport(client=client).preflight(config())
    assert len(calls) == 1 and calls[0][0] == 'GET'


async def test_create_blanks_inherited_secret_and_never_reads_it_into_request():
    sent = []

    async def handle(request):
        if request.url.path.endswith('/info'):
            return httpx.Response(200, json={'ID': 'fixture-daemon'})
        if request.method == 'GET':
            return httpx.Response(200, json={'Id': config().image, 'Config': {
                'Labels': {'kleos.mcp.protocol': 'kleos-mcp-inspection-v1'},
                'Env': ['HOST_TOKEN=private-test-value', 'PATH=/unexpected']}})
        sent.append(json.loads(request.content))
        return httpx.Response(201, json={'Id': 'a' * 64})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://docker/v1.45') as client:
        transport = DockerTransport(client=client)
        names = await transport.preflight(config())
        await transport.create('b' * 32, 'c' * 64, config(), '/private/inputs',
                               image_env=names['environmentKeys'], daemon_id=names['daemonId'])
    assert 'private-test-value' not in json.dumps(sent)
    assert 'HOST_TOKEN=' in sent[0]['Env'] and 'QWEN_MM_AUTOLAUNCH=0' in sent[0]['Env']


@pytest.mark.parametrize('reason', ['expired', 'cancelled', 'revoked', 'stale'])
async def test_maintenance_leaves_valid_active_work_then_cleans_only_due_sessions(authority, tmp_path, reason):
    repo, _, scope, task = authority
    inspector, runner, capacity, docker = setup(authority, tmp_path)
    docker.fail_create = docker.fail_remove = True
    with pytest.raises(DomainError):
        await inspector.invoke(scope, task, arguments(task))
    docker.fail_remove = False
    removed_before = docker.removes
    assert await runner.reconcile_due() == []
    assert docker.removes == removed_before and capacity.usage()['jobs'] == 1
    if reason == 'cancelled':
        repo.cancel(scope.actor, scope.project_id, scope.run_id)
    else:
        from kleos.projects.store import Membership, Project
        with repo.store.transaction() as session:
            row = session.scalar(select(McpSession))
            if reason == 'expired':
                row.created_at -= row.request['maxLifetimeSeconds'] + 1
            elif reason == 'stale':
                session.get(Project, scope.project_id).advance_revision()
            else:
                member = session.scalar(select(Membership).where(Membership.project_id == scope.project_id))
                session.delete(member)
    results = await runner.reconcile_due()
    assert len(results) == 1 and results[0]['status'] == 'removed'
    assert capacity.usage()['jobs'] == 0 and docker.creates == 1


def test_vendored_subset_and_schema_match_committed_provenance_without_executing_plugin():
    from kleos.capabilities.mcp_bridge.bridge import RemoteArguments
    root = Path(__file__).parents[1] / 'src/kleos/capabilities/mcp_bridge'
    manifest = json.loads((root / 'vendor/source.json').read_bytes())
    raw = (root / 'vendor/qwen-mm-core-read-image.tar.gz').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == manifest['archiveSha256']
    assert manifest['commit'] == '33a54695774020e5da2d6b1ed13119d4014f848f'
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        members = archive.getmembers()
        assert len(members) == len(manifest['files']) == 6
        for member in members:
            assert member.isfile() and member.name in manifest['files'] and member.size < 65536
            content = archive.extractfile(member).read()
            assert len(content) == manifest['files'][member.name]['size']
            assert hashlib.sha256(content).hexdigest() == manifest['files'][member.name]['sha256']
        assert archive.extractfile('LICENSE').read() == (root / 'vendor/LICENSE.qwen-mm-plugins').read_bytes()
    assert json.loads((root / 'tool-schema.json').read_bytes()) == RemoteArguments.model_json_schema(by_alias=True)


async def test_foreign_and_malformed_rows_do_not_starve_due_cleanup_across_restart(authority, tmp_path):
    repo, _, scope, task = authority
    inspector, runner, capacity, docker = setup(authority, tmp_path)
    docker.fail_create = docker.fail_remove = True
    with pytest.raises(DomainError):
        await inspector.invoke(scope, task, arguments(task))
    with repo.store.transaction() as session:
        live = session.scalar(select(McpSession))
        live.created_at -= 200
        for index in range(3):
            request = dict(live.request)
            request['capacityPoolId'] = 'foreign-pool'
            if index == 0:
                request['deadlineAt'] = 'malformed'
            session.add(McpSession(id=f'{index:032x}', project_id=live.project_id, run_id=live.run_id,
                actor_id=live.actor_id, tenant_id=live.tenant_id, request_hash='e' * 64, status='dispatching',
                request=request, container_id=None, created_at=live.created_at - 100 + index,
                finished_at=None, termination={}))
    docker.fail_remove = False
    first = await runner.reconcile_due(limit=2)
    assert len(first) == 2 and all(row['status'] == 'blocked' for row in first)
    restarted = DockerRunner(repo, capacity, docker, control_root=tmp_path / 'mcp')
    second = await restarted.reconcile_due(limit=2)
    assert any(row['status'] == 'removed' for row in second)
    assert capacity.usage()['jobs'] == 0


async def test_scan_progress_is_committed_before_slow_cleanup_cancellation(authority, tmp_path):
    repo, _, scope, task = authority
    inspector, runner, capacity, docker = setup(authority, tmp_path)
    docker.fail_create = docker.fail_remove = True
    with pytest.raises(DomainError):
        await inspector.invoke(scope, task, arguments(task))
    with repo.store.transaction() as session:
        row = session.scalar(select(McpSession))
        row.created_at -= 200
        ident = row.id
    started = asyncio.Event()

    async def slow_remove(*args):
        started.set()
        await asyncio.Future()

    docker.remove = slow_remove
    before = time.time()
    work = asyncio.create_task(runner.reconcile_due(limit=1))
    await started.wait()
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    with repo.store.transaction() as session:
        assert session.get(McpSession, ident).termination['lastCheckedAt'] >= before
    assert capacity.usage()['jobs'] == 1


async def test_changed_daemon_never_removes_or_releases_original_work(authority, tmp_path):
    repo, _, scope, task = authority
    inspector, runner, capacity, docker = setup(authority, tmp_path)
    docker.fail_create = docker.fail_remove = True
    with pytest.raises(DomainError):
        await inspector.invoke(scope, task, arguments(task))
    with repo.store.transaction() as session:
        ident = session.scalar(select(McpSession.id))
    docker.daemon_id = 'different-engine'
    count = docker.removes
    with pytest.raises(DomainError) as failure:
        await runner.reconcile(ident)
    assert failure.value.code == 'mcp_daemon_conflict'
    assert docker.removes == count and capacity.usage()['jobs'] == 1


def test_pinned_image_provenance_labels_do_not_replace_exact_job_ownership():
    value = {'Id': 'a' * 64, 'Image': config().image, 'Config': {'Labels': {
        **labels('b' * 32, 'c' * 64), 'kleos.mcp.plugin': 'qwen-mm-plugins-core@1.3.0',
        'kleos.mcp.commit': '33a54695774020e5da2d6b1ed13119d4014f848f'}}}
    assert DockerTransport.owned(value, 'b' * 32, 'c' * 64, config().image) == 'a' * 64
    value['Config']['Labels']['kleos.mcp.request'] = 'd' * 64
    with pytest.raises(DomainError):
        DockerTransport.owned(value, 'b' * 32, 'c' * 64, config().image)
