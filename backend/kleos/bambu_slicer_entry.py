"""Fixed entrypoint for the offline Bambu slicer image, never run on the host."""

import base64
import json
import os
import sys
import tempfile
from pathlib import Path

from .bambu_slicing import slice_mesh
from .native_runner import run_command


def main():
    if sys.platform != "linux" or os.environ.get("KLEOS_BAMBU_SANDBOX") != "1":
        raise RuntimeError("Slicing requires the isolated native image")
    bundle_hash = sys.argv[1]
    with tempfile.TemporaryDirectory(prefix="bambu-") as tmp:
        work = Path(tmp)
        work.chmod(0o755)
        raw = Path("/input/input.stl").read_bytes()
        (work / "input.stl").write_bytes(raw)
        package, metrics = slice_mesh(work, Path("/profiles"), bundle_hash, run_command)
        print(json.dumps({"schemaVersion": "bambu-slice-v1", "measurements": metrics,
                          "packageBase64": base64.b64encode(package).decode()}, allow_nan=False))


if __name__ == "__main__":
    main()
