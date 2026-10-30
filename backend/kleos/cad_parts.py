"""Physical CAD part exports and immutable manifest closure checks.

All geometry stays in assembled millimetre coordinates. Exploding an assembly
is a display operation and must never alter these manufacturing files.
"""
import hashlib
import json
import math
import re
import runpy
import struct
from pathlib import Path, PurePosixPath

MAX_PARTS = 20
MAX_MANIFEST_BYTES = 512 * 1024
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
ZERO = {"x": 0, "y": 0, "z": 0}


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def _vector(value, positive=False):
    if not isinstance(value, dict) or set(value) != {"x", "y", "z"}:
        raise ValueError("CAD vectors require x/y/z millimetre values")
    for number in value.values():
        if (isinstance(number, bool) or not isinstance(number, (int, float)) or abs(number) > 1_000_000
                or not math.isfinite(number) or (positive and number <= 0)):
            raise ValueError("CAD vectors must be finite and within the supported millimetre range")
    return value


def _path(value):
    if not isinstance(value, str) or len(value) > 240 or any(ord(char) < 32 for char in value):
        raise ValueError("Invalid CAD artifact path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value or not value.startswith("native/outputs/"):
        raise ValueError("CAD artifact references must stay inside native/outputs/")
    return value


def parse_assembly_manifest(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("CAD assembly manifest exceeds its supported size")
    try:
        value = json.loads(raw)
    except RecursionError as exc:
        raise ValueError('CAD assembly manifest nesting is unsupported') from exc
    pending = [(value, 0)]
    while pending:
        node, depth = pending.pop()
        if depth > 64:
            raise ValueError('CAD assembly manifest nesting is unsupported')
        if isinstance(node, dict):
            pending.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            pending.extend((child, depth + 1) for child in node)
    if (not isinstance(value, dict) or value.get("schemaVersion") != "cad-assembly-v1"
            or value.get("units") != "mm" or value.get("coordinateFrame") != "assembled"):
        raise ValueError("Unsupported CAD assembly manifest or coordinate frame")
    parts, assembly = value.get("parts"), value.get("assembly")
    if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_PARTS or not isinstance(assembly, dict):
        raise ValueError("CAD assembly needs a bounded list of actual physical parts")
    ids, paths = set(), set()
    for entry in [assembly, *parts]:
        if not isinstance(entry, dict):
            raise ValueError("CAD assembly/part entries must be objects")
        bounds = entry.get("bounds")
        if not isinstance(bounds, dict):
            raise ValueError("Actual CAD bounds are required")
        for key in ("min", "max", "center", "size"):
            _vector(bounds.get(key), positive=key == "size")
        for axis in ZERO:
            if (abs(bounds["max"][axis] - bounds["min"][axis] - bounds["size"][axis]) > 1e-5
                    or abs((bounds["max"][axis] + bounds["min"][axis]) / 2 - bounds["center"][axis]) > 1e-5):
                raise ValueError("CAD bounds and centres are inconsistent")
        for kind in ("step", "stl"):
            ref = entry.get(kind)
            if not isinstance(ref, dict) or not re.fullmatch(r"[a-f0-9]{64}", str(ref.get("sha256", ""))):
                raise ValueError("CAD files require SHA-256 references")
            path = _path(ref.get("path"))
            if path in paths or not path.endswith("." + kind):
                raise ValueError("CAD file references are duplicated or have the wrong format")
            paths.add(path)
        if entry is assembly:
            continue
        ident = entry.get("id")
        if not isinstance(ident, str) or not IDENTIFIER.fullmatch(ident) or ident in ids:
            raise ValueError("Physical part IDs must be stable and unique")
        ids.add(ident)
        if entry.get("quantity") != 1 or isinstance(entry.get("quantity"), bool):
            raise ValueError("Every exported physical instance has quantity 1")
        if not isinstance(entry.get("label"), str) or not 1 <= len(entry["label"]) <= 120:
            raise ValueError("Physical part labels must be bounded text")
        transform = entry.get("transform")
        if not isinstance(transform, dict) or any(transform.get(key) != ZERO for key in ("translation", "rotation")):
            raise ValueError("Manufacturing geometry must retain assembled coordinates without display transforms")
    expected = "monolithic" if len(parts) == 1 else value.get("separation")
    if expected not in {"monolithic", "named_parts", "separate_solids"} or value.get("separation") != expected:
        raise ValueError("CAD separation status does not match its actual parts")
    return value


def validate_manifest_closure(manifest, files):
    """Check only exact bytes already in the caller's authorized artifact closure."""
    manifest = parse_assembly_manifest(json.dumps(manifest, allow_nan=False).encode())
    refs = [entry[kind] for entry in [manifest["assembly"], *manifest["parts"]] for kind in ("step", "stl")]
    for ref in refs:
        raw = files.get(ref["path"])
        if not isinstance(raw, bytes) or sha256(raw) != ref["sha256"]:
            raise ValueError(f"CAD part closure is missing or changed: {ref['path']}")
    return {"schemaVersion": "cad-assembly-v1", "fileCount": len(refs), "partCount": len(manifest["parts"]),
            "coordinateFrame": "assembled", "units": "mm", "closureVerified": True}


def _shape_bounds(shape):
    box = shape.bounding_box()
    low = {key: float(getattr(box.min, key.upper())) for key in ZERO}
    high = {key: float(getattr(box.max, key.upper())) for key in ZERO}
    result = {"min": low, "max": high,
              "size": {key: high[key] - low[key] for key in ZERO},
              "center": {key: (high[key] + low[key]) / 2 for key in ZERO}}
    for key, value in result.items():
        _vector(value, positive=key == "size")
    return result


def _valid_solid(shape):
    if not all(hasattr(shape, name) for name in ("is_valid", "volume", "solids", "bounding_box")):
        raise ValueError("Physical CAD output must be an actual build123d shape")
    valid = shape.is_valid
    if not (valid() if callable(valid) else valid) or not math.isfinite(shape.volume) or shape.volume <= 0:
        raise ValueError("Physical CAD part must be a valid positive-volume solid")


def stl_bounds(raw):
    """Bounded mesh bounds only; this does not certify slicing/printability."""
    count = struct.unpack_from("<I", raw, 80)[0] if len(raw) >= 84 else 0
    points = []
    if count and count <= 200_000 and len(raw) == 84 + count * 50:
        for index in range(count):
            values = struct.unpack_from("<12f", raw, 84 + index * 50)
            points.extend((values[3:6], values[6:9], values[9:12]))
    else:
        if len(raw) > 16 * 1024 * 1024 or not raw.lstrip().startswith(b"solid"):
            raise ValueError("Unsupported/oversized CAD STL mesh")
        points = [tuple(map(float, match)) for match in re.findall(
            rb"\bvertex\s+([-+\deE.]+)\s+([-+\deE.]+)\s+([-+\deE.]+)", raw)]
        if not points or len(points) % 3 or len(points) > 600_000:
            raise ValueError("CAD STL contains no bounded triangle mesh")
    if any(not math.isfinite(value) or abs(value) > 1_000_000 for point in points for value in point):
        raise ValueError("CAD STL contains nonfinite or unsupported coordinates")
    return {"min": dict(zip(ZERO, (min(point[index] for point in points) for index in range(3)))),
            "max": dict(zip(ZERO, (max(point[index] for point in points) for index in range(3))))}


def _fingerprint(shape):
    """Geometry/placement identity, independent of STEP timestamps and export order."""
    vertices, triangles = shape.tessellate(0.1, 0.2)
    if len(triangles) > 100_000:
        raise ValueError("CAD part tessellation exceeds the identity ceiling")
    points = [tuple(round(float(value), 6) for value in vertex.to_tuple()) for vertex in vertices]
    faces = sorted(tuple(sorted(points[index] for index in triangle)) for triangle in triangles)
    return sha256(json.dumps(faces, separators=(",", ":")).encode())


def _as_shape(value):
    # ShapeList in build123d 0.10 also exposes solids(), but is not a Shape
    # and has no volume. Normalize nonempty boolean result collections.
    if hasattr(value, "solids") and hasattr(value, "volume"):
        return value
    from build123d import Compound
    shapes = list(value) if value is not None else []
    if not shapes:
        raise ValueError("CAD boolean union returned no physical geometry")
    return Compound(children=shapes)


def _volume(value):
    """Measure actual boolean residuals, including nested/empty ShapeLists."""
    if value is None or not value:
        return 0.0
    if hasattr(value, "volume"):
        result = float(value.volume)
    else:
        result = sum(_volume(shape) for shape in value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("CAD boolean operation returned an invalid residual volume")
    return result


def _coverage(assembly, parts):
    if not parts:
        raise ValueError("No physical CAD parts exist")
    # OCCT subtraction of a Compound with touching solids can incorrectly keep
    # a complete child when its tool is the same fused union. Compare actual
    # regularized solids on both sides, preserving separate manufacturing files.
    assembly_solids = list(assembly.solids())
    if not assembly_solids:
        raise ValueError("CAD assembly contains no physical solids")
    assembled_union = assembly_solids[0]
    for solid in assembly_solids[1:]:
        assembled_union = _as_shape(assembled_union.fuse(solid))
    union = parts[0]
    for part in parts[1:]:
        union = _as_shape(union.fuse(part))
    missing, extra = assembled_union - union, union - assembled_union
    missing_volume = _volume(missing)
    extra_volume = _volume(extra)
    overlap = max(0, sum(_volume(part) for part in parts) - _volume(union))
    assembly_overlap = max(0, _volume(assembly) - _volume(assembled_union))
    tolerance = max(1e-4, float(assembly.volume) * 1e-7)
    if any(not math.isfinite(number) or number > tolerance
           for number in (missing_volume, extra_volume, overlap, assembly_overlap)):
        raise ValueError("Named/exported parts do not form the assembly exactly, or physical parts overlap")
    return {"missingVolumeMm3": missing_volume, "extraVolumeMm3": extra_volume,
            "overlapVolumeMm3": overlap, "assemblyOverlapVolumeMm3": assembly_overlap, "toleranceMm3": tolerance}


def export_build123d_parts(source_path="enclosure.py", output_directory="outputs"):
    """Called only by the isolated native builder under its generated-code UID."""
    from build123d import export_step, export_stl

    source = Path(source_path)
    original = source.read_bytes()
    scope = runpy.run_path(str(source))
    assembly = scope.get("assembly")
    if assembly is None:
        raise ValueError("enclosure.py must define assembly")
    if source.read_bytes() != original:
        raise ValueError("CAD generation modified its pinned source")
    _valid_solid(assembly)
    definitions = scope.get("parts")
    if not isinstance(definitions, dict):
        # Older source commonly uses `parts` as a list/BuildPart scratch
        # variable. Only the explicit mapping opts into named-part metadata.
        definitions = None
    selected = []
    if definitions is not None:
        if not isinstance(definitions, dict) or not 1 <= len(definitions) <= MAX_PARTS:
            raise ValueError("parts must be a bounded mapping of stable physical part IDs")
        for ident, entry in sorted(definitions.items()):
            if not isinstance(ident, str) or not IDENTIFIER.fullmatch(ident) or not isinstance(entry, dict):
                raise ValueError("Each named CAD part needs a safe ID and {shape,label} entry")
            shape = entry.get("shape")
            if shape is None or not hasattr(shape, "solids"):
                raise ValueError("Named CAD part is missing its actual shape")
            solids = list(shape.solids())
            quantity = entry.get("quantity", 1)
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity != len(solids):
                raise ValueError("Part quantity must match actual separate solid instances; provide each placement")
            label = entry.get("label", ident)
            if not isinstance(label, str) or not 1 <= len(label) <= 120:
                raise ValueError("Named CAD part label is invalid")
            instances = sorted(solids, key=_fingerprint)
            for index, solid in enumerate(instances):
                part_id = ident if quantity == 1 else f"{ident}-instance-{index + 1:03d}"
                if not IDENTIFIER.fullmatch(part_id):
                    raise ValueError("Named CAD instance ID exceeds its bound")
                selected.append((part_id, solid, {"label": label, "componentId": ident,
                    "groupQuantity": quantity, "instanceIndex": index + 1, "material": entry.get("material"),
                    "source": "explicit"}))
        separation = "named_parts"
    else:
        solids = sorted(list(assembly.solids()), key=_fingerprint)
        for index, solid in enumerate(solids):
            selected.append(("solid-" + _fingerprint(solid)[:20], solid,
                             {"label": f"Solid {index + 1}", "source": "assembly_solids"}))
        separation = "separate_solids"
    if not 1 <= len(selected) <= MAX_PARTS or len({ident for ident, _, _ in selected}) != len(selected):
        raise ValueError("CAD physical instance count or stable identities are ambiguous")
    for _, shape, _ in selected:
        _valid_solid(shape)
    coverage = _coverage(assembly, [shape for _, shape, _ in selected])
    output = Path(output_directory)
    if output.is_symlink() or not output.resolve().is_relative_to(Path.cwd().resolve()):
        raise ValueError("CAD outputs must stay in the job workspace")
    output.mkdir(exist_ok=True)
    (output / "parts").mkdir(exist_ok=True)
    def export(shape, stem):
        refs = {}
        for kind, writer in (("step", export_step), ("stl", export_stl)):
            path = output / (stem + "." + kind)
            if path.is_symlink():
                raise ValueError("CAD output path must not be a symlink")
            writer(shape, str(path))
            refs[kind] = {"path": "native/" + path.as_posix(), "sha256": sha256(path.read_bytes())}
        return {**refs, "bounds": _shape_bounds(shape)}
    manifest = {"schemaVersion": "cad-assembly-v1", "units": "mm", "coordinateFrame": "assembled",
                "separation": "monolithic" if len(selected) == 1 else separation,
                "source": {"path": str(source), "sha256": sha256(original), "kind": "build123d_source"},
                "assembly": export(assembly, "model"), "parts": [], "candidate": True,
                "manufacturing": {"productionAuthorized": False, "physicalStagesSimulated": True,
                                  "dimensionsAndFitRequireIndependentValidation": True, "printability": "UNKNOWN",
                                  "slicing": "UNKNOWN", "materialQualification": "UNKNOWN"}, "coverage": coverage}
    for ident, shape, metadata in selected:
        material = metadata.get("material")
        if material is not None and (not isinstance(material, str) or len(material) > 200):
            raise ValueError("Part material must be bounded source-declared text")
        manifest["parts"].append({"id": ident, **metadata, "quantity": 1, **export(shape, "parts/" + ident),
                                  "transform": {"translation": dict(ZERO), "rotation": dict(ZERO)},
                                  "materialStatus": "source_declared_unverified" if material else "unspecified"})
    raw = json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False).encode()
    parse_assembly_manifest(raw)
    (output / "assembly-manifest.json").write_bytes(raw)
    return manifest


def inspect_exported_parts(manifest_path, lane_directory):
    """Independent STEP inspection; no generated Python or generated PASS records execute."""
    from build123d import import_step

    manifest = parse_assembly_manifest(Path(manifest_path).read_bytes())
    base = Path(lane_directory).resolve()
    def path_for(ref):
        raw = base / ref["path"]
        path = raw.resolve()
        if not path.is_relative_to(base) or raw.is_symlink():
            raise ValueError("CAD part path escapes the pinned closure")
        return path
    files = {entry[kind]["path"]: path_for(entry[kind]).read_bytes()
             for entry in [manifest["assembly"], *manifest["parts"]] for kind in ("step", "stl")}
    closure = validate_manifest_closure(manifest, files)
    assembly = import_step(str(path_for(manifest["assembly"]["step"])))
    _valid_solid(assembly)
    parts = []
    for entry in [manifest["assembly"], *manifest["parts"]]:
        part = import_step(str(path_for(entry["step"])))
        _valid_solid(part)
        if entry is not manifest["assembly"] and len(part.solids()) != 1:
            raise ValueError("Each physical instance must contain exactly one solid")
        actual = _shape_bounds(part)
        if any(abs(actual[key][axis] - entry["bounds"][key][axis]) > 1e-5
               for key in actual for axis in ZERO):
            raise ValueError("Exported physical part bounds differ from its manifest")
        mesh = stl_bounds(files[entry["stl"]["path"]])
        if any(abs(mesh[key][axis] - actual[key][axis]) > max(.01, abs(actual[key][axis]) * 1e-6)
               for key in mesh for axis in ZERO):
            raise ValueError("STL manufacturing mesh bounds differ from the actual STEP geometry")
        if entry is not manifest["assembly"]:
            parts.append(part)
    return {**closure, **_coverage(assembly, parts), "independentGeometryVerified": True,
            "stlBoundsVerified": True, "printability": "UNKNOWN"}
