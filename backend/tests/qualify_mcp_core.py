"""Opt-in exact-image MCP qualification; no inference or plugin execution on host."""
import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
import struct
import sys
import time
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image
from sqlalchemy import select

from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.mcp_bridge.bridge import ArtifactInspector, StagedInput
from kleos.capabilities.mcp_bridge.docker import DockerTransport, seccomp_hash
from kleos.capabilities.mcp_bridge.installation import REQUIRED_CHECKS, Installation, source_hashes
from kleos.capabilities.mcp_bridge.runner import DockerRunner, McpSession
from kleos.contracts.models import Actor, TaskSpec
from kleos.execution.capacity import CapacityService
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import ProjectStore, fingerprint


class ObservedDocker(DockerTransport):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.observed = []

    async def start(self, ident, *args):
        await super().start(ident, *args)
        observed = await self.inspect(ident)
        host = observed['HostConfig']
        self.observed.append({'image': observed['Image'], 'user': observed['Config']['User'],
            'readonlyRootfs': host['ReadonlyRootfs'], 'networkMode': host['NetworkMode'],
            'memory': host['Memory'], 'nanoCpus': host['NanoCpus'], 'pids': host['PidsLimit'],
            'scratch': host['Tmpfs'], 'readOnlyInputs': all(not item['RW'] for item in observed['Mounts']),
            'watchdog': observed['Config']['Entrypoint'][:3], 'capDrop': host['CapDrop']})


def installation(image):
    root = Path(__file__).parents[1] / 'src/kleos/capabilities/mcp_bridge'
    source = json.loads((root / 'vendor/source.json').read_bytes())
    return Installation(id='mm-core-image-inspector', version='1.0.0', image=image,
        package_sha256=source['archiveSha256'], server_name='kleos-mm-core-inspector', server_version='1.3.0',
        tool_name='inspect_artifacts', timeout_seconds=30, max_input_bytes=8388608,
        media_types=('image/png', 'image/jpeg'))


def bootstrap_report(config):
    # This explicit test-environment permit bootstraps a qualification fixture.
    # It is not the output evidence or an independent production review.
    hashes = source_hashes()
    return {'qualified': True, 'testOnly': True, 'installation': config.model_dump(mode='json'),
        'sourceSha256': hashes, 'review': {'independent': True,
            'reviewer': 'Synthetic fixture-only bootstrap, not independent release evidence',
            'reference': 'Test environment only; actual output remains unqualified until reviewed',
            'reviewedAt': datetime.now(UTC).isoformat(), 'sourceHash': fingerprint(hashes)},
        'checks': dict.fromkeys(REQUIRED_CHECKS, 'PASS')}


def fixture(store, artifacts, content, name, *, media_type='image/png'):
    actor = Actor(tenant_id='mcp-qualification', user_id='fixture')
    project = store.create(actor, 'Inspect exact image fixture', 'create-' + name)
    uploaded = artifacts.upload(actor, project['projectId'], {'expectedEngineeringRevision': 1,
        'sha256': hashlib.sha256(content).hexdigest(), 'file': {
            'path': name + ('.jpg' if media_type == 'image/jpeg' else '.png'), 'mediaType': media_type,
            'encoding': 'base64', 'content': base64.b64encode(content).decode()}}, 'upload-' + name)
    run = store.submit(actor, project['projectId'], 'Inspect input image without semantic claims', 2, 'run-' + name)
    scope = RunScope(actor=actor, project_id=project['projectId'], run_id=run['runId'], revision=3,
        deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    task = TaskSpec(id='image-inspection', objective='Inspect exact image bytes', capability='research',
        input_artifact_ids=[uploaded['artifact']['id']])
    return scope, task


async def watchdog_child(args):
    work = Path(args.work)
    context = json.loads((work / 'watchdog-context.json').read_bytes())
    scope, task = RunScope.model_validate(context['scope']), TaskSpec.model_validate(context['task'])
    store = ProjectStore(f'sqlite:///{work}/state.db')
    repo, artifacts = GraphRepository(store), ArtifactService(store, BlobStore(work / 'blobs'))
    docker = DockerTransport(socket=args.socket)
    runner = DockerRunner(repo, CapacityService(store), docker, control_root=work / 'control')
    refs = await repo.resolve_artifacts(scope, task.input_artifact_ids)
    inputs = tuple(StagedInput(ref, f'inputs/{index:03}', artifacts.content(scope.actor, ref.id).data)
                   for index, ref in enumerate(refs))
    async with runner.open(scope, task, installation(args.image), inputs, action_id='watchdog-host-loss'):
        # Deliberately exit the trusted qualification controller without its
        # finally cleanup: the image-owned watchdog must stop the idle server.
        os._exit(0)


async def qualify(args):
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=False)
    config = installation(args.image)
    evidence = {'qualified': False, 'independentReview': 'pending', 'sourceOverride': False,
        'installation': config.model_dump(mode='json'), 'sourceSha256': source_hashes(),
        'seccompSha256': seccomp_hash(), 'startedAt': datetime.now(UTC).isoformat(), 'cases': [],
        'capacityScope': 'Persistent local SQLite qualification ledger; coordinated exclusive window, '
            'not cross-database global admission', 'limitations': ['PNG/JPEG inspection fixture only',
            'No semantic understanding, design acceptance, provider calls or paid work',
            'FreeCAD/Blender and other MM-core tools remain unqualified']}
    store = ProjectStore(f'sqlite:///{work}/state.db')
    store.initialize_for_tests()
    repo, artifacts = GraphRepository(store), ArtifactService(store, BlobStore(work / 'blobs'))
    capacity, docker = CapacityService(store), ObservedDocker(socket=args.socket)
    runner = DockerRunner(repo, capacity, docker, control_root=work / 'control')
    inspector = ArtifactInspector(config, bootstrap_report(config), environment='test', repository=repo,
                                 artifacts=artifacts, runner=runner)
    png = io.BytesIO()
    Image.new('RGB', (64, 32), (34, 101, 180)).save(png, format='JPEG' if args.jpeg_only else 'PNG')
    source = png.getvalue()
    (work / ('fixture.jpg' if args.jpeg_only else 'fixture.png')).write_bytes(source)
    try:
        active = await docker.client.get('/containers/json')
        active.raise_for_status()
        assert not any(any(key.startswith('kleos.') for key in row.get('Labels', {}))
                       or any(any(lane in name for lane in ('native', 'slicer', 'checker', 'validation', 'kleos-mcp'))
                              for name in row.get('Names', [])) for row in active.json()), 'Native window is occupied'
        scope, task = fixture(store, artifacts, source, 'positive',
                              media_type='image/jpeg' if args.jpeg_only else 'image/png')
        print('Starting real MM-core image inspection', flush=True)
        result = await inspector.invoke(scope, task, {'actionId': 'inspect', 'artifactIds': task.input_artifact_ids})
        assert not result.blockers and len(result.observations) == 1
        record = result.observations[0].details['records'][0]
        assert (record['metadata']['width'], record['metadata']['height']) == (64, 32)
        assert record['metadata']['plugin'] == 'qwen-mm-plugins-core'
        assert await inspector.invoke(scope, task, {'actionId': 'inspect', 'artifactIds': task.input_artifact_ids}) == result
        assert capacity.usage()['jobs'] == 0
        evidence['cases'].append({'name': 'real-mm-core-jpeg-inspection-and-replay' if args.jpeg_only
                                  else 'real-mm-core-inspection-and-replay', 'status': 'PASS',
                                  'result': result.model_dump(mode='json'), 'capacityAfter': capacity.usage()})
        if args.jpeg_only:
            evidence['fixturePassed'] = True
            return
        for name, payload in [('malformed', b'not an image'), ('pixel-limit', pixel_limit_png(source))]:
            scope, task = fixture(store, artifacts, payload, name)
            result = await inspector.invoke(scope, task, {'actionId': name, 'artifactIds': task.input_artifact_ids})
            assert result.blockers == ['mcp_tool_error'] and not result.observations
            evidence['cases'].append({'name': name, 'status': 'PASS', 'blocked': result.blockers})
            assert capacity.usage()['jobs'] == 0
        if not args.skip_watchdog:
            scope, task = fixture(store, artifacts, source, 'watchdog')
            (work / 'watchdog-context.json').write_text(json.dumps({'scope': scope.model_dump(mode='json'),
                'task': task.model_dump(mode='json')}))
            print('Starting host-loss watchdog fixture', flush=True)
            child = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()),
                '--watchdog-child', '--image', args.image, '--socket', args.socket, '--work', str(work),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(child.communicate(), 30)
            (work / 'watchdog-controller.stdout').write_bytes(stdout)
            (work / 'watchdog-controller.stderr').write_bytes(stderr)
            assert child.returncode == 0, 'Watchdog controller did not dispatch successfully'
            with store.transaction() as session:
                row = session.scalar(select(McpSession).where(McpSession.run_id == scope.run_id))
                ident, container = row.id, row.container_id
            assert capacity.usage()['jobs'] == 1
            started = time.monotonic()
            while time.monotonic() - started < 40:
                observed = await docker.inspect(container)
                if not observed['State']['Running']:
                    break
                await asyncio.sleep(.5)
            assert not observed['State']['Running'] and observed['State']['ExitCode'] == 137
            assert capacity.usage()['jobs'] == 1
            await runner.reconcile(ident)
            assert capacity.usage()['jobs'] == 0
            evidence['cases'].append({'name': 'image-watchdog-after-controller-loss', 'status': 'PASS',
                'exitCode': observed['State']['ExitCode'], 'observedAfterSeconds': time.monotonic() - started,
                'retainedCapacityUntilRemoval': True, 'capacityAfter': capacity.usage()})
        evidence['fixturePassed'] = True
    except Exception as error:
        evidence['fixturePassed'] = False
        evidence['errorType'] = type(error).__name__
        evidence['errorCode'] = getattr(error, 'code', 'qualification_failed')
        raise
    finally:
        with store.transaction() as session:
            ids = list(session.scalars(select(McpSession.id).where(McpSession.status != 'removed')))
        for ident in ids:
            try:
                await runner.reconcile(ident)
            except Exception:
                evidence.setdefault('cleanupPending', []).append(ident)
        evidence['containers'] = docker.observed
        evidence['finalCapacity'] = capacity.usage()
        evidence['finishedAt'] = datetime.now(UTC).isoformat()
        (work / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
        await docker.close()
        print('Evidence saved; independent qualification review remains required.', flush=True)


def pixel_limit_png(source):
    raw = bytearray(source)
    raw[16:24] = struct.pack('>II', 10000, 10000)
    raw[29:33] = struct.pack('>I', zlib.crc32(raw[12:29]))
    return bytes(raw)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--work', required=True)
    parser.add_argument('--socket', default='/var/run/docker.sock')
    parser.add_argument('--watchdog-child', action='store_true')
    parser.add_argument('--skip-watchdog', action='store_true')
    parser.add_argument('--jpeg-only', action='store_true')
    args = parser.parse_args()
    asyncio.run(watchdog_child(args) if args.watchdog_child else qualify(args))
