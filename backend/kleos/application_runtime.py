"""Prepare private writable Flutter state from immutable image-owned toolchains."""
import os
import stat
from pathlib import Path

_FLUTTER_SOURCE = Path("/opt/flutter")
_PUB_SOURCE = Path("/opt/pub-cache")
_GRADLE_SOURCE = Path("/opt/gradle-cache")
_SDK_MAX_BYTES = 4 * 1024**3
_PUB_MAX_BYTES = 2 * 1024**3
_GRADLE_MAX_BYTES = 4 * 1024**3
_MAX_ENTRIES = 150000
_MAX_DEPTH = 64


def _plain_directory(path):
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Application runtime requires plain source/work directories")


def _inventory(source, ceiling):
    _plain_directory(source)
    root = source.resolve(strict=True)
    items, total = [], 0

    def visit(path, relative, ancestors, depth):
        nonlocal total
        if depth > _MAX_DEPTH or len(items) >= _MAX_ENTRIES:
            raise ValueError("Application runtime tree exceeds its entry/depth budget")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError("Application runtime source link escapes its immutable tree")
        info = resolved.stat()
        identity = (info.st_dev, info.st_ino)
        if stat.S_ISDIR(info.st_mode):
            if identity in ancestors:
                raise ValueError("Application runtime source contains a directory-link cycle")
            items.append((relative, None, None))
            # Bound enumeration before sorting: a malformed tree cannot allocate an unbounded list.
            children = []
            with os.scandir(resolved) as entries:
                for entry in entries:
                    children.append(entry.name)
                    if len(children) + len(items) > _MAX_ENTRIES:
                        raise ValueError("Application runtime tree exceeds its entry budget")
            for name in sorted(children):
                visit(resolved / name, relative / name, ancestors | {identity}, depth + 1)
        elif stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                raise ValueError("Application runtime source files must not be hardlinked")
            total += info.st_size
            if total > ceiling:
                raise ValueError("Application runtime tree exceeds its byte budget")
            items.append((relative, resolved, info))
        else:
            raise ValueError("Application runtime source contains a special file")

    visit(root, Path(), set(), 0)
    return root, items


def _copy_inventory(root, inventory, destination):
    destination.mkdir(mode=0o755)
    for relative, source, expected in inventory:
        if relative == Path():
            continue
        target = destination / relative
        if source is None:
            target.mkdir(mode=0o755)
            continue
        # Revalidate after inventory. No links, device files or external targets are opened.
        if source.is_symlink() or not source.resolve(strict=True).is_relative_to(root):
            raise ValueError("Application runtime source changed during preparation")
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            current = os.fstat(descriptor)
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                    or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                    != (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns)):
                raise ValueError("Application runtime source changed during preparation")
            # Each destination is newly created; copying never shares writable inodes with the image.
            with os.fdopen(descriptor, "rb", closefd=False) as incoming, target.open("xb") as outgoing:
                remaining = current.st_size
                while remaining:
                    chunk = incoming.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Application runtime source was truncated during preparation")
                    outgoing.write(chunk)
                    remaining -= len(chunk)
                if incoming.read(1):
                    raise ValueError("Application runtime source grew during preparation")
            target.chmod(0o755 if current.st_mode & 0o111 else 0o644)
            # Flutter bootstrap compares pubspec.yaml/lock mtimes before it can
            # honor --offline. Preserve baked cache relationships rather than
            # accidentally starting an internal online SDK pub upgrade per job.
            os.utime(target, ns=(current.st_atime_ns, current.st_mtime_ns), follow_symlinks=False)
        finally:
            os.close(descriptor)


def prepare_application_runtime(work: Path, *, _flutter_source=None, _pub_source=None, _gradle_source=None) -> dict:
    """Copy SDK/git/cache/pub state into a fresh reserved job directory; execute nothing.

    Private keyword sources exist for offline fixtures only. Production callers use
    the fixed image paths. Existing runtime directories are rejected even if empty.
    A failed copy is retained as an unusable reserved directory, never reused as a
    partially qualified SDK on retry.
    """
    work = Path(work)
    _plain_directory(work)
    work = work.resolve(strict=True)
    flutter = Path(_flutter_source) if _flutter_source is not None else _FLUTTER_SOURCE
    pub = Path(_pub_source) if _pub_source is not None else _PUB_SOURCE
    gradle = Path(_gradle_source) if _gradle_source is not None else _GRADLE_SOURCE
    sources = [("flutter", flutter, _SDK_MAX_BYTES), ("pub-cache", pub, _PUB_MAX_BYTES)]
    if gradle.exists() or gradle.is_symlink():
        sources.append(("gradle-cache", gradle, _GRADLE_MAX_BYTES))
    for _name, source, _ceiling in sources:
        _plain_directory(source)
        resolved = source.resolve(strict=True)
        if work.is_relative_to(resolved) or resolved.is_relative_to(work):
            raise ValueError("Application runtime sources must be separate from generated work")
    scratch = work / ".tmp"
    if scratch.is_symlink() or (scratch.exists() and not scratch.is_dir()):
        raise ValueError("Unsafe application scratch directory")
    scratch.mkdir(mode=0o755, exist_ok=True)
    destination = scratch / "toolchains"
    if os.path.lexists(destination):
        raise ValueError("Reserved application runtime already exists; refusing untrusted cache reuse")
    inventories = [(name, *_inventory(source, ceiling)) for name, source, ceiling in sources]
    # Exclusive reservation prevents prior generated content from becoming executable toolchain state.
    destination.mkdir(mode=0o755)
    for name, root, items in inventories:
        _copy_inventory(root, items, destination / name)
    return {"flutterRoot": str(destination / "flutter"), "pubCache": str(destination / "pub-cache"),
            "preparation": "job-private-flutter-runtime-v1",
            **({"gradleUserHome": str(destination / "gradle-cache")} if any(name == "gradle-cache" for name, _, _ in sources) else {})}
