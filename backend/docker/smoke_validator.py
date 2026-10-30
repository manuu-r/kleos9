"""Exercise real independent C/Dart harnesses inside the isolated validator image."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/opt/kleos")
from kleos.native_runner import run_command, validate_protocol  # noqa: E402

with tempfile.TemporaryDirectory(prefix="validator-smoke-") as temporary:
    root = Path(temporary)
    root.chmod(0o755)
    results = {}
    for lane in ["firmware", "application"]:
        work = root / lane
        shutil.copytree(Path("/fixtures/watch-telemetry-v1") / lane, work)
        results[lane] = validate_protocol(lane, work, run_command)
    print(json.dumps(results))
