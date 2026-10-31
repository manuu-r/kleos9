"""The explicitly authorized post-fix probe preserves the original paid limit."""
import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / 'tools/check_gemini_session_runtime.py'
_SPEC = importlib.util.spec_from_file_location('gemini_probe_checker', _PATH)
checker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(checker)


def origin(tmp_path, **overrides):
    path = tmp_path / 'verification/gemini-session-runtime.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'status': 'blocked', 'paidAttempted': True,
        'imageId': 'sha256:original', 'nativeSourceHashes': {'native_session.py': 'before'}, **overrides}))
    return path


@pytest.mark.parametrize('overrides', [{'status': 'passed'}, {'paidAttempted': False}, {'nativeSourceHashes': None}])
def test_postfix_requires_a_blocked_paid_original_with_provenance(tmp_path, overrides):
    path = origin(tmp_path, **overrides)
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        checker.postfix_origin(tmp_path)
    assert path.read_bytes() == before


def test_postfix_requires_both_a_changed_image_and_changed_native_source(tmp_path):
    path = origin(tmp_path)
    before = path.read_bytes()
    _, report = checker.postfix_origin(tmp_path)
    with pytest.raises(RuntimeError, match='changed native image'):
        checker.require_postfix_changes(report, report['imageId'], {'native_session.py': 'after'})
    with pytest.raises(RuntimeError, match='changed native image'):
        checker.require_postfix_changes(report, 'sha256:new', report['nativeSourceHashes'])
    checker.require_postfix_changes(report, 'sha256:new', {'native_session.py': 'after'})
    assert path.read_bytes() == before


def test_postfix_needs_explicit_live_flag_without_inspecting_docker(monkeypatch):
    monkeypatch.setattr('sys.argv', ['check_gemini_session_runtime.py', '--post-fix-retest'])
    monkeypatch.setattr(checker.subprocess, 'check_output', lambda *args, **kwargs: pytest.fail('Docker inspection must not run'))
    with pytest.raises(SystemExit) as exited:
        checker.main()
    assert exited.value.code == 2


def test_used_postfix_sentinel_blocks_another_probe_before_docker(tmp_path, monkeypatch):
    tool = tmp_path / 'tools/check_gemini_session_runtime.py'
    tool.parent.mkdir()
    origin(tmp_path)
    (tmp_path / 'verification/gemini-session-postfix-runtime.json').write_text('{"paidAttempted":true,"status":"started"}')
    monkeypatch.setattr(checker, '__file__', str(tool))
    monkeypatch.setattr('sys.argv', [str(tool), '--live', '--post-fix-retest'])
    monkeypatch.setattr(checker.subprocess, 'check_output', lambda *args, **kwargs: pytest.fail('A used paid sentinel must fail before Docker'))
    with pytest.raises(RuntimeError, match='already attempted'):
        checker.main()


def test_postfix_rejects_a_matching_dry_with_the_old_three_request_limit(tmp_path, monkeypatch):
    tool = tmp_path / 'tools/check_gemini_session_runtime.py'
    tool.parent.mkdir()
    original = origin(tmp_path)
    before = original.read_bytes()
    (tmp_path / 'kleos').mkdir()
    for name in set(checker.NATIVE_MODULES + checker.TRUSTED_MODULES):
        (tmp_path / 'kleos' / name).write_text('new fixture source')
    (tmp_path / 'docker').mkdir()
    fixture = tmp_path / 'docker/smoke_gemini_session.py'
    fixture.write_text('public fixture')
    def hashes(names):
        return {name: checker.hashlib.sha256((tmp_path / 'kleos' / name).read_bytes()).hexdigest() for name in names}
    dry = {'status': 'passed', 'imageId': 'sha256:new', 'trustedSourceHashes': hashes(checker.TRUSTED_MODULES),
        'fixtureSha256': checker.hashlib.sha256(fixture.read_bytes()).hexdigest(), 'limits': {'maxModelRequests': 3}}
    (tmp_path / 'verification/gemini-session-dry-runtime.json').write_text(json.dumps(dry))
    monkeypatch.setattr(checker, '__file__', str(tool))
    monkeypatch.setattr('sys.argv', [str(tool), '--live', '--post-fix-retest'])
    replies = iter(['sha256:new\n', json.dumps(hashes(checker.NATIVE_MODULES))])
    monkeypatch.setattr(checker.subprocess, 'check_output', lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(checker.subprocess, 'run', lambda *args, **kwargs: pytest.fail('Mismatched dry must never reach provider transport'))
    with pytest.raises(RuntimeError, match='passing dry run'):
        checker.main()
    assert not (tmp_path / 'verification/gemini-session-postfix-runtime.json').exists()
    assert original.read_bytes() == before
