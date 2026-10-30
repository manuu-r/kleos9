import base64
import copy
import io
import json
import struct
import zipfile
from types import SimpleNamespace

import pytest
from test_control_plane import create

from kleos.cad_parts import (
    MAX_PARTS,
    _volume,
    parse_assembly_manifest,
    sha256,
    stl_bounds,
    validate_manifest_closure,
)
from kleos.contracts import DomainError
from kleos.db import Artifact, Release
from kleos.native_runner import run_job
from kleos.packaging import ReleasePackaging


def bounds(low=0, high=1):
    return {"min": dict(x=low, y=0, z=0), "max": dict(x=high, y=1, z=1),
            "size": dict(x=high - low, y=1, z=1), "center": dict(x=(low + high) / 2, y=.5, z=.5)}


def fixture():
    files = {}
    def entry(stem, box):
        refs = {}
        for kind in ("step", "stl"):
            path = "native/outputs/" + stem + "." + kind
            files[path] = (path + " actual fixture bytes").encode()
            refs[kind] = {"path": path, "sha256": sha256(files[path])}
        return {**refs, "bounds": box}
    manifest = {"schemaVersion": "cad-assembly-v1", "units": "mm", "coordinateFrame": "assembled",
                "separation": "named_parts", "assembly": entry("model", bounds(0, 4)), "parts": []}
    for ident, low, high in (("case", 0, 1), ("cover", 3, 4)):
        manifest["parts"].append({"id": ident, "label": ident.title(), "source": "explicit", "quantity": 1,
                                  "material": None, "materialStatus": "unspecified",
                                  "transform": {"translation": dict(x=0, y=0, z=0), "rotation": dict(x=0, y=0, z=0)},
                                  **entry("parts/" + ident, bounds(low, high))})
    return manifest, files


def test_part_manifest_binds_actual_assembly_and_each_physical_manufacturing_file():
    manifest, files = fixture()
    result = validate_manifest_closure(manifest, files)
    assert result["partCount"] == 2 and result["fileCount"] == 6 and result["closureVerified"]
    assert parse_assembly_manifest(json.dumps(manifest).encode()) == manifest


@pytest.mark.parametrize("mutation", ["changed_file", "missing_file", "unsafe_path", "duplicate_id", "quantity",
                                     "display_transform", "wrong_units", "bad_bounds", "part_ceiling"])
def test_invalid_or_tampered_manufacturing_closure_cannot_be_accepted(mutation):
    manifest, files = fixture()
    if mutation == "changed_file":
        files[manifest["parts"][0]["stl"]["path"]] = b"wrong geometry"
    elif mutation == "missing_file":
        files.pop(manifest["parts"][0]["step"]["path"])
    elif mutation == "unsafe_path":
        manifest["parts"][0]["step"]["path"] = "native/outputs/../../private.step"
    elif mutation == "duplicate_id":
        manifest["parts"][1]["id"] = manifest["parts"][0]["id"]
    elif mutation == "quantity":
        manifest["parts"][0]["quantity"] = 2
    elif mutation == "display_transform":
        manifest["parts"][0]["transform"]["translation"]["z"] = 10
    elif mutation == "wrong_units":
        manifest["units"] = "relative"
    elif mutation == "bad_bounds":
        manifest["parts"][0]["bounds"]["center"]["x"] = 100
    else:
        manifest["parts"] = [copy.deepcopy(manifest["parts"][0]) for _ in range(MAX_PARTS + 1)]
    with pytest.raises(ValueError):
        validate_manifest_closure(manifest, files)


def test_stl_bounds_read_actual_binary_vertices_and_reject_nonfinite_mesh():
    vertices = (0., 0., 0., 1., 0., 0., 0., 1., 1.)
    raw = b"fixture".ljust(80, b" ") + struct.pack("<I", 1) + struct.pack("<12fH", 0, 0, 1, *vertices, 0)
    assert stl_bounds(raw) == {"min": dict(x=0, y=0, z=0), "max": dict(x=1, y=1, z=1)}
    bad = raw[:96] + struct.pack("<f", float("nan")) + raw[100:]
    with pytest.raises(ValueError):
        stl_bounds(bad)


def test_extreme_manifest_integer_is_rejected_without_float_overflow():
    manifest, _ = fixture()
    manifest["parts"][0]["bounds"]["center"]["x"] = 10 ** 1000
    with pytest.raises(ValueError, match="finite"):
        parse_assembly_manifest(json.dumps(manifest).encode())


def test_deep_candidate_metadata_is_rejected_as_invalid_manifest():
    with pytest.raises(ValueError, match='nesting'):
        parse_assembly_manifest(b'[' * 2000 + b'0' + b']' * 2000)


def test_boolean_shapelist_residuals_keep_real_volume_and_empty_results():
    assert _volume([]) == _volume([[], []]) == 0
    assert _volume([SimpleNamespace(volume=3), [SimpleNamespace(volume=2)]]) == 5
    with pytest.raises(ValueError, match="invalid residual"):
        _volume([SimpleNamespace(volume=float("nan"))])


def test_native_validation_uses_manifest_assembly_instead_of_mistaking_parts_for_extra_assemblies(tmp_path):
    manifest, files = fixture()
    files["native/outputs/assembly-manifest.json"] = json.dumps(manifest).encode()
    artifacts = [{"id": path, "lane": "mechanics", "name": path, "sha256": sha256(raw),
                  "contentBase64": base64.b64encode(raw).decode(), "provenance": {"nativeJobId": "cad-build"}}
                 for path, raw in files.items()]
    def inspect(args, work, timeout):
        if "inspect_exported_parts" in args[3]:
            return 0, json.dumps({"independentGeometryVerified": True, "partCount": 2})
        assert args[4].endswith("native/outputs/model.step")
        return 0, json.dumps({"sizeMm": dict(x=40, y=30, z=10), "volumeMm3": 1000, "solidCount": 2,
                              "valid": True, "internalObstructionMm3": 0, "componentIntersections": []})
    result = run_job({"schemaVersion": 1, "purpose": "validate", "inputArtifacts": artifacts,
        "interfaces": {"mechanical": {"externalMm": dict(x=40, y=30, z=10), "internalMm": dict(x=38, y=28, z=8),
            "components": [{"id": "board", "sizeMm": dict(x=1, y=1, z=1), "positionMm": dict(x=1, y=1, z=1)}]}}},
        tmp_path, inspect)
    checks = {check.name: check for check in result.checks}
    assert checks["mechanics"].status == checks["mechanical_parts"].status == "PASS"


@pytest.mark.parametrize("missing_part", [False, True])
def test_release_download_preserves_exact_parts_and_manufacturing_references(platform, missing_part):
    project_id = create(platform)["projectId"]
    cad, files = fixture()
    files["native/outputs/assembly-manifest.json"] = json.dumps(cad).encode()
    refs = {}
    with platform.db.transaction() as session:
        for index, (name, raw) in enumerate(files.items()):
            if missing_part and name.endswith("parts/case.step"):
                continue
            ident, key = f"cad-{index}", f"fixture/{index}"
            platform.store.put(key, raw)
            refs[ident] = sha256(raw)
            session.add(Artifact(id=ident, tenant_id="org-a", project_id=project_id, revision=1, lane="mechanics",
                name=name, key=key, sha256=sha256(raw), size=len(raw), status="accepted"))
        session.add(Release(id="parts-release", tenant_id="org-a", project_id=project_id, revision=1,
                            mode="digital", manifest={"artifacts": refs, "interfaces": {}}, approved_by="development"))
    packaging = ReleasePackaging(platform.db, platform.store)
    if missing_part:
        with pytest.raises(DomainError, match="manufacturing part closure"):
            packaging.bundle(platform.principal, "parts-release")
        return
    content = packaging.bundle(platform.principal, "parts-release")
    assert packaging.bundle(platform.principal, "parts-release") == content
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name, raw in files.items():
            assert archive.read("mechanics/" + name) == raw
        manufacturing = json.loads(archive.read("manufacturing-manifest.json"))
        assert manufacturing["productionAuthorized"] is False
        parts = manufacturing["assemblies"][0]["parts"]
        assert {part["id"] for part in parts} == {"case", "cover"}
        assert all(part["quantity"] == 1 for part in parts)
        assert manufacturing["assemblies"][0]["coordinateFrame"] == "assembled"
