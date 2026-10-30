"""Operator fixture only: execute actual Renode before marking an image qualified."""
import json
import sys
import tempfile
from pathlib import Path

from kleos.native_runner import run_renode_uart

if sys.platform != "linux":
    raise SystemExit("Run this fixture in the isolated Linux Renode validator image")
firmware = Path(sys.argv[1]).read_bytes()
with tempfile.TemporaryDirectory(prefix="renode-qualification-") as tmp:
    Path(tmp).chmod(0o755)
    print(json.dumps(run_renode_uart(firmware, Path(tmp)), indent=2))
