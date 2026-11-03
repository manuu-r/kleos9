"""Opt-in exact-image offline Flutter web qualification with hard-bounded private scratch."""
import argparse
import asyncio
import io
import json
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, TaskSpec
from kleos.execution.docker import DockerClient, NativeSettings
from kleos.execution.gateway import NativeGateway, NativeJob
from kleos.execution.protocol import digest, fingerprint
from kleos.execution.security import daemon_socket_profile
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import ProjectStore


async def qualify(args):
    sources = json.loads(Path(__file__).with_name('web-fixture-sources.json').read_bytes())
    work = Path(tempfile.mkdtemp(prefix='kleos-web-qualification-'))
    store = ProjectStore(f'sqlite:///{work}/state.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='qualification', user_id='fixture')
    project = store.create(actor, 'Fresh offline companion web build', 'create')
    receipt = store.submit(actor, project['projectId'], 'Build web only, not Android', 1, 'objective')
    scope = SimpleNamespace(actor=actor, project_id=project['projectId'], run_id=receipt['runId'], revision=2, compute_budget_seconds=None,
                            deadline_at=datetime.now(UTC) + timedelta(minutes=10))
    task = TaskSpec(id='trusted-web-fixture', objective='Compile fresh Flutter web without network or legacy app templates',
                    capability='application', allowed_paths=sorted(sources), max_actions=8)
    docker = DockerClient(socket=args.socket)
    artifacts = ArtifactService(store, BlobStore(work / 'blobs'))
    settings = NativeSettings(work / 'control', {'application': args.image}, session_seconds=600,
                              docker_desktop_compatibility=not args.strict_linux, deployment='local')
    gateway = NativeGateway(store, artifacts, settings, docker, GraphRepository(store))
    observations, success, zip_entries = [], False, []
    try:
        for number, (path, source) in enumerate(sources.items()):
            result = await gateway.invoke(scope, task, 'patch', {'actionId': 'source-' + str(number),
                'path': path, 'content': source, 'expectedSha256': None})
            observations.append({'action': 'patch', 'response': result})
            assert result['observations'][0]['status'] != 'ERROR', result['summary']
        for action in ('build', 'finish'):
            print('Starting ' + action, flush=True)
            result = await gateway.invoke(scope, task, action, {'actionId': action})
            observations.append({'action': action, 'response': result})
            if result['observations'][0]['status'] == 'ERROR':
                raise RuntimeError(result['summary'])
            print('Completed ' + action, flush=True)
        references = {item['path']: item for item in observations[-1]['response']['artifacts']}
        with zipfile.ZipFile(io.BytesIO(artifacts.blobs.get(references['outputs/companion-web.zip']['sha256']))) as archive:
            zip_entries = sorted(archive.namelist())
            assert {'index.html', 'main.dart.js', 'flutter_bootstrap.js'} <= set(zip_entries)
            assert len(archive.read('main.dart.js')) > 10000
            assert b'flutter_bootstrap.js' in archive.read('index.html')
        for ref in references.values():
            content = artifacts.blobs.get(ref['sha256'])
            assert digest(content) == ref['sha256'] and len(content) == ref['size']
        assert await gateway.invoke(scope, task, 'finish', {'actionId': 'finish'}) == observations[-1]['response']
        success = True
    finally:
        with store.transaction() as session:
            jobs = list(session.scalars(select(NativeJob)))
        containers = []
        for job in jobs:
            inspected = await docker.inspect('kleos-native-' + job.id)
            if inspected:
                logs = await docker.client.get('/containers/kleos-native-' + job.id + '/logs',
                    params={'stdout': 'true', 'stderr': 'true', 'tail': '100'})
                if not success:
                    print(logs.content[-16000:].decode(errors='replace'), flush=True)
                host = inspected['HostConfig']
                assert host['Memory'] == 6 * 1024**3 and host['MemorySwap'] == host['Memory']
                assert host['PidsLimit'] == 256 and 'size=4294967296' in host['Tmpfs']['/tmp']
                assert host['Tmpfs']['/tmp'].endswith(',exec')
                assert len(host['Mounts']) == 1 and host['Mounts'][0]['ReadOnly']
                containers.append({'image': inspected['Image'], 'networkMode': host['NetworkMode'],
                    'readonlyRootfs': host['ReadonlyRootfs'], 'memory': host['Memory'], 'nanoCpus': host['NanoCpus'],
                    'pidsLimit': host['PidsLimit'], 'scratch': host['Tmpfs']['/tmp'], 'writableHostMounts': False})
                await docker.stop(job.id)
                await docker.cleanup(job.id)
        report = {'qualified': success, 'imageId': args.image, 'protocol': 'kleos-native-session-v2', 'lane': 'application',
            'applicationTarget': 'web', 'deployment': 'local', 'sourceOverride': False, 'qualificationKind': 'exact-image',
            'storagePolicy': 'hard-bounded-private-tmpfs', 'resources': settings.application_resources.wire(),
            'networkPolicy': 'linux-supervisor-seccomp' if args.strict_linux else 'docker-desktop-daemon-seccomp',
            'parentImageId': args.parent_image, 'daemonSeccompSha256': fingerprint(daemon_socket_profile()),
            'finishedAt': datetime.now(UTC).isoformat(), 'fixtureSourcesSha256': fingerprint(sources),
            'checks': observations, 'containers': containers, 'webArchiveEntries': zip_entries,
            'limitations': ['Offline Flutter web build fixture only', 'No browser/device execution or acceptance',
                            'APK remains unqualified; no silent platform substitution']}
        Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
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
