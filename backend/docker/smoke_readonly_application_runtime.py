"""Reuse the existing web build fixture with immutable SDK marker verification."""
import base64
import contextlib
import hashlib
import io
import json
import runpy
import sys
import zipfile
from pathlib import Path

from kleos import native_runner
from kleos.native_usage import capture_usage

MARKERS = ('bin/cache/engine.stamp', 'bin/cache/flutter_tools.stamp', 'bin/cache/dart-sdk/version', '.git/HEAD')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def markers():
    return {name: sha((Path('/opt/flutter') / name).read_bytes())
            if (Path('/opt/flutter') / name).is_file() else None for name in MARKERS}


before = markers()
captured = {}
original = native_runner.run_job


def observe(*arguments, **keywords):
    result = original(*arguments, **keywords)
    captured['result'] = result
    return result


native_runner.run_job = observe
sys.argv = ['/application-fixture.py', 'application']
logs = io.StringIO()
try:
    with contextlib.redirect_stdout(logs):
        runpy.run_path('/application-fixture.py', run_name='__main__')
finally:
    native_runner.run_job = original
    after = markers()
    assert after == before, {'immutableSdkBefore': before, 'immutableSdkAfter': after}
result = captured['result']
assert result.status == 'succeeded' and not result.checks
assert result.measurements['applicationRuntime'] == {
    'preparation': 'job-private-flutter-runtime-v1', 'writablePaths': 'job_tmp_only'}
files = {item.path: base64.b64decode(item.content) for item in result.files}
source = files['native/companion-source.zip']
assert sha(source) == result.measurements['preparedSourceSha256']
with zipfile.ZipFile(io.BytesIO(source)) as bundle:
    source_files = bundle.namelist()
    assert source_files and not any(part in {'.tmp', '.git', 'toolchains'}
                                   for name in source_files for part in Path(name).parts)
with zipfile.ZipFile(io.BytesIO(files['native/companion-web.zip'])) as bundle:
    assert {'index.html', 'main.dart.js'}.issubset(bundle.namelist())
print(json.dumps({'schemaVersion': 'readonly-app-runtime-v1', 'status': 'passed', 'modelCalls': 0,
    'rootFilesystem': 'read_only', 'network': 'none', 'immutableSdkBefore': before, 'immutableSdkAfter': after,
    'immutableSdkMarkersUnchanged': True, 'applicationRuntime': result.measurements['applicationRuntime'],
    'preparedSourceSha256': sha(source), 'preparedSourceFiles': source_files, 'sourceArchiveExcludesToolchainState': True,
    'outputs': [{'path': name, 'sha256': sha(raw), 'size': len(raw)} for name, raw in files.items()],
    'usage': capture_usage(), 'checks': [],
    'sourceHashes': {name: sha((Path('/usr/local/lib/python3.12/site-packages/kleos') / name).read_bytes())
                     for name in ('application_runtime.py', 'native_runner.py', 'native_network.py', 'native_usage.py')},
    'scope': 'Actual offline Flutter web build with a private writable SDK/cache on read-only root; no Android or physical interaction qualification'}))
