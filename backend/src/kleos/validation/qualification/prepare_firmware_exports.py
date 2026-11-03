"""Opt-in unchanged trusted NRF fixture regeneration through its exact existing image."""
import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, TaskSpec
from kleos.execution.capacity import CapacityService
from kleos.execution.docker import DockerClient, NativeSettings
from kleos.execution.gateway import NativeGateway
from kleos.execution.qualification.qualify_firmware import BOARD, SOURCES
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Objective, ProjectStore, fingerprint

IMAGE = 'sha256:1ea0aa4e85133be3a85fd7399d53c94cb2d1dc6d426b603235fcf44beb93e3bc'


async def prepare(args):
    if args.work.exists() or args.output.exists():
        raise ValueError('Use new persistent work and report paths')
    historic = json.loads((Path(__file__).parents[2]/'execution/qualification/firmware-exact-image.json').read_text())
    assert historic['imageId'] == IMAGE and historic['fixtureSourcesSha256'] == fingerprint(SOURCES)
    args.work.mkdir(parents=True)
    store = ProjectStore(f'sqlite:///{args.work}/state.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='qualification', user_id='firmware-export-fixture')
    project = store.create(actor, 'Unchanged trusted NRF export fixture', 'create')
    with store.transaction() as session:
        row = store.authorized(session, actor, project['projectId'], write=True)
        row.data = {**row.data, 'interfaces': {'firmware': {'board': BOARD}}}
    submitted = store.submit(actor, project['projectId'], 'Regenerate exact trusted firmware sources', 1, 'build')
    scope = RunScope(actor=actor, project_id=project['projectId'], run_id=submitted['runId'],
                     revision=submitted['revision'], deadline_at=datetime.now(UTC)+timedelta(minutes=10))
    task = TaskSpec(id='trusted-firmware-fixture', capability='firmware', objective='Compile original trusted fixture only',
                    allowed_paths=sorted(SOURCES), max_actions=8)
    repository = GraphRepository(store)
    await repository.publish_plan(scope, tasks=[task], superseded={}, turn=0, summary='Trusted fixed fixture', question=None)
    artifacts = ArtifactService(store, BlobStore(args.work/'blobs'))
    docker = DockerClient(socket=args.socket)
    capacity = CapacityService(store, pool_id='isolated-firmware-export-fixture')
    settings = NativeSettings(args.work/'control', {'firmware': IMAGE}, docker_desktop_compatibility=True,
                              deployment='local', firmware_boards=(BOARD,))
    gateway = NativeGateway(store, artifacts, settings, docker, repository, capacity=capacity)
    report = {'prepared': False, 'imageId': IMAGE, 'sourceOverride': False, 'fixtureSourcesSha256': fingerprint(SOURCES),
        'scope': 'New regeneration of unchanged trusted historical fixture; not historical byte recovery or independent validation',
        'capacityScope': 'Persistent fixture ledger only; serial root-coordinated execution, no cross-database capacity claim',
        'startedAt': datetime.now(UTC).isoformat(), 'actions': [], 'modelCalls': 0}
    try:
        for index, (path, source) in enumerate(SOURCES.items()):
            result = await gateway.invoke(scope, task, 'patch', {'actionId': f'source-{index}',
                'path': path, 'content': source, 'expectedSha256': None})
            report['actions'].append({'action': 'patch', 'response': result})
            assert result['observations'][0]['details']['executionSucceeded'], result
        for action in ('build', 'finish'):
            print('START '+action, flush=True)
            result = await gateway.invoke(scope, task, action, {'actionId': action})
            report['actions'].append({'action': action, 'response': result})
            assert result['observations'][0]['details']['executionSucceeded'], result
            print('DONE '+action, flush=True)
        report['artifacts'] = result['artifacts']
        for ref in result['artifacts']:
            if ref['path'] in {'outputs/firmware.elf', 'outputs/firmware.bin'}:
                content = artifacts.blobs.get(ref['sha256'])
                assert len(content) == ref['size']
                (args.work/Path(ref['path']).name).write_bytes(content)
        report['prepared'] = True
    except Exception as error:
        report['failure'] = {'type': type(error).__name__, 'message': str(error)[:2000]}
        raise
    finally:
        if not report['prepared']:
            with store.transaction() as session:
                session.get(Objective, scope.run_id).status = 'cancelled'
        await gateway.reconcile()
        report['capacityFinal'] = capacity.usage()
        report['finishedAt'] = datetime.now(UTC).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2)+'\n')
        await docker.client.aclose()
        store.engine.dispose()
    assert report['capacityFinal']['jobs'] == 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(prepare(parser.parse_args()))
