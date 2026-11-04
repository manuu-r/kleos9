import hashlib

import pytest

from kleos.artifacts.storage import BlobStore, apply_changes, source_path
from kleos.contracts.models import DomainError


def test_blob_integrity(tmp_path):
    store = BlobStore(tmp_path)
    digest = store.put(b"CAD source")
    assert store.put(b"CAD source") == digest
    assert BlobStore(tmp_path).get(digest) == b"CAD source"
    (tmp_path / digest).write_bytes(b"corrupt")
    with pytest.raises(DomainError, match="integrity"):
        store.get(digest)


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "a/../../b", "a\\b", ".env", "a//b", "a/./b"])
def test_source_path_blocks_escape(path):
    with pytest.raises(DomainError):
        source_path(path)


def test_task_changes_preserve_neighbour_hashes_and_reject_stale_sources():
    a, b, c = [hashlib.sha256(x).hexdigest() for x in [b"a", b"b", b"c"]]
    original = {"bracket.py": a, "board.kicad_pcb": b}
    change = {"path": "bracket.py", "expectedSha256": a, "sha256": c}
    result = apply_changes(original, [change], ["bracket.py"])
    assert result == {"bracket.py": c, "board.kicad_pcb": b}
    assert original["bracket.py"] == a
    with pytest.raises(DomainError):
        apply_changes(result, [change], ["bracket.py"])
    with pytest.raises(DomainError):
        apply_changes(original, [change], ["board.kicad_pcb"])
