"""Explicit exact-image PCB qualification; all KiCad/routing execution is isolated."""
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
from kleos.execution.docker import DockerClient, NativeSettings, Resources
from kleos.execution.gateway import NativeGateway, NativeJob
from kleos.execution.pcb_scaffold import scaffold
from kleos.execution.protocol import digest, fingerprint
from kleos.execution.security import daemon_socket_profile
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import ProjectStore

CONTRACT = {'references': [
    {'reference': 'J1', 'value': 'Connector', 'footprint': 'Fixture:TwoPin', 'pins': {'1': '/NET_A', '2': '/NET_B'}},
    {'reference': 'R1', 'value': '1k', 'footprint': 'Fixture:TwoPin', 'pins': {'1': '/NET_A', '2': '/NET_B'}},
], 'conceptReferences': {'power-connector': 'J1'}, 'constraints': {
    'maxWidthMm': 42, 'maxHeightMm': 32, 'minTrackWidthMm': .25, 'minClearanceMm': .2,
    'copperLayers': ['F.Cu', 'B.Cu']}}


async def qualify(args):
    sources = json.loads(Path(__file__).with_name('pcb-fixture-sources.json').read_bytes())
    work = Path(tempfile.mkdtemp(prefix='kleos-pcb-qualification-'))
    store = ProjectStore(f'sqlite:///{work}/state.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='qualification', user_id='fixture')
    project = store.create(actor, 'Trusted two-component routing fixture', 'create')
    with store.transaction() as session:
        record = store.authorized(session, actor, project['projectId'], write=True)
        record.data = {**record.data, 'interfaces': {'circuit': CONTRACT},
                       'approvedDesign': {'peripheralConceptIds': ['power-connector'], 'schemaVersion': 'approved-design-v1'}}
    receipt = store.submit(actor, project['projectId'], 'Qualify exact PCB runtime', 1, 'objective')
    scope = SimpleNamespace(actor=actor, project_id=project['projectId'], run_id=receipt['runId'], revision=2, compute_budget_seconds=None,
                            deadline_at=datetime.now(UTC) + timedelta(minutes=10))
    task = TaskSpec(id='trusted-pcb-qualification', objective='Route the fixed accepted circuit', capability='pcb',
                    allowed_paths=sorted(sources), max_actions=16)
    docker = DockerClient(socket=args.socket)
    artifacts = ArtifactService(store, BlobStore(work / 'blobs'))
    gateway = NativeGateway(store, artifacts, NativeSettings(work / 'control', {'pcb': args.image},
        resources=Resources(), session_seconds=600, docker_desktop_compatibility=not args.strict_linux, deployment='local'),
        docker, GraphRepository(store))
    observations, negatives, success = [], [], False
    # The gateway seeds the contract-derived starter, so these fixture sources replace those exact bytes.
    starter = {path: digest(content.encode()) for path, content in scaffold(CONTRACT).items()}
    try:
        for number, (path, source) in enumerate(sorted(sources.items())):
            result = await gateway.invoke(scope, task, 'patch', {'actionId': 'source-' + str(number),
                'path': path, 'content': source, 'expectedSha256': starter.get(path)})
            observations.append({'action': 'patch', 'path': path, 'response': result})
            assert result['observations'][0]['status'] != 'ERROR', result['summary']
        wrong = sources['board.kicad_pcb'].replace('"R1"', '"R99"')
        assert wrong != sources['board.kicad_pcb']
        await gateway.invoke(scope, task, 'patch', {'actionId': 'wrong-reference-source', 'path': 'board.kicad_pcb',
            'content': wrong, 'expectedSha256': digest(sources['board.kicad_pcb'].encode())})
        rejected = await gateway.invoke(scope, task, 'build', {'actionId': 'wrong-reference-build'})
        assert rejected['observations'][0]['status'] == 'ERROR'
        assert 'PCB references' in rejected['summary'] and rejected['artifacts'] == [], rejected['summary']
        negatives.append({'name': 'wrong-and-missing-accepted-reference', 'rejected': True, 'response': rejected})
        await gateway.invoke(scope, task, 'patch', {'actionId': 'restore-reference-source', 'path': 'board.kicad_pcb',
            'content': sources['board.kicad_pcb'], 'expectedSha256': digest(wrong.encode())})
        for action in ['build', 'finish']:
            print('Starting ' + action, flush=True)
            result = await gateway.invoke(scope, task, action, {'actionId': action})
            observations.append({'action': action, 'response': result})
            if result['observations'][0]['status'] == 'ERROR':
                raise RuntimeError(result['summary'])
            print('Completed ' + action, flush=True)
        references = {item['path']: item for item in observations[-1]['response']['artifacts']}
        assert {'outputs/routed.kicad_pcb', 'outputs/erc.json', 'outputs/drc.json', 'outputs/routing.json'} <= references.keys()
        assert any(path.endswith('.gbr') for path in references)
        assert any(path.endswith('.drl') for path in references)
        routing = json.loads(artifacts.blobs.get(references['outputs/routing.json']['sha256']))
        assert routing['references'] == ['J1', 'R1'] and routing['trackCount'] >= 2
        assert routing['conceptReferences'] == CONTRACT['conceptReferences']
        assert routing['constraints'] == CONTRACT['constraints']
        assert routing['boardSha256'] == references['outputs/routed.kicad_pcb']['sha256']
        erc = json.loads(artifacts.blobs.get(references['outputs/erc.json']['sha256']))
        drc = json.loads(artifacts.blobs.get(references['outputs/drc.json']['sha256']))
        assert erc['sheets'] and all(sheet['violations'] == [] for sheet in erc['sheets'])
        assert drc['violations'] == [] and drc['unconnected_items'] == []
        with zipfile.ZipFile(io.BytesIO(artifacts.blobs.get(references['outputs/pcb-project.zip']['sha256']))) as archive:
            assert {'board.kicad_pcb', 'circuit.kicad_sch', 'board.kicad_pro', 'board.kicad_dru',
                    'fp-lib-table', 'sym-lib-table', 'Fixture.pretty/TwoPin.kicad_mod'} <= set(archive.namelist())
            assert digest(archive.read('board.kicad_pcb')) == references['outputs/routed.kicad_pcb']['sha256']
        for ref in references.values():
            content = artifacts.blobs.get(ref['sha256'])
            assert len(content) == ref['size'] and digest(content) == ref['sha256']
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
                containers.append({'image': inspected['Image'], 'networkMode': inspected['HostConfig']['NetworkMode'],
                    'readonlyRootfs': inspected['HostConfig']['ReadonlyRootfs'], 'memory': inspected['HostConfig']['Memory'],
                    'nanoCpus': inspected['HostConfig']['NanoCpus'], 'pidsLimit': inspected['HostConfig']['PidsLimit']})
                await docker.stop(job.id)
                await docker.cleanup(job.id)
        report = {'qualified': success, 'imageId': args.image, 'protocol': 'kleos-native-session-v2', 'lane': 'pcb',
                  'deployment': 'local', 'sourceOverride': False, 'qualificationKind': 'exact-image',
                  'networkPolicy': 'in-process-seccomp' if args.strict_linux else 'docker-desktop-daemon-seccomp',
                  'parentImageId': args.parent_image, 'daemonSeccompSha256': fingerprint(daemon_socket_profile()),
                  'finishedAt': datetime.now(UTC).isoformat(), 'fixtureSourcesSha256': fingerprint(sources),
                  'circuitContract': CONTRACT, 'circuitContractHash': fingerprint(CONTRACT),
                  'checks': observations, 'negativeChecks': negatives, 'containers': containers,
                  'limitations': ['Two-layer passive circuit fixture only', 'No physical fabrication or acceptance']}
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
