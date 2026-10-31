"""Offline native smoke; --direct is an explicit host-only numeric-deck fallback.

Run in validate/electronics with network none, read-only root, 256 MiB tmpfs,
1 CPU, 512 MiB memory and 64 pids. No generated program is executed.
"""

import argparse
import hashlib
import json
import math
import os
import resource
import shutil
import subprocess
import tempfile
from pathlib import Path

from ngspice_fixture import specimen

from kleos.circuit_simulation import MAX_OUTPUT_BYTES, MAX_RAW_SAMPLES, plan_simulation


def direct_execute(args, directory, timeout):
    """Explicitly opted-in fallback; never used by production validation."""
    binary = shutil.which(args[0])
    if not binary:
        raise RuntimeError("ngspice is not installed")

    def limits():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen([binary, *args[1:]], cwd=directory,
            env={"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": str(directory), "LANG": "C"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, preexec_fn=limits)
        try:
            code = process.wait(timeout=timeout)
        finally:
            if process.poll() is None:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        log.seek(0)
        return code, log.read(MAX_OUTPUT_BYTES).decode(errors="replace")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct", action="store_true")
    args = parser.parse_args()
    if args.direct:
        execute = direct_execute
    else:
        from kleos.native_runner import run_command
        execute = run_command
    plan = plan_simulation(specimen())
    assert plan.status == "READY"
    with tempfile.TemporaryDirectory(prefix="kleos-spice-smoke-") as tmp:
        work = Path(tmp)
        work.chmod(0o755)
        deck = plan.decks[0]
        (work / "circuit.cir").write_text(deck.source)
        code, log = execute(["ngspice", "-n", "-b", "circuit.cir"], work, 15)
        assert code == 0, log
        measurements = {}
        for name in ("operating.dat", "transient.dat"):
            output = work / name
            assert output.is_file() and not output.is_symlink() and output.stat().st_size <= MAX_OUTPUT_BYTES
            rows = output.read_text().splitlines()
            assert 2 <= len(rows) <= MAX_RAW_SAMPLES + 1
            values = [list(map(float, row.split())) for row in rows[1:]]
            assert all(math.isfinite(v) for row in values for v in row)
            assert all(len(row) == len(plan.metadata["vectors"]) + 1 for row in values)
            measurements[name] = {"samples": len(values), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
        print(json.dumps({"mode": "direct-not-image-qualified" if args.direct else "native-sandbox",
                          "deckSha256": deck.sha256, "outputs": measurements}, indent=2))


if __name__ == "__main__":
    main()
