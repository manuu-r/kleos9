"""One optional bounded paid Gemini probe after an isolated no-network-model dry run."""
import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

NATIVE_MODULES = ('native_runner.py', 'native_session.py', 'native_network.py', 'cad_render.py', 'cad_parts.py', 'contracts.py')
TRUSTED_MODULES = ('engine.py', 'models.py', 'session_bridge.py', 'native_sessions.py', 'execution.py')


def postfix_origin(backend):
    """A deliberate second qualification never clears the original paid sentinel."""
    path = backend / 'verification/gemini-session-runtime.json'
    original = json.loads(path.read_text())
    if original.get('status') != 'blocked' or original.get('paidAttempted') is not True:
        raise RuntimeError('Post-fix qualification requires the original blocked paid probe')
    if not original.get('imageId') or not isinstance(original.get('nativeSourceHashes'), dict):
        raise RuntimeError('The original image/source provenance is required')
    return path, original


def require_postfix_changes(original, image_id, native_hashes):
    if image_id == original['imageId'] or native_hashes == original['nativeSourceHashes']:
        raise RuntimeError('Post-fix qualification requires a changed native image and changed source hashes')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--post-fix-retest', action='store_true')
    parser.add_argument('--image', default='kleos-mechanics:development')
    options = parser.parse_args()
    if options.post_fix_retest and not options.live:
        parser.error('--post-fix-retest requires the explicit --live option')
    backend = Path(__file__).resolve().parents[1]
    max_model_requests = 5 if options.post_fix_retest or not options.live else 3
    target = backend / ('verification/gemini-session-postfix-runtime.json' if options.post_fix_retest else
        'verification/gemini-session-runtime.json' if options.live else 'verification/gemini-session-dry-runtime.json')
    if options.live and target.is_file() and json.loads(target.read_text()).get('paidAttempted'):
        raise RuntimeError('The single paid probe was already attempted; this tool will not retry it')
    origin_path, original = postfix_origin(backend) if options.post_fix_retest else (None, None)
    image_id = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', options.image], text=True).strip()
    probe = "import hashlib,json; from pathlib import Path; print(json.dumps({name:hashlib.sha256((Path('/usr/local/lib/python3.12/site-packages/kleos')/name).read_bytes()).hexdigest() for name in " + repr(NATIVE_MODULES) + '}))'
    raw = subprocess.check_output(['docker', 'run', '--rm', '--pull', 'never', '--platform', 'linux/amd64',
        '--network', 'none', '--read-only', '--entrypoint', 'python', image_id, '-I', '-c', probe], text=True)
    actual = json.loads(raw.strip().splitlines()[-1])
    expected = {name: hashlib.sha256((backend / 'kleos' / name).read_bytes()).hexdigest() for name in NATIVE_MODULES}
    trusted = {name: hashlib.sha256((backend / 'kleos' / name).read_bytes()).hexdigest() for name in TRUSTED_MODULES}
    fixture_hash = hashlib.sha256((backend / 'docker/smoke_gemini_session.py').read_bytes()).hexdigest()
    if actual != expected:
        target.write_text(json.dumps({'status': 'blocked', 'blocker': 'native_image_source_mismatch',
            'imageId': image_id, 'paidAttempted': False, 'modelCalls': 0}, indent=2) + '\n')
        raise SystemExit('Native image is older than current sources; no provider call was made')
    if options.live:
        dry = json.loads((backend / 'verification/gemini-session-dry-runtime.json').read_text())
        if (dry.get('status') != 'passed' or dry.get('imageId') != image_id
                or dry.get('trustedSourceHashes') != trusted or dry.get('fixtureSha256') != fixture_hash
                or dry.get('limits', {}).get('maxModelRequests') != max_model_requests):
            raise RuntimeError('A passing dry run with this exact image is required before the single paid probe')
        if original is not None:
            require_postfix_changes(original, image_id, expected)
            history = backend / 'verification/gemini-session-original-runtime.json'
            original_bytes = origin_path.read_bytes()
            if history.exists() and history.read_bytes() != original_bytes:
                raise RuntimeError('The preserved original report differs; it cannot be overwritten')
            if not history.exists():
                with history.open('xb') as saved:
                    saved.write(original_bytes)
        target.write_text(json.dumps({'status': 'started', 'paidAttempted': True, 'maxSpendUsd': .10,
                                     'maxModelRequests': max_model_requests,
                                     'postFixQualification': options.post_fix_retest, 'imageId': image_id}, indent=2) + '\n')
    name = 'kleos-gemini-proof-' + uuid4().hex
    bootstrap = '''import json,runpy,sys,traceback
sys.argv=sys.argv[1:]
try:
 runpy.run_path(sys.argv[0],run_name='__main__')
except SystemExit:
 raise
except Exception as exc:
 print(json.dumps({'status':'blocked','blockerType':type(exc).__name__,
  'missingModule':getattr(exc,'name',None),'frames':[{'file':x.filename.rsplit('/',1)[-1],'line':x.lineno,'function':x.name} for x in traceback.extract_tb(exc.__traceback__)],
  'providerRequestsAttempted':False,'modelCalls':0}),flush=True)
 raise SystemExit(1)
'''
    command = ['docker', 'compose', '-f', str(backend / 'compose.yaml'), '--profile', 'backend', 'run', '--rm',
        '--no-deps', '-T', '--name', name, '--volume',
        f'{backend / "docker/smoke_gemini_session.py"}:/gemini-session-proof.py:ro',
        'engineering', 'python', '-c', bootstrap, '/gemini-session-proof.py', '--mode', 'live' if options.live else 'dry',
        '--image', image_id, '--max-model-requests', str(max_model_requests)]
    process = subprocess.run(command, cwd=backend, capture_output=True, text=True, timeout=360)
    proof = None
    for line in reversed(process.stdout.splitlines()):
        try:
            candidate = json.loads(line)
            if isinstance(candidate, dict) and candidate.get('status') in {'passed', 'blocked'}:
                proof = candidate
                break
        except ValueError:
            continue
    if proof is None:
        text = (process.stderr + process.stdout).lower()
        hint = next((value for token, value in [('no space left on device', 'docker_storage_full'),
            ('permission denied', 'docker_permission'), ('no such service', 'compose_service_unavailable')]
            if token in text), 'trusted_probe_exited_without_report')
        proof = {'status': 'blocked', 'blocker': hint, 'processExitCode': process.returncode, 'modelCalls': None}
    report = {'schemaVersion': 'gemini-session-runtime-v1', 'checkedAt': datetime.now(UTC).isoformat(),
              'imageId': image_id, 'nativeSourceHashes': expected, 'trustedSourceHashes': trusted,
              'fixtureSha256': fixture_hash, 'paidAttempted': options.live,
              'postFixQualification': options.post_fix_retest, **proof}
    if options.post_fix_retest:
        report['originalReport'] = 'gemini-session-original-runtime.json'
    target.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'report': str(target), 'modelCalls': report.get('modelCalls')}))
    if process.returncode or report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
