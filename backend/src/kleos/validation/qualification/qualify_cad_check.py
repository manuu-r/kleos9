"""Explicit local-only independent checker qualification using already-exported files."""
import argparse
import asyncio
import copy
import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from kleos.execution.docker import DockerClient
from kleos.execution.security import daemon_socket_profile
from kleos.validation.cad import IsolatedCadProcess
from kleos.validation.native_check import PROTOCOL


async def qualify(args):
    docker = DockerClient(socket=args.socket)
    containers = {}
    original_get = docker.client.get

    async def inspect_get(url, **kwargs):
        response = await original_get(url, **kwargs)
        if str(url).startswith('/containers/') and str(url).endswith('/json') and response.status_code == 200:
            value = response.json()
            host = value['HostConfig']
            containers[value['Id']] = {'image': value['Image'], 'readonlyRootfs': host['ReadonlyRootfs'],
                                       'networkMode': host['NetworkMode'], 'memoryBytes': host['Memory'],
                                       'nanoCpus': host['NanoCpus'], 'pidsLimit': host['PidsLimit'],
                                       'user': value['Config']['User'], 'capDrop': host['CapDrop'],
                                       'readonlyInput': all(not mount['RW'] for mount in value['Mounts']),
                                       'seccompMatches': 'seccomp='+json.dumps(daemon_socket_profile(), separators=(',', ':')) in host['SecurityOpt']}
        return response

    docker.client.get = inspect_get
    checker = IsolatedCadProcess(docker, args.image)
    work = Path(tempfile.mkdtemp(prefix='kleos-independent-cad-'))
    work.chmod(0o755)
    source_hash = hashlib.sha256(Path(__file__).parent.parent.joinpath('native_check.py').read_bytes()).hexdigest()
    generator = json.loads(args.generator_report.read_text())
    exports = [a for a in generator['checks'][-1]['response']['artifacts'] if a['path'].endswith(('.step', '.stl'))]
    request = {'protocol': PROTOCOL, 'units': 'mm', 'subjectHash': 'a'*64, 'files': [],
               'constraints': [{'part': 'outputs/base', 'metric': 'extent_x_mm', 'expected': 24, 'tolerance': .001},
                               {'part': 'outputs/base', 'metric': 'extent_y_mm', 'expected': 16, 'tolerance': .001},
                               {'part': 'outputs/lid', 'metric': 'extent_z_mm', 'expected': 2, 'tolerance': .001}],
               'unsupportedRequirements': []}
    for artifact in exports:
        data = (args.blobs/artifact['sha256']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == artifact['sha256']
        (work/artifact['sha256']).write_bytes(data)
        (work/artifact['sha256']).chmod(0o644)
        request['files'].append({'part': str(Path(artifact['path']).with_suffix('')),
                                 'kind': Path(artifact['path']).suffix[1:],
                                 'sha256': artifact['sha256'], 'size': len(data)})
    report = {'qualified': False, 'imageId': args.image, 'protocol': PROTOCOL,
              'sourceOverride': False, 'deployment': 'local', 'qualificationKind': 'exact-image',
              'networkPolicy': 'docker-desktop-daemon-seccomp', 'checkerSourceSha256': source_hash,
              'scope': 'Independent STEP/STL export integrity and explicit extents for one two-part fixture',
              'passedCases': [], 'cases': [], 'inputArtifacts': exports,
              'daemonSeccompSha256': hashlib.sha256(json.dumps(daemon_socket_profile(), sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
              'limits': {'memoryBytes': 2*1024**3, 'cpuCount': 2, 'pids': 64, 'timeoutSeconds': 120},
              'limitations': ['Not manufacturing, stress, fit, material, electrical, physical or arbitrary-device acceptance',
                              'Mesh intersection and STEP/STL surface correspondence beyond bounds/volume are not qualified',
                              'Only explicit STEP SI millimetres and paired closed oriented STL meshes',
                              'Local Docker Desktop only; no ECS qualification']}
    try:
        for case, expected in [('valid_exports', 'PASS'), ('dimension_mismatch', 'FAIL'),
                               ('digest_mismatch', 'FAIL'), ('unsupported_requirement', 'UNKNOWN'),
                               ('wrong_units', 'UNKNOWN'), ('mesh_mismatch', 'FAIL')]:
            req = copy.deepcopy(request)
            req['executionId'] = 'qualification-'+uuid4().hex
            if case == 'dimension_mismatch':
                req['constraints'][0]['expected'] = 25
            if case == 'unsupported_requirement':
                req['unsupportedRequirements'] = ['Fatigue strength under repeated load']
            if case == 'digest_mismatch':
                req['files'][0]['size'] += 1
            if case == 'wrong_units':
                item = req['files'][0]
                wrong = (work/item['sha256']).read_bytes().replace(b'.MILLI.,.METRE.', b'.CENTI.,.METRE.')
                assert wrong != (work/item['sha256']).read_bytes()
                item['sha256'], item['size'] = hashlib.sha256(wrong).hexdigest(), len(wrong)
                (work/item['sha256']).write_bytes(wrong)
                (work/item['sha256']).chmod(0o644)
            if case == 'mesh_mismatch':
                base = next(a for a in req['files'] if a['kind'] == 'stl' and a['part'] == 'outputs/base')
                lid = next(a for a in req['files'] if a['kind'] == 'stl' and a['part'] == 'outputs/lid')
                base['sha256'], base['size'] = lid['sha256'], lid['size']
            (work/'request.json').write_text(json.dumps(req))
            (work/'request.json').chmod(0o644)
            result = await checker.run(work, req['executionId'])
            report['cases'].append({'case': case, 'expected': expected, 'result': result})
            assert result['checkerSourceSha256'] == source_hash
            assert result['executionId'] == req['executionId'] and result['subjectHash'] == req['subjectHash']
            assert result['status'] == expected, f'{case}: {result}'
            if case == 'valid_exports':
                assert result['isolation']['generatorCodeAbsent'] and result['isolation']['internetSocketDenied']
                assert result['measurements']['outputs/lid']['step']['volumeMm3'] == 768
            report['passedCases'].append(case)
            print(case+' '+expected, flush=True)
        assert len(containers) == 6
        for container in containers.values():
            assert container['image'] == args.image
            assert container['readonlyRootfs'] and container['readonlyInput'] and container['seccompMatches']
            assert container['networkMode'] == 'none' and container['user'] == '10001:10001'
            assert container['memoryBytes'] == 2*1024**3 and container['nanoCpus'] == 2_000_000_000
            assert container['pidsLimit'] == 64 and container['capDrop'] == ['ALL']
        report['containerChecksPassed'] = True
        report['qualified'] = True
    finally:
        report['containers'] = list(containers.values())
        report['finishedAt'] = datetime.now(UTC).isoformat()
        args.report.write_text(json.dumps(report, indent=2)+'\n')
        await docker.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--socket', default='/var/run/docker.sock')
    parser.add_argument('--blobs', type=Path, required=True)
    parser.add_argument('--generator-report', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    asyncio.run(qualify(parser.parse_args()))
