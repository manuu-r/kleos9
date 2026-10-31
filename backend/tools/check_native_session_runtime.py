"""Qualify bounded native sessions offline with the existing local CAD image."""
import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='kleos-mechanics:development')
    parser.add_argument('--source-override', action='store_true')
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    identity = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', options.image], text=True).strip()
    command = ['docker', 'run', '--rm', '--pull', 'never', '--platform', 'linux/amd64', '--network', 'none', '--read-only',
        '--memory', '2g', '--cpus', '2', '--pids-limit', '256', '--tmpfs', '/tmp:rw,exec,nosuid,size=1073741824',
        '--env', 'KLEOS_NATIVE_DOCKER_NETWORK_NONE=1',
        '--tmpfs', '/control:rw,nosuid,mode=0700,size=134217728',
        '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--cap-add', 'SETUID', '--cap-add', 'SETGID', '--cap-add', 'KILL',
        '--cap-add', 'DAC_OVERRIDE', '--security-opt', 'no-new-privileges:true',
        '--mount', f'type=bind,source={root / "docker/smoke_native_session.py"},target=/smoke_native_session.py,readonly']
    if options.source_override:
        for target in ('/opt/kleos/kleos', '/usr/local/lib/python3.12/site-packages/kleos'):
            command += ['--mount', f'type=bind,source={root / "kleos"},target={target},readonly']
    process = subprocess.run([*command, '--entrypoint', 'python', identity, '/smoke_native_session.py'],
                             capture_output=True, text=True, timeout=360)
    if process.returncode:
        raise RuntimeError(process.stderr[-10000:] + process.stdout[-10000:])
    proof = json.loads(process.stdout.strip().splitlines()[-1])
    report = {'image': options.image, 'imageId': identity, 'sourceOverride': options.source_override,
              'checkedAt': datetime.now(UTC).isoformat(), **proof}
    destination = root / 'verification/native-session-runtime.json'
    destination.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'report': str(destination), 'modelCalls': 0}))


if __name__ == '__main__':
    main()
