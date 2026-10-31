"""Offline native session repair/build/real-STEP render qualification."""
import base64
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from kleos.native_session import SessionSandbox

INVALID = b'from build123d import Box\nassembly = Box(40,30,10) -\n'
REPAIRED = '''from build123d import Box
from pathlib import Path
journals=list(Path.cwd().parent.parent.glob('.kleos-session-*'))
assert journals, 'Supervisor journal was not found for the isolation fixture'
for journal in journals:
    try:
        (journal/'state.json').read_bytes()
    except PermissionError:
        print('SUPERVISOR_JOURNAL_DENIED')
    else:
        raise ValueError('Generated process could read supervisor journal')
assembly=Box(40,30,10)-Box(38,28,8)
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def action(session, ident, kind, **arguments):
    return session.apply({'id': ident, 'action': kind, 'arguments': arguments})


def qualify_local_transport(original_request):
    """Drive the real --local-files supervisor, not a mocked transport function."""
    control = Path('/control')
    assert control.is_dir(), 'The offline fixture requires a private /control tmpfs'
    control.chmod(0o700)
    steps = control / 'steps'
    steps.mkdir(mode=0o700)
    source = REPAIRED + '''
try:
    Path('/control/request.json').read_bytes()
except PermissionError:
    print('PRIVATE_CONTROL_READ_DENIED')
else:
    raise ValueError('Generated code could read private native control')
try:
    Path('/control/steps/forged.result.json').write_text('forged')
except PermissionError:
    print('PRIVATE_CONTROL_WRITE_DENIED')
else:
    raise ValueError('Generated code could forge private native control')
'''
    request = {**original_request, 'jobId': 'local-session-transport-fixture', 'purpose': 'session',
               'attemptId': 'local-transport-attempt'}
    raw = json.dumps(request, sort_keys=True, separators=(',', ':')).encode()
    (control / 'request.json').write_bytes(raw)
    (control / 'request.json').chmod(0o600)
    supervisor_environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'KLEOS_NATIVE_SANDBOX': '1',
                              'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1'}
    if os.environ.get('KLEOS_NATIVE_DOCKER_NETWORK_NONE') == '1':
        supervisor_environment['KLEOS_NATIVE_DOCKER_NETWORK_NONE'] = '1'
    process = subprocess.Popen([sys.executable, '-I', '-m', 'kleos.native_runner', '--local-files'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=supervisor_environment)
    responses = {}
    def wait_response(ident):
        response_path = steps / (ident + '.result.json')
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if response_path.is_file():
                assert response_path.stat().st_mode & 0o777 == 0o644
                envelope = json.loads(response_path.read_bytes())
                assert envelope['status'] == 'succeeded' and envelope['result']['checks'] == [], envelope
                return envelope
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(('Native transport exited before step result', stdout.decode(), stderr.decode()))
            time.sleep(.05)
        raise AssertionError('Native transport step deadline exhausted')
    def send(ident, kind, **arguments):
        payload = {'id': ident, 'action': kind, 'arguments': arguments}
        target = steps / (ident + '.request.json')
        temporary = steps / (ident + '.request.tmp')
        temporary.write_text(json.dumps(payload, sort_keys=True, separators=(',', ':')))
        temporary.chmod(0o600)
        temporary.replace(target)
        response = wait_response(ident)
        responses[ident] = response
        return response
    try:
        read = send('transport-read', 'read', path='enclosure.py')
        assert read['result']['content'] == INVALID.decode()
        send('transport-patch', 'patch', path='enclosure.py', expectedSha256=sha(INVALID), content=source)
        built = send('transport-build', 'build')
        assert built['result']['status'] == 'succeeded', built
        assert 'PRIVATE_CONTROL_READ_DENIED' in built['result']['logs']
        assert 'PRIVATE_CONTROL_WRITE_DENIED' in built['result']['logs']
        assert not (steps / 'forged.result.json').exists()
        # A lost delivery is replayed from the supervisor journal without a build.
        (steps / 'transport-build.result.json').unlink()
        assert wait_response('transport-build') == built
        rendered = send('transport-render', 'render')
        assert len(rendered['result']['images']) == 3, rendered
        send('transport-finish', 'finish')
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, (stdout.decode(), stderr.decode())
        envelope = json.loads((control / 'result.json').read_bytes())
        assert envelope['jobId'] == request['jobId'] and envelope['requestSha256'] == sha(raw)
        result = envelope['result']
        assert result['status'] == 'succeeded' and result['checks'] == []
        assert result['measurements']['nativeSession']['steps'] == 5
        files = {item['path']: base64.b64decode(item['content']) for item in result['files']}
        assert files['enclosure.py'] == source.encode()
        step_hash = sha(files['native/outputs/model.step'])
        assert rendered['result']['stepSha256'] == step_hash
        for image in rendered['result']['images']:
            image_raw = base64.b64decode(image['contentBase64'])
            assert image_raw.startswith(b'\x89PNG\r\n\x1a\n') and sha(image_raw) == image['sha256']
            assert image['stepSha256'] == step_hash and len(image_raw) <= 512 * 1024
        return {'status': 'passed', 'entrypoint': 'python -I -m kleos.native_runner --local-files',
                'actions': ['read', 'patch', 'build', 'render', 'finish'], 'stepCount': 5,
                'lostResponseReplayVerified': True, 'privateControlReadDenied': True,
                'privateControlWriteDenied': True, 'candidateOnly': True, 'checks': [],
                'outputFileCount': len(files), 'requestSha256': sha(raw), 'sourceSha256': sha(source.encode()),
                'stepSha256': step_hash, 'renderedViews': len(rendered['result']['images'])}
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


with tempfile.TemporaryDirectory(prefix='native-session-smoke-') as temporary:
    work = Path(temporary)
    work.chmod(0o755)
    request = {'schemaVersion': 1, 'jobId': 'native-session-public-fixture', 'attemptId': 'fixture-attempt',
        'lane': 'mechanics', 'sessionLimits': {'maxSteps': 16, 'maxSeconds': 300},
        'interfaces': {'mechanical': {'externalMm': {'x': 40, 'y': 30, 'z': 10},
            'internalMm': {'x': 38, 'y': 28, 'z': 8}, 'minimumWallMm': 1,
            'components': [{'id': 'board', 'sizeMm': {'x': 10, 'y': 10, 'z': 1},
                            'positionMm': {'x': 10, 'y': 8, 'z': 2}}]}},
        'inputArtifacts': [{'id': 'source', 'lane': 'mechanics', 'name': 'enclosure.py', 'sha256': sha(INVALID),
                            'contentBase64': base64.b64encode(INVALID).decode()}]}
    session = SessionSandbox(request, work)
    first = action(session, 'build-failure', 'build')
    assert first['status'] == 'succeeded' and first['result']['status'] == 'failed', first
    assert 'SyntaxError' in first['result']['logs'], first
    read = action(session, 'read', 'read', path='enclosure.py')
    assert read['result']['content'] == INVALID.decode()
    patched = action(session, 'patch', 'patch', path='enclosure.py', expectedSha256=sha(INVALID), content=REPAIRED)
    assert patched['status'] == 'succeeded', patched
    built = action(session, 'build-success', 'build')
    assert built['result']['status'] == 'succeeded', built
    assert 'SUPERVISOR_JOURNAL_DENIED' in built['result']['logs'], built
    assert action(session, 'build-success', 'build') == built
    assert not any('content' in entry or 'contentBase64' in entry for entry in built['result']['files'])
    views = action(session, 'render', 'render')
    assert views['status'] == 'succeeded' and len(views['result']['images']) == 3, views
    images = views['result']['images']
    assert len({image['sha256'] for image in images}) == 3
    for image in images:
        raw = base64.b64decode(image['contentBase64'])
        assert raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) <= 512 * 1024
        assert struct.unpack('>II', raw[16:24]) == (512, 384)
        assert sha(raw) == image['sha256'] and image['mediaType'] == 'image/png'
    analysis = action(session, 'analyze', 'analyze')
    assert analysis['status'] == 'succeeded' and analysis['result']['status'] == 'succeeded', analysis
    assert analysis['result']['measurements']['internalObstructionMm3'] < 1e-4, analysis
    assert analysis['result']['checks'] == [] and analysis['result']['authority'] == 'diagnostic_only'
    resumed = SessionSandbox(request, work)
    assert action(resumed, 'render', 'render') == views
    finished = action(resumed, 'finish', 'finish')
    assert finished['status'] == 'succeeded', finished
    result = resumed.finish_result()
    assert result.status == 'succeeded' and result.checks == [], result.model_dump()
    source = next(item for item in result.files if item.path == 'enclosure.py')
    assert base64.b64decode(source.content) == REPAIRED.encode()
    assert result.measurements['nativeSession']['sourceHashes']['enclosure.py'] == sha(REPAIRED.encode())
    assert action(resumed, 'after-finish', 'read', path='enclosure.py')['status'] == 'failed'
    recorded_images = [{key: value for key, value in image.items() if key != 'contentBase64'} for image in images]
    transport = qualify_local_transport(request)
    print(json.dumps({'schemaVersion': 'native-session-runtime-v1', 'status': 'passed', 'modelCalls': 0,
        'uid': os.geteuid(), 'syntaxFailureCaptured': True, 'sourcePatchHashVerified': True,
        'generatedUidCannotReadJournal': True, 'replayedBuildNotExecuted': True, 'resumedReplayVerified': True,
        'finishChecks': [], 'outputFileCount': len(result.files),
        'sourceSha256': sha(REPAIRED.encode()), 'stepSha256': views['result']['stepSha256'],
        'images': recorded_images, 'steps': result.measurements['nativeSession']['steps'],
        'localTransport': transport,
        'sourceHashes': {name: sha((Path('/usr/local/lib/python3.12/site-packages/kleos') / name).read_bytes())
                         for name in ('native_session.py', 'cad_render.py', 'native_runner.py', 'cad_parts.py')},
        'fixtureSha256': sha(Path(__file__).read_bytes()),
        'scope': 'Actual per-attempt source repair/build, fixed analysis and three actual STEP orthographic PNGs; no independent release authority'}))
