"""Qualify real APK/source delivery with an existing Android worker image offline."""
import argparse
import json
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='kleos-application:android-qualification')
    parser.add_argument('--source-override', action='store_true')
    parser.add_argument('--disk-backed-scratch', action='store_true')
    options = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    target = backend / 'verification/companion-apk-runtime.json'
    if options.disk_backed_scratch and target.is_file():
        previous = json.loads(target.read_text())
        history = backend / 'verification/companion-apk-tmpfs-runtime.json'
        if previous.get('memoryPressureEvidence') and not history.exists():
            with history.open('xb') as saved:
                saved.write(target.read_bytes())
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', options.image], text=True))[0]
    profile = json.loads((backend / 'kleos/native-resources.json').read_text())['application']
    name = 'kleos-apk-proof-' + uuid4().hex
    with tempfile.TemporaryDirectory(prefix='kleos-apk-profile-') as temporary:
        profile_path = Path(temporary) / 'resources.json'
        profile_path.write_text(json.dumps(profile))
        command = ['docker', 'run', '--rm', '--pull', 'never', '--name', name, '--platform', 'linux/amd64',
            '--network', 'none', '--read-only', '--memory', f'{profile["memoryMiB"]}m',
            '--cpus', str(profile['cpuUnits'] / 1024), '--pids-limit', str(profile['pidsLimit']),
            '--tmpfs', '/control:rw,nosuid,mode=0700,size=134217728',
            '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--cap-add', 'SETUID', '--cap-add', 'SETGID',
            '--cap-add', 'KILL', '--cap-add', 'DAC_OVERRIDE', '--security-opt', 'no-new-privileges:true',
            '--env', 'KLEOS_NATIVE_DOCKER_NETWORK_NONE=1',
            '--mount', f'type=bind,source={backend / "docker/smoke_companion_apk.py"},target=/apk-proof.py,readonly',
            '--mount', f'type=bind,source={backend / "docker/templates"},target=/fixtures,readonly',
            '--mount', f'type=bind,source={profile_path},target=/resource-profile.json,readonly']
        command += (['--mount', 'type=volume,target=/tmp,volume-nocopy'] if options.disk_backed_scratch else
            ['--tmpfs', f'/tmp:rw,exec,nosuid,size={profile["tmpMiB"] * 1024**2}'])
        if options.source_override:
            for target in ('/opt/kleos/kleos', '/usr/local/lib/python3.12/site-packages/kleos'):
                command += ['--mount', f'type=bind,source={backend / "kleos"},target={target},readonly']
        started = time.monotonic()
        try:
            process = subprocess.run([*command, '--entrypoint', 'python', image['Id'], '/apk-proof.py'],
                                     capture_output=True, text=True, timeout=990)
        except subprocess.TimeoutExpired:
            subprocess.run(['docker', 'rm', '--force', '--volumes', name], capture_output=True, text=True, timeout=30)
            raise
    try:
        proof = json.loads(process.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        proof = {'status': 'failed', 'failure': (process.stderr + process.stdout)[-24000:]}
    report = {'image': options.image, 'imageId': image['Id'], 'imageSizeBytes': image['Size'],
              'sourceOverride': options.source_override, 'profile': profile,
              'scratch': {'backing': 'private-anonymous-disk-volume' if options.disk_backed_scratch else 'tmpfs',
                          'hardByteQuota': None if options.disk_backed_scratch else profile['tmpMiB'] * 1024**2,
                          'cleanup': 'Docker --rm removes this anonymous volume'},
              'containerExitCode': process.returncode,
              'checkedAt': datetime.now(UTC).isoformat(), 'containerWallSeconds': time.monotonic() - started, **proof}
    target.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'report': str(target), 'modelCalls': 0}))
    if process.returncode or report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
