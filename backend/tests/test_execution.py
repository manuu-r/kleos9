"""Boundary tests use fixed fixture writers; generated sources are never executed on the host."""
import base64
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError, TaskSpec
from kleos.execution.docker import NativeSettings, Resources, container_spec
from kleos.execution.gateway import NativeAction, NativeGateway, NativeJob, export_path
from kleos.execution.native_runner import NativeSession, atomic_json, clean_environment, run_fixed
from kleos.execution.protocol import PROTOCOL, digest, fingerprint, source_path
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import ProjectStore


def request():
    return {'protocol': PROTOCOL, 'jobId': 'fixture', 'lane': 'cad', 'resources': Resources().wire(),
            'deadline': time.time() + 60, 'allowedPaths': ['model.py'], 'maxActions': 12,
            'sources': [{'path': 'model.py', 'sha256': digest(b'fixture'),
                         'contentBase64': base64.b64encode(b'fixture').decode()}]}


def fixture_writer(command, work, timeout, limits, application):
    # These are test bytes, not geometric evidence. Never run the passed command.
    (work / 'outputs/parts.json').write_text('{"parts":[{"id":"fixture"}]}')
    (work / 'outputs/fixture.step').write_bytes(b'trusted test STEP bytes')
    (work / 'outputs/fixture.stl').write_bytes(b'trusted test STL bytes')
    return 'fixture observation'


class FakeDocker:
    def __init__(self):
        self.sessions, self.stopped, self.dispatches = {}, [], 0
        self.cleaned = []
        self.tamper = None
        self.on_dispatch = None

    async def ensure(self, job_id, data, image, control_directory, *, allow_create=True, on_observed=None):
        self.dispatches += 1
        root = Path(control_directory)
        if job_id not in self.sessions:
            if not allow_create:
                raise RuntimeError('Native container missing after dispatch')
            self.sessions[job_id] = NativeSession(data, root / 'journal', root / 'work', fixture_writer)
        if on_observed is not None:
            await on_observed(digest(job_id.encode()))
        for action_file in sorted((root / 'actions').glob('*.json')):
            output = root / 'results' / action_file.name
            if output.exists():
                continue
            action = json.loads(action_file.read_bytes())
            response = self.sessions[job_id].apply(action)
            envelope = {'protocol': PROTOCOL, 'jobId': job_id, 'requestHash': fingerprint(data),
                        'actionId': action['actionId'], 'actionHash': fingerprint(action), 'response': response}
            if self.tamper:
                self.tamper(envelope)
            atomic_json(output, envelope)
        if self.on_dispatch:
            self.on_dispatch()
        return job_id

    async def running(self, job_id):
        return job_id not in self.stopped

    async def stop(self, job_id):
        self.stopped.append(job_id)

    async def confirm_terminated(self, job_id, image, *, observed_id=None):
        observed = job_id in self.sessions
        await self.stop(job_id)
        assert not await self.running(job_id)
        return digest(job_id.encode()) if observed else observed_id

    async def cleanup(self, job_id, *, observed_id=None):
        self.sessions.pop(job_id, None)
        self.cleaned.append(job_id)


@pytest.fixture
def gateway(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path}/native.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    project = store.create(actor, 'Bracket', 'create')
    run = store.submit(actor, project['projectId'], 'Make bracket', 1, 'submit')
    scope = SimpleNamespace(actor=actor, project_id=project['projectId'], run_id=run['runId'], revision=2, compute_budget_seconds=None,
                            deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    task = TaskSpec(id='cad-one', objective='Design part', capability='cad', allowed_paths=['model.py'])
    artifacts = ArtifactService(store, BlobStore(tmp_path / 'blobs'))
    docker = FakeDocker()
    settings = NativeSettings(tmp_path / 'control', {'cad': 'sha256:' + 'a' * 64}, poll_seconds=.01)
    value = NativeGateway(store, artifacts, settings, docker, GraphRepository(store))
    return value, scope, task


async def patch(gateway):
    native, scope, task = gateway
    return await native.invoke(scope, task, 'patch', {'actionId': 'patch-1', 'path': 'model.py',
                                                    'expectedSha256': None, 'content': 'fixture'})


async def test_patch_build_finish_publishes_candidates_with_pinned_provenance(gateway):
    native, scope, task = gateway
    await patch(gateway)
    built = await native.invoke(scope, task, 'build', {'actionId': 'build-1'})
    assert built['observations'][0]['status'] == 'UNKNOWN'
    assert built['observations'][0]['details']['executionSucceeded'] is True
    assert built['observations'][0]['details']['candidateOnly'] is True
    result = await native.invoke(scope, task, 'finish', {'actionId': 'finish-1'})
    assert len(result['artifacts']) == 4
    with native.projects.transaction() as session:
        job = session.scalar(select(NativeJob))
        assert job.status == 'finished'
        assert job.request['sources'] == []
        assert job.request['imageDigest'] == 'sha256:' + 'a' * 64
        assert native.artifacts.list(session, scope.project_id)[0]['status'] == 'candidate'
    count = native.docker.dispatches
    assert await native.invoke(scope, task, 'finish', {'actionId': 'finish-1'}) == result
    assert native.docker.dispatches == count


async def test_action_id_conflict_and_restart_replays_recorded_result(gateway):
    native, scope, task = gateway
    result = await patch(gateway)
    reopened = NativeGateway(native.projects, native.artifacts, native.settings, native.docker, native.repository)
    replay = await reopened.invoke(scope, task, 'patch', {'actionId': 'patch-1', 'path': 'model.py',
                                                        'expectedSha256': None, 'content': 'fixture'})
    assert replay == result
    assert native.docker.dispatches == 1
    with pytest.raises(DomainError, match='different arguments'):
        await native.invoke(scope, task, 'patch', {'actionId': 'patch-1', 'path': 'model.py',
                                                 'expectedSha256': None, 'content': 'changed'})


async def test_wrong_result_identity_stops_job_without_publication(gateway):
    native, scope, task = gateway
    native.docker.tamper = lambda envelope: envelope.update(requestHash='wrong')
    with pytest.raises(ValueError, match='identity'):
        await patch(gateway)
    assert len(native.docker.stopped) == 1
    with native.projects.transaction() as session:
        assert session.scalar(select(NativeJob)).status == 'failed'
        assert native.artifacts.list(session, scope.project_id) == []


async def test_cancel_fences_observation_and_cleanup_even_after_compute(gateway):
    native, scope, task = gateway
    native.docker.on_dispatch = lambda: native.repository.cancel(scope.actor, scope.project_id, scope.run_id)
    with pytest.raises(DomainError, match='no longer active'):
        await patch(gateway)
    assert native.docker.stopped
    with native.projects.transaction() as session:
        assert native.artifacts.list(session, scope.project_id) == []
        assert session.scalar(select(NativeAction)).status == 'pending'


async def test_patch_scope_and_shell_args_are_rejected_before_dispatch(gateway):
    native, scope, task = gateway
    with pytest.raises(DomainError, match='authority'):
        await native.invoke(scope, task, 'patch', {'actionId': 'bad', 'path': 'other.py',
                                                 'expectedSha256': None, 'content': 'bad'})
    with pytest.raises(ValueError, match='unsupported arguments'):
        await native.invoke(scope, task, 'build', {'actionId': 'bad', 'command': 'echo injected'})
    assert native.docker.dispatches == 0


async def test_read_reports_hash_and_bounded_content(gateway):
    native, scope, task = gateway
    await patch(gateway)
    result = await native.invoke(scope, task, 'read', {'actionId': 'read', 'path': 'model.py',
                                                     'offset': 1, 'maxBytes': 3})
    assert result['observations'][0]['details']['content'] == 'ixt'
    assert result['observations'][0]['details']['sha256'] == digest(b'fixture')


async def test_failed_build_is_not_a_pass_and_can_be_repaired(gateway):
    native, scope, task = gateway
    result = await native.invoke(scope, task, 'build', {'actionId': 'empty'})
    assert result['observations'][0]['status'] == 'ERROR'
    assert result['artifacts'] == []
    await patch(gateway)
    assert (await native.invoke(scope, task, 'build', {'actionId': 'repaired'}))['observations'][0]['status'] == 'UNKNOWN'


def test_supervisor_idempotency_hash_and_untracked_mutation(tmp_path):
    session = NativeSession(request(), tmp_path / 'control', tmp_path / 'work', fixture_writer)
    action = {'actionId': 'build', 'action': 'build', 'arguments': {}}
    result = session.apply(action)
    assert session.apply(action) == result
    (tmp_path / 'work/model.py').write_text('unauthorized change')
    finished = session.apply({'actionId': 'finish', 'action': 'finish', 'arguments': {}})
    assert finished['status'] == 'failed'
    assert 'outside an authorized patch' in finished['error']


def test_supervisor_symlink_export_and_stale_patch_rejected(tmp_path):
    session = NativeSession(request(), tmp_path / 'control', tmp_path / 'work', fixture_writer)
    patch_result = session.apply({'actionId': 'patch', 'action': 'patch', 'arguments': {
        'path': 'model.py', 'content': 'changed', 'expectedSha256': None}})
    assert patch_result['status'] == 'failed'
    session.apply({'actionId': 'build', 'action': 'build', 'arguments': {}})
    exported = tmp_path / 'work/outputs/fixture.step'
    exported.unlink()
    exported.symlink_to(tmp_path / 'work/model.py')
    result = session.apply({'actionId': 'finish', 'action': 'finish', 'arguments': {}})
    assert result['status'] == 'failed' and 'Symlink' in result['error']


def test_interrupted_action_never_reexecutes(tmp_path):
    session = NativeSession(request(), tmp_path / 'control', tmp_path / 'work', fixture_writer)
    action = {'actionId': 'lost', 'action': 'build', 'arguments': {}}
    session.state['actions']['lost'] = {'requestHash': fingerprint(action)}
    assert session.apply(action)['status'] == 'failed'
    assert not (tmp_path / 'work/outputs/parts.json').exists()


def test_no_host_execution_and_environment_never_inherits_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'private')
    monkeypatch.setenv('DATABASE_URL', 'private')
    env = clean_environment(tmp_path)
    assert 'OPENAI_API_KEY' not in env and 'DATABASE_URL' not in env
    monkeypatch.delenv('KLEOS_NATIVE_SANDBOX', raising=False)
    with pytest.raises(RuntimeError, match='isolated Linux'):
        run_fixed(['python', '-c', 'raise Exception("must not execute")'], tmp_path, 2, Resources().wire())


@pytest.mark.parametrize('path', ['../x', '/etc/passwd', 'a//b', '.env', 'a/../b', 'a\\b', 'outputs/fake.py'])
def test_source_paths_reject_escape_and_reserved_output(path):
    with pytest.raises(ValueError):
        source_path(path)


def test_docker_enforces_isolation_and_pins_resource_contract():
    data = request()
    spec = container_spec('job', data, 'sha256:' + 'a' * 64, '/private/control/job')
    host = spec['HostConfig']
    assert host['ReadonlyRootfs'] and host['NetworkMode'] == 'none'
    assert host['CapDrop'] == ['ALL'] and host['PidsLimit'] == 128
    assert host['MemorySwap'] == host['Memory']
    assert host['SecurityOpt'] == ['no-new-privileges:true']
    assert spec['Env'] == ['KLEOS_NATIVE_SANDBOX=1']
    assert len(host['Mounts']) == 1 and host['Mounts'][0]['Target'] == '/control'
    assert 'docker.sock' not in json.dumps(spec)
    data['lane'] = 'application'
    app = container_spec('job', data, 'sha256:' + 'a' * 64, '/private/control/job')
    assert len(app['HostConfig']['Mounts']) == 1
    assert '/tmp' in app['HostConfig']['Tmpfs']
    assert 'size=' in app['HostConfig']['Tmpfs']['/tmp']
    assert app['HostConfig']['Tmpfs']['/tmp'].endswith(',exec')


def test_output_contract_rejects_arbitrary_files():
    assert export_path('cad', 'outputs/bracket.step')
    assert export_path('pcb', 'outputs/gerbers/board-F_Cu.gbr')
    for lane, name in [('cad', 'outputs/secrets.txt'), ('firmware', 'outputs/run.sh'),
                       ('application', 'outputs/companion.exe')]:
        with pytest.raises(ValueError):
            export_path(lane, name)


def test_image_pins_are_mandatory(tmp_path):
    with pytest.raises(ValueError, match='immutable'):
        NativeSettings(tmp_path, {'cad': 'latest'})


async def test_reconciliation_terminates_cancelled_idle_session(gateway):
    native, scope, task = gateway
    await patch(gateway)
    native.repository.cancel(scope.actor, scope.project_id, scope.run_id)
    assert await native.reconcile() == {'reconciled': 1, 'pending': []}
    with native.projects.transaction() as session:
        assert session.scalar(select(NativeJob)).status == 'cancelled'


async def test_finish_rejects_omitted_source_even_with_valid_output_hashes(gateway):
    native, scope, task = gateway
    await patch(gateway)
    await native.invoke(scope, task, 'build', {'actionId': 'build'})

    def omit_source(envelope):
        files = envelope['response']['result']['files']
        envelope['response']['result']['files'] = [file for file in files if file['role'] == 'output']

    native.docker.tamper = omit_source
    with pytest.raises(ValueError, match='omitted source'):
        await native.invoke(scope, task, 'finish', {'actionId': 'finish'})
    with native.projects.transaction() as session:
        assert native.artifacts.list(session, scope.project_id) == []


async def test_docker_api_does_not_inherit_parent_credentials(tmp_path):
    import httpx

    from kleos.execution.docker import DockerClient

    created = []
    image = 'sha256:' + 'a' * 64

    def respond(req):
        path = req.url.path
        if path.endswith('/images/' + image + '/json'):
            return httpx.Response(200, json={'Id': image, 'Config': {
                'Labels': {'kleos.native.protocol': PROTOCOL},
                'Env': ['OPENAI_API_KEY=must-never-be-inherited', 'PATH=/trusted/toolchain']}})
        if path.endswith('/containers/create'):
            created.append(json.loads(req.content))
            return httpx.Response(201, json={'Id': 'c' * 64})
        if path.endswith('/json'):
            if not created:
                return httpx.Response(404)
            return httpx.Response(200, json={'Id': 'c' * 64, 'Image': image, 'Config': created[0],
                'HostConfig': created[0]['HostConfig'], 'State': {'Status': 'created', 'Running': False}})
        return httpx.Response(204)

    client = DockerClient(client=httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url='http://docker/v1.45'))
    assert await client.ensure('fixture', request(), image, str(tmp_path)) == 'c' * 64
    assert 'OPENAI_API_KEY=' in created[0]['Env']
    assert 'must-never-be-inherited' not in json.dumps(created[0])
    await client.close()


def test_network_namespace_check_ignores_linux_bonding_control_file(tmp_path):
    from kleos.execution.native_runner import verify_no_network

    network = tmp_path / 'net'
    network.mkdir()
    (network / 'lo').mkdir()
    (network / 'bonding_masters').write_text('')
    routes, ipv6 = tmp_path / 'route', tmp_path / 'ipv6_route'
    routes.write_text('Iface Destination Gateway\n')
    ipv6.write_text('00000000 lo\n')
    verify_no_network(network, routes, ipv6)
    (network / 'eth0').mkdir()
    (network / 'eth0/operstate').write_text('up')
    with pytest.raises(RuntimeError, match='active external'):
        verify_no_network(network, routes, ipv6)


def test_network_namespace_check_accepts_kernels_without_ipv6_but_not_other_failures(tmp_path):
    from kleos.execution.native_runner import verify_no_network

    network = tmp_path / 'net'
    network.mkdir()
    (network / 'lo').mkdir()
    routes = tmp_path / 'route'
    routes.write_text('Iface Destination Gateway\n')
    verify_no_network(network, routes, tmp_path / 'absent_ipv6_route')  # no IPv6 in this kernel: no IPv6 routes
    with pytest.raises(RuntimeError, match='external route'):
        routes.write_text('Iface Destination Gateway\neth0 00000000 0102A8C0\n')
        verify_no_network(network, routes, tmp_path / 'absent_ipv6_route')
    routes.write_text('Iface Destination Gateway\n')
    ipv6 = tmp_path / 'ipv6_route'
    ipv6.mkdir()  # an unreadable (non-missing) file must still fail closed
    with pytest.raises(OSError):
        verify_no_network(network, routes, ipv6)


def test_desktop_mode_is_explicit_local_only_and_preserves_docker_baseline(tmp_path):
    from kleos.execution.security import daemon_socket_profile

    with pytest.raises(ValueError, match='local-only'):
        NativeSettings(tmp_path, {}, docker_desktop_compatibility=True, deployment='ecs')
    profile = daemon_socket_profile()
    assert profile['defaultAction'] == 'SCMP_ACT_ERRNO'
    socket_rules = [rule for rule in profile['syscalls'] if any(
        name in {'socket', 'socketpair', 'socketcall'} for name in rule['names'])]
    assert socket_rules == [{'names': ['socket', 'socketpair'], 'action': 'SCMP_ACT_ALLOW',
                             'args': [{'index': 0, 'value': 1, 'op': 'SCMP_CMP_EQ'}]}]
    data = {**request(), 'networkPolicy': 'docker-desktop-daemon-seccomp'}
    spec = container_spec('fixture', data, 'sha256:' + 'a' * 64, '/control')
    assert spec['HostConfig']['Mounts'][0]['ReadOnly']
    assert spec['HostConfig']['SecurityOpt'][0] == 'no-new-privileges:true'
    assert json.loads(spec['HostConfig']['SecurityOpt'][1].removeprefix('seccomp=')) == profile


async def test_desktop_mode_rejects_non_desktop_daemon():
    import httpx

    from kleos.execution.docker import DockerClient

    def respond(req):
        assert req.url.path.endswith('/info')
        return httpx.Response(200, json={'OperatingSystem': 'Amazon Linux'})

    client = DockerClient(client=httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url='http://docker/v1.45'))
    with pytest.raises(RuntimeError, match='verified local Docker Desktop'):
        await client.ensure('fixture', {**request(), 'networkPolicy': 'docker-desktop-daemon-seccomp'},
                            'sha256:' + 'a' * 64, '/control')
    await client.close()


async def test_result_transfer_uses_fixed_trusted_reader_and_demultiplexes_frames():
    import httpx

    from kleos.execution.docker import DockerClient

    expected = {'protocol': PROTOCOL, 'response': {'status': 'succeeded'}}
    raw = json.dumps(expected).encode()
    framed = b'\x01\x00\x00\x00' + len(raw).to_bytes(4, 'big') + raw
    filename = 'a' * 64 + '.json'

    def respond(req):
        if req.url.path.endswith('/exec'):
            body = json.loads(req.content)
            assert body['Cmd'] == ['python', '-I', '/opt/kleos-native/result_reader.py', filename]
            assert body['User'] == '0:0' and not body['Tty']
            return httpx.Response(201, json={'Id': 'fixed-reader'})
        if req.url.path.endswith('/start'):
            return httpx.Response(200, content=framed)
        return httpx.Response(200, json={'Running': False, 'ExitCode': 0})

    client = DockerClient(client=httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url='http://docker/v1.45'))
    assert await client.result('fixture', filename) == expected
    with pytest.raises(ValueError, match='filename'):
        await client.result('fixture', '../secret')
    await client.close()


def test_native_preexec_failure_returns_error_envelope(tmp_path):
    import subprocess

    def fail_start(*args):
        raise subprocess.SubprocessError('Isolation setup failed')

    session = NativeSession(request(), tmp_path / 'control', tmp_path / 'work', fail_start)
    result = session.apply({'actionId': 'build', 'action': 'build', 'arguments': {}})
    assert result == {'status': 'failed', 'error': 'Isolation setup failed'}
    assert session.apply({'actionId': 'build', 'action': 'build', 'arguments': {}}) == result


def accepted_circuit():
    return {'references': [{'reference': 'J1', 'value': 'Connector', 'footprint': 'Fixture:TwoPin',
                            'pins': {'1': 'NET_A', '2': 'NET_B'}}],
            'conceptReferences': {'port': 'J1'}, 'constraints': {'maxWidthMm': 40, 'maxHeightMm': 30,
            'minTrackWidthMm': .25, 'minClearanceMm': .2, 'copperLayers': ['F.Cu', 'B.Cu']}}


def test_accepted_circuit_requires_exact_injective_peripheral_reference_coverage():
    from kleos.execution.pcb_contract import circuit_contract

    value = accepted_circuit()
    assert circuit_contract(value, ['port']) == value
    for ids in [None, ['port', 'port'], ['port', 'extra'], []]:
        with pytest.raises(ValueError):
            circuit_contract(value, ids)
    value['conceptReferences']['other'] = 'J1'
    with pytest.raises(ValueError, match='uniquely'):
        circuit_contract(value, ['port', 'other'])
    value['conceptReferences'] = {'port': 'MISSING1'}
    with pytest.raises(ValueError, match='uniquely'):
        circuit_contract(value, ['port'])


@pytest.mark.parametrize('number', [float('nan'), float('inf'), -.1, 0, True, 10000])
def test_accepted_pcb_constraints_are_finite_positive_and_bounded(number):
    from kleos.execution.pcb_contract import circuit_contract

    value = accepted_circuit()
    value['constraints']['minClearanceMm'] = number
    with pytest.raises(ValueError, match='finite and bounded'):
        circuit_contract(value, ['port'])


async def test_pcb_missing_accepted_authority_blocks_before_docker(gateway):
    from kleos.contracts.models import Capability

    native, scope, task = gateway
    native.settings.images['pcb'] = native.settings.images['cad']
    pcb_task = task.model_copy(update={'capability': Capability.PCB})
    with pytest.raises(DomainError, match='explicit no-concept'):
        await native.invoke(scope, pcb_task, 'patch', {'actionId': 'bad', 'path': 'model.py',
                                                     'content': 'fixture', 'expectedSha256': None})
    assert native.docker.dispatches == 0


async def test_native_pcb_job_pins_accepted_circuit_and_peripheral_identity(gateway):
    from kleos.contracts.models import Capability

    native, scope, task = gateway
    native.settings.images['pcb'] = native.settings.images['cad']
    with native.projects.transaction() as session:
        project = native.projects.authorized(session, scope.actor, scope.project_id, write=True)
        project.data = {**project.data, 'interfaces': {'circuit': accepted_circuit()},
                        'approvedDesign': {'peripheralConceptIds': ['port']}}
    pcb_task = task.model_copy(update={'capability': Capability.PCB})
    job = native._job(scope, pcb_task)
    assert job.request['circuitContract'] == accepted_circuit()
    assert job.request['circuitContractHash'] == fingerprint(accepted_circuit())
    assert job.request['peripheralConceptIds'] == ['port']
    # The connection table in the contract is turned into a starter project, so a model need not write KiCad.
    seeded = {item['path']: item for item in job.request['sources']}
    assert job.request['scaffold'] == 'pcb-contract-scaffold-v1'
    assert {'circuit.kicad_sch', 'board.kicad_pcb', 'sym-lib-table', 'fp-lib-table'} <= set(seeded)
    assert all('artifactId' not in item for item in seeded.values())
    assert job.source_manifest == {path: item['sha256'] for path, item in seeded.items()}


async def test_composition_accepts_a_starter_source_only_when_it_can_be_reproduced_from_the_contract(gateway):
    from kleos.contracts.models import Capability
    from kleos.execution.gateway import artifact_inputs

    native, scope, task = gateway
    native.settings.images['pcb'] = native.settings.images['cad']
    with native.projects.transaction() as session:
        project = native.projects.authorized(session, scope.actor, scope.project_id, write=True)
        project.data = {**project.data, 'interfaces': {'circuit': accepted_circuit()},
                        'approvedDesign': {'peripheralConceptIds': ['port']}}
    job = native._job(scope, task.model_copy(update={'capability': Capability.PCB}))
    with native.projects.transaction() as session:
        stored = session.get(NativeJob, job.id)
        assert artifact_inputs(session, stored) == []  # every source is a reproducible starter file
        tampered = dict(stored.request)
        tampered['sources'] = [{**item, 'sha256': digest(b'edited')} if item['path'] == 'sym-lib-table' else item
                               for item in stored.request['sources']]
        stored.request = tampered
        with pytest.raises(DomainError, match='provenance'):
            artifact_inputs(session, stored)
        unexplained = dict(stored.request, scaffold=None)
        stored.request = unexplained
        with pytest.raises(DomainError, match='provenance'):
            artifact_inputs(session, stored)


async def test_firmware_requires_explicit_report_qualified_target_before_dispatch(gateway):
    from dataclasses import replace

    from kleos.contracts.models import Capability

    native, scope, task = gateway
    native.settings = replace(native.settings, images={**native.settings.images, 'firmware': 'sha256:' + 'b' * 64},
                              firmware_boards=('nrf52840dk/nrf52840',))
    task = task.model_copy(update={'capability': Capability.FIRMWARE})
    with pytest.raises(DomainError, match='explicit firmware board'):
        await native.invoke(scope, task, 'build', {'actionId': 'missing'})
    with native.projects.transaction() as session:
        project = native.projects.authorized(session, scope.actor, scope.project_id, write=True)
        project.data = {**project.data, 'interfaces': {'firmware': {'board': 'native_sim'}}}
    with pytest.raises(DomainError, match='qualified installation'):
        await native.invoke(scope, task, 'build', {'actionId': 'unqualified'})
    assert native.docker.dispatches == 0
    with native.projects.transaction() as session:
        project = native.projects.authorized(session, scope.actor, scope.project_id, write=True)
        project.data = {**project.data, 'interfaces': {'firmware': {'board': 'nrf52840dk/nrf52840'}}}
    job = native._job(scope, task)
    assert job.request['board'] == 'nrf52840dk/nrf52840'
    assert job.request['firmwareTargetHash'] == fingerprint({'board': 'nrf52840dk/nrf52840'})


def test_firmware_supervisor_has_no_implicit_board(tmp_path):
    data = request()
    data['lane'] = 'firmware'
    session = NativeSession(data, tmp_path / 'control', tmp_path / 'work', fixture_writer)
    result = session.apply({'actionId': 'build', 'action': 'build', 'arguments': {}})
    assert result['status'] == 'failed' and 'explicit supported firmware board' in result['error']
    assert not (tmp_path / 'work/outputs/parts.json').exists()


async def test_gateway_rechecks_exact_current_input_authority(gateway):
    from kleos.artifacts.service import Artifact

    native, scope, task = gateway
    checksum = native.artifacts.blobs.put(b'old unrelated candidate')
    with native.projects.transaction() as session:
        session.add(Artifact(id='unselected-old-candidate', project_id=scope.project_id, revision=scope.revision,
            name='model.py', sha256=checksum, size=len(b'old unrelated candidate'), media_type='text/plain',
            status='candidate', lane='cad', details={'origin': 'native_candidate', 'runId': 'another-run'}))
    task = task.model_copy(update={'input_artifact_ids': ['unselected-old-candidate']})
    with pytest.raises(DomainError, match='authorized task input'):
        await native.invoke(scope, task, 'build', {'actionId': 'blocked'})
    assert native.docker.dispatches == 0


async def finished_job(gateway):
    native, scope, task = gateway
    await patch(gateway)
    await native.invoke(scope, task, 'build', {'actionId': 'build'})
    await native.invoke(scope, task, 'finish', {'actionId': 'finish'})
    with native.projects.transaction() as session:
        return session.scalar(select(NativeJob))


async def test_exact_rebuild_keeps_original_files_image_resources_and_replay(gateway):
    from dataclasses import replace

    from kleos.orchestration.engine import RunScope
    from kleos.projects.rebuild import NativeRebuildService
    from kleos.projects.store import Objective

    native, scope, task = gateway
    original = await finished_job(gateway)
    with native.projects.transaction() as session:
        session.get(Objective, scope.run_id).status = 'succeeded'
    receipt = NativeRebuildService(native.projects, native).submit(scope.actor, scope.project_id, original.id, 2, 'rebuild')
    with native.projects.transaction() as session:
        run = session.get(Objective, receipt['runId'])
        rebuilt_scope = RunScope.model_validate(run.data['scope'])
        snapshot = run.data['nativeRebuild']['snapshot']
        assert run.data['nativeRebuild']['grant']['parentActionCount'] == 3
    assert snapshot['sources'][0]['contentBase64'] == base64.b64encode(b'fixture').decode()
    assert native.artifacts.blobs.get(digest(b'fixture')) == b'fixture'
    native.settings = replace(native.settings, images={}, resources=Resources(cpus=1), session_seconds=1)
    rebuilt_task = task.model_copy(update={'id': 'native-rebuild', 'max_actions': 3})
    for action in ('build', 'render', 'finish'):
        result = await native.invoke_rebuild(rebuilt_scope, rebuilt_task, snapshot, action,
                                             {'actionId': 'rebuild:' + action})
        assert result['observations'][0]['details']['executionSucceeded']
    dispatches = native.docker.dispatches
    assert await native.invoke_rebuild(rebuilt_scope, rebuilt_task, snapshot, 'finish',
                                      {'actionId': 'rebuild:finish'}) == result
    assert native.docker.dispatches == dispatches
    with native.projects.transaction() as session:
        rebuilt = session.scalar(select(NativeJob).where(NativeJob.run_id == rebuilt_scope.run_id))
        assert rebuilt.id != original.id and rebuilt.request['rebuildOf'] == original.id
        for key in ('imageDigest', 'resources', 'networkPolicy', 'baselines', 'allowedPaths'):
            assert rebuilt.request[key] == original.request[key]
        assert rebuilt.request['maxActions'] == 3 and rebuilt.request['sessionSeconds'] == 300
        assert rebuilt.source_manifest == original.source_manifest
        artifacts = native.artifacts.list(session, scope.project_id)
        assert all(artifact['status'] == 'candidate' for artifact in artifacts)
    with pytest.raises(DomainError, match='cannot edit'):
        await native.invoke_rebuild(rebuilt_scope, rebuilt_task, snapshot, 'patch', {'actionId': 'forbidden'})
    with pytest.raises(DomainError, match='snapshot changed'):
        await native.invoke_rebuild(rebuilt_scope, rebuilt_task, {**snapshot, 'sources': []}, 'build', {'actionId': 'bad'})


@pytest.mark.parametrize('damage', ['proof', 'pending', 'duration', 'source', 'request'])
async def test_rebuild_admission_fails_closed_on_missing_or_uncertain_history(gateway, damage):
    native, scope, _ = gateway
    original = await finished_job(gateway)
    with native.projects.transaction() as session:
        job = session.get(NativeJob, original.id)
        if damage == 'proof':
            session.delete(session.get(NativeAction, (job.id, '_termination')))
        elif damage == 'pending':
            session.add(NativeAction(job_id=job.id, action_id='lost', request_hash='x', status='pending', result={}))
        elif damage == 'duration':
            job.request = {k: v for k, v in job.request.items() if k != 'sessionSeconds'}
            job.request_hash = fingerprint(job.request)
            session.get(NativeAction, (job.id, '_termination')).request_hash = job.request_hash
        elif damage == 'request':
            job.request = {**job.request, 'imageDigest': 'sha256:' + 'b' * 64}
        else:
            job.source_manifest = {'model.py': 'e' * 64}
    with native.projects.transaction() as session, pytest.raises(DomainError):
        native.rebuild_snapshot(session, scope.actor, session.get(NativeJob, original.id))


async def test_missing_dispatched_container_never_reexecutes(gateway):
    native, scope, task = gateway
    await patch(gateway)
    assert native.artifacts.blobs.get(digest(b'fixture')) == b'fixture'
    native.docker.sessions.clear()
    with pytest.raises(RuntimeError, match='missing after dispatch'):
        await native.invoke(scope, task, 'build', {'actionId': 'uncertain'})
    with native.projects.transaction() as session:
        job = session.scalar(select(NativeJob))
        assert job.active_action == 'uncertain'
        with pytest.raises(DomainError, match='reconciliation'):
            native.rebuild_snapshot(session, scope.actor, job)


async def test_termination_failure_does_not_invent_rebuild_proof(gateway):
    native, scope, _ = gateway

    async def uncertain(job_id, image, **kwargs):
        raise RuntimeError('Daemon unavailable')

    native.docker.confirm_terminated = uncertain
    original = await finished_job(gateway)
    with native.projects.transaction() as session:
        assert session.get(NativeAction, (original.id, '_termination')) is None
        with pytest.raises(DomainError, match='termination is not confirmed'):
            native.rebuild_snapshot(session, scope.actor, session.get(NativeJob, original.id))
    assert (await native.reconcile())['pending'] == [original.id]


async def test_docker_termination_checks_identity_and_uses_immutable_id():
    import httpx

    from kleos.execution.docker import DockerClient

    seen = []
    image = 'sha256:' + 'a' * 64
    metadata = {'Id': 'c' * 64, 'Image': image, 'State': {'Running': True},
                'Config': {'Labels': {'kleos.native.job': 'job', 'kleos.native.protocol': PROTOCOL}}}

    def handle(request):
        seen.append(request.url.path)
        if request.url.path.endswith('/kill'):
            metadata['State']['Running'] = False
            return httpx.Response(204)
        return httpx.Response(200, json=metadata)

    docker = DockerClient(socket='/unused')
    await docker.client.aclose()
    docker.client = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://docker')
    await docker.confirm_terminated('job', image)
    assert '/containers/' + 'c' * 64 + '/kill' in seen
    seen.clear()
    metadata['Image'] = 'wrong'
    with pytest.raises(RuntimeError, match='ownership or image'):
        await docker.confirm_terminated('job', image)
    assert not any(path.endswith('/kill') for path in seen)
    await docker.close()


async def test_maintenance_cleans_once_and_retains_unknown_action_fences(gateway):
    native, scope, task = gateway
    await patch(gateway)
    native.docker.sessions.clear()
    with pytest.raises(RuntimeError, match='missing after dispatch'):
        await native.invoke(scope, task, 'build', {'actionId': 'uncertain'})
    assert await native.reconcile() == {'reconciled': 1, 'pending': []}
    stops, cleanups = len(native.docker.stopped), len(native.docker.cleaned)
    assert await native.reconcile() == {'reconciled': 0, 'pending': []}
    assert (len(native.docker.stopped), len(native.docker.cleaned)) == (stops, cleanups)
    with native.projects.transaction() as session:
        job = session.scalar(select(NativeJob))
        assert job.active_action == 'uncertain'
        assert session.get(NativeAction, (job.id, 'uncertain')).status == 'pending'
        assert session.get(NativeAction, (job.id, '_cleanup')).result['containerRemoved']
        with pytest.raises(DomainError, match='reconciliation'):
            native.rebuild_snapshot(session, scope.actor, job)


async def test_finish_and_maintenance_share_one_termination_receipt(gateway):
    import asyncio

    native, scope, task = gateway
    await patch(gateway)
    await native.invoke(scope, task, 'build', {'actionId': 'build'})
    first, both = asyncio.Event(), asyncio.Event()
    original, arrivals = native.docker.confirm_terminated, []

    async def overlapping(job_id, image, **kwargs):
        arrivals.append(job_id)
        first.set()
        if len(arrivals) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=2)
        return await original(job_id, image, **kwargs)

    native.docker.confirm_terminated = overlapping
    finishing = asyncio.create_task(native.invoke(scope, task, 'finish', {'actionId': 'finish'}))
    await asyncio.wait_for(first.wait(), timeout=2)
    maintenance = asyncio.create_task(native.reconcile())
    result, reconciled = await asyncio.gather(finishing, maintenance)
    assert result['artifacts'] and reconciled == {'reconciled': 1, 'pending': []}
    with native.projects.transaction() as session:
        job = session.scalar(select(NativeJob))
        controls = list(session.scalars(select(NativeAction).where(NativeAction.action_id.in_(['_termination', '_cleanup']))))
        assert len(controls) == 2 and all(row.status == 'completed' for row in controls)
        assert native.rebuild_snapshot(session, scope.actor, job)['sourceManifest'] == job.source_manifest
    assert await native.reconcile() == {'reconciled': 0, 'pending': []}
    assert await native.invoke(scope, task, 'finish', {'actionId': 'finish'}) == result


async def test_failed_cleanup_retries_without_false_completed_receipt(gateway):
    native, _, _ = gateway
    job = await finished_job(gateway)
    original = native.docker.cleanup

    async def unavailable(job_id):
        raise RuntimeError('Daemon unavailable')

    native.docker.cleanup = unavailable
    assert await native.reconcile() == {'reconciled': 0, 'pending': [job.id]}
    with native.projects.transaction() as session:
        assert session.get(NativeAction, (job.id, '_cleanup')) is None
    native.docker.cleanup = original
    assert await native.reconcile() == {'reconciled': 1, 'pending': []}
    assert await native.reconcile() == {'reconciled': 0, 'pending': []}


def enable_capacity(native):
    from kleos.execution.capacity import CapacityService
    from kleos.infrastructure.schema import metadata

    metadata().create_all(native.projects.engine)
    native.capacity = CapacityService(native.projects)


async def test_native_waits_for_shared_capacity_before_action_claim_and_container(gateway):
    import asyncio

    native, scope, task = gateway
    enable_capacity(native)
    await patch(gateway)
    assert native.capacity.usage()['jobs'] == 1
    other = task.model_copy(update={'id': 'second-cad'})
    waiting = asyncio.create_task(native.invoke(scope, other, 'patch', {'actionId': 'second', 'path': 'model.py',
        'content': 'other fixture', 'expectedSha256': None}))
    try:
        await asyncio.sleep(.05)
        assert not waiting.done() and native.docker.dispatches == 1
        with native.projects.transaction() as session:
            second = session.scalar(select(NativeJob).where(NativeJob.task_id == other.id))
            assert second.status == 'pending' and second.active_action is None
            assert session.get(NativeAction, (second.id, 'second')) is None
        await native.invoke(scope, task, 'build', {'actionId': 'build'})
        await native.invoke(scope, task, 'finish', {'actionId': 'finish'})
        assert native.capacity.usage()['jobs'] == 1  # Stopped is not removed scratch.
        await native.reconcile()
        result = await asyncio.wait_for(waiting, timeout=2)
        assert result['observations'][0]['details']['executionSucceeded']
        assert native.capacity.usage()['jobs'] == 1
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


async def test_native_capacity_wait_rechecks_cancellation_without_dispatch(gateway):
    import asyncio

    native, scope, task = gateway
    enable_capacity(native)
    native.capacity.reserve('slicer:busy', scope.project_id, 'b'*64, Resources().wire())
    waiting = asyncio.create_task(patch(gateway))
    await asyncio.sleep(.05)
    native.repository.cancel(scope.actor, scope.project_id, scope.run_id)
    with pytest.raises(DomainError, match='no longer active'):
        await asyncio.wait_for(waiting, timeout=2)
    assert native.docker.dispatches == 0
    with native.projects.transaction() as session:
        assert list(session.scalars(select(NativeAction))) == []
    assert native.capacity.usage()['jobs'] == 1  # Unrelated running slicer retains its slot.


async def test_uncertain_native_termination_retains_capacity_until_cleanup(gateway):
    native, scope, task = gateway
    enable_capacity(native)
    await patch(gateway)
    native.docker.sessions.clear()
    original = native.docker.confirm_terminated

    async def unavailable(job_id, image, **kwargs):
        raise RuntimeError('Unknown container state')

    native.docker.confirm_terminated = unavailable
    with pytest.raises(RuntimeError, match='missing after dispatch'):
        await native.invoke(scope, task, 'build', {'actionId': 'lost'})
    assert native.capacity.usage()['jobs'] == 1
    assert (await native.reconcile())['pending']
    assert native.capacity.usage()['jobs'] == 1
    native.docker.confirm_terminated = original
    assert await native.reconcile() == {'reconciled': 1, 'pending': []}
    assert native.capacity.usage()['jobs'] == 0
    with native.projects.transaction() as session:
        job = session.scalar(select(NativeJob))
        assert job.active_action == 'lost'
        with pytest.raises(DomainError, match='reconciliation'):
            native.rebuild_snapshot(session, scope.actor, job)


async def test_lost_native_create_response_with_absent_lookup_keeps_reservation(gateway):
    native, scope, _ = gateway
    enable_capacity(native)

    async def late_create(*args, **kwargs):
        raise TimeoutError('Create response was lost')

    native.docker.ensure = late_create
    with pytest.raises(TimeoutError, match='response was lost'):
        await patch(gateway)
    with native.projects.transaction() as session:
        job = session.scalar(select(NativeJob))
        assert session.get(NativeAction, (job.id, '_dispatch')).status == 'pending'
        assert session.get(NativeAction, (job.id, '_termination')) is None
    assert native.capacity.usage()['jobs'] == 1
    assert await native.reconcile() == {'reconciled': 0, 'pending': [job.id]}
    assert native.capacity.usage()['jobs'] == 1
    # The late owned container is eventually observed, terminated and removed.
    native.docker.sessions[job.id] = object()
    assert await native.reconcile() == {'reconciled': 1, 'pending': []}
    assert native.capacity.usage()['jobs'] == 0
    with native.projects.transaction() as session:
        assert session.get(NativeAction, (job.id, '_dispatch')).status == 'completed'
        assert session.get(NativeAction, (job.id, 'patch-1')).status == 'pending'
        assert session.get(NativeJob, job.id).active_action == 'patch-1'


async def test_native_cleanup_follows_durable_id_after_container_rename():
    import httpx

    from kleos.execution.docker import DockerClient

    ident, image, seen = 'd' * 64, 'sha256:' + 'a' * 64, []
    metadata = {'Id': ident, 'Image': image, 'State': {'Running': True},
                'Config': {'Labels': {'kleos.native.job': 'job', 'kleos.native.protocol': PROTOCOL}}}
    present = True

    def handle(request):
        nonlocal present
        path = request.url.path
        seen.append((request.method, path))
        if path == '/containers/' + ident + '/json':
            return httpx.Response(200, json=metadata) if present else httpx.Response(404)
        if path == '/containers/' + ident + '/kill':
            metadata['State']['Running'] = False
            return httpx.Response(204)
        if request.method == 'DELETE' and path == '/containers/' + ident:
            present = False
            return httpx.Response(204)
        return httpx.Response(404)  # Original name disappeared after operator rename.

    docker = DockerClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://docker'))
    assert await docker.confirm_terminated('job', image, observed_id=ident) == ident
    await docker.cleanup('job', observed_id=ident)
    assert ('POST', '/containers/' + ident + '/kill') in seen
    assert ('DELETE', '/containers/' + ident) in seen
    assert not present
    await docker.close()


async def test_legacy_completed_dispatch_without_id_cannot_release_on_name_absence(gateway):
    native, scope, task = gateway
    enable_capacity(native)
    await patch(gateway)
    with native.projects.transaction() as session:
        job = session.scalar(select(NativeJob))
        dispatch = session.get(NativeAction, (job.id, '_dispatch'))
        assert dispatch.result['containerId'] == digest(job.id.encode())
        dispatch.result = {'kind': 'dispatch-record', 'jobId': job.id}
    native.docker.sessions.clear()
    with pytest.raises(RuntimeError, match='missing after dispatch'):
        await native.invoke(scope, task, 'build', {'actionId': 'missing'})
    assert await native.reconcile() == {'reconciled': 0, 'pending': [job.id]}
    assert native.capacity.usage()['jobs'] == 1
    with native.projects.transaction() as session:
        assert session.get(NativeAction, (job.id, '_cleanup')) is None


@pytest.mark.parametrize('missing', [False, True])
async def test_imported_pcb_admission_pins_complete_sources_and_disallows_patch(gateway, monkeypatch, missing):
    from kleos.artifacts.service import Artifact
    from kleos.contracts.models import ArtifactRef, Capability
    from kleos.projects.store import Project

    native, scope, task = gateway
    native.settings.images['pcb'] = native.settings.images['cad']
    refs = []
    with native.projects.transaction() as session:
        for name in ('board.kicad_pcb', 'circuit.kicad_sch'):
            content = b'fixture imported data '+name.encode()
            sha = native.artifacts.blobs.put(content)
            row = Artifact(id=name, project_id=scope.project_id, revision=scope.revision, name=name,
                sha256=sha, size=len(content), media_type='text/plain', status='input', lane='pcb',
                details={'origin': 'user_upload'})
            session.add(row)
            refs.append(ArtifactRef(id=row.id, path=row.name, sha256=row.sha256, revision=row.revision,
                                    size=row.size, media_type=row.media_type))
        project = session.get(Project, scope.project_id)
        circuit = accepted_circuit()
        circuit['conceptReferences'] = {}
        project.data = {**project.data, 'interfaces': {'circuit': circuit},
                        'sourceManifest': {ref.path: ref.id for ref in refs}}
    identity = {'mode': 'imported_no_concept', 'peripheralConceptIds': [], 'importedSourceIds': [refs[0].id],
                'sourceArtifacts': [ref.model_dump(mode='json', by_alias=True) for ref in refs], 'bindingHash': 'f'*64}
    # InterfaceService's separate tests cover user approval. Here test only gateway consumption.
    monkeypatch.setattr('kleos.projects.interfaces.accepted_circuit_identity', lambda *args: identity)
    pcb_task = task.model_copy(update={'capability': Capability.PCB, 'allowed_paths': ['board.kicad_pcb'],
                                      'input_artifact_ids': [ref.id for ref in refs[:1 if missing else 2]]})
    if missing:
        with pytest.raises(DomainError, match='complete approved source closure'):
            native._job(scope, pcb_task, refs[:1])
    else:
        job = native._job(scope, pcb_task, refs)
        assert job.request['acceptedCircuitIdentity'] == identity
        assert [entry['sha256'] for entry in job.request['sources']] == [ref.sha256 for ref in refs]
        with pytest.raises(DomainError, match='preserves approved source bytes'):
            await native._invoke_job(scope, pcb_task, {'action': 'patch', 'actionId': 'overwrite',
                'arguments': {'path': 'board.kicad_pcb', 'content': 'replacement', 'expectedSha256': refs[0].sha256}}, job)
    assert native.docker.dispatches == 0


async def test_closed_job_is_continued_from_its_saved_sources_instead_of_redoing_the_work(gateway):
    native, scope, task = gateway
    await patch(gateway)
    with native.projects.transaction() as session:
        first = session.scalar(select(NativeJob))
        first.status, first_id = 'failed', first.id
    # Without proof that its container stopped, the closed job is never silently replaced.
    with pytest.raises(DomainError, match='closed'):
        await native.invoke(scope, task, 'build', {'actionId': 'build-early'})
    await native._confirm_termination(first_id)
    built = await native.invoke(scope, task, 'build', {'actionId': 'build-2'})
    assert built['observations'][0]['status'] == 'UNKNOWN'  # model.py was already there: no new patch was needed
    result = await native.invoke(scope, task, 'finish', {'actionId': 'finish-2'})
    assert len(result['artifacts']) == 4
    with native.projects.transaction() as session:
        jobs = {job.id: job for job in session.scalars(select(NativeJob))}
        assert len(jobs) == 2 and jobs[first_id].status == 'failed'
        resumed = next(job for job in jobs.values() if job.id != first_id)
        assert resumed.status == 'finished' and resumed.request['resumeOf'] == first_id
        assert resumed.request['resumedSourcePaths'] == ['model.py']
        assert resumed.source_manifest == {'model.py': digest(b'fixture')}
        from kleos.artifacts.service import Artifact
        details = session.scalars(select(Artifact)).first().details
        assert details['resumeOf'] == first_id and details['resumeAttempt'] == 2 and details['inputs'] == {}


async def test_a_task_that_ends_unfinished_releases_its_job_and_can_continue_from_its_saved_sources(gateway):
    native, scope, task = gateway
    await patch(gateway)
    await native.close_task(scope, task.id)
    with native.projects.transaction() as session:
        job = session.scalar(select(NativeJob))
        assert job.status == 'cancelled' and job.active_action is None
        proof = session.get(NativeAction, (job.id, '_termination'))
        assert proof is not None and proof.status == 'completed'
    assert native.docker.stopped  # the container is gone, so its shared capacity is free again
    result = await native.invoke(scope, task, 'build', {'actionId': 'after-close'})
    assert result['observations'][0]['status'] == 'UNKNOWN'  # model.py was carried over; nothing was redone
    with native.projects.transaction() as session:
        assert {job.request.get('resumeAttempt', 1) for job in session.scalars(select(NativeJob))} == {1, 2}


def routing_session(tmp_path, drc_failures):
    commands = []

    def execute(command, work, timeout, limits, application):
        commands.append(command)
        if 'drc' in command and sum('drc' in value for value in commands) <= drc_failures:
            raise RuntimeError('Found 1 unconnected items')
        return 'ok'

    session = NativeSession(request(), tmp_path / 'control', tmp_path / 'work', execute)
    session.state['deadline'] = time.time() + 3600
    stage = tmp_path / 'work/build/pcb'
    stage.mkdir(parents=True)
    return session, stage, commands


def test_pcb_routing_restarts_from_the_authored_board_when_drc_fails(tmp_path):
    session, stage, commands = routing_session(tmp_path, 1)
    session._route(stage, b'authored board')
    assert sum('/opt/freerouting/freerouting.jar' in command for command in commands) == 2
    assert sum(command[-1:] == ['prepare'] for command in commands) == 1
    assert (stage / 'board.kicad_pcb').read_bytes() == b'authored board'


def test_pcb_routing_reports_the_last_failure_after_bounded_attempts(tmp_path):
    session, stage, commands = routing_session(tmp_path, 99)
    with pytest.raises(RuntimeError, match='unconnected'):
        session._route(stage, b'authored board')
    assert sum('/opt/freerouting/freerouting.jar' in command for command in commands) == 3
