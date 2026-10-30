"""Qualify physical part exports with real offline CAD tools, without inference."""
import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--images', nargs='+', default=['kleos-mechanics:development', 'kleos-validator:development'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    script = root / 'docker/smoke_cad_parts.py'
    records = []
    for image in args.images:
        identity = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
        process = subprocess.run([
            'docker', 'run', '--rm', '--pull', 'never', '--platform', 'linux/amd64', '--network', 'none',
            '--read-only', '--memory', '2g', '--cpus', '2', '--pids-limit', '256',
            '--tmpfs', '/tmp:rw,exec,nosuid,size=1073741824', '--cap-drop', 'ALL',
            '--cap-add', 'CHOWN', '--cap-add', 'SETUID', '--cap-add', 'SETGID', '--cap-add', 'KILL',
            '--cap-add', 'DAC_OVERRIDE', '--security-opt', 'no-new-privileges:true',
            '--mount', f'type=bind,source={script},target=/smoke_cad_parts.py,readonly',
            '--entrypoint', 'python', identity, '/smoke_cad_parts.py',
        ], check=True, capture_output=True, text=True, timeout=300)
        result = json.loads(process.stdout.strip().splitlines()[-1])
        assert result['independentPartCoverage']['status'] == 'PASS'
        assert result['missingExportedPartRejected'] and result['monolithicPreserved']
        assert result['sourcePreserved'] and result['modelCalls'] == 0
        records.append({'image': image, 'imageId': identity, **result})
    report = {**records[0], 'schemaVersion': 'cad-parts-runtime-v1', 'status': 'passed',
              'checkedAt': datetime.now(UTC).isoformat(), 'images': records, 'modelCalls': 0}
    target = root / 'verification/cad-parts-runtime.json'
    target.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'images': len(records), 'modelCalls': 0, 'report': str(target)}))


if __name__ == '__main__':
    main()
