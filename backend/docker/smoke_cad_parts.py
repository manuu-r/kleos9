"""Real offline physical-part export/coverage qualification; never run generated CAD on host."""
import base64
import importlib.metadata
import json
import tempfile
from pathlib import Path

from kleos.cad_parts import parse_assembly_manifest, sha256, validate_manifest_closure
from kleos.native_runner import run_job

BASE = '''from build123d import Box, Compound
case = Box(40, 30, 6) - Box(38, 28, 5).translate((0, 0, 0.5))
cover = Box(40, 30, 1).translate((0, 0, 4))
assembly = Compound(children=[case, cover])
'''
NAMED = '''parts = {"case": {"shape": case, "label": "Case", "material": "PLA", "quantity": 1},
                    "cover": {"shape": cover, "label": "Cover", "quantity": 1}}
'''
TOUCHING = '''from build123d import Cylinder, Compound
case = Cylinder(20, 9).translate((0, 0, -.5)) - Cylinder(19, 8)
cover = Cylinder(20, 1).translate((0, 0, 4.5))
assembly = Compound(children=[case, cover])
'''


def build(source, expected_parts=2):
    with tempfile.TemporaryDirectory(prefix="cad-part-build-") as temporary:
        folder = Path(temporary)
        folder.chmod(0o755)
        result = run_job({"schemaVersion": 1, "purpose": "build", "lane": "mechanics", "inputArtifacts": [{
            "id": "source", "lane": "mechanics", "name": "enclosure.py", "sha256": sha256(source),
            "contentBase64": base64.b64encode(source).decode()}]}, folder)
        assert result.status == "succeeded", result.model_dump()
        files = {file.path: base64.b64decode(file.content) for file in result.files}
    manifest = parse_assembly_manifest(files["native/outputs/assembly-manifest.json"])
    assert manifest["source"]["sha256"] == sha256(source)
    assert validate_manifest_closure(manifest, files)["partCount"] == expected_parts
    assert manifest["manufacturing"]["printability"] == "UNKNOWN"
    assert len(files) <= 50
    return manifest, files


named, files = build((BASE + NAMED).encode())
assert {part["id"] for part in named["parts"]} == {"case", "cover"}
assert all(part["quantity"] == 1 for part in named["parts"])
assert named["separation"] == "named_parts"
for part in named["parts"]:
    assert part["transform"]["translation"] == {"x": 0, "y": 0, "z": 0}
with tempfile.TemporaryDirectory(prefix="cad-part-validate-") as temporary:
    folder = Path(temporary)
    folder.chmod(0o755)
    inputs = [{"id": str(index), "lane": "mechanics", "name": name, "sha256": sha256(raw),
               "contentBase64": base64.b64encode(raw).decode(), "provenance": {"nativeJobId": "cad-part-qualification"}}
              for index, (name, raw) in enumerate(files.items())]
    result = run_job({"schemaVersion": 1, "purpose": "validate", "inputArtifacts": inputs}, folder)
    check = next(check for check in result.checks if check.name == "mechanical_parts")
    assert check.status == "PASS" and check.details["independentGeometryVerified"], check.model_dump()

with tempfile.TemporaryDirectory(prefix="cad-part-missing-") as temporary:
    folder = Path(temporary)
    folder.chmod(0o755)
    missing = json.loads(files["native/outputs/assembly-manifest.json"])
    missing["parts"] = missing["parts"][:1]
    missing["separation"] = "monolithic"
    tampered = {**files, "native/outputs/assembly-manifest.json": json.dumps(missing).encode()}
    inputs = [{"id": str(index), "lane": "mechanics", "name": name, "sha256": sha256(raw),
               "contentBase64": base64.b64encode(raw).decode(), "provenance": {"nativeJobId": "negative-qualification"}}
              for index, (name, raw) in enumerate(tampered.items())]
    negative = run_job({"schemaVersion": 1, "purpose": "validate", "inputArtifacts": inputs}, folder)
    assert next(item for item in negative.checks if item.name == "mechanics").status == "FAIL", negative.model_dump()

legacy, _ = build(BASE.encode())
repeat, _ = build(BASE.encode())
assert legacy["separation"] == "separate_solids"
assert {part["id"] for part in legacy["parts"]} == {part["id"] for part in repeat["parts"]}
monolithic, _ = build(b"from build123d import Box\nassembly=Box(10,10,10)\n", expected_parts=1)
assert monolithic["separation"] == "monolithic"
touching, touching_files = build((TOUCHING + NAMED).encode())
assert touching['coverage']['missingVolumeMm3'] < 1e-4 and touching['coverage']['overlapVolumeMm3'] < 1e-4
with tempfile.TemporaryDirectory(prefix='cad-touching-check-') as temporary:
    folder = Path(temporary)
    folder.chmod(0o755)
    inputs = [{'id': str(index), 'lane': 'mechanics', 'name': name, 'sha256': sha256(raw),
               'contentBase64': base64.b64encode(raw).decode(), 'provenance': {'nativeJobId': 'touching-parts-qualification'}}
              for index, (name, raw) in enumerate(touching_files.items())]
    touching_check = next(item for item in run_job({'schemaVersion': 1, 'purpose': 'validate', 'inputArtifacts': inputs},
                                                  folder).checks if item.name == 'mechanical_parts')
    assert touching_check.status == 'PASS', touching_check.model_dump()
with tempfile.TemporaryDirectory(prefix='cad-overlapping-check-') as temporary:
    folder = Path(temporary)
    folder.chmod(0o755)
    source = (TOUCHING.replace('(0, 0, 4.5)', '(0, 0, 4.4)') + NAMED).encode()
    overlap = run_job({'schemaVersion': 1, 'purpose': 'build', 'lane': 'mechanics', 'inputArtifacts': [{
        'id': 'source', 'lane': 'mechanics', 'name': 'enclosure.py', 'sha256': sha256(source),
        'contentBase64': base64.b64encode(source).decode()}]}, folder)
    assert overlap.status == 'failed' and 'physical parts overlap' in overlap.logs, overlap.model_dump()
print(json.dumps({"namedParts": [part["id"] for part in named["parts"]],
                  "legacyPartIds": [part["id"] for part in legacy["parts"]],
                  "independentPartCoverage": check.model_dump(), "sourcePreserved": True,
                  "manufacturingCoordinates": "assembled_mm", "modelCalls": 0,
                  "missingExportedPartRejected": True,
                  "monolithicPreserved": True,
                  "touchingCircularParts": touching_check.model_dump(),
                  "touchingCircularPartsSourceSha256": touching['source']['sha256'],
                  "positiveVolumeOverlapRejected": True,
                  "build123dVersion": importlib.metadata.version("build123d"),
                  "sourceHashes": {name: sha256((Path("/usr/local/lib/python3.12/site-packages/kleos") / name).read_bytes())
                                   for name in ("cad_parts.py", "native_runner.py")},
                  "fixtureScriptSha256": sha256(Path(__file__).read_bytes()),
                  "inputSourceSha256": named["source"]["sha256"],
                  "assemblyManifestSha256": sha256(files["native/outputs/assembly-manifest.json"]),
                  "assemblyFileHashes": {kind: named["assembly"][kind]["sha256"] for kind in ("step", "stl")},
                  "scope": "Real build123d STEP/STL parts and closure; no slicing or physical operation qualification"}))
