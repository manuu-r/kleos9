"""Immutable content blobs and explicitly authorized project source changes."""

import hashlib
import os
import re
import tempfile
from pathlib import Path, PurePosixPath

from kleos.contracts.models import DomainError

MAX_FILE_BYTES = 32 * 1024 * 1024


def source_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 240 or "\\" in value:
        raise DomainError("Invalid source path", "invalid_path", 422)
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/")):
        raise DomainError("Source path must be relative without traversal", "invalid_path", 422)
    if any(ord(c) < 32 for c in value) or any(p.startswith(".") for p in path.parts):
        raise DomainError("Private source paths are not allowed", "invalid_path", 422)
    return value


class BlobStore:
    """No public path API: callers authorize project/version references before reading."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, digest):
        if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
            raise DomainError("Invalid artifact hash", "invalid_hash", 422)
        path = self.root / digest
        if path.is_symlink():
            raise DomainError("Unsafe artifact storage", "storage_error", 500)
        return path

    def put(self, content: bytes):
        if not isinstance(content, bytes) or len(content) > MAX_FILE_BYTES:
            raise DomainError("Artifact exceeds its byte limit", "artifact_limit", 413)
        digest = hashlib.sha256(content).hexdigest()
        target = self._path(digest)
        if target.exists():
            if self.get(digest) != content:
                raise DomainError("Artifact integrity failed", "storage_error", 500)
            return digest
        descriptor, temporary = tempfile.mkstemp(dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # Atomic no-overwrite publication; identical concurrent writers are harmless.
            try:
                os.link(temporary, target)
            except FileExistsError:
                if self.get(digest) != content:
                    raise DomainError("Artifact integrity failed", "storage_error", 500) from None
        finally:
            os.unlink(temporary)
        return digest

    def get(self, digest):
        path = self._path(digest)
        try:
            with path.open("rb") as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            raise DomainError("Artifact not found", "not_found", 404) from None
        if len(content) > MAX_FILE_BYTES or hashlib.sha256(content).hexdigest() != digest:
            raise DomainError("Artifact integrity failed", "storage_error", 500)
        return content


def apply_changes(manifest: dict[str, str], changes: list[dict], allowed_paths: list[str]):
    """Compute a new manifest; publication must separately compare the project revision."""
    allowed = {source_path(path) for path in allowed_paths}
    result = dict(manifest)
    seen = set()
    for change in changes:
        path = source_path(change["path"])
        if path not in allowed or path in seen:
            raise DomainError("Change is outside the task scope or duplicated", "scope_conflict")
        seen.add(path)
        if "expectedSha256" not in change or manifest.get(path) != change["expectedSha256"]:
            raise DomainError("Source changed since this task started", "revision_conflict")
        digest = change.get("sha256")
        if digest is None:
            result.pop(path, None)
        elif isinstance(digest, str) and re.fullmatch("[a-f0-9]{64}", digest):
            result[path] = digest
        else:
            raise DomainError("Invalid proposed artifact hash", "invalid_hash", 422)
    return result
