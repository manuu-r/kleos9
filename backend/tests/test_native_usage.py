import math
import os

import pytest

from kleos import native_usage


@pytest.fixture
def cgroup(tmp_path, monkeypatch):
    monkeypatch.setattr(native_usage, "_CGROUP_ROOT", tmp_path)
    return tmp_path


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_cgroup_v2_reports_real_peaks_and_microseconds(cgroup):
    write(cgroup, "memory.peak", "2147483648\n")
    write(cgroup, "cpu.stat", "usage_usec 1250010\nuser_usec 1000000\nsystem_usec 250010\n")
    write(cgroup, "pids.peak", "42\n")
    assert native_usage.capture_usage() == {"scope": "task_cgroup", "memoryPeakBytes": 2147483648,
                                             "cpuUsageSeconds": 1.25001, "pidsPeak": 42}


@pytest.mark.parametrize("prefix", ["", "cpuacct/", "cpu,cpuacct/"])
def test_cgroup_v1_fallback_converts_nanoseconds_and_preserves_scope(cgroup, prefix):
    write(cgroup, "memory/memory.max_usage_in_bytes", "123456\n")
    write(cgroup, prefix + "cpuacct.usage", "2500000010\n")
    assert native_usage.capture_usage() == {"scope": "task_cgroup", "memoryPeakBytes": 123456,
                                             "cpuUsageSeconds": 2.50000001}


def test_v2_takes_precedence_over_v1_and_actual_zero_is_not_missing(cgroup):
    write(cgroup, "memory.peak", "0")
    write(cgroup, "memory/memory.max_usage_in_bytes", "999")
    write(cgroup, "cpu.stat", "usage_usec 0\n")
    write(cgroup, "cpuacct.usage", "9999999")
    assert native_usage.capture_usage() == {"scope": "task_cgroup", "memoryPeakBytes": 0, "cpuUsageSeconds": 0}


def test_absent_counters_are_omitted_not_fabricated(cgroup):
    assert native_usage.capture_usage() == {}
    write(cgroup, "memory.peak", "123")
    assert native_usage.capture_usage() == {"scope": "task_cgroup", "memoryPeakBytes": 123}


@pytest.mark.parametrize("invalid", ["max", "-1", "NaN", "inf", "1.5", "9" * 10000, str(2**63), "١٢"])
def test_malformed_unbounded_or_nonfinite_counters_are_ignored(cgroup, invalid):
    write(cgroup, "memory.peak", invalid)
    write(cgroup, "cpu.stat", "usage_usec " + invalid)
    write(cgroup, "pids.peak", invalid)
    assert native_usage.capture_usage() == {}


def test_malformed_v2_cpu_can_use_valid_v1_counter(cgroup):
    write(cgroup, "cpu.stat", "usage_usec NaN\n")
    write(cgroup, "cpuacct.usage", "1000000000")
    assert native_usage.capture_usage() == {"scope": "task_cgroup", "cpuUsageSeconds": 1}


def test_duplicate_cpu_counter_is_ambiguous_and_omitted(cgroup):
    write(cgroup, "cpu.stat", "usage_usec 1\nusage_usec 2\n")
    assert native_usage.capture_usage() == {}


def test_symlink_fifo_and_oversize_files_are_not_consumed(cgroup, tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("123")
    (cgroup / "memory.peak").symlink_to(outside)
    os.mkfifo(cgroup / "pids.peak")
    write(cgroup, "cpu.stat", "x" * 16385)
    assert native_usage.capture_usage() == {}


def test_observation_failure_never_changes_execution_outcome(cgroup, monkeypatch):
    monkeypatch.setattr(native_usage, "_read", lambda _: (_ for _ in ()).throw(OSError("no permission")))
    assert native_usage.capture_usage() == {}


def test_largest_accepted_counter_remains_finite(cgroup):
    write(cgroup, "memory.peak", str(2**63 - 1))
    write(cgroup, "cpu.stat", f"usage_usec {2**63 - 1}\n")
    for key, number in native_usage.capture_usage().items():
        if key != "scope":
            assert math.isfinite(number) and number >= 0
