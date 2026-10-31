"""Companion source contract; physical radio results are deliberately not claimed."""
import json
import plistlib
from pathlib import Path
from xml.etree import ElementTree

import yaml

from kleos.source_templates import MANIFEST_SHA256, load_source_templates

ROOT = Path(__file__).parents[1] / "docker/templates/watch-telemetry-v1"


def test_mobile_dependencies_match_qualified_sdk_and_are_exactly_pinned():
    spec = yaml.safe_load((ROOT / "application/pubspec.yaml").read_text())
    assert {key: value for key, value in spec["dependencies"].items() if key != "flutter"} == {
        "flutter_reactive_ble": "5.4.2", "permission_handler": "12.0.1",
        "shared_preferences": "2.5.3", "device_info_plus": "11.5.0"}
    assert spec["dev_dependencies"]["flutter_lints"] == "5.0.0"
    options = yaml.safe_load((ROOT / "application/analysis_options.yaml").read_text())
    assert options["include"] == "package:flutter_lints/flutter.yaml"
    assert all(options["analyzer"]["language"].values())
    assert spec["dependency_overrides"] == {
        "reactive_ble_mobile": "5.4.2", "reactive_ble_platform_interface": "5.4.2"}


def test_android_requests_only_necessary_foreground_ble_permissions():
    root = ElementTree.fromstring((ROOT / "application/android/app/src/main/AndroidManifest.xml").read_text())
    android = "{http://schemas.android.com/apk/res/android}"
    permissions = {node.attrib[android + "name"]: node.attrib for node in root.findall("uses-permission")}
    for permission in ["BLUETOOTH", "BLUETOOTH_ADMIN", "ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION"]:
        assert permissions[f"android.permission.{permission}"][android + "maxSdkVersion"] == "30"
    assert permissions["android.permission.BLUETOOTH_SCAN"][android + "usesPermissionFlags"] == "neverForLocation"
    assert "android.permission.BLUETOOTH_CONNECT" in permissions
    assert not any("BACKGROUND" in name or "INTERNET" in name for name in permissions)
    platform = (ROOT / "application/lib/ble_platform.dart").read_text()
    assert "sdk >= 31" in platform
    assert "Permission.bluetoothScan" in platform and "Permission.bluetoothConnect" in platform
    assert "Permission.locationWhenInUse" in platform


def test_ios_source_has_consent_purpose_and_corresponding_plugin_macro():
    plist = plistlib.loads((ROOT / "application/ios/Runner/Info.plist").read_bytes())
    assert len(plist["NSBluetoothAlwaysUsageDescription"]) > 30
    assert len(plist["NSBluetoothPeripheralUsageDescription"]) > 30
    assert "UIBackgroundModes" not in plist
    assert "PERMISSION_BLUETOOTH=1" in (ROOT / "application/ios/Podfile").read_text()


def test_ble_code_is_lazy_guarded_and_contains_no_sample_physical_reading():
    main = (ROOT / "application/lib/main.dart").read_text()
    platform = (ROOT / "application/lib/ble_platform.dart").read_text()
    controller = (ROOT / "application/lib/ble_controller.dart").read_text()
    assert "WatchTelemetry.decode([" not in main
    assert "reading == null" in main and "'—'" in main
    assert "!kIsWeb" in platform
    assert "if (!supported)" in platform and "throw UnsupportedError" in platform
    assert "_client ??= FlutterReactiveBle()" in platform
    assert "if (!supported || _disposed)" in controller
    assert "WatchTelemetry.decode(bytes)" in controller
    assert "telemetry = null" in controller
    assert "example UUIDs" in main


def test_manifest_exposes_ble_sources_permissions_and_tests_without_generated_binaries():
    files = load_source_templates({"validationProfile": "watch-telemetry-v1"}, "application")
    names = {item["name"] for item in files}
    assert {"lib/ble_controller.dart", "lib/ble_platform.dart", "lib/main.dart", "lib/watch_protocol.dart",
            "android/app/src/main/AndroidManifest.xml", "ios/Runner/Info.plist", "ios/Podfile",
            "test/ble_controller_test.dart", "test/companion_test.dart"}.issubset(names)
    assert len(names) <= 50
    assert all(item["templateManifestSha256"] == MANIFEST_SHA256 for item in files)
    assert not any(name.endswith((".apk", ".ipa", ".jar", ".zip")) for name in names)
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert len(manifest["lanes"]["firmware"]) == 5


def test_immutable_dependency_lock_matches_all_qualified_direct_pins():
    lock = yaml.safe_load((ROOT / "application/pubspec.lock").read_text())
    expected = {"flutter_reactive_ble": "5.4.2", "reactive_ble_mobile": "5.4.2",
                "reactive_ble_platform_interface": "5.4.2", "permission_handler": "12.0.1",
                "shared_preferences": "2.5.3", "device_info_plus": "11.5.0", "flutter_lints": "5.0.0"}
    for name, version in expected.items():
        assert lock["packages"][name]["version"] == version
    for package in lock["packages"].values():
        assert package["source"] in {"hosted", "sdk"}
        if package["source"] == "hosted":
            assert package["description"]["url"] == "https://pub.dev"
            assert len(package["description"]["sha256"]) == 64
