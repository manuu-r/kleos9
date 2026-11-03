"""Opt-in local exact-image CAD qualification; fixture code runs only inside Docker."""
import argparse
import asyncio
import json
import math
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError, TaskSpec
from kleos.execution.docker import DockerClient, NativeSettings, Resources
from kleos.execution.gateway import NativeGateway, NativeJob
from kleos.execution.protocol import digest
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import ProjectStore

FIXTURE = '''import errno,json,os,socket
from pathlib import Path
from build123d import Box,Cylinder,Pos
assert os.getuid()==10001 and os.getgid()==10001
assert not any(key in os.environ for key in ['OPENAI_API_KEY','DATABASE_URL','AWS_ACCESS_KEY_ID'])
status=Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\\t1' in status and 'Seccomp:\\t2' in status
assert int(next(line.split()[1] for line in status.splitlines() if line.startswith('CapEff:')),16)==0
for path in ['/run/kleos-control/journal/state.json','/control/forged-result','/opt/kleos-native/credential-probe']:
 try:
  if path.startswith('/run/'): Path(path).read_bytes()
  else: Path(path).write_text('forbidden')
 except OSError as error:
  assert error.errno in [errno.EACCES,errno.EPERM,errno.EROFS]
 else: raise AssertionError('Native authority boundary was writable/readable')
try: socket.socket(socket.AF_INET,socket.SOCK_STREAM)
except OSError as error: assert error.errno==errno.EPERM
else: raise AssertionError('Internet sockets were allowed')
def cgroup(v2,v1):
 root=Path('/sys/fs/cgroup')
 return (root/v2 if (root/v2).exists() else root/v1).read_text().split()[0]
assert cgroup('memory.max','memory/memory.limit_in_bytes')=='4294967296'
assert cgroup('pids.max','pids/pids.max')=='128'
assert cgroup('cpu.max','cpu/cpu.cfs_quota_us')=='200000'
base=Box(24,16,4)-Cylinder(2,10)
lid=Pos(0,0,7)*Box(24,16,2)
parts={'base':base,'lid':lid}
'''


async def qualify(args):
    work = Path(tempfile.mkdtemp(prefix='kleos-cad-qualification-'))
    store = ProjectStore(f'sqlite:///{work}/state.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='qualification', user_id='fixture')
    project = store.create(actor, 'Trusted two-part CAD fixture', 'create')
    receipt = store.submit(actor, project['projectId'], 'Qualify exact native image', 1, 'objective')
    scope = SimpleNamespace(actor=actor, project_id=project['projectId'], run_id=receipt['runId'],
                            revision=2, compute_budget_seconds=None, deadline_at=datetime.now(UTC) + timedelta(minutes=10))
    task = TaskSpec(id='trusted-cad-qualification', objective='Export the fixed two-part fixture',
                    capability='cad', allowed_paths=['model.py'], max_actions=8)
    docker = DockerClient(socket=args.socket)
    artifacts = ArtifactService(store, BlobStore(work / 'blobs'))
    settings = NativeSettings(work / 'control', {'cad': args.image}, resources=Resources(), session_seconds=600,
                              docker_desktop_compatibility=not args.strict_linux, deployment="local")
    gateway = NativeGateway(store, artifacts, settings, docker, GraphRepository(store))
    observations = []
    success = False
    try:
        for action, arguments in [
            ('patch', {'path': 'model.py', 'content': FIXTURE, 'expectedSha256': None}),
            ('build', {}), ('analyze', {}), ('render', {'view': 'isometric'}), ('finish', {}),
        ]:
            print('Starting ' + action, flush=True)
            response = await gateway.invoke(scope, task, action, {'actionId': action, **arguments})
            observations.append({'action': action, 'response': response})
            if response['observations'][0]['status'] == 'ERROR':
                raise RuntimeError(response['summary'])
            print('Completed ' + action, flush=True)
        measured = json.loads(observations[2]['response']['summary'])
        parts = {item['id']: item for item in measured['parts']}
        assert set(parts) == {'base', 'lid'}
        assert all(part['valid'] for part in parts.values())
        assert math.isclose(parts['base']['volumeMm3'], 24 * 16 * 4 - math.pi * 2**2 * 4, abs_tol=1e-5)
        assert math.isclose(parts['lid']['volumeMm3'], 24 * 16 * 2, abs_tol=1e-5)
        assert parts['base']['sizeMm'] == [24, 16, 4]
        assert parts['lid']['sizeMm'] == [24, 16, 2]
        exported = observations[-1]['response']['artifacts']
        assert len(exported) == 8
        names = {item['path'] for item in exported}
        assert names == {'model.py', 'outputs/parts.json', 'outputs/base.step', 'outputs/base.stl',
                         'outputs/lid.step', 'outputs/lid.stl', 'outputs/base.svg', 'outputs/lid.svg'}
        for ref in exported:
            content = artifacts.blobs.get(ref['sha256'])
            assert len(content) == ref['size'] and digest(content) == ref['sha256']
            if ref['path'].endswith('.step'):
                assert b'ISO-10303-21' in content
            elif ref['path'].endswith('.stl'):
                assert len(content) > 84
            elif ref['path'].endswith('.svg'):
                assert content.startswith(b'<svg') and b'<polygon' in content
        replay = await gateway.invoke(scope, task, 'finish', {'actionId': 'finish'})
        assert replay == observations[-1]['response']
        try:
            await gateway.invoke(scope, task, 'patch', {'actionId': 'outside', 'path': 'other.py',
                                                       'content': 'forbidden', 'expectedSha256': None})
        except DomainError:
            pass
        else:
            raise AssertionError('Task scope escape accepted')
        success = True
    finally:
        with store.transaction() as session:
            jobs = list(session.scalars(select(NativeJob)))
        inspections = []
        for job in jobs:
            inspected = await docker.inspect('kleos-native-' + job.id)
            if inspected:
                log_response = await docker.client.get('/containers/kleos-native-' + job.id + '/logs',
                    params={'stdout': 'true', 'stderr': 'true', 'tail': '100'})
                runtime_logs = log_response.content[-16000:].decode(errors='replace')
                if not success:
                    print(runtime_logs, flush=True)
                inspections.append({'runtimeLogs': runtime_logs, 'image': inspected['Image'], 'status': inspected['State']['Status'],
                                    'readonlyRootfs': inspected['HostConfig']['ReadonlyRootfs'],
                                    'networkMode': inspected['HostConfig']['NetworkMode'],
                                    'memory': inspected['HostConfig']['Memory'],
                                    'nanoCpus': inspected['HostConfig']['NanoCpus'],
                                    'pidsLimit': inspected['HostConfig']['PidsLimit']})
                await docker.stop(job.id)
                await docker.cleanup(job.id)
        evidence = {'qualified': success, 'imageId': args.image, 'finishedAt': datetime.now(UTC).isoformat(),
                    'fixtureSha256': digest(FIXTURE.encode()), 'protocol': 'kleos-native-session-v2', 'lane': 'cad',
                    'sourceOverride': False,
                    'networkPolicy': 'in-process-seccomp' if args.strict_linux else 'docker-desktop-daemon-seccomp',
                    'parentImageId': args.parent_image, 'scope': 'exact-image CAD two-part fixture only',
                    'checks': observations, 'containers': inspections,
                    'limitations': ['No physical acceptance', 'No PCB/firmware/application qualification',
                                    'No independent manufacturing or tolerancing validation']}
        Path(args.output).write_text(json.dumps(evidence, indent=2) + '\n')
        print('Evidence: ' + args.output, flush=True)
        await docker.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--socket', default='/var/run/docker.sock')
    parser.add_argument('--output', required=True)
    parser.add_argument('--parent-image', required=True, help='Toolchain parent image ID recorded in the evidence')
    parser.add_argument('--strict-linux', action='store_true',
                        help='Use the supervisor seccomp path on a Linux engine instead of the Docker Desktop mode')
    asyncio.run(qualify(parser.parse_args()))
