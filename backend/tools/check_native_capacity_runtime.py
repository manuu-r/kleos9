"""Measure cached local worker startup and bounded fixture resource use, without pulls."""
import argparse
import json
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

IMAGES = {lane: f'kleos-{lane}:development' for lane in ('mechanics', 'electronics', 'firmware', 'application')}
IMAGES['validate'] = 'kleos-validator:development'
FIXTURES = {'mechanics': ('smoke_native_session.py', []), 'electronics': ('smoke_compiled_circuit.py', []),
            'firmware': ('smoke_native_network.py', ['firmware']), 'validate': ('smoke_validator.py', [])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--lanes', nargs='+', choices=list(IMAGES), default=list(IMAGES))
    parser.add_argument('--noop-repeats', type=int, choices=range(1, 11), default=3)
    parser.add_argument('--source-override', action='store_true')
    parser.add_argument('--skip-builds', action='store_true')
    parser.add_argument('--merge-existing', action='store_true')
    options = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    profiles = json.loads((backend / 'kleos/native-resources.json').read_text())
    daemon_arch = subprocess.check_output(['docker', 'info', '--format', '{{.Architecture}}'], text=True).strip()
    records = []
    for lane in options.lanes:
        image = IMAGES[lane]
        inspected = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True))[0]
        profile = profiles.get(lane, profiles['_default'])
        identifier, architecture = inspected['Id'], inspected['Architecture']
        record = {'lane': lane, 'image': image, 'imageId': identifier, 'architecture': architecture,
            'imageSizeBytes': inspected['Size'],
            'imageSizeKind': 'Docker image inspect Size; storage backend accounting may differ from docker images',
            'profile': profile, 'sourceOverride': options.source_override,
            'coldEcrPull': {'status': 'UNKNOWN', 'seconds': None, 'reason': 'Existing local images only; no pull or ECR transfer measured'}}
        def run(entrypoint, arguments, *, measured=False):
            name = 'kleos-capacity-' + uuid4().hex
            command = ['docker', 'run', '--rm', '--pull', 'never', '--name', name,
                '--platform', f'linux/{architecture}', '--network', 'none', '--read-only', '--user', '0',
                '--memory', f'{profile["memoryMiB"]}m', '--cpus', str(profile['cpuUnits'] / 1024),
                '--pids-limit', str(profile['pidsLimit']),
                '--tmpfs', f'/tmp:rw,exec,nosuid,size={profile["tmpMiB"] * 1024**2}',
                '--tmpfs', '/control:rw,nosuid,mode=0700,size=134217728',
                '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--cap-add', 'SETUID', '--cap-add', 'SETGID',
                '--cap-add', 'KILL', '--cap-add', 'DAC_OVERRIDE', '--security-opt', 'no-new-privileges:true']
            if architecture == 'amd64':
                command += ['--env', 'KLEOS_NATIVE_DOCKER_NETWORK_NONE=1']
            if options.source_override:
                for target in ('/opt/kleos/kleos', '/usr/local/lib/python3.12/site-packages/kleos'):
                    command += ['--mount', f'type=bind,source={backend / "kleos"},target={target},readonly']
            if measured:
                fixture = FIXTURES[lane][0]
                command += ['--mount', f'type=bind,source={backend / "docker/smoke_native_capacity.py"},target=/capacity.py,readonly',
                    '--mount', f'type=bind,source={backend / "docker" / fixture},target=/native-fixture.py,readonly',
                    '--mount', f'type=bind,source={backend / "docker/templates"},target=/fixtures,readonly']
            started = time.monotonic()
            try:
                result = subprocess.run([*command, '--entrypoint', entrypoint, identifier, *arguments],
                                        capture_output=True, text=True, timeout=600 if measured else 30)
            except subprocess.TimeoutExpired:
                subprocess.run(['docker', 'rm', '--force', name], capture_output=True, text=True, timeout=30)
                return {'status': 'failed', 'wallSeconds': time.monotonic() - started, 'failure': 'Owned test container exceeded deadline'}
            wall = time.monotonic() - started
            if measured:
                try:
                    proof = json.loads(result.stdout.strip().splitlines()[-1])
                except (ValueError, IndexError):
                    proof = {'failure': (result.stderr + result.stdout)[-20000:]}
                return {**proof, 'status': 'passed' if result.returncode == 0 and proof.get('status') == 'passed' else 'failed',
                        'containerWallSeconds': wall}
            return {'status': 'passed' if result.returncode == 0 else 'failed', 'wallSeconds': wall,
                    **({'failure': result.stderr[-4000:]} if result.returncode else {})}
        noops = [run('/bin/true', []) for _ in range(options.noop_repeats)]
        timings = [item['wallSeconds'] for item in noops if item['status'] == 'passed']
        record['cachedNoop'] = {'command': ['/bin/true'], 'samples': noops,
            'medianWallSeconds': statistics.median(timings) if timings else None,
            'minimumWallSeconds': min(timings) if timings else None,
            'scope': 'Local Docker create/start/wait/remove wall time with an already-present image; not ECS launch or registry pull'}
        print(json.dumps({'lane': lane, 'imageSizeBytes': record['imageSizeBytes'],
                          'cachedNoopMedianSeconds': record['cachedNoop']['medianWallSeconds']}), flush=True)
        if lane == 'application':
            proof_path = backend / 'verification/companion-apk-runtime.json'
            proof = json.loads(proof_path.read_text()) if proof_path.is_file() else {}
            if (proof.get('status') == 'passed' and proof.get('imageId') == identifier
                    and proof.get('profile') == profile and not proof.get('sourceOverride')):
                record['build'] = {'status': 'passed', 'evidence': str(proof_path.relative_to(backend)),
                    'usage': proof.get('nativeResourceUsage', {}),
                    'nativeExecutionSeconds': proof.get('nativeExecutionSeconds'),
                    'qualificationHelperUsage': proof.get('usage', {}), 'scratch': proof.get('scratch', {}),
                    'containerWallSeconds': proof.get('containerWallSeconds'),
                    'scope': 'Existing exact-image offline web and ARM64 APK qualification; no extra build run'}
            else:
                record['build'] = {'status': 'not_measured',
                    'reason': 'Run check_companion_apk_runtime.py for this exact image and resource profile first'}
        elif options.skip_builds:
            record['build'] = {'status': 'not_measured'}
        else:
            _, arguments = FIXTURES[lane]
            record['build'] = run('python', ['/capacity.py', '/native-fixture.py', *arguments], measured=True)
            print(json.dumps({'lane': lane, 'build': record['build']['status'],
                              'usage': record['build'].get('usage', {})}), flush=True)
        records.append(record)
    target = backend / 'verification/native-capacity-runtime.json'
    if options.merge_existing:
        previous = json.loads(target.read_text())
        if previous.get('schemaVersion') != 'native-capacity-runtime-v1':
            raise ValueError('Cannot merge a different capacity report schema')
        combined = {record['lane']: record for record in previous['records']}
        for record in records:
            record['recheckedAt'] = datetime.now(UTC).isoformat()
            combined[record['lane']] = record
        records = [combined[lane] for lane in IMAGES if lane in combined]
    failed = any(sample['status'] != 'passed' for record in records for sample in record['cachedNoop']['samples']) or any(
        record['build']['status'] == 'failed' for record in records)
    report = {'schemaVersion': 'native-capacity-runtime-v1', 'status': 'failed' if failed else 'passed',
        'checkedAt': datetime.now(UTC).isoformat(), 'dockerDaemonArchitecture': daemon_arch,
        'modelCalls': 0, 'imagesPulled': 0, 'imagesPublished': 0, 'imagesPruned': 0, 'records': records,
        'interpretation': {'coldPullOptimization': 'UNKNOWN until actual ECR transfer/launch timestamps are measured; cached no-op timing cannot predict cold pulls',
            'resourceSizing': 'Small existing qualification fixtures are a baseline, not worst-case or growth/load qualification',
            'cpuPeak': 'Sampled cgroup CPU deltas; cumulative CPU seconds and kernel memory peaks are reported separately',
            'deployment': 'Source override evidence must be rerun after final image refresh; local amd64 emulation is not native Fargate performance'}}
    target.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'report': str(target), 'modelCalls': 0}))
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
