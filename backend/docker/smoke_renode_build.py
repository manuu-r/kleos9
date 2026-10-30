"""Compile the trusted UART specimen in the isolated, offline Zephyr image."""
import base64
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/opt/kleos")
from kleos.native_runner import run_job  # noqa: E402

source = Path("/fixture")
artifacts = []
for path in source.rglob("*"):
    if path.is_file():
        content = path.read_bytes()
        artifacts.append({"id": str(path.relative_to(source)), "name": str(path.relative_to(source)),
                          "lane": "firmware", "sha256": hashlib.sha256(content).hexdigest(),
                          "contentBase64": base64.b64encode(content).decode()})
with tempfile.TemporaryDirectory(prefix="renode-build-") as temporary:
    root = Path(temporary)
    root.chmod(0o755)
    result = run_job({
        "schemaVersion": 1, "purpose": "build", "lane": "firmware", "inputArtifacts": artifacts,
        "timeoutSeconds": 45,
        "interfaces": {"firmware": {"board": "stm32f4_disco"}},
    }, root)
    if result.status != "succeeded":
        raise RuntimeError(json.dumps(result.model_dump(mode="json", exclude={"files"})))
    output = Path("/output")
    for artifact in result.files:
        content = base64.b64decode(artifact.content)
        destination = output / Path(artifact.path).name
        destination.write_bytes(content)
        destination.chmod(0o644)
    (output / "build-log.txt").write_text(result.logs)
    print(json.dumps({"status": result.status, "files": [artifact.path for artifact in result.files]}))
