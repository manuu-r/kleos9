"""Opt-in serial exact-image firmware/application export checker qualification. No host engineering execution."""
import argparse
import asyncio
import copy
import hashlib
import io
import json
import stat
import struct
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from kleos.contracts.models import Actor
from kleos.execution.capacity import CapacityService
from kleos.execution.docker import DockerClient
from kleos.execution.security import daemon_socket_profile
from kleos.projects.store import ProjectStore, fingerprint
from kleos.validation.export_check import BOARD, PROTOCOL
from kleos.validation.export_process import CHECKER_RESOURCES, IsolatedExportProcess
from kleos.validation.exports import EXPECTED_CASES, source_hashes


def fixtures(args):
    if args.lane == 'firmware':
        if not args.elf or not args.binary:
            raise ValueError('Real qualified firmware ELF/BIN paths are required')
        return {'outputs/firmware.elf': args.elf.read_bytes(), 'outputs/firmware.bin': args.binary.read_bytes()}
    if not args.web:
        raise ValueError('Real qualified Flutter web archive is required')
    return {'outputs/companion-web.zip': args.web.read_bytes()}


def mutate(source, case):
    source = dict(source)
    if 'outputs/firmware.elf' in source:
        elf, binary = bytearray(source['outputs/firmware.elf']), bytearray(source['outputs/firmware.bin'])
        if case == 'wrong_machine':
            struct.pack_into('<H', elf, 18, 62)
        elif case == 'truncated_elf':
            elf = elf[:100]
        elif case == 'binary_mismatch':
            binary[-1] ^= 1
        elif case == 'memory_overflow':
            phoff = struct.unpack_from('<I', elf, 28)[0]
            phnum = struct.unpack_from('<H', elf, 44)[0]
            for index in range(phnum):
                if struct.unpack_from('<I', elf, phoff+index*32)[0] == 1:
                    struct.pack_into('<I', elf, phoff+index*32+8, 0x30000000)
                    break
            else:
                raise ValueError('Trusted fixture has no load segment')
        elif case in {'invalid_vectors', 'unsupported_section'}:
            shoff = struct.unpack_from('<I', elf, 32)[0]
            shnum = struct.unpack_from('<H', elf, 48)[0]
            for index in range(shnum):
                section = shoff+index*40
                _, typ, flags, address, off, size, *_ = struct.unpack_from('<10I', elf, section)
                if typ == 1 and flags & 2 and size and address == 0:
                    if case == 'invalid_vectors':
                        elf[off:off+4] = b'\0'*4
                        binary[:4] = b'\0'*4
                    else:
                        struct.pack_into('<I', elf, section+4, 0x70000002)
                    break
            else:
                raise ValueError('Trusted firmware has no allocated vectors section')
        source.update({'outputs/firmware.elf': bytes(elf), 'outputs/firmware.bin': bytes(binary)})
        return source
    raw = source['outputs/companion-web.zip']
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        files = {entry.filename: zipped.read(entry) for entry in zipped.infolist() if not entry.is_dir()}
    extra = []
    if case == 'missing_bootstrap':
        files.pop('flutter_bootstrap.js')
    elif case == 'missing_font':
        fonts = json.loads(files['assets/FontManifest.json'])
        asset = next(row['fonts'][0]['asset'] for row in fonts if row['family'] == 'Roboto')
        files.pop('assets/'+asset)
    elif case == 'path_traversal':
        files['../escape'] = b'escape'
    elif case == 'symlink':
        entry = zipfile.ZipInfo('link')
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        extra.append((entry, b'index.html'))
    elif case == 'duplicate_path':
        extra.append(('index.html', files['index.html']))
    elif case == 'expansion_limit':
        files['bomb'] = b'x'*(2*1024**2)
    elif case == 'nul_filename':
        files['index.htmlXevil'] = files.pop('index.html')
    elif case == 'file_directory_collision':
        files['assets'] = b'not a directory'
    elif case == 'case_prefix_collision':
        files['Assets/other'] = b'alias'
    elif case == 'alternate_zip_name':
        entry = zipfile.ZipInfo('alternate')
        entry.extra = struct.pack('<HH', 0x7075, 0)
        extra.append((entry, b'alias'))
    elif case == 'duplicate_html_attribute':
        files['index.html'] = b'<script src="https://example.invalid/bad.js" src="flutter_bootstrap.js"></script>'
    elif case == 'external_entry_resource':
        files['index.html'] = b'<script src="https://example.invalid/bad.js"></script>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zipped:
        for name, data in [*files.items(), *extra]:
            zipped.writestr(name, data)
    # Positive case preserves the exact qualified builder's bytes, including metadata.
    raw = raw if case in {'valid_web', 'digest_mismatch', 'parser_timeout'} else out.getvalue()
    if case == 'nul_filename':
        raw = raw.replace(b'index.htmlXevil', b'index.html\0evil')
    elif case == 'invalid_crc':
        raw = bytearray(raw)
        index = raw.index(b'PK\x01\x02')
        raw[index+16:index+20] = b'\xff'*4
        local = struct.unpack_from('<I', raw, index+42)[0]
        raw[local+14:local+18] = b'\xff'*4  # Matching headers, wrong actual payload CRC.
        raw = bytes(raw)
    source['outputs/companion-web.zip'] = raw
    return source


async def qualify(args):
    if args.output.exists() or args.work.exists():
        raise ValueError('Use new evidence and persistent work paths; never overwrite prior attempts')
    args.work.mkdir(parents=True, mode=0o755)
    store = ProjectStore(f'sqlite:///{args.work}/state.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='qualification', user_id='export-check-fixture')
    project = store.create(actor, 'Independent firmware/application export checker qualification', 'create')
    capacity = CapacityService(store, pool_id='isolated-export-check-qualification')
    docker = DockerClient(socket=args.socket)
    checker = IsolatedExportProcess(docker, args.image, lane=args.lane, capacity=capacity)
    report = {'schemaVersion': 'kleos-export-check-qualification-v1', 'qualified': False,
        'lane': args.lane, 'qualifiedBy': 'export-checker-qualification-runner', 'imageId': args.image, 'protocol': PROTOCOL,
        'deployment': 'local', 'sourceOverride': False, 'sourceHashes': source_hashes(),
        'networkPolicy': 'docker-desktop-daemon-seccomp', 'daemonSeccompSha256': fingerprint(daemon_socket_profile()),
        'resources': CHECKER_RESOURCES, 'watchdogSeconds': 175, 'startedAt': datetime.now(UTC).isoformat(),
        'caseStatuses': {}, 'cases': [], 'containers': {}, 'capacityLedger': str(args.work/'state.db'),
        'capacityScope': 'This persistent fixture ledger only; no cross-database capacity claim',
        'limitations': ['Local Docker Desktop only', 'NRF52840 ELF/BIN or strict Flutter ZIP data integrity only',
            'No source-build reproduction, firmware/app execution, security, functional or physical acceptance',
            'Watchdog case substitutes a trusted sleep workload under the exact fixed kill wrapper; no source override']}
    source = fixtures(args)
    report['fixtureFiles'] = {name: {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)} for name, raw in source.items()}
    report['fixtureHash'] = fingerprint(report['fixtureFiles'])
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
                    and 'HOME=/tmp' in config.get('Env', []) and 'HOSTNAME=kleos-export-checker' in config.get('Env', [])}
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
        selected_cases = {key: value for key, value in EXPECTED_CASES[args.lane].items() if not args.only or key == args.only}
        for case, expected in selected_cases.items():
            context.update(case=case, executionId='export-qual-'+uuid4().hex)
            folder = args.work/case
            folder.mkdir(mode=0o755)
            request = {'protocol': PROTOCOL, 'subjectHash': fingerprint({'fixture': report['fixtureHash'], 'case': case}),
                'executionId': context['executionId'], 'lane': args.lane,
                'firmwareTarget': {'board': 'other' if case == 'wrong_board' else BOARD}, 'files': []}
            for name, content in mutate(source, case).items():
                raw = content
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
                assert result['checkerSourceSha256'] == source_hashes()['export_check.py']
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
        report['qualificationPassed'] = set(selected_cases) == set(EXPECTED_CASES[args.lane])
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
    parser.add_argument('--lane', required=True, choices=['firmware', 'application'])
    parser.add_argument('--elf', type=Path)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--web', type=Path)
    parser.add_argument('--only')
    parser.add_argument('--socket', required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(0 if asyncio.run(qualify(parser.parse_args())) else 1)
