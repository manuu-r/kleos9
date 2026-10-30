"""Run in the native mechanics image, with --network none, to qualify CAD export."""
import base64
import hashlib
import tempfile
from pathlib import Path

from kleos.native_runner import run_job

contract = {
    "externalMm": {"x": 40, "y": 30, "z": 10}, "internalMm": {"x": 38, "y": 28, "z": 8},
    "minimumWallMm": 1,
    "components": [{"id": "battery", "sizeMm": {"x": 20, "y": 15, "z": 5},
                    "positionMm": {"x": 1, "y": 1, "z": 1}}],
}
for geometry, expected in [("Box(40, 30, 10) - Box(38, 28, 8)", "PASS"), ("Box(40, 30, 10)", "FAIL")]:
    source = f"from build123d import Box\nassembly = {geometry}\n".encode()
    request = {
        "schemaVersion": 1, "purpose": "build", "lane": "mechanics", "timeoutSeconds": 120,
        "inputArtifacts": [{"id": "source", "lane": "mechanics", "name": "enclosure.py",
                            "sha256": hashlib.sha256(source).hexdigest(),
                            "contentBase64": base64.b64encode(source).decode()}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp).chmod(0o755)
        result = run_job(request, Path(tmp))
        assert result.status == "succeeded", result.model_dump()
        assert {Path(file.path).suffix for file in result.files} == {".step", ".stl", ".json"}
        inputs = [{"id": str(index), "name": file.path, "lane": "mechanics",
                   "sha256": hashlib.sha256(base64.b64decode(file.content)).hexdigest(),
                   "contentBase64": file.content, "provenance": {"nativeJobId": "smoke-build"}}
                  for index, file in enumerate(result.files)]
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp).chmod(0o755)
        validation = run_job({
            "schemaVersion": 1, "purpose": "validate", "inputArtifacts": inputs,
            "interfaces": {"mechanical": contract},
        }, Path(tmp))
        check = next(check for check in validation.checks if check.name == "mechanics")
        assert check.status == expected, check.model_dump()
        print({"geometry": geometry, "cadExport": result.status, "independentGeometryCheck": check.model_dump(),
               "otherLanes": "UNKNOWN — not qualified by this smoke"})
