"""Best-effort task-cgroup telemetry, never billing or engineering evidence."""
import os
import stat
from pathlib import Path

_CGROUP_ROOT = Path("/sys/fs/cgroup")
_MAX_READ_BYTES = 16384
_MAX_COUNTER = 2**63 - 1


def _read(path):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(_MAX_READ_BYTES + 1)
        return raw.decode("ascii") if len(raw) <= _MAX_READ_BYTES else None
    except (OSError, ValueError, UnicodeError):
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _counter(raw):
    # Bound before int conversion, including Python's large-integer guard.
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value or len(value) > 19 or not value.isascii() or not value.isdecimal():
        return None
    number = int(value)
    return number if number <= _MAX_COUNTER else None


def _first_counter(paths):
    for path in paths:
        value = _counter(_read(path))
        if value is not None:
            return value
    return None


def capture_usage():
    """Read task-wide kernel counters; unavailable values are absent, not zero.

    Peaks include the trusted supervisor and every child in this cgroup. They
    do not identify a particular compiler or represent qualified measurements
    of a generated product. Counters are cumulative for this fresh task.
    """
    try:
        root = _CGROUP_ROOT
        metrics = {}
        memory = _first_counter([root / "memory.peak", root / "memory.max_usage_in_bytes",
                                 root / "memory/memory.max_usage_in_bytes"])
        if memory is not None:
            metrics["memoryPeakBytes"] = memory
        cpu = None
        raw = _read(root / "cpu.stat")
        if raw is not None:
            lines = [line.split() for line in raw.splitlines()]
            usages = [parts[1] for parts in lines if len(parts) == 2 and parts[0] == "usage_usec"]
            if len(usages) == 1:
                value = _counter(usages[0])
                if value is not None:
                    cpu = value / 1_000_000
        if cpu is None:
            nanos = _first_counter([root / "cpuacct.usage", root / "cpuacct/cpuacct.usage",
                                    root / "cpu,cpuacct/cpuacct.usage"])
            if nanos is not None:
                cpu = nanos / 1_000_000_000
        if cpu is not None:
            metrics["cpuUsageSeconds"] = cpu
        pids = _first_counter([root / "pids.peak", root / "pids/pids.peak"])
        if pids is not None:
            metrics["pidsPeak"] = pids
        return {"scope": "task_cgroup", **metrics} if metrics else {}
    except Exception:
        # Observation failures must not alter tool outcomes, gates or settlement.
        return {}
