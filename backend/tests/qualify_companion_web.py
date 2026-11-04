"""Opt-in exact-image offline Flutter web qualification with hard-bounded private scratch."""
import argparse
import asyncio
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, TaskSpec
from kleos.execution.capacity import CapacityService
from kleos.execution.docker import DockerClient, NativeSettings
from kleos.execution.gateway import NativeGateway, NativeJob
from kleos.execution.protocol import digest, fingerprint
from kleos.execution.security import daemon_socket_profile
from kleos.execution.web_bundle import FONTS, LICENSE_SHA256, PROVENANCE
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Objective, ProjectStore


async def qualify(args):
    fixture = Path(__file__).parents[1] / 'src/kleos/execution/qualification/web-fixture-sources.json'
    sources = json.loads(fixture.read_bytes())
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=False)
    (work / 'fixture-sources.json').write_bytes(fixture.read_bytes())
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
                              docker_desktop_compatibility=True, deployment='local')
    capacity = CapacityService(store)
    gateway = NativeGateway(store, artifacts, settings, docker, GraphRepository(store), capacity=capacity)
    observations, success, zip_entries, references = [], False, [], {}
    held_capacity, font_packaging = None, None
    try:
        metadata = await docker.client.get('/images/' + args.image + '/json')
        metadata.raise_for_status()
        assert metadata.json()['Id'] == args.image
        active = await docker.client.get('/containers/json')
        active.raise_for_status()
        assert not any(any(key.startswith('kleos.') for key in row.get('Labels', {}))
                       or any(any(lane in name for lane in ('native', 'slicer', 'checker', 'validation'))
                              for name in row.get('Names', [])) for row in active.json()), 'Engineering compute is already running'
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
            if args.require_fonts:
                font_packaging = json.loads(archive.read(PROVENANCE))
                assert font_packaging['mode'] == 'pinned-sdk-default'
                assert len(font_packaging['fonts']) == len(FONTS)
                for item, expected in zip(font_packaging['fonts'], FONTS, strict=True):
                    assert digest(archive.read(item['path'])) == item['sha256'] == expected[3]
                assert digest(archive.read(font_packaging['license']['path'])) == LICENSE_SHA256
                assert digest(archive.read('assets/FontManifest.json')) == font_packaging['manifestSha256']
        assert all(references[path]['sha256'] == digest(content.encode()) for path, content in sources.items())
        for ref in references.values():
            content = artifacts.blobs.get(ref['sha256'])
            assert digest(content) == ref['sha256'] and len(content) == ref['size']
        assert await gateway.invoke(scope, task, 'finish', {'actionId': 'finish'}) == observations[-1]['response']
        held_capacity = capacity.usage()
        assert held_capacity['jobs'] == 1
        bundle = artifacts.blobs.get(references['outputs/companion-web.zip']['sha256'])
        (work / 'companion-web.zip').write_bytes(bundle)
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
        # The production reconciler verifies termination and removal before releasing capacity.
        if not success:
            with store.transaction() as session:
                session.get(Objective, scope.run_id).status = 'cancelled'
        await gateway.reconcile()
        final_capacity = capacity.usage()
        if success:
            assert final_capacity['jobs'] == 0
        report = {'qualified': success, 'imageId': args.image, 'protocol': 'kleos-native-session-v2', 'lane': 'application',
            'applicationTarget': 'web', 'deployment': 'local', 'sourceOverride': False, 'qualificationKind': 'exact-image',
            'storagePolicy': 'hard-bounded-private-tmpfs', 'resources': settings.application_resources.wire(),
            'networkPolicy': 'docker-desktop-daemon-seccomp', 'daemonSeccompSha256': fingerprint(daemon_socket_profile()),
            'finishedAt': datetime.now(UTC).isoformat(), 'fixtureSourcesSha256': fingerprint(sources),
            'workDirectory': str(work), 'artifactBlobDirectory': str(work / 'blobs'),
            'artifacts': list(references.values()), 'sharedCapacity': True,
            'capacityScope': 'Persistent qualification SQLite pool; coordinated exclusive native window, not cross-database admission',
            'heldCapacity': held_capacity, 'finalCapacity': final_capacity,
            'sourceClosureUnchanged': success, 'fontPackaging': font_packaging,
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
    parser.add_argument('--work', required=True)
    parser.add_argument('--require-fonts', action='store_true')
    asyncio.run(qualify(parser.parse_args()))
