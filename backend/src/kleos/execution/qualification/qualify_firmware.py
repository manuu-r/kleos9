"""Explicit isolated Zephyr build qualification. No device or emulator execution."""
import argparse
import asyncio
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, TaskSpec
from kleos.execution.docker import DockerClient, NativeSettings, Resources
from kleos.execution.gateway import NativeGateway, NativeJob
from kleos.execution.protocol import digest, fingerprint
from kleos.execution.security import daemon_socket_profile
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import ProjectStore

BOARD = 'nrf52840dk/nrf52840'
SOURCES = {
    'CMakeLists.txt': '''cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(kleos_fixture)
target_sources(app PRIVATE src/main.c)
''',
    'prj.conf': 'CONFIG_BUILD_OUTPUT_BIN=y\nCONFIG_PRINTK=y\n',
    'src/main.c': '''#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
int main(void) {
    printk("Kleos firmware build fixture\\n");
    while (1) { k_msleep(1000); }
    return 0;
}
''',
}


async def qualify(args):
    work = Path(tempfile.mkdtemp(prefix='kleos-firmware-qualification-'))
    store = ProjectStore(f'sqlite:///{work}/state.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='qualification', user_id='fixture')
    project = store.create(actor, 'Fresh Zephyr compile fixture', 'create')
    with store.transaction() as session:
        row = store.authorized(session, actor, project['projectId'], write=True)
        row.data = {**row.data, 'interfaces': {'firmware': {'board': BOARD}}}
    receipt = store.submit(actor, project['projectId'], 'Compile explicitly selected board', 1, 'objective')
    scope = SimpleNamespace(actor=actor, project_id=project['projectId'], run_id=receipt['runId'], revision=2, compute_budget_seconds=None,
                            deadline_at=datetime.now(UTC) + timedelta(minutes=10))
    task = TaskSpec(id='trusted-firmware-fixture', objective='Compile explicit board without executing firmware',
                    capability='firmware', allowed_paths=sorted(SOURCES), max_actions=8)
    docker = DockerClient(socket=args.socket)
    artifacts = ArtifactService(store, BlobStore(work / 'blobs'))
    settings = NativeSettings(work / 'control', {'firmware': args.image}, resources=Resources(),
        session_seconds=600, docker_desktop_compatibility=not args.strict_linux, deployment='local', firmware_boards=(BOARD,))
    gateway = NativeGateway(store, artifacts, settings, docker, GraphRepository(store))
    observations, success = [], False
    try:
        for number, (path, source) in enumerate(SOURCES.items()):
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
        elf = artifacts.blobs.get(references['outputs/firmware.elf']['sha256'])
        binary = artifacts.blobs.get(references['outputs/firmware.bin']['sha256'])
        assert elf[:4] == b'\x7fELF' and elf[4] == 1 and elf[5] == 1
        assert int.from_bytes(elf[18:20], 'little') == 40  # ELF EM_ARM, compile target only.
        assert 1024 < len(binary) < 1024 * 1024
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
                containers.append({'image': inspected['Image'], 'networkMode': inspected['HostConfig']['NetworkMode'],
                    'readonlyRootfs': inspected['HostConfig']['ReadonlyRootfs'], 'memory': inspected['HostConfig']['Memory'],
                    'nanoCpus': inspected['HostConfig']['NanoCpus'], 'pidsLimit': inspected['HostConfig']['PidsLimit']})
                await docker.stop(job.id)
                await docker.cleanup(job.id)
        report = {'qualified': success, 'imageId': args.image, 'protocol': 'kleos-native-session-v2', 'lane': 'firmware',
            'deployment': 'local', 'sourceOverride': False, 'qualificationKind': 'exact-image', 'firmwareBoard': BOARD,
            'networkPolicy': 'linux-supervisor-seccomp' if args.strict_linux else 'docker-desktop-daemon-seccomp',
            'parentImageId': args.parent_image, 'daemonSeccompSha256': fingerprint(daemon_socket_profile()),
            'finishedAt': datetime.now(UTC).isoformat(), 'fixtureSourcesSha256': fingerprint(SOURCES),
            'checks': observations, 'containers': containers,
            'limitations': ['Compile-only nrf52840dk/nrf52840 fixture', 'Firmware was not executed',
                            'No flashing, device behavior, emulator or physical acceptance qualification']}
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
