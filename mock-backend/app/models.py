"""Validated JSON command contract shared with the Flutter frontend."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


PROJECT_COMMANDS = ["research", "requestReview", "confirm", "solveInterfaces", "generate", "verify", "repair", "swapBattery", "acceptRelease", "requestQuotes", "placeOrders", "createPhysicalTasks", "acceptPhysical", "prepareKit", "acceptKit", "ship", "activate"]
TASK_COMMANDS = ["usePortal", "submitEvidence", "accept", "requestRevision"]
CONNECTOR_COMMANDS = ["test", "calibrate", "disconnect"]
FIELDS: dict[str, dict[str, Any]] = {
    **{f"project.{name}": {"projectId": (str, ...)} for name in PROJECT_COMMANDS},
    **{f"task.{name}": {"taskId": (str, ...)} for name in TASK_COMMANDS},
    **{f"connector.{name}": {"connectorId": (str, ...)} for name in CONNECTOR_COMMANDS},
    "project.create": {"brief": (str, ...)},
    "project.answer": {"projectId": (str, ...), "questionKey": (str, ...), "values": (list[str], ...)},
    "project.toggleFeature": {"projectId": (str, ...), "name": (str, ...)},
    "project.selectTool": {"projectId": (str, ...), "lane": (str, ...), "toolId": (str, ...)},
    "project.selectPrint": {"projectId": (str, ...), "mode": (Literal["home", "order"], ...)},
    "project.assemblyStep": {"projectId": (str, ...), "step": (int, ...)},
    "task.assign": {"taskId": (str, ...), "applicantId": (str, ...)},
    "connector.authorize": {"connectorId": (str, ...), "apiKey": (str | None, None)},
    "connector.discover": {"connectorId": (str, ...), "host": (str, ...)},
    "connector.save": {"connectorId": (str, ...), "capabilities": (list[str], ...), "scope": (float, ...)},
    "connector.savePrinter": {"connectorId": (str, ...), "host": (str, ...), "confirmEach": (bool, ...)},
    "connector.setEnabled": {"connectorId": (str, ...), "enabled": (bool, ...)},
    "provider.quote": {"jobId": (str, ...), "price": (float, ...), "lead": (float, ...)},
    "provider.decline": {"jobId": (str, ...)},
    "provider.uploadEvidence": {"jobId": (str, ...)},
    "developer.configure": {"kind": (Literal["service", "tool", "printer"] | None, None), "capability": (str | None, None)},
    "developer.manifest": {"manifest": (str, ...)},
    "developer.run": {"step": (Literal["init", "fixtures", "conformance", "submit", "qualify"], ...)},
    "developer.applyFix": {},
    "agent.nextMessage": {},
}
PAYLOAD_MODELS = {name: create_model(name.replace(".", "_") + "Payload", __base__=Payload, **fields) for name, fields in FIELDS.items()}


def command_schema(schema: dict[str, Any]) -> None:
    schema["oneOf"] = [
        {"type": "object", "properties": {"type": {"const": name}, "payload": model.model_json_schema()}, "required": ["type", "payload"]}
        for name, model in PAYLOAD_MODELS.items()
    ]


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=command_schema)
    type: str
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_command(self):
        model = PAYLOAD_MODELS.get(self.type)
        if model is None:
            raise ValueError(f"Unsupported command: {self.type}")
        self.payload = model.model_validate(self.payload).model_dump(exclude_none=True)
        return self


class SpeedRequest(Payload):
    speed: float = Field(gt=0, le=100)


class Snapshot(BaseModel):
    revision: int
    state: dict[str, Any]
    demo: dict[str, Any]


class CommandResponse(BaseModel):
    snapshot: Snapshot
    result: dict[str, Any] | None = None
