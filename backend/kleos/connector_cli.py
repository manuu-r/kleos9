"""Local connector manifests and protocol conformance; qualification is never self-awarded."""

import argparse
import json
from pathlib import Path

from .connectors import conformance
from .contracts import ConnectorManifest

EXAMPLE = {
    "name": "Example catalog", "version": "0.1.0", "transport": "rest",
    "capabilities": ["parts.search"], "mode": "read_only",
    "endpoint": "https://replace-with-your-adapter.example",
    "supportsIdempotency": True, "supportsCancellation": False,
    "disclosure": ["search query"],
}


def main():
    parser = argparse.ArgumentParser(description="Kleos9 connector development kit")
    parser.add_argument("operation", choices=["init", "fixtures", "conformance"])
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    if args.operation == "init":
        with args.manifest.open("x") as file:
            json.dump(EXAMPLE, file, indent=2)
        return
    manifest = ConnectorManifest.model_validate_json(args.manifest.read_text())
    result = conformance(manifest)
    if args.operation == "fixtures":
        result["requiredFixtures"] = [
            "duplicate operationId returns the same receipt",
            "reused operationId with different input is rejected",
            "stale input revision is rejected",
            "unsupported capability returns UNKNOWN",
            "no credentials or private project files in public output",
            "ambiguous effects require reconciliation",
            "no self-approval of artifacts or physical evidence",
        ]
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
