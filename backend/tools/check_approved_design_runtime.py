"""Run real approved-reference geometry checks offline against synthetic fixtures."""
import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='kleos-validator:development')
    parser.add_argument('--source-override', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    image_id = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', args.image], text=True).strip()
    command = ['docker', 'run', '--rm', '--pull', 'never', '--platform', 'linux/amd64', '--network', 'none',
        '--read-only', '--memory', '2g', '--cpus', '2', '--pids-limit', '256',
        '--tmpfs', '/tmp:rw,exec,nosuid,size=1073741824', '--cap-drop', 'ALL',
        '--cap-add', 'CHOWN', '--cap-add', 'SETUID', '--cap-add', 'SETGID', '--cap-add', 'KILL', '--cap-add', 'DAC_OVERRIDE',
        '--security-opt', 'no-new-privileges:true',
        '--mount', f'type=bind,source={root / "docker/smoke_approved_design.py"},target=/smoke_approved_design.py,readonly',
        '--mount', f'type=bind,source={root / "fixtures/approved_design_validation.json"},target=/approved_design_validation.json,readonly']
    if args.source_override:
        for target in ('/opt/kleos/kleos', '/usr/local/lib/python3.12/site-packages/kleos'):
            command += ['--mount', f'type=bind,source={root / "kleos"},target={target},readonly']
    process = subprocess.run([*command, '--entrypoint', 'python', image_id, '/smoke_approved_design.py'],
                             capture_output=True, text=True, timeout=420)
    if process.returncode:
        raise RuntimeError(process.stderr[-20000:] + process.stdout[-10000:])
    result = json.loads(process.stdout.strip().splitlines()[-1])
    report = {'status': 'passed', 'checkedAt': datetime.now(UTC).isoformat(), 'image': args.image,
              'imageId': image_id, 'sourceOverride': args.source_override, **result}
    target = root / 'verification/approved-design-runtime.json'
    target.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'report': str(target), 'modelCalls': 0}))


if __name__ == '__main__':
    main()
