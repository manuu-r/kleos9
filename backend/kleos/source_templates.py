"""Pinned starter sources, never generated test reports or physical qualification evidence."""
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath

from .contracts import DomainError

TEMPLATE_ID = "watch-telemetry-v1"
MANIFEST_SHA256 = "d7a4ce169b62fef882abcc808f6dce54a1eb74787d3bf2ee76dfd0a90c646ea3"
_IMAGE_ROOT = Path("/opt/kleos/templates")
_REPO_ROOT = Path(__file__).resolve().parents[1] / "docker/templates"
_MAX_FILE_BYTES = 256 * 1024
_MAX_TOTAL_BYTES = 512 * 1024


def _root():
    # No environment variable, project input, model path or caller-selected directory.
    return _IMAGE_ROOT if _IMAGE_ROOT.exists() or _IMAGE_ROOT.is_symlink() else _REPO_ROOT


def _read(root, relative):
    name = PurePosixPath(relative)
    if (not relative or name.is_absolute() or "\\" in relative
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise DomainError("Invalid qualified template path", 503, "template_integrity")
    cursor = root
    for part in (None, *name.parts):
        if part is not None:
            cursor = cursor / part
        if cursor.is_symlink():
            raise DomainError("Qualified templates cannot contain symlinks", 503, "template_integrity")
    try:
        descriptor = os.open(cursor, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE_BYTES:
                raise DomainError("Qualified template file is invalid or oversized", 503, "template_integrity")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                content = handle.read(_MAX_FILE_BYTES + 1)
            if len(content) > _MAX_FILE_BYTES:
                raise DomainError("Qualified template file is oversized", 503, "template_integrity")
            return content
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise DomainError("Qualified source templates are unavailable", 503, "template_unavailable") from exc


def load_source_templates(requirements, lane):
    """Return freshly read, hash-verified sources only for the explicitly accepted profile.

    An updated template requires an intentional manifest/hash update. Returned maps
    are independent values; caller modifications never mutate a process-wide cache.
    """
    if (not isinstance(requirements, dict) or requirements.get("validationProfile") != TEMPLATE_ID
            or lane not in {"firmware", "application"}):
        return []
    root = _root()
    manifest_raw = _read(root, f"{TEMPLATE_ID}/manifest.json")
    if hashlib.sha256(manifest_raw).hexdigest() != MANIFEST_SHA256:
        raise DomainError("Qualified template manifest has changed", 503, "template_integrity")
    try:
        manifest = json.loads(manifest_raw)
        if manifest["schemaVersion"] != "qualified-source-template-v1" or manifest["templateId"] != TEMPLATE_ID:
            raise ValueError("Unexpected qualified template manifest")
        entries = manifest["lanes"][lane]
        if not isinstance(entries, list) or not 1 <= len(entries) <= 50:
            raise ValueError("Invalid qualified source closure")
        result, seen, total = [], set(), 0
        for entry in entries:
            name = entry["name"]
            if not isinstance(name, str) or name in seen:
                raise ValueError("Invalid or duplicate qualified source path")
            seen.add(name)
            raw = _read(root, f"{TEMPLATE_ID}/{lane}/{name}")
            total += len(raw)
            sha256 = hashlib.sha256(raw).hexdigest()
            if total > _MAX_TOTAL_BYTES or sha256 != entry["sha256"]:
                raise ValueError("Qualified source hash or closure size mismatch")
            result.append({"name": name, "content": raw.decode("utf-8"), "sha256": sha256,
                           "mediaType": entry["mediaType"], "templateId": TEMPLATE_ID,
                           "templateManifestSha256": MANIFEST_SHA256})
        return result
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise DomainError("Qualified source template integrity check failed", 503, "template_integrity") from exc
