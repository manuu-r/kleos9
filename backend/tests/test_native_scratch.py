"""Trusted filesystem accounting only; no generated process executes on the host."""
from pathlib import Path

import pytest

from kleos.execution.native_runner import scratch_size


def test_scratch_counts_regular_files_without_following_links(tmp_path):
    (tmp_path / 'source').write_bytes(b'123')
    directory = tmp_path / 'cache'
    directory.mkdir()
    (directory / 'data').write_bytes(b'12345')
    (tmp_path / 'link').symlink_to(directory / 'data')
    assert scratch_size(tmp_path) == 8


def test_scratch_tolerates_file_removed_after_directory_listing(tmp_path, monkeypatch):
    stable, transient = tmp_path / 'stable', tmp_path / 'stderr.tmp'
    stable.write_bytes(b'123')
    transient.write_bytes(b'12345')
    original = Path.lstat

    def disappearing(path, *args, **kwargs):
        if path == transient:
            path.unlink()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'lstat', disappearing)
    assert scratch_size(tmp_path) == 3


def test_scratch_does_not_ignore_permission_errors(tmp_path, monkeypatch):
    target = tmp_path / 'unreadable'
    target.write_bytes(b'123')
    original = Path.lstat

    def denied(path, *args, **kwargs):
        if path == target:
            raise PermissionError('fixture permission failure')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'lstat', denied)
    with pytest.raises(PermissionError):
        scratch_size(tmp_path)
