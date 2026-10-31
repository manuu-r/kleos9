"""Qualify existing application fixture with job-private writable toolchain state."""
import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='kleos-application:development')
    parser.add_argument('--source-override', action='store_true')
    options = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    identity = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', options.image], text=True).strip()
    profile = json.loads((backend / 'kleos/native-resources.json').read_text())['application']
    name = 'kleos-readonly-web-' + uuid4().hex
    command = ['docker', 'run', '--rm', '--pull', 'never', '--name', name, '--platform', 'linux/amd64',
        '--network', 'none', '--read-only', '--memory', f'{profile["memoryMiB"]}m',
        '--cpus', str(profile['cpuUnits'] / 1024), '--pids-limit', str(profile['pidsLimit']),
        '--tmpfs', f'/tmp:rw,exec,nosuid,size={profile["tmpMiB"] * 1024**2}',
        '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--cap-add', 'SETUID', '--cap-add', 'SETGID',
        '--cap-add', 'KILL', '--cap-add', 'DAC_OVERRIDE', '--security-opt', 'no-new-privileges:true',
        '--env', 'KLEOS_NATIVE_DOCKER_NETWORK_NONE=1',
        '--mount', f'type=bind,source={backend / "docker/smoke_readonly_application_runtime.py"},target=/readonly-proof.py,readonly',
        '--mount', f'type=bind,source={backend / "docker/smoke_native_network.py"},target=/application-fixture.py,readonly',
        '--mount', f'type=bind,source={backend / "docker/templates"},target=/fixtures,readonly']
    if options.source_override:
        for target in ('/opt/kleos/kleos', '/usr/local/lib/python3.12/site-packages/kleos'):
            command += ['--mount', f'type=bind,source={backend / "kleos"},target={target},readonly']
    started = time.monotonic()
    try:
        process = subprocess.run([*command, '--entrypoint', 'python', identity, '/readonly-proof.py'],
                                 capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        subprocess.run(['docker', 'rm', '--force', name], capture_output=True, text=True, timeout=30)
        raise
    if process.returncode:
        proof = {'status': 'failed', 'logs': (process.stderr + process.stdout)[-20000:]}
    else:
        proof = json.loads(process.stdout.strip().splitlines()[-1])
    report = {'image': options.image, 'imageId': identity, 'sourceOverride': options.source_override,
              'checkedAt': datetime.now(UTC).isoformat(), 'containerWallSeconds': time.monotonic() - started,
              'profile': profile, **proof}
    destination = backend / 'verification/readonly-app-runtime.json'
    destination.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'report': str(destination), 'modelCalls': 0}))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
