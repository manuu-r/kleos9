import base64
import hashlib
import io
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from kleos import native_session as sessions
from kleos.cad_render import png_bytes, rasterize
from kleos.contracts import CheckResult, GeneratedFile, JobResult


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def request(source=b'original source', **settings):
    return {'jobId': 'job-1', 'attemptId': 'attempt-1', 'lane': 'mechanics', 'schemaVersion': 1,
            'inputArtifacts': [{'id': 'source', 'lane': 'mechanics', 'name': 'enclosure.py',
                                'contentBase64': base64.b64encode(source).decode(), 'sha256': sha(source)}], **settings}


def step(session, ident, action, **arguments):
    return session.apply({'id': ident, 'action': action, 'arguments': arguments})


def fake_build(request, directory, execute):
    (directory / 'mechanics' / 'outputs').mkdir()
    return JobResult(status='succeeded', files=[GeneratedFile(path='native/outputs/model.step',
        content=base64.b64encode(b'actual synthetic step').decode(), encoding='base64')],
        checks=[CheckResult(name='generator_claim', status='PASS')], logs='real builder log')


def test_read_patch_creation_optimistic_conflict_and_source_finish(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, 'build_lane', fake_build)
    session = sessions.SessionSandbox(request(), tmp_path / 'workspace')
    read = step(session, 'read', 'read', path='enclosure.py')
    assert read['result']['content'] == 'original source'
    patch = step(session, 'patch', 'patch', path='enclosure.py', expectedSha256=read['result']['sha256'], content='new source')
    assert patch['status'] == 'succeeded'
    assert step(session, 'stale', 'patch', path='enclosure.py', expectedSha256=read['result']['sha256'], content='bad')['status'] == 'failed'
    assert step(session, 'create', 'patch', path='lib/helper.py', expectedSha256=None, content='helper')['status'] == 'succeeded'
    built = step(session, 'build', 'build')
    assert built['result']['status'] == 'succeeded' and built['result']['checks'] == []
    assert all('content' not in file for file in built['result']['files'])
    assert step(session, 'finish', 'finish')['status'] == 'succeeded'
    result = session.finish_result()
    assert result.status == 'succeeded' and result.checks == []
    files = {file.path: base64.b64decode(file.content) for file in result.files}
    assert files['enclosure.py'] == b'new source' and files['lib/helper.py'] == b'helper'
    assert files['native/outputs/model.step'] == b'actual synthetic step'
    assert result.measurements['nativeSession']['sourceHashes']['enclosure.py'] == sha(b'new source')
    assert step(session, 'after', 'read', path='enclosure.py')['status'] == 'failed'


def test_action_replay_is_exact_and_changed_replay_cannot_replace_record(tmp_path):
    session = sessions.SessionSandbox(request(), tmp_path / 'workspace')
    first = step(session, 'id', 'read', path='enclosure.py')
    assert step(session, 'id', 'read', path='enclosure.py') == first
    assert step(session, 'id', 'patch', path='enclosure.py', expectedSha256=sha(b'original source'), content='bad')['status'] == 'failed'
    assert step(session, 'id', 'read', path='enclosure.py') == first
    resumed = sessions.SessionSandbox(request(), tmp_path / 'workspace')
    assert step(resumed, 'id', 'read', path='enclosure.py') == first
    assert resumed.state['steps'] == 1
    assert resumed.journal.parent == resumed.directory.parent and not resumed.journal.is_relative_to(resumed.directory)
    assert stat.S_IMODE(resumed.journal.stat().st_mode) == 0o700
    with pytest.raises(ValueError, match='changed execution contract'):
        sessions.SessionSandbox(request(requirements={'new': True}), tmp_path / 'workspace')


@pytest.mark.parametrize('path', ['../secret', '/etc/passwd', 'outputs/model.step', 'build/file', 'native/file',
                                  '.home/token', '.tmp/file', '.git/config', '.kleos-tool-request.json',
                                  'kleos-build.json', 'assets/kleos_fonts/file', 'a\\b', 'a/../b', 'a//b', 'a/./b'])
def test_patch_cannot_escape_sources_or_edit_control_outputs(tmp_path, path):
    session = sessions.SessionSandbox(request(), tmp_path / 'workspace')
    assert step(session, 'bad', 'patch', path=path, expectedSha256=None, content='x')['status'] == 'failed'


def test_symlink_hardlink_and_untracked_files_cannot_be_read_or_patched(tmp_path):
    session = sessions.SessionSandbox(request(), tmp_path / 'workspace')
    secret = tmp_path / 'secret'
    secret.write_text('private')
    (session.work / 'link').symlink_to(tmp_path, target_is_directory=True)
    assert step(session, 'link', 'patch', path='link/secret', expectedSha256=None, content='changed')['status'] == 'failed'
    assert secret.read_text() == 'private'
    (session.work / 'untracked').write_text('untracked')
    assert step(session, 'untracked', 'read', path='untracked')['status'] == 'failed'
    os.link(secret, session.work / 'hardlink')
    assert step(session, 'hardlink', 'patch', path='hardlink', expectedSha256=None, content='changed')['status'] == 'failed'


def test_source_changes_outside_patch_are_fenced_and_build_mutation_is_restored(tmp_path, monkeypatch):
    session = sessions.SessionSandbox(request(), tmp_path / 'workspace')
    def mutating_build(request, directory, execute):
        (directory / 'mechanics' / 'enclosure.py').write_text('unauthorized')
        return JobResult(status='succeeded')
    monkeypatch.setattr(sessions, 'build_lane', mutating_build)
    result = step(session, 'build', 'build')
    assert result['result']['status'] == 'failed'
    assert (session.work / 'enclosure.py').read_bytes() == b'original source'
    (session.work / 'enclosure.py').write_text('tampered')
    assert step(session, 'read', 'read', path='enclosure.py')['status'] == 'failed'
    assert session.finish_result().status == 'failed'


def test_build_after_patch_invalidates_old_outputs_and_cleans_derived_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, 'build_lane', fake_build)
    session = sessions.SessionSandbox(request(), tmp_path / 'workspace')
    assert step(session, 'build1', 'build')['result']['status'] == 'succeeded'
    assert step(session, 'patch', 'patch', path='enclosure.py', expectedSha256=sha(b'original source'), content='new')['status'] == 'succeeded'
    assert step(session, 'stale-read', 'read', path='native/outputs/model.step')['status'] == 'failed'
    assert step(session, 'stale-finish', 'finish')['status'] == 'failed'
    assert step(session, 'build2', 'build')['result']['status'] == 'succeeded'
    assert step(session, 'finish', 'finish')['status'] == 'succeeded'


def test_fixed_action_allowlist_and_step_time_size_limits(tmp_path, monkeypatch):
    session = sessions.SessionSandbox(request(sessionLimits={'maxSteps': 2, 'maxPatchBytes': 4}), tmp_path / 'workspace')
    assert step(session, 'shell', 'shell', command='rm -rf /')['status'] == 'failed'
    assert session.state['steps'] == 0
    assert step(session, 'oversize', 'patch', path='new', expectedSha256=None, content='12345')['status'] == 'failed'
    assert step(session, 'read', 'read', path='enclosure.py')['status'] == 'succeeded'
    assert step(session, 'exhausted', 'read', path='enclosure.py')['status'] == 'failed'
    assert session.state['steps'] == 2
    second = sessions.SessionSandbox(request(sessionLimits={'maxSeconds': 1}), tmp_path / 'other')
    second.state['startedAt'] -= 2
    assert step(second, 'late', 'read', path='enclosure.py')['status'] == 'failed'
    with pytest.raises(ValueError, match='limit'):
        sessions.SessionSandbox(request(sessionLimits={'maxSteps': 1000}), tmp_path / 'large')


def test_interrupted_patch_replays_exact_bytes_and_interrupted_build_restores_source(tmp_path, monkeypatch):
    payload = {'id': 'patch', 'action': 'patch', 'arguments': {'path': 'enclosure.py', 'expectedSha256': sha(b'original source'), 'content': 'new'}}
    session = sessions.SessionSandbox(request(), tmp_path / 'workspace')
    original = session._save
    def crash_after_patch():
        if session.state['pending'] is None and 'patch' in session.state['actions']:
            raise RuntimeError('simulated supervisor crash')
        original()
    monkeypatch.setattr(session, '_save', crash_after_patch)
    with pytest.raises(RuntimeError, match='crash'):
        session.apply(payload)
    resumed = sessions.SessionSandbox(request(), tmp_path / 'workspace')
    assert resumed.apply(payload)['status'] == 'succeeded'
    assert (resumed.work / 'enclosure.py').read_text() == 'new'
    action = {'id': 'build', 'action': 'build', 'arguments': {}}
    resumed.state['pending'] = {'id': 'build', 'action': 'build', 'requestHash': sha(sessions._json(action))}
    resumed._save()
    (resumed.work / 'enclosure.py').write_text('interrupted build mutation')
    again = sessions.SessionSandbox(request(), tmp_path / 'workspace')
    assert (again.work / 'enclosure.py').read_text() == 'new'
    assert again.apply(action)['status'] == 'failed'
    assert again.state['latestBuild'] is None


def test_render_fixed_subprocess_returns_hashed_pngs_no_validation_authority(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, 'build_lane', fake_build)
    calls = []
    def execute(args, directory, timeout):
        calls.append(args)
        assert args[:4] == ['python', '-I', '-m', 'kleos.cad_render']
        output = directory / 'images'
        output.mkdir()
        images = []
        raw = png_bytes(bytes([80, 100, 120]) * 4, 2, 2)
        for view in json.loads(args[-1]):
            (output / f'{view}.png').write_bytes(raw)
            images.append({'name': f'{view}.png', 'sha256': sha(raw)})
        return 0, json.dumps({'images': images, 'stepSha256': sha(b'actual synthetic step')})
    session = sessions.SessionSandbox(request(), tmp_path / 'workspace', execute)
    step(session, 'build', 'build')
    result = step(session, 'render', 'render')
    assert result['status'] == 'succeeded' and len(result['result']['images']) == 3
    assert result['result']['checks'] == [] and result['result']['authority'] == 'diagnostic_only'
    for image in result['result']['images']:
        assert image['mediaType'] == 'image/png' and sha(base64.b64decode(image['contentBase64'])) == image['sha256']
    assert step(session, 'render', 'render') == result and len(calls) == 1
    assert step(session, 'bad-render', 'render', views=['front', ';sh'])['status'] == 'failed'


def test_native_raster_is_actual_triangle_geometry_and_bounded_views():
    vertices, triangles = [(0., 0., 0.), (2., 0., 0.), (0., 1., 0.)], [(0, 1, 2)]
    raw = rasterize(vertices, triangles, 'top', 64, 64)
    assert raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) < 512 * 1024
    with pytest.raises(ValueError):
        rasterize(vertices, triangles, 'shell')


def test_prepared_companion_sources_are_retained_and_repeat_builds_clean_caches(tmp_path, monkeypatch):
    payload = request(b'name: fixture\n')
    payload['lane'] = 'application'
    payload['inputArtifacts'][0].update(lane='application', name='pubspec.yaml')
    calls = []
    def prepared_build(request, directory, execute):
        work = directory / 'application'
        assert not (work / 'assets/kleos_fonts').exists() and not (work / '.home').exists()
        font = work / 'assets/kleos_fonts/Roboto.ttf'
        font.parent.mkdir(parents=True)
        font.write_bytes(b'font')
        (work / 'web').mkdir(exist_ok=True)
        (work / 'web/index.html').write_text('<html>actual scaffold</html>')
        (work / 'pubspec.yaml').write_text('name: fixture\nflutter: {fonts: [Roboto]}\n')
        (work / 'kleos-build.json').write_text(json.dumps({'preparationVersion': 2, 'iteration': len(calls)}))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as bundle:
            for path in work.rglob('*'):
                if path.is_file():
                    bundle.write(path, str(path.relative_to(work)))
        raw = archive.getvalue()
        calls.append(True)
        (work / '.home').mkdir()
        (work / '.home/cache').write_text('must be excluded from next preparation')
        return JobResult(status='succeeded', measurements={'preparation': 'offline-companion-scaffold-v2',
            'preparedSourceSha256': sha(raw)}, files=[GeneratedFile(path='native/companion-source.zip',
                content=base64.b64encode(raw).decode(), encoding='base64')])
    monkeypatch.setattr(sessions, 'build_lane', prepared_build)
    session = sessions.SessionSandbox(payload, tmp_path / 'workspace')
    assert step(session, 'first', 'build')['result']['status'] == 'succeeded'
    assert step(session, 'second', 'build')['result']['status'] == 'succeeded'
    assert step(session, 'finish', 'finish')['status'] == 'succeeded'
    result = session.finish_result()
    assert result.status == 'succeeded'
    names = {file.path for file in result.files}
    assert {'pubspec.yaml', 'web/index.html', 'native/companion-source.zip'}.issubset(names)
    assert not {'assets/kleos_fonts/Roboto.ttf', 'kleos-build.json'}.intersection(names)
    source = next(file for file in result.files if file.path == 'native/companion-source.zip')
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(source.content))) as archive:
        assert {'assets/kleos_fonts/Roboto.ttf', 'kleos-build.json'}.issubset(archive.namelist())
    assert all('.home' not in name for name in names)


def test_missing_fixed_tool_is_a_reported_step_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, 'build_lane', fake_build)
    def missing(*args):
        raise sessions.UnsupportedTool('Required tool is absent')
    session = sessions.SessionSandbox(request(), tmp_path / 'workspace', missing)
    step(session, 'build', 'build')
    response = step(session, 'analyze', 'analyze')
    assert response['status'] == 'failed' and 'absent' in response['result']['message']


def test_steps_publish_with_control_owner_and_preserve_existing_requests(tmp_path):
    control = tmp_path / 'control'
    control.mkdir(mode=0o700)
    steps = sessions.prepare_session_steps(control)
    assert steps.stat().st_uid == control.stat().st_uid
    assert stat.S_IMODE(steps.stat().st_mode) == 0o700
    (steps / 'existing.request.json').write_text('{}')
    assert sessions.prepare_session_steps(control) == steps
    assert (steps / 'existing.request.json').read_text() == '{}'
    assert not list(control.glob('.steps-ready-*'))


def test_steps_publish_race_preserves_the_controllers_request(tmp_path, monkeypatch):
    control = tmp_path / 'control'
    control.mkdir(mode=0o700)
    rename = sessions.os.rename
    def controller_wins(source, target):
        target.mkdir(mode=0o700)
        (target / 'controller.request.json').write_text('request')
        return rename(source, target)
    monkeypatch.setattr(sessions.os, 'rename', controller_wins)
    steps = sessions.prepare_session_steps(control)
    assert (steps / 'controller.request.json').read_text() == 'request'
    assert not list(control.glob('.steps-ready-*'))


def test_steps_set_permissions_before_transferring_ownership(tmp_path, monkeypatch):
    control = tmp_path / 'control'
    control.mkdir(mode=0o700)
    transferred = set()
    chmod, chown = Path.chmod, sessions.os.chown
    def without_fowner(path, mode, **kwargs):
        assert path not in transferred, 'CAP_FOWNER must not be required after chown'
        return chmod(path, mode, **kwargs)
    def transfer(path, uid, gid):
        transferred.add(path)
        return chown(path, uid, gid)
    monkeypatch.setattr(Path, 'chmod', without_fowner)
    monkeypatch.setattr(sessions.os, 'chown', transfer)
    steps = sessions.prepare_session_steps(control)
    assert stat.S_IMODE(steps.stat().st_mode) == 0o700
    # The shared controller's existing private directory also requires no chmod.
    transferred.add(steps)
    assert sessions.prepare_session_steps(control) == steps


def test_steps_reject_symlinks_without_changing_the_external_directory(tmp_path):
    control, outside = tmp_path / 'control', tmp_path / 'outside'
    control.mkdir(mode=0o700)
    outside.mkdir(mode=0o755)
    (control / 'steps').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='plain private'):
        sessions.prepare_session_steps(control)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o755
