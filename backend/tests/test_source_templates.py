import hashlib
import json
import shutil
from pathlib import Path

import pytest

from kleos import source_templates as templates
from kleos.contracts import DomainError


@pytest.mark.parametrize("lane,expected", [
    ("firmware", {"CMakeLists.txt", "prj.conf", "src/main.c", "src/watch_protocol.c", "src/watch_protocol.h"}),
    ("application", {"analysis_options.yaml", "pubspec.yaml", "lib/main.dart", "lib/watch_protocol.dart", "web/index.html",
                     "lib/ble_controller.dart", "lib/ble_platform.dart",
                     "android/app/src/main/AndroidManifest.xml", "ios/Runner/Info.plist", "ios/Podfile",
                     "test/ble_controller_test.dart", "test/ble_platform_test.dart", "test/companion_test.dart"}),
])
def test_real_profile_has_complete_pinned_source_closure(lane, expected):
    files = templates.load_source_templates({"validationProfile": "watch-telemetry-v1"}, lane)
    if lane == "application" and (templates._REPO_ROOT / "watch-telemetry-v1/application/pubspec.lock").is_file():
        expected = expected | {"pubspec.lock"}
    assert {item["name"] for item in files} == expected
    for item in files:
        assert item["sha256"] == hashlib.sha256(item["content"].encode()).hexdigest()
        assert item["templateId"] == "watch-telemetry-v1"
        assert item["templateManifestSha256"] == templates.MANIFEST_SHA256
        assert not item["name"].endswith((".elf", ".bin", ".zip", ".report"))
    files[0]["content"] = "caller mutation"
    assert templates.load_source_templates({"validationProfile": "watch-telemetry-v1"}, lane)[0]["content"] != "caller mutation"


@pytest.mark.parametrize("requirements,lane", [
    ({}, "firmware"), ({"validationProfile": "other"}, "firmware"),
    ({"validationProfile": " watch-telemetry-v1"}, "firmware"),
    ({"validationProfile": "WATCH-TELEMETRY-V1"}, "firmware"),
    ({"validationProfile": ["watch-telemetry-v1"]}, "firmware"),
    ({"validationProfile": "watch-telemetry-v1"}, "mechanics"),
    ({"validationProfile": "watch-telemetry-v1"}, "../../firmware"), (None, "application"),
])
def test_non_exact_profiles_or_lanes_perform_no_io(monkeypatch, requirements, lane):
    monkeypatch.setattr(templates, "_root", lambda: pytest.fail("Unexpected template I/O"))
    assert templates.load_source_templates(requirements, lane) == []


@pytest.fixture
def copied(tmp_path, monkeypatch):
    root = tmp_path / "templates"
    shutil.copytree(templates._REPO_ROOT, root)
    monkeypatch.setattr(templates, "_IMAGE_ROOT", root)
    monkeypatch.setattr(templates, "_REPO_ROOT", tmp_path / "unavailable")
    return root


@pytest.mark.parametrize("mutation", ["manifest", "source", "missing", "symlink", "oversize"])
def test_template_tamper_fails_closed(copied, mutation, tmp_path):
    target = copied / "watch-telemetry-v1/firmware/src/main.c"
    if mutation == "manifest":
        (copied / "watch-telemetry-v1/manifest.json").write_text("{}")
    elif mutation == "source":
        target.write_text("tampered")
    elif mutation == "missing":
        target.unlink()
    elif mutation == "symlink":
        target.unlink()
        secret = tmp_path / "private"
        secret.write_text("outside private file")
        target.symlink_to(secret)
    else:
        target.write_bytes(b"x" * (256 * 1024 + 1))
    with pytest.raises(DomainError) as error:
        templates.load_source_templates({"validationProfile": "watch-telemetry-v1"}, "firmware")
    assert error.value.status == 503


def test_installed_template_root_does_not_fall_back_after_bad_manifest(copied, monkeypatch):
    monkeypatch.setattr(templates, "_REPO_ROOT", Path(__file__).parents[1] / "docker/templates")
    (copied / "watch-telemetry-v1/manifest.json").unlink()
    with pytest.raises(DomainError):
        templates.load_source_templates({"validationProfile": "watch-telemetry-v1"}, "firmware")


@pytest.mark.parametrize("name", ["../private", "/private", "a/../../private", "a\\private", "a//private"])
def test_manifest_source_paths_cannot_escape_root(copied, monkeypatch, name):
    path = copied / "watch-telemetry-v1/manifest.json"
    value = json.loads(path.read_bytes())
    value["lanes"]["firmware"][0]["name"] = name
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    # Exercise path validation independently of the preceding manifest pin guard.
    monkeypatch.setattr(templates, "MANIFEST_SHA256", hashlib.sha256(raw).hexdigest())
    with pytest.raises(DomainError):
        templates.load_source_templates({"validationProfile": "watch-telemetry-v1"}, "firmware")


def test_control_image_and_all_compose_control_surfaces_receive_single_template_tree():
    import yaml

    root = Path(__file__).parents[1]
    dockerfile = (root / "docker/Dockerfile").read_text()
    assert "COPY docker/templates ./templates" in dockerfile
    compose = yaml.safe_load((root / "compose.yaml").read_text())
    mount = "./docker/templates:/opt/kleos/templates:ro"
    for service in ["initialize", "migrate", "api", "companion-preview", "engineering", "connectors"]:
        assert mount in compose["services"][service]["volumes"]
