"""Qualify existing local KiCad images offline using public, synthetic fixtures only."""
import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", nargs="+", default=["kleos-validator:development", "kleos-electronics:development"])
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    fixture = backend / "docker/smoke_circuit_correspondence.py"
    records = []
    for name in args.images:
        identity = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", name], text=True).strip()
        result = subprocess.run([
            "docker", "run", "--rm", "--pull", "never", "--platform", "linux/amd64", "--network", "none",
            "--read-only", "--memory", "2g", "--cpus", "2", "--pids-limit", "128",
            "--tmpfs", "/tmp:rw,size=256m", "--mount", f"type=bind,source={fixture},target=/smoke_circuit.py,readonly",
            "--entrypoint", "python", identity, "/smoke_circuit.py",
        ], text=True, capture_output=True, timeout=180, check=True)
        evidence = json.loads(result.stdout.strip().splitlines()[-1])
        assert evidence["fullElectronicsPipeline"] == evidence["ercDrc"] == evidence["positive"] == "PASS"
        assert evidence["wrongPadNet"] == "FAIL" and evidence["modelCalls"] == 0
        records.append({"image": name, "imageId": identity, **evidence})
    report = {"schemaVersion": "circuit-correspondence-runtime-v1", "status": "passed",
              "checkedAt": datetime.now(UTC).isoformat(), "modelCalls": 0,
              "scope": "Real all-severity ERC/DRC and complete electronics validator branch on a two-resistor fixture; "
                       "other engineering lanes and physical operation are outside this check",
              "images": records}
    destination = backend / "verification/circuit-correspondence-runtime.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": "passed", "images": len(records), "report": str(destination), "modelCalls": 0}))


if __name__ == "__main__":
    main()
