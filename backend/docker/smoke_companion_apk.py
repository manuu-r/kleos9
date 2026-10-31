"""Actual offline web/ARM64 APK source-delivery proof through local session transport."""
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

from kleos.native_usage import capture_usage

SDK_MARKERS = ('/opt/flutter/bin/cache/engine.stamp', '/opt/flutter/bin/cache/flutter_tools.stamp',
               '/opt/flutter/.git/HEAD', '/opt/android-sdk/packages.xml', '/opt/java17/release',
               '/opt/qualified-android/qualification.json')
HELPER_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def markers():
    return {name: sha(Path(name).read_bytes()) if Path(name).is_file() else None for name in SDK_MARKERS}


def failure_diagnostics():
    """Read bounded public-fixture daemon logs before the disposable task exits."""
    logs = []
    for path in sorted(Path('/tmp').glob('kleos-native-*/application/.tmp/toolchains/gradle-cache/daemon/*/*.out.log'))[:2]:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024**2:
            continue
        with path.open('rb') as handle:
            handle.seek(max(0, path.stat().st_size - 24000))
            logs.append({'name': path.name, 'tail': handle.read(24000).decode(errors='replace')})
    tmp = Path('/tmp').stat()
    apks = []
    for path in sorted(Path('/tmp').glob('kleos-native-*/application/build/app/outputs/flutter-apk/*.apk'))[:4]:
        metadata = path.lstat()
        apks.append({'name': path.name, 'bytes': metadata.st_size, 'symlink': path.is_symlink(),
                     'regularFile': path.is_file()})
    return {'tmpMode': oct(tmp.st_mode & 0o7777), 'tmpOwner': tmp.st_uid, 'gradleDaemonLogs': logs, 'apks': apks}


def prove():
    profile = json.loads(Path('/resource-profile.json').read_text())
    qualified = json.loads(Path('/opt/qualified-android/qualification.json').read_text())
    original_sdk = markers()
    template = Path('/fixtures/watch-telemetry-v1/application')
    artifacts = []
    for path in sorted(template.rglob('*')):
        if path.is_file():
            raw = path.read_bytes()
            artifacts.append({'id': str(path.relative_to(template)), 'lane': 'application',
                'name': str(path.relative_to(template)), 'sha256': sha(raw), 'contentBase64': base64.b64encode(raw).decode()})
    request = {'schemaVersion': 1, 'jobId': 'companion-apk-public-fixture', 'attemptId': 'companion-apk-attempt',
        'lane': 'application', 'purpose': 'session', 'resources': profile,
        'interfaces': {'application': {'targets': ['web', 'android']}},
        'sessionLimits': {'maxSteps': 8, 'maxSeconds': 900}, 'inputArtifacts': artifacts}
    raw_request = json.dumps(request, sort_keys=True, separators=(',', ':')).encode()
    control = Path('/control')
    control.chmod(0o700)
    steps = control / 'steps'
    steps.mkdir(mode=0o700)
    (control / 'request.json').write_bytes(raw_request)
    (control / 'request.json').chmod(0o600)
    environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'KLEOS_NATIVE_SANDBOX': '1',
                   'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1'}
    if os.environ.get('KLEOS_NATIVE_DOCKER_NETWORK_NONE') == '1':
        environment['KLEOS_NATIVE_DOCKER_NETWORK_NONE'] = '1'
    process = subprocess.Popen([sys.executable, '-I', '-m', 'kleos.native_runner', '--local-files'],
                               env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    def send(ident, action):
        target = steps / (ident + '.request.json')
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps({'id': ident, 'action': action, 'arguments': {}}))
        temporary.chmod(0o600)
        temporary.replace(target)
        result = steps / (ident + '.result.json')
        deadline = time.monotonic() + (870 if action == 'build' else 30)
        while time.monotonic() < deadline:
            if result.is_file():
                response = json.loads(result.read_bytes())
                assert response['status'] == 'succeeded', response
                assert response['result']['checks'] == [], response
                return response['result']
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(('Native supervisor exited before its result', stdout.decode()[-4000:], stderr.decode()[-16000:]))
            time.sleep(.1)
        raise AssertionError('APK session action deadline exhausted')
    try:
        built = send('apk-build', 'build')
        assert built['status'] == 'succeeded', built
        send('apk-finish', 'finish')
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout.decode()[-4000:], stderr.decode()[-16000:])
        callback = json.loads((control / 'result.json').read_bytes())
        assert callback['jobId'] == request['jobId'] and callback['requestSha256'] == sha(raw_request)
        result = callback['result']
        assert result['status'] == 'succeeded' and result['checks'] == [], result.get('limitations')
        native_usage = result['measurements'].get('resourceUsage', {})
        native_seconds = result['measurements'].get('executionSeconds')
        assert native_usage.get('memoryPeakBytes', 0) > 0, 'Native main did not record its actual memory peak'
        assert native_usage.get('cpuUsageSeconds', 0) > 0, 'Native main did not record its actual CPU usage'
        assert isinstance(native_seconds, (int, float)) and native_seconds > 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    assert markers() == original_sdk, 'Image-owned Flutter/Android/JVM metadata changed'
    files = {file['path']: base64.b64decode(file['content']) if file.get('encoding') == 'base64'
             else file['content'].encode() for file in result['files']}
    apk_path = 'native/build/app/outputs/flutter-apk/app-release.apk'
    manifest_path = 'native/companion-build-manifest.json'
    assert {apk_path, manifest_path, 'native/companion-source.zip', 'native/companion-web.zip'}.issubset(files)
    apk, source = files[apk_path], files['native/companion-source.zip']
    manifest = json.loads(files[manifest_path])
    assert manifest['schemaVersion'] == 'companion-build-v1' and manifest['targets'] == ['web', 'android']
    assert manifest['preparedSourceSha256'] == sha(source) and manifest['android']['sha256'] == sha(apk)
    assert manifest['ios']['delivery'] == 'source-only-unsigned' and manifest['ios']['built'] is False
    assert manifest['physicalBleValidated'] is False
    for entry in manifest['files']:
        assert sha(files['native/' + entry['path']]) == entry['sha256']
    source_hashes = result['measurements']['nativeSession']['sourceHashes']
    assert 50 < len(source_hashes) <= 256 and len(files) <= 256
    assert not any(name == 'kleos-build.json' or name.startswith('assets/kleos_fonts/') for name in source_hashes)
    for name, digest in source_hashes.items():
        assert sha(files[name]) == digest
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        source_names = archive.namelist()
        assert 'pubspec.yaml' in source_names and any(name.startswith('android/') for name in source_names)
        assert any(name.startswith('ios/') for name in source_names)
        assert 'assets/kleos_fonts/Roboto-Regular.ttf' in source_names and 'kleos-build.json' in source_names
        forbidden = {'.tmp', '.home', '.gradle', '.dart_tool', 'build', '.git', '.symlinks', 'Pods', 'ephemeral'}
        assert not any(set(Path(name).parts) & forbidden or Path(name).name == 'local.properties'
                       or name.endswith('.keystore') for name in source_names)
    with zipfile.ZipFile(io.BytesIO(apk)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert {'AndroidManifest.xml', 'classes.dex', 'lib/arm64-v8a/libapp.so', 'lib/arm64-v8a/libflutter.so'}.issubset(names)
    with tempfile.TemporaryDirectory(prefix='apk-independent-proof-') as temporary:
        path = Path(temporary) / 'candidate.apk'
        path.write_bytes(apk)
        verify_env = {'PATH': '/opt/java17/bin:/usr/local/bin:/usr/bin:/bin', 'JAVA_HOME': '/opt/java17', 'HOME': temporary}
        signed = subprocess.check_output(['/opt/android-sdk/build-tools/35.0.0/apksigner', 'verify', '--verbose',
                                          '--print-certs', str(path)], env=verify_env, text=True, timeout=60)
        certificate = re.search(r'Signer #1 certificate SHA-256 digest:\s*([a-fA-F0-9:]+)', signed)[1].replace(':', '').lower()
        assert certificate == qualified['developmentCertificateSha256'].replace(':', '').lower()
        badging = subprocess.check_output(['/opt/android-sdk/build-tools/35.0.0/aapt', 'dump', 'badging', str(path)],
                                         env=verify_env, text=True, timeout=30)
        package_lines = [line for line in badging.splitlines() if line.startswith(('package:', 'sdkVersion:', 'targetSdkVersion:', 'native-code:'))]
    return {'schemaVersion': 'companion-apk-runtime-v1', 'status': 'passed', 'modelCalls': 0,
        'transport': 'actual --local-files session build/finish', 'targets': manifest['targets'], 'checks': [],
        'sourceClosureCount': len(source_hashes), 'portableSourceZipCount': len(source_names),
        'sourceHashesVerified': True, 'reservedPreparationExcludedFromReseed': True,
        'sourceZipExcludesSdkCachesAndMachineSettings': True, 'immutableSdkMarkersUnchanged': True,
        'immutableSdkBefore': original_sdk, 'immutableSdkAfter': markers(),
        'apk': {'path': apk_path, 'sha256': sha(apk), 'size': len(apk), 'arm64LibrariesPresent': True,
                'signatureVerifiedIndependently': True, 'developmentCertificateSha256': certificate,
                'signing': 'public-development-debug-certificate', 'packageMetadata': package_lines},
        'outputs': [{'path': name, 'size': len(raw), 'sha256': sha(raw)} for name, raw in files.items() if name.startswith('native/')],
        'buildManifest': manifest, 'ios': manifest['ios'], 'physicalBleValidated': False,
        'sourceHashes': {name: sha((Path('/usr/local/lib/python3.12/site-packages/kleos') / name).read_bytes())
                         for name in ('native_runner.py', 'native_session.py', 'application_runtime.py', 'companion_android.py', 'native_network.py')},
        'nativeResourceUsage': native_usage,
        'nativeExecutionSeconds': native_seconds,
        'fixtureSha256': HELPER_HASH,
        'scope': 'Actual offline web and ARM64 APK build, independent development certificate verification and portable Android/iOS source delivery; no production signing, iOS build, emulator or physical BLE claim'}


try:
    evidence = prove()
except BaseException as exc:
    evidence = {'schemaVersion': 'companion-apk-runtime-v1', 'status': 'failed', 'modelCalls': 0,
                'failure': ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-24000:],
                'diagnostics': failure_diagnostics()}
evidence['usage'] = capture_usage()
print(json.dumps(evidence), flush=True)
if evidence['status'] != 'passed':
    raise SystemExit(1)
