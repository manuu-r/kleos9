"""Portable companion source closure across separate native attempts."""
import base64
import hashlib
import io
import warnings
import zipfile

import pytest
from pydantic import ValidationError

from kleos import native_session
from kleos.contracts import AgentOutput, GeneratedFile, JobResult


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def seed():
    raw = b'name: portable_companion\n'
    return {'schemaVersion': 1, 'jobId': 'first-job', 'attemptId': 'first-attempt', 'lane': 'application',
            'inputArtifacts': [{'id': 'pubspec', 'lane': 'application', 'name': 'pubspec.yaml',
                'sha256': sha(raw), 'contentBase64': base64.b64encode(raw).decode()}]}


def step(session, ident, action):
    return session.apply({'id': ident, 'action': action, 'arguments': {}})


def builder(platform_files=80, mutation=None, preparation='offline-companion-scaffold-v2'):
    def build(request, directory, execute):
        work = directory / 'application'
        files = {'pubspec.yaml': b'name: portable_companion\nflutter: {fonts: [Roboto]}\n',
                 'lib/main.dart': b'void main() {}\n', 'web/index.html': b'<html>portable</html>',
                 'assets/kleos_fonts/Roboto-Regular.ttf': b'font' * (200 if mutation == 'reserved_budget' else 1),
                 'assets/kleos_fonts/Roboto_LICENSE.txt': b'Apache-2.0 license',
                 'kleos-build.json': b'{"preparation":"offline-companion-scaffold-v2"}'}
        for index in range(platform_files):
            family = 'android' if index % 2 == 0 else 'ios'
            files[f'{family}/scaffold-{index}.txt'] = f'portable platform source {index}'.encode()
        if mutation == 'private_toolchain':
            files['.tmp/toolchains/flutter/cache/state'] = b'not portable SDK state'
        if mutation == 'private_gradle':
            files['android/.gradle/cache-state'] = b'not portable Gradle state'
        for name, raw in files.items():
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as bundle:
            for name, raw in files.items():
                if mutation == 'reserved_mismatch' and name == 'assets/kleos_fonts/Roboto-Regular.ttf':
                    raw = b'tampered archive font'
                if mutation == 'marker_mismatch' and name == 'kleos-build.json':
                    raw = b'tampered preparation metadata'
                bundle.writestr(name, raw)
            if mutation == 'reserved_duplicate':
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    bundle.writestr('assets/kleos_fonts/Roboto-Regular.ttf', files['assets/kleos_fonts/Roboto-Regular.ttf'])
        raw = archive.getvalue()
        return JobResult(status='succeeded', measurements={'preparation': preparation,
            'preparedSourceSha256': sha(raw)}, files=[GeneratedFile(path='native/companion-source.zip',
                content=base64.b64encode(raw).decode(), encoding='base64')])
    return build


@pytest.mark.parametrize('preparation', ['offline-companion-scaffold-v2', 'offline-companion-scaffold-v3'])
def test_platform_closure_survives_a_new_attempt_without_reseeding_reserved_preparation_files(tmp_path, monkeypatch, preparation):
    monkeypatch.setattr(native_session, 'build_lane', builder(preparation=preparation))
    first = native_session.SessionSandbox(seed(), tmp_path / 'first')
    built = step(first, 'build', 'build')
    assert built['status'] == 'succeeded' and built['result']['status'] == 'succeeded', built
    assert step(first, 'finish', 'finish')['status'] == 'succeeded'
    result = first.finish_result()
    assert result.status == 'succeeded' and result.checks == []
    hashes = result.measurements['nativeSession']['sourceHashes']
    assert len(hashes) > 50
    assert not any(name == 'kleos-build.json' or name.startswith('assets/kleos_fonts/') for name in hashes)
    artifact_files = {file.path: file for file in result.files}
    archive = base64.b64decode(artifact_files['native/companion-source.zip'].content)
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        assert {'kleos-build.json', 'assets/kleos_fonts/Roboto-Regular.ttf', 'assets/kleos_fonts/Roboto_LICENSE.txt'}.issubset(bundle.namelist())
        assert len(bundle.namelist()) > len(hashes)
        assert any(name.startswith('android/') for name in bundle.namelist())
        assert any(name.startswith('ios/') for name in bundle.namelist())
        assert not any('.tmp' in name.split('/') or 'toolchains' in name.split('/') for name in bundle.namelist())
    request = {**seed(), 'jobId': 'second-job', 'attemptId': 'second-attempt', 'inputArtifacts': [
        {'id': f'source-{index}', 'lane': 'application', 'name': name, 'sha256': digest,
         'contentBase64': artifact_files[name].content} for index, (name, digest) in enumerate(hashes.items())]}
    second = native_session.SessionSandbox(request, tmp_path / 'second')
    assert step(second, 'new-build', 'build')['result']['status'] == 'succeeded'
    assert step(second, 'new-finish', 'finish')['status'] == 'succeeded'
    assert second.finish_result().status == 'succeeded'


@pytest.mark.parametrize('mutation', ['reserved_mismatch', 'marker_mismatch', 'reserved_duplicate', 'private_toolchain', 'private_gradle'])
def test_excluded_preparation_entries_still_require_full_zip_integrity_and_portability_checks(tmp_path, monkeypatch, mutation):
    monkeypatch.setattr(native_session, 'build_lane', builder(platform_files=0, mutation=mutation))
    session = native_session.SessionSandbox(seed(), tmp_path / 'workspace')
    built = step(session, 'build', 'build')
    assert built['result']['status'] == 'failed', built
    assert built['result']['checks'] == []


def test_excluded_font_bytes_still_count_against_the_prepared_archive_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(native_session, 'build_lane', builder(platform_files=0, mutation='reserved_budget'))
    session = native_session.SessionSandbox({**seed(), 'sessionLimits': {'maxSourceBytes': 600}}, tmp_path / 'workspace')
    assert step(session, 'build', 'build')['result']['status'] == 'failed'


def test_native_scaffold_capacity_does_not_expand_the_model_generated_file_limit():
    with pytest.raises(ValidationError):
        AgentOutput(summary='Model file ceiling remains bounded', files=[GeneratedFile(path=f'file-{index}', content='x') for index in range(51)])


def test_repeat_build_clears_generated_android_ios_machine_files_before_portable_archive(tmp_path, monkeypatch):
    derived = ['companion-build-manifest.json', 'android/local.properties', 'android/.gradle/cache-state', '.flutter-plugins-dependencies', '.packages',
        'android/app/src/main/java/io/flutter/plugins/GeneratedPluginRegistrant.java',
        'ios/.symlinks/plugin/state', 'ios/Flutter/ephemeral/cache-state', 'ios/Flutter/Generated.xcconfig',
        'ios/Flutter/flutter_export_environment.sh', 'ios/Runner/GeneratedPluginRegistrant.h',
        'ios/Runner/GeneratedPluginRegistrant.m']
    iterations = []
    def compile(request, directory, execute):
        work = directory / 'application'
        assert not any((work / name).exists() for name in derived), 'Previous machine files reached source preparation'
        (work / 'lib').mkdir(exist_ok=True)
        (work / 'lib/main.dart').write_text('void main() {}')
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as bundle:
            for path in work.rglob('*'):
                if path.is_file():
                    bundle.write(path, str(path.relative_to(work)))
        raw = archive.getvalue()
        for name in derived:
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('machine state /tmp/previous-private-sdk')
        iterations.append(True)
        return JobResult(status='succeeded', measurements={'preparation': 'offline-companion-scaffold-v3',
            'preparedSourceSha256': sha(raw)}, files=[GeneratedFile(path='native/companion-source.zip',
                encoding='base64', content=base64.b64encode(raw).decode())])
    monkeypatch.setattr(native_session, 'build_lane', compile)
    session = native_session.SessionSandbox(seed(), tmp_path / 'workspace')
    assert step(session, 'first', 'build')['result']['status'] == 'succeeded'
    assert step(session, 'second', 'build')['result']['status'] == 'succeeded'
    assert len(iterations) == 2
    assert step(session, 'finish', 'finish')['status'] == 'succeeded'
    result = session.finish_result()
    assert result.status == 'succeeded'
    source = next(file for file in result.files if file.path == 'native/companion-source.zip')
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(source.content))) as bundle:
        assert not set(derived).intersection(bundle.namelist())
        assert not any(b'previous-private-sdk' in bundle.read(name) for name in bundle.namelist())
