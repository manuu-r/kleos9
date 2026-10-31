"""Data-only input/profile/package checks and fixed Bambu Studio CLI invocation."""

import hashlib
import io
import json
import math
import stat
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import PurePosixPath

from .bambu_contracts import MAX_PACKAGE_BYTES, STUDIO_VERSION, model_catalog

PROFILE_FILES = ("machine.json", "process.json", "filament.json")


def mesh_bounds(raw):
    # Only bounded binary STL is accepted. It cannot carry a 3MF's scripts/config.
    if not 84 < len(raw) <= MAX_PACKAGE_BYTES:
        raise ValueError("Use a bounded binary STL mesh")
    count = struct.unpack_from("<I", raw, 80)[0]
    if not 1 <= count <= 300000 or len(raw) != 84 + count * 50:
        raise ValueError("Invalid binary STL triangle count")
    low, high = [math.inf] * 3, [-math.inf] * 3
    for index in range(count):
        values = struct.unpack_from("<12f", raw, 84 + 50 * index)
        if any(not math.isfinite(v) or abs(v) > 10000 for v in values):
            raise ValueError("Invalid mesh coordinates")
        for vertex in range(3):
            for axis in range(3):
                value = values[3 + vertex * 3 + axis]
                low[axis], high[axis] = min(low[axis], value), max(high[axis], value)
    dimensions = [b - a for a, b in zip(low, high, strict=True)]
    if any(d <= 0 for d in dimensions):
        raise ValueError("The printable mesh must have nonzero dimensions")
    return dimensions


def load_profile(root, expected_hash):
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 50000:
        raise ValueError("Invalid profile manifest")
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("Profile bundle identity mismatch")
    manifest = json.loads(raw)
    if manifest["studioVersion"] != STUDIO_VERSION or set(manifest["files"]) != set(PROFILE_FILES):
        raise ValueError("Use the pinned Studio version and three complete exported profiles")
    configs = {}
    for name in PROFILE_FILES:
        path = root / name
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            raise ValueError("Invalid profile file")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != manifest["files"][name]:
            raise ValueError("Profile checksum mismatch")
        config = json.loads(content)
        if not isinstance(config, dict) or config.get("inherits") or config.get("include") or config.get("post_process"):
            raise ValueError("Profiles must be fully resolved and have no post-processing commands")
        configs[name] = config
    volume = manifest["buildVolumeMm"]
    if (not isinstance(volume, list) or len(volume) != 3
            or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
                   or not 1 <= v <= 1000 for v in volume)):
        raise ValueError("Profile requires an explicit build volume")
    models = {m["id"]: m for m in model_catalog()["models"]}
    model = models.get(manifest["printerModelId"])
    machine = configs["machine.json"]
    if (not model or machine.get("printer_model") != model["name"]
            or manifest["nozzleMm"] not in model["nozzlesMm"]
            or any(float(n) != manifest["nozzleMm"] for n in machine.get("nozzle_diameter", []))
            or not machine.get("nozzle_diameter")
            or configs["filament.json"].get("filament_type") != [manifest["material"]]):
        raise ValueError("Profile model, nozzle or material identity mismatch")
    area = [list(map(float, point.split("x"))) for point in machine["printable_area"]]
    if not area or any(len(point) != 2 or any(not math.isfinite(v) for v in point) for point in area):
        raise ValueError("Invalid printable bed polygon")
    dimensions = [max(p[a] for p in area) - min(p[a] for p in area) for a in (0, 1)] + [float(machine["printable_height"])]
    if any(not math.isfinite(d) or abs(d - v) > 1e-6 for d, v in zip(dimensions, volume, strict=True)):
        raise ValueError("Build volume differs from the trusted machine profile")
    return manifest


def inspect_package(raw):
    try:
        return _inspect_package(raw)
    except (zipfile.BadZipFile, ET.ParseError, RuntimeError, NotImplementedError) as exc:
        raise ValueError("Malformed sliced package") from exc


def _inspect_package(raw):
    """Bound ZIP expansion; never extract or execute generated G-code."""
    if not raw or len(raw) > MAX_PACKAGE_BYTES:
        raise ValueError("Sliced package exceeds the 16 MiB transfer limit")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if len(entries) > 256 or len(names) != len(set(names)) or sum(i.file_size for i in entries) > 64 * 1024 * 1024:
            raise ValueError("Invalid sliced package expansion")
        for item in entries:
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename
                    or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1
                    or item.file_size > 32 * 1024 * 1024):
                raise ValueError("Unsafe sliced package entry")
        gcode = [n for n in names if n.endswith(".gcode")]
        if gcode != ["Metadata/plate_1.gcode"]:
            raise ValueError("Only one sliced plate is supported; unsliced 3MF is not printable output")
        content = archive.read(gcode[0])
        if not content.strip() or b"\x00" in content:
            raise ValueError("Sliced package has no readable G-code")
        metrics = {"plateCount": 1, "gcodeSha256": hashlib.sha256(content).hexdigest(),
                   "estimatedSeconds": None, "filamentGrams": None}
        if "Metadata/slice_info.config" in names:
            info = archive.read("Metadata/slice_info.config")
            if len(info) > 500000 or b"<!" in info:
                raise ValueError("Invalid slice metadata")
            root = ET.fromstring(info)
            values = {e.get("key"): e.get("value") for e in root.iter("metadata")}
            for key, target in (("prediction", "estimatedSeconds"), ("weight", "filamentGrams")):
                if key in values:
                    number = float(values[key])
                    if not math.isfinite(number) or number < 0 or number > 1e9:
                        raise ValueError("Invalid slicer estimate")
                    metrics[target] = number
        return metrics


def slice_mesh(work, profile_root, bundle_hash, execute):
    manifest = load_profile(profile_root, bundle_hash)
    dimensions = mesh_bounds((work / "input.stl").read_bytes())
    if any(d > v for d, v in zip(dimensions, manifest["buildVolumeMm"], strict=True)):
        raise ValueError("Part exceeds the selected printer build volume; scaling is never automatic")
    args = ["xvfb-run", "-a", "-s", "-screen 0 1024x768x24 -nolisten tcp",
            "/usr/bin/env", "LIBGL_ALWAYS_SOFTWARE=1", "/opt/bambu/AppRun",
            "--debug", "2", "--load-settings", str(profile_root / "machine.json") + ";" + str(profile_root / "process.json"),
            "--load-filaments", str(profile_root / "filament.json"), "--arrange", "1", "--slice", "1",
            "--export-3mf", "output.gcode.3mf", "input.stl"]
    code, _logs = execute(args, work, 300)
    if code:
        raise RuntimeError("Bambu Studio slicing failed; no printable package is available")
    output = work / "output.gcode.3mf"
    if output.is_symlink() or not output.is_file() or output.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("Slicer did not produce a bounded regular package")
    raw = output.read_bytes()
    return raw, {**inspect_package(raw), "dimensionsMm": dimensions, "studioVersion": STUDIO_VERSION,
                 "bundleSha256": bundle_hash, "sourceSha256": hashlib.sha256((work / "input.stl").read_bytes()).hexdigest()}
