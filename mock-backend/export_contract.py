"""Export language-neutral specifications from the validated FastAPI models."""
import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
from app.main import app
from app.models import PAYLOAD_MODELS

shared = BACKEND / "contracts"
shared.mkdir(exist_ok=True)
(shared / "openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n")
commands = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Kleos simulated backend command",
    "description": "All commands manipulate in-memory mock state and simulated jobs only.",
    "oneOf": [
        {
            "type": "object", "additionalProperties": False,
            "required": ["type", "payload"],
            "properties": {"type": {"const": name}, "payload": model.model_json_schema()},
        }
        for name, model in sorted(PAYLOAD_MODELS.items())
    ],
}
(shared / "commands.schema.json").write_text(json.dumps(commands, indent=2) + "\n")
print(f"Exported OpenAPI and {len(PAYLOAD_MODELS)} simulated command schemas.")
