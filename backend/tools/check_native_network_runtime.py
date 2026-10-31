"""Qualify network filtering and selected real toolchains without inference."""
import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--images', nargs='+', default=['kleos-mechanics:development', 'kleos-electronics:development',
                                                       'kleos-firmware:development', 'kleos-application:development'])
    parser.add_argument('--source-override', action='store_true')
    parser.add_argument('--with-build-smokes', action='store_true')
    parser.add_argument('--docker-network-none-fallback', action='store_true')
    parser.add_argument('--application-ipc', action='store_true')
    options = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    records, builds = [], []
    def execute(image, fixture, argument=None):
        identity = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
        architecture = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Architecture}}', image], text=True).strip()
        fallback = options.docker_network_none_fallback and architecture == 'amd64'
        command = ['docker', 'run', '--rm', '--pull', 'never', '--platform', f'linux/{architecture}', '--network', 'none', '--read-only',
            '--user', '0',
            '--memory', '3g', '--cpus', '2', '--pids-limit', '256', '--tmpfs', '/tmp:rw,exec,nosuid,size=1073741824',
            '--tmpfs', '/control:rw,nosuid,mode=0700,size=134217728', '--cap-drop', 'ALL', '--cap-add', 'CHOWN',
            '--cap-add', 'SETUID', '--cap-add', 'SETGID', '--cap-add', 'KILL', '--cap-add', 'DAC_OVERRIDE',
            '--security-opt', 'no-new-privileges:true',
            '--mount', f'type=bind,source={backend / "docker" / fixture},target=/fixture.py,readonly',
            '--mount', f'type=bind,source={backend / "docker/templates"},target=/fixtures,readonly']
        if fallback:
            command += ['--env', 'KLEOS_NATIVE_DOCKER_NETWORK_NONE=1']
        if options.source_override:
            for target in ('/opt/kleos/kleos', '/usr/local/lib/python3.12/site-packages/kleos'):
                command += ['--mount', f'type=bind,source={backend / "kleos"},target={target},readonly']
        process = subprocess.run([*command, '--entrypoint', 'python', identity, '/fixture.py', *([argument] if argument else [])],
                                 capture_output=True, text=True, timeout=360)
        record = {'image': image, 'imageId': identity, 'architecture': architecture, 'fixture': fixture,
                  'sourceOverride': options.source_override, 'dockerNetworkNoneFallbackAuthorized': fallback}
        if process.returncode:
            return {**record, 'status': 'failed', 'logs': (process.stderr + process.stdout)[-20000:]}
        return {**record, 'status': 'passed', **json.loads(process.stdout.strip().splitlines()[-1])}
    for image in options.images:
        record = execute(image, 'smoke_native_network.py', 'application_ipc' if options.application_ipc else 'network')
        records.append(record)
        print(json.dumps({'image': image, 'network': record['status']}), flush=True)
    if options.with_build_smokes:
        fixtures = {'kleos-mechanics:development': ('smoke_native_session.py', None),
                    'kleos-electronics:development': ('smoke_compiled_circuit.py', None),
                    'kleos-firmware:development': ('smoke_native_network.py', 'firmware'),
                    'kleos-application:development': ('smoke_native_network.py', 'application')}
        for image in options.images:
            if image not in fixtures:
                continue
            fixture, argument = fixtures[image]
            record = execute(image, fixture, argument)
            builds.append(record)
            print(json.dumps({'image': image, 'build': record['status']}), flush=True)
    report = {'schemaVersion': 'native-application-ipc-runtime-v1' if options.application_ipc else 'native-network-runtime-v1',
              'checkedAt': datetime.now(UTC).isoformat(),
              'status': 'passed' if all(item['status'] == 'passed' for item in [*records, *builds]) else 'failed',
              'networkStatus': 'passed' if all(item['status'] == 'passed' for item in records) else 'failed',
              'buildCompatibilityStatus': ('passed' if all(item['status'] == 'passed' for item in builds) else 'failed') if builds else 'not_checked',
              'modelCalls': 0, 'network': records, 'buildCompatibility': builds,
              'scope': ('Application seccomp permits AF_UNIX/AF_INET/AF_INET6/AF_NETLINK; destinations remain governed by task namespace/VPC policy, not this filter.'
                        if options.application_ipc else
                        'Native seccomp permits only AF_UNIX; explicitly authorized emulated Docker workers instead prove network-none namespace isolation. Modes are recorded separately.')}
    target = backend / ('verification/native-application-ipc-runtime.json' if options.application_ipc else 'verification/native-network-runtime.json')
    target.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'report': str(target), 'modelCalls': 0}))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
