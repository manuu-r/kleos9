import os
import stat
from pathlib import Path

import pytest

from kleos import application_runtime


@pytest.fixture
def runtime(tmp_path):
    work, flutter, pub = [tmp_path / name for name in ("job", "image-flutter", "image-pub")]
    for directory in (work, flutter / "bin/cache/dart-sdk/bin", flutter / ".git", pub / "hosted/pub.dev/pkg"):
        directory.mkdir(parents=True)
    (flutter / "bin/flutter").write_text("#!/bin/sh\nimmutable SDK fixture")
    (flutter / "bin/flutter").chmod(0o755)
    (flutter / "bin/cache/lockfile").write_text("SDK lock state")
    (flutter / "bin/cache/dart-sdk/bin/dart").write_text("dart executable fixture")
    (flutter / ".git/config").write_text("[core]\nrepositoryformatversion=0\n")
    (pub / "hosted/pub.dev/pkg/pubspec.yaml").write_text("name: pkg")
    return work, flutter, pub


def prepare(runtime):
    work, flutter, pub = runtime
    return application_runtime.prepare_application_runtime(work, _flutter_source=flutter, _pub_source=pub)


def test_private_sdk_git_cache_and_pub_are_independent_and_originals_immutable(runtime):
    work, flutter, pub = runtime
    before = {str(path): (path.read_bytes(), path.stat().st_ino) for source in (flutter, pub)
              for path in source.rglob("*") if path.is_file()}
    detail = prepare(runtime)
    sdk_copy, pub_copy = Path(detail["flutterRoot"]), Path(detail["pubCache"])
    assert sdk_copy == work / ".tmp/toolchains/flutter"
    assert pub_copy == work / ".tmp/toolchains/pub-cache"
    assert detail["preparation"] == "job-private-flutter-runtime-v1"
    assert stat.S_IMODE((sdk_copy / "bin/flutter").stat().st_mode) == 0o755
    assert stat.S_IMODE((pub_copy / "hosted/pub.dev/pkg/pubspec.yaml").stat().st_mode) == 0o644
    for source, destination in ((flutter, sdk_copy), (pub, pub_copy)):
        for original in source.rglob("*"):
            if original.is_file():
                copied = destination / original.relative_to(source)
                assert copied.read_bytes() == original.read_bytes()
                assert copied.stat().st_ino != original.stat().st_ino
                assert copied.stat().st_nlink == 1
    (sdk_copy / "bin/cache/lockfile").write_text("updated private SDK lock")
    (sdk_copy / ".git/config").write_text("private git configuration")
    (pub_copy / "hosted/pub.dev/pkg/pubspec.yaml").write_text("private mutation")
    assert before == {str(path): (path.read_bytes(), path.stat().st_ino) for source in (flutter, pub)
                      for path in source.rglob("*") if path.is_file()}


def test_safe_internal_file_and_directory_links_become_copies(runtime):
    _work, flutter, _pub = runtime
    (flutter / "bin/dart").symlink_to("cache/dart-sdk/bin/dart")
    (flutter / "dart-alias").symlink_to("bin/cache/dart-sdk", target_is_directory=True)
    copied = Path(prepare(runtime)["flutterRoot"])
    assert not (copied / "bin/dart").is_symlink()
    assert (copied / "bin/dart").read_text() == "dart executable fixture"
    assert (copied / "dart-alias/bin/dart").read_text() == "dart executable fixture"


@pytest.mark.parametrize("location", ["work", "scratch", "destination", "sdk", "pub"])
def test_reserved_directory_symlinks_and_cache_reuse_are_rejected(runtime, tmp_path, location):
    work, flutter, pub = runtime
    external = tmp_path / "external"
    external.mkdir()
    if location == "work":
        replacement = tmp_path / "work-link"
        replacement.symlink_to(work, target_is_directory=True)
        runtime = replacement, flutter, pub
    elif location == "scratch":
        (work / ".tmp").symlink_to(external, target_is_directory=True)
    elif location == "destination":
        (work / ".tmp").mkdir()
        (work / ".tmp/toolchains").symlink_to(external, target_is_directory=True)
    else:
        replacement = tmp_path / "source-link"
        replacement.symlink_to(flutter if location == "sdk" else pub, target_is_directory=True)
        runtime = (work, replacement, pub) if location == "sdk" else (work, flutter, replacement)
    with pytest.raises(ValueError):
        prepare(runtime)
    assert not list(external.iterdir())


@pytest.mark.parametrize("kind", ["outside_link", "cycle", "hardlink", "fifo"])
def test_source_escapes_cycles_hardlinks_and_special_files_fail_closed(runtime, tmp_path, kind):
    work, flutter, _pub = runtime
    path = flutter / "unsafe"
    if kind == "outside_link":
        outside = tmp_path / "private-file"
        outside.write_text("must not copy private data")
        path.symlink_to(outside)
    elif kind == "cycle":
        path.symlink_to(flutter, target_is_directory=True)
    elif kind == "hardlink":
        os.link(flutter / "bin/flutter", path)
    else:
        os.mkfifo(path)
    with pytest.raises(ValueError):
        prepare(runtime)
    assert not (work / ".tmp/toolchains").exists()


def test_preexisting_plain_runtime_never_silently_reused(runtime):
    work, _flutter, _pub = runtime
    destination = work / ".tmp/toolchains/flutter"
    destination.mkdir(parents=True)
    poisoned = destination / "bin"
    poisoned.write_text("untrusted generated executable")
    with pytest.raises(ValueError, match="already exists"):
        prepare(runtime)
    assert poisoned.read_text() == "untrusted generated executable"


def test_repeated_preparation_is_explicitly_rejected(runtime):
    prepare(runtime)
    with pytest.raises(ValueError, match="already exists"):
        prepare(runtime)


@pytest.mark.parametrize("budget", ["_SDK_MAX_BYTES", "_PUB_MAX_BYTES", "_MAX_ENTRIES", "_MAX_DEPTH"])
def test_copy_byte_entry_and_depth_budgets_are_enforced_before_runtime_creation(runtime, monkeypatch, budget):
    work, _flutter, _pub = runtime
    monkeypatch.setattr(application_runtime, budget, 1)
    with pytest.raises(ValueError, match="budget"):
        prepare(runtime)
    assert not (work / ".tmp/toolchains").exists()


def test_work_cannot_contain_or_live_inside_immutable_sources(runtime):
    work, flutter, pub = runtime
    with pytest.raises(ValueError, match="separate"):
        application_runtime.prepare_application_runtime(work, _flutter_source=work, _pub_source=pub)
    nested = flutter / "generated-job"
    nested.mkdir()
    with pytest.raises(ValueError, match="separate"):
        application_runtime.prepare_application_runtime(nested, _flutter_source=flutter, _pub_source=pub)


def test_sdk_copies_strip_privileged_mode_bits_and_execute_nothing(runtime, monkeypatch):
    import subprocess

    _work, flutter, _pub = runtime
    (flutter / "bin/flutter").chmod(0o6755)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("No executables may run in preparation"))
    copied = Path(prepare(runtime)["flutterRoot"])
    assert stat.S_IMODE((copied / "bin/flutter").stat().st_mode) == 0o755


def test_source_growth_after_inventory_is_detected_and_not_reused(runtime, monkeypatch):
    work, flutter, _pub = runtime
    original = application_runtime._copy_inventory

    def mutate(source, entries, target):
        if source == flutter:
            (flutter / "bin/flutter").write_text("changed immutable source during copy")
        return original(source, entries, target)

    monkeypatch.setattr(application_runtime, "_copy_inventory", mutate)
    with pytest.raises(ValueError, match="changed"):
        prepare(runtime)
    assert (work / ".tmp/toolchains").exists()
    with pytest.raises(ValueError, match="already exists"):
        prepare(runtime)


def test_optional_gradle_cache_is_independent_writable_and_leaves_seed_immutable(runtime, tmp_path):
    work, flutter, pub = runtime
    gradle = tmp_path / "image-gradle"
    (gradle / "caches/modules-2").mkdir(parents=True)
    seed = gradle / "caches/modules-2/plugin.jar"
    seed.write_bytes(b"immutable dependency archive fixture")
    result = application_runtime.prepare_application_runtime(work, _flutter_source=flutter,
                                                              _pub_source=pub, _gradle_source=gradle)
    destination = Path(result["gradleUserHome"])
    copied = destination / "caches/modules-2/plugin.jar"
    assert destination == work / ".tmp/toolchains/gradle-cache"
    assert copied.read_bytes() == seed.read_bytes()
    assert copied.stat().st_ino != seed.stat().st_ino
    copied.write_bytes(b"private gradle state")
    assert seed.read_bytes() == b"immutable dependency archive fixture"
    from kleos.native_runner import clean_environment
    environment = clean_environment(work)
    assert environment['GRADLE_USER_HOME'] == str(destination)
    assert '-Duser.home=' + str(work / '.home') in environment['JAVA_TOOL_OPTIONS']
    assert '-Djava.io.tmpdir=' + str(work / '.tmp') in environment['JAVA_TOOL_OPTIONS']


def test_legacy_web_image_without_gradle_cache_retains_runtime_contract(runtime, tmp_path):
    work, flutter, pub = runtime
    result = application_runtime.prepare_application_runtime(work, _flutter_source=flutter,
        _pub_source=pub, _gradle_source=tmp_path / "absent-gradle")
    assert "gradleUserHome" not in result
    assert Path(result["flutterRoot"]).is_dir() and Path(result["pubCache"]).is_dir()


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "oversize"])
def test_gradle_seed_obeys_same_safe_copy_contract(runtime, tmp_path, monkeypatch, unsafe):
    work, flutter, pub = runtime
    gradle = tmp_path / "gradle"
    gradle.mkdir()
    seed = gradle / "dependency.jar"
    seed.write_bytes(b"dependency")
    if unsafe == "symlink":
        (gradle / "outside").symlink_to(flutter / "bin/flutter")
    elif unsafe == "hardlink":
        os.link(seed, gradle / "linked.jar")
    else:
        monkeypatch.setattr(application_runtime, "_GRADLE_MAX_BYTES", 1)
    with pytest.raises(ValueError):
        application_runtime.prepare_application_runtime(work, _flutter_source=flutter,
            _pub_source=pub, _gradle_source=gradle)
    assert not (work / ".tmp/toolchains").exists()


def test_sdk_cache_timestamps_preserve_flutter_offline_bootstrap_relationship(runtime):
    _work, flutter, _pub = runtime
    tools = flutter / "packages/flutter_tools"
    tools.mkdir(parents=True)
    yaml, lock = tools / "pubspec.yaml", tools / "pubspec.lock"
    yaml.write_text("name: flutter_tools")
    lock.write_text("packages: {}")
    yaml_ns, lock_ns = 1700000000123456789, 1700000001123456789
    os.utime(yaml, ns=(yaml_ns, yaml_ns))
    os.utime(lock, ns=(lock_ns, lock_ns))
    stamp = flutter / "bin/cache/flutter_tools.stamp"
    stamp.write_text("qualified SDK compile key")
    stamp_ns = 1700000002123456789
    os.utime(stamp, ns=(stamp_ns, stamp_ns))
    private = Path(prepare(runtime)["flutterRoot"])
    copied_yaml = private / "packages/flutter_tools/pubspec.yaml"
    copied_lock = private / "packages/flutter_tools/pubspec.lock"
    assert copied_yaml.stat().st_mtime_ns == yaml_ns
    assert copied_lock.stat().st_mtime_ns == lock_ns
    assert copied_yaml.stat().st_mtime_ns <= copied_lock.stat().st_mtime_ns
    assert (private / "bin/cache/flutter_tools.stamp").stat().st_mtime_ns == stamp_ns
    assert yaml.stat().st_mtime_ns == yaml_ns and lock.stat().st_mtime_ns == lock_ns
