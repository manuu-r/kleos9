"""Opt-in serial exact-image PCB checker qualification. No host engineering execution."""
import argparse
import asyncio
import copy
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from kleos.contracts.models import Actor
from kleos.execution.capacity import CapacityService
from kleos.execution.docker import DockerClient
from kleos.execution.security import daemon_socket_profile
from kleos.projects.store import ProjectStore, fingerprint
from kleos.validation.pcb import EXPECTED_CASES, source_hashes
from kleos.validation.pcb_check import PROTOCOL, source_supported
from kleos.validation.pcb_process import CHECKER_RESOURCES, IsolatedPcbProcess


def fixtures():
    root = Path(__file__).parents[2]/'execution/qualification'
    source = json.loads((root/'pcb-fixture-sources.json').read_text())
    source = {key: value for key, value in source.items() if source_supported(key)}
    contract = json.loads((root/'pcb-exact-image.json').read_text())['circuitContract']
    tracks = '\n'.join(f'(segment (start 10 {y}) (end 30 {y}) (width 0.25) (layer "F.Cu") (net {net}) (uuid "{uuid4()}"))'
                       for y, net in [(12, 1), (17.08, 2)])
    source['outputs/routed.kicad_pcb'] = source['board.kicad_pcb'].rstrip()[:-1]+tracks+'\n)\n'
    return source, contract


def mutate(source, case):
    source = dict(source)
    routed = source['outputs/routed.kicad_pcb']
    swaps = {'wrong_reference': ('"R1"', '"R99"'), 'wrong_net': ('(net 1 "/NET_A")', '(net 2 "/NET_B")'),
        'moved_component': ('(at 30 12)', '(at 31 12)'), 'changed_outline': ('(end 40 0)', '(end 41 0)'),
        'pad_position_mismatch': ('(at 0 5.08)', '(at 0 5.5)'), 'pad_size_mismatch': ('(size 2 2)', '(size 2.2 2)'),
        'pad_layer_mismatch': ('(layers "*.Cu" "*.Mask")', '(layers "F.Cu" "F.Mask")')}
    if case in swaps:
        old, new = swaps[case]
        assert old in routed
        routed = routed.replace(old, new, 1)
    if case == 'clearance_violation':
        routed = routed.rstrip()[:-1]+f'(segment (start 10 17.08) (end 20 12.3) (width 0.25) (layer "F.Cu") (net 2) (uuid "{uuid4()}"))\n)'
    if case == 'unconnected_board':
        lines = routed.splitlines()
        routed = '\n'.join(line for line in lines if not line.startswith('(segment (start 10 17.08)'))
    if case == 'erc_violation':
        for name in ('circuit.kicad_sch', 'fixture.kicad_sym'):
            assert '(pin passive line' in source[name]
            source[name] = source[name].replace('(pin passive line', '(pin output line')
    if case == 'unsupported_zone':
        routed = routed.rstrip()[:-1]+f'''(zone (net 1) (net_name "/NET_A") (layer "F.Cu") (uuid "{uuid4()}")
          (hatch edge 0.5) (connect_pads (clearance 0.5)) (min_thickness 0.25)
          (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
          (polygon (pts (xy 2 2) (xy 8 2) (xy 8 8) (xy 2 8))))\n)'''
    if case == 'untrusted_rule_exemption':
        source['outputs/routed.kicad_pro'] = json.dumps({'board': {'design_settings': {'drc_exclusions': ['all']}}})
    source['outputs/routed.kicad_pcb'] = routed
    return source


async def qualify(args):
    if args.output.exists() or args.work.exists():
        raise ValueError('Use new evidence and persistent work paths; never overwrite prior attempts')
    args.work.mkdir(parents=True, mode=0o755)
    store = ProjectStore(f'sqlite:///{args.work}/state.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='qualification', user_id='pcb-check-fixture')
    project = store.create(actor, 'Independent PCB checker qualification', 'create')
    capacity = CapacityService(store, pool_id='isolated-pcb-check-qualification')
    docker = DockerClient(socket=args.socket)
    checker = IsolatedPcbProcess(docker, args.image, capacity=capacity)
    report = {'schemaVersion': 'kleos-pcb-check-qualification-v1', 'qualified': False,
        'qualifiedBy': 'pcb-checker-qualification-runner', 'imageId': args.image, 'protocol': PROTOCOL,
        'deployment': 'local', 'sourceOverride': False, 'sourceHashes': source_hashes(),
        'networkPolicy': 'docker-desktop-daemon-seccomp', 'daemonSeccompSha256': fingerprint(daemon_socket_profile()),
        'resources': CHECKER_RESOURCES, 'watchdogSeconds': 175, 'startedAt': datetime.now(UTC).isoformat(),
        'caseStatuses': {}, 'cases': [], 'containers': {}, 'capacityLedger': str(args.work/'state.db'),
        'capacityScope': 'This persistent fixture ledger only; no cross-database capacity claim',
        'limitations': ['Local Docker Desktop only', 'Straight outline, two copper layers and simple pads only',
            'No fabrication, functional, physical, Gerber/drill or arbitrary-device acceptance',
            'Watchdog case substitutes a trusted sleep workload under the exact fixed kill wrapper; no source override']}
    source, contract = fixtures()
    report['fixtureHash'] = fingerprint(source)
    context = {'case': None, 'executionId': None}
    original_get, original_post = docker.client.get, docker.client.post

    async def inspect_get(url, **kwargs):
        response = await original_get(url, **kwargs)
        if str(url).startswith('/containers/') and str(url).endswith('/json') and response.status_code == 200:
            item = response.json()
            host, config = item['HostConfig'], item['Config']
            report['containers'][item['Id']] = {
                'case': context['case'], 'image': item['Image'], 'running': item['State']['Running'],
                'exitCode': item['State'].get('ExitCode'), 'readonlyRootfs': host['ReadonlyRootfs'],
                'readonlyInput': all(not mount['RW'] for mount in item['Mounts']),
                'mountDestinations': [mount['Destination'] for mount in item['Mounts']],
                'networkMode': host['NetworkMode'], 'memoryBytes': host['Memory'], 'memorySwap': host['MemorySwap'],
                'nanoCpus': host['NanoCpus'], 'pidsLimit': host['PidsLimit'], 'tmpfs': host['Tmpfs'],
                'user': config['User'], 'capDrop': host['CapDrop'], 'capAdd': host['CapAdd'],
                'privileged': host['Privileged'], 'noNewPrivileges': 'no-new-privileges:true' in host['SecurityOpt'],
                'seccompMatches': 'seccomp='+json.dumps(daemon_socket_profile(), separators=(',', ':')) in host['SecurityOpt'],
                'entrypoint': config['Entrypoint'],
                'credentialEnvironmentEmpty': all(not entry.partition('=')[2] for entry in config.get('Env', [])
                                                  if entry.partition('=')[0] not in {'PATH', 'LANG', 'HOME', 'HOSTNAME'})
                    and 'HOME=/tmp' in config.get('Env', []) and 'HOSTNAME=kleos-pcb-checker' in config.get('Env', [])}
        return response

    async def inspect_post(url, **kwargs):
        if str(url) == '/containers/create':
            assert capacity.usage()['jobs'] == 1
            if context['case'] == 'parser_timeout':
                spec = copy.deepcopy(kwargs['json'])
                assert spec['Entrypoint'][:4] == ['/usr/bin/timeout', '--signal=KILL', '175', '/usr/bin/python3']
                spec['Entrypoint'] = spec['Entrypoint'][:5]+['-c', 'import time; time.sleep(600)']
                kwargs['json'] = spec
        return await original_post(url, **kwargs)

    docker.client.get, docker.client.post = inspect_get, inspect_post
    def persist():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    try:
        assert capacity.usage()['jobs'] == 0
        selected_cases = {key: value for key, value in EXPECTED_CASES.items() if not args.only or key == args.only}
        for case, expected in selected_cases.items():
            context.update(case=case, executionId='pcb-qual-'+uuid4().hex)
            folder = args.work/case
            folder.mkdir(mode=0o755)
            request = {'protocol': PROTOCOL, 'subjectHash': fingerprint({'fixture': report['fixtureHash'], 'case': case}),
                'executionId': context['executionId'], 'circuitContract': contract, 'circuitContractHash': fingerprint(contract), 'files': []}
            for name, content in mutate(source, case).items():
                raw = content.encode()
                checksum = hashlib.sha256(raw).hexdigest()
                (folder/checksum).write_bytes(raw)
                (folder/checksum).chmod(0o644)
                request['files'].append({'path': name, 'sha256': checksum, 'size': len(raw)})
            if case == 'digest_mismatch':
                request['files'][0]['size'] += 1
            (folder/'request.json').write_text(json.dumps(request, allow_nan=False))
            (folder/'request.json').chmod(0o644)
            start = time.monotonic()
            print('START '+case+' '+context['executionId'], flush=True)
            try:
                result = await checker.run(folder, context['executionId'], project_id=project['projectId'])
            except Exception as error:
                result = {'status': 'ERROR', 'failureClass': type(error).__name__, 'reason': str(error)[:1000]}
            elapsed = time.monotonic()-start
            report['cases'].append({'case': case, 'expected': expected, 'requestHash': fingerprint(request),
                'result': result, 'elapsedSeconds': elapsed, 'capacityAfter': capacity.usage()})
            report['caseStatuses'][case] = result['status']
            persist()
            assert result['status'] == expected, f'{case}: {result}'
            assert capacity.usage()['jobs'] == 0
            if case == 'parser_timeout':
                observations = [item for item in report['containers'].values() if item['case'] == case]
                assert 174 <= elapsed <= 185 and len(observations) == 1
                assert observations[0]['exitCode'] == 137 and observations[0]['running'] is False
            else:
                assert result['checkerSourceSha256'] == source_hashes()['pcb_check.py']
                assert result['subjectHash'] == request['subjectHash'] and result['executionId'] == request['executionId']
                assert all(result['isolation'][key] for key in ('generatorCodeAbsent', 'internetSocketDenied', 'credentialEnvironmentEmpty'))
            print('DONE '+case+' '+result['status']+' '+str(round(elapsed, 2))+'s', flush=True)
        for item in report['containers'].values():
            assert item['image'] == args.image and item['readonlyRootfs'] and item['readonlyInput']
            assert item['networkMode'] == 'none' and item['user'] == '10001:10001'
            assert item['memoryBytes'] == item['memorySwap'] == 2*1024**3
            assert item['nanoCpus'] == 2_000_000_000 and item['pidsLimit'] == 64
            assert item['capDrop'] == ['ALL'] and not item['capAdd'] and not item['privileged']
            assert item['seccompMatches'] and item['noNewPrivileges'] and item['credentialEnvironmentEmpty']
            assert item['mountDestinations'] == ['/input']
        assert len(report['containers']) == len(selected_cases)
        report['containerChecksPassed'] = True
        report['qualificationPassed'] = set(selected_cases) == set(EXPECTED_CASES)
        # Only independent review may promote this report to qualified installation evidence.
        report['awaitingIndependentReview'] = True
    except Exception as error:
        report['failure'] = {'type': type(error).__name__, 'message': str(error)[:3000]}
        print('FAILED '+report['failure']['message'], flush=True)
    finally:
        report['finishedAt'] = datetime.now(UTC).isoformat()
        report['capacityFinal'] = capacity.usage()
        persist()
        await docker.client.aclose()
        store.engine.dispose()
    return report.get('qualificationPassed') is True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--only', choices=sorted(EXPECTED_CASES))
    parser.add_argument('--socket', required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(0 if asyncio.run(qualify(parser.parse_args())) else 1)
