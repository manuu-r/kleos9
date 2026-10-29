"""In-memory domain simulation. No real engineering, orders, hardware or workers."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import json
import math
from pathlib import Path
import random
import re
from typing import Any, Callable
from uuid import uuid4

DATA = json.loads((Path(__file__).parent / "data" / "fixtures.json").read_text())
Step = tuple[float, Callable[[], None]]


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def new_hash() -> str:
    return uuid4().hex[:8]


def find(items: list[dict], ident: str, label: str = "Record") -> dict:
    item = next((item for item in items if item["id"] == ident), None)
    require(item, f"{label} {ident} missing")
    return item


def log(record: dict, text: str, kind: str = "agent") -> None:
    record.setdefault("activity", []).insert(0, {"t": datetime.now().strftime("%H:%M:%S"), "text": text, "kind": kind})


def note(task: dict, text: str) -> None:
    task.setdefault("history", []).insert(0, {"t": datetime.now().strftime("%H:%M:%S"), "text": text, "kind": "human"})


def complete(project: dict, stage: int) -> None:
    project["done"][stage] = True
    if project["stage"] == stage and stage < 9:
        project["stage"] += 1


def has_feature(project: dict, feature: str) -> bool:
    return feature in project["answers"].get("features", []) and not any(f["name"] == feature and f["status"] == "dropped" for f in project["features"])


def display_part(project: dict) -> str:
    display = project["answers"].get("display", [""])[0]
    return '2.0" color IPS, 240×320' if display.startswith("2-inch") else '2.9" e-paper, 296×128' if display.startswith("E-paper") else '1.3" OLED, 128×64'


def parts_for(project: dict) -> list[dict]:
    parts = [
        {"role": "Core module", "name": "ESP32-S3 Wi-Fi module (pre-certified)", "source": "PartsDirect", "note": "In stock"},
        {"role": "Display", "name": display_part(project), "source": "PartsDirect", "note": "In stock"},
        {"role": "Battery", "name": f'LiPo {project["batteryMah"]} mAh pack with protection circuit', "source": "PartsDirect", "note": "In stock"},
    ]
    for feature, role, name, note_text in [
        ("CO2 level", "CO2 sensor", "SCD41 CO2 sensor card", "Low stock (14)"),
        ("Temperature and humidity", "Climate", "SHT45 temperature and humidity sensor", "In stock"),
        ("Fine dust (PM2.5)", "Dust", "PMSA003 particulate sensor and fan", "In stock"),
        ("Alerts on your phone", "Alert", "12 mm piezo buzzer", "Via procurement browser"),
    ]:
        if has_feature(project, feature):
            parts.append({"role": role, "name": name, "source": "Supplier storefront" if role == "Alert" else "PartsDirect", "note": note_text})
    parts.extend([
        {"role": "Power", "name": "USB-C receptacle and cable", "source": "Supplier storefront", "note": "Via procurement browser"},
        {"role": "Case", "name": "Two printed shells, PETG", "source": "Print provider", "note": "Print-first"},
    ])
    return parts


def interfaces_for(project: dict) -> list[dict]:
    rows = deepcopy(DATA["interfaces"])
    rows[1]["value"] = rows[1]["value"].replace("2000", str(project["batteryMah"]))
    return [r for r in rows if r["name"] != "Wi-Fi protocol" or has_feature(project, "Alerts on your phone")]


def invalidate(project: dict, changed: str) -> list[str]:
    stale = {changed}
    while True:
        before = len(stale)
        for artifact in project["artifacts"]:
            if any(dep in stale for dep in artifact["deps"]):
                stale.add(artifact["id"])
        if len(stale) == before:
            break
    names = []
    for artifact in project["artifacts"]:
        if artifact["id"] in stale:
            artifact["status"] = "stale"
            names.append(artifact["name"])
    for check in project["checks"]:
        if any(inp in stale for inp in check["inputs"]):
            check.update(state="STALE", detail="Input changed; result kept as history only")
    return names


def outcome(check: dict, project: dict, repaired: bool) -> dict:
    match check["id"]:
        case "c.strap":
            return {"state": "PASS" if repaired else "FAIL", "detail": "0.22 mm side clearance after repair" if repaired else "0.05 mm side clearance, below 0.2 mm minimum"}
        case "c.fwhr":
            return {"state": "UNKNOWN", "detail": "Simulator does not model the CO2 sensor. Needs a physical functional test."}
        case "c.buttons":
            return {"state": "UNKNOWN", "detail": "requires_human_judgement: tactile feel through printed caps. Needs a physical fit check."}
        case "c.power":
            return {"state": "PASS", "detail": f'{project["batteryMah"] / 2000 * 11:.1f} days estimated on {project["batteryMah"]} mAh'}
        case "c.battfit":
            return {"state": "PASS", "detail": f'0.35 mm clearance for {project["batteryMah"]} mAh pack'}
        case _:
            return {"state": "PASS", "detail": "OK"}


class Simulation:
    def __init__(self, *, time_scale: float = 1.0):
        self.state = deepcopy(DATA["seed"])
        self.revision = 0
        self.speed = 1.0
        self.connection_runs: dict[str, dict] = {}
        self.shown = 0
        self.time_scale = time_scale
        self.lock = asyncio.Lock()
        self.jobs: set[asyncio.Task] = set()
        self.active: set[str] = set()
        self.epoch = 0
        self.closed = False
        self.errors: list[str] = []

    def changed(self) -> None:
        self.revision += 1

    def project(self, ident: str) -> dict:
        return find(self.state["projects"], ident, "Project")

    def task(self, ident: str) -> dict:
        return find(self.state["humanTasks"], ident, "Human task")

    def snapshot(self) -> dict:
        state = deepcopy(self.state)
        for project in state["projects"]:
            project.update(
                candidateParts=parts_for(project), interfacePreview=interfaces_for(project),
                generationLanes=["Mechanical", "Electronics", "Firmware"] + (["App"] if has_feature(project, "Alerts on your phone") else []),
                generatedSource={lane: source.replace("2000", str(project["batteryMah"])).replace('1.3" OLED, 128×64', display_part(project)) for lane, source in DATA["generatedSource"].items()} if project["generation"] == "done" else {},
            )
        state.update(connectionRuns=deepcopy(self.connection_runs), printerProfiles=deepcopy(DATA["printers"]), developerMeta={"templates": deepcopy(DATA["templates"]), "fixHints": deepcopy(DATA["fixHints"])}, agentExchange=deepcopy(DATA["exchange"][:self.shown]), agentExchangeTotal=len(DATA["exchange"]), providerSummary=deepcopy(DATA["providerSummary"]))
        return {"revision": self.revision, "state": state, "demo": {"speed": self.speed}}

    def enqueue(self, key: str, steps: list[Step]) -> None:
        if key in self.active or not steps:
            return
        self.active.add(key)
        epoch = self.epoch

        async def work():
            try:
                for delay, apply in steps:
                    await asyncio.sleep(delay / 1000 / self.speed * self.time_scale)
                    async with self.lock:
                        if self.closed or epoch != self.epoch:
                            return
                        apply()
                        self.changed()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.errors.append(str(error))
                self.changed()
            finally:
                if epoch == self.epoch:
                    self.active.discard(key)
        task = asyncio.create_task(work(), name=f"mock:{key}")
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)

    async def drain(self):
        while self.jobs:
            jobs = list(self.jobs)
            await asyncio.gather(*jobs, return_exceptions=True)
            self.jobs.difference_update(jobs)
        require(not self.errors, f"Simulation errors: {self.errors}")

    async def close(self):
        self.closed = True
        self.epoch += 1
        jobs = list(self.jobs)
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
            self.jobs.difference_update(jobs)
        self.active.clear()

    async def reset(self) -> dict:
        async with self.lock:
            self.epoch += 1
            for job in list(self.jobs):
                job.cancel()
            self.active.clear()
            self.state = deepcopy(DATA["seed"])
            self.connection_runs = {}
            self.shown = 0
            self.errors.clear()
            self.changed()
            return self.snapshot()

    async def set_speed(self, speed: float) -> dict:
        async with self.lock:
            require(math.isfinite(speed) and 0 < speed <= 100, "Speed must be between 0 and 100")
            self.speed = speed
            self.changed()
            return self.snapshot()

    async def execute(self, kind: str, payload: dict) -> dict:
        from .projects import project_command
        from .tasks import task_command
        from .other import connector_command, developer_command, provider_command
        async with self.lock:
            require(not self.closed, "Backend has been disposed")
            ident = next((payload[k] for k in ("projectId", "taskId", "connectorId", "jobId") if k in payload), "")
            key = f"{kind}:{ident}"
            if key in self.active:
                return {"snapshot": self.snapshot(), "result": None}
            if kind == "project.create":
                brief = payload["brief"].strip()
                require(brief, "Describe your build first")
                project = deepcopy(DATA["project"])
                head = re.split(r"\b(?:with|that|for|which|to|so)\b", brief.lower())[0]
                words = [w for w in re.sub("[^a-z0-9 ]", " ", head).split() if w not in {"a", "an", "the", "simple", "small", "minimal", "my"}][-3:]
                project.update(id=uid("prj"), name=" ".join(w.title() for w in words) or "New Device", brief=brief, createdAt=datetime.now().strftime("%m/%d/%Y"), activity=[])
                log(project, "Project created from your request", "system")
                self.state["projects"].insert(0, project)
                result = {"projectId": project["id"]}
            elif kind.startswith("project."):
                project_command(self, kind, payload)
                result = None
            elif kind.startswith("task."):
                task_command(self, kind, payload)
                result = None
            elif kind.startswith("connector."):
                connector_command(self, kind, payload)
                result = None
            elif kind.startswith("provider."):
                provider_command(self, kind, payload)
                result = None
            elif kind.startswith("developer."):
                developer_command(self, kind, payload)
                result = None
            elif kind == "agent.nextMessage":
                self.shown = min(self.shown + 1, len(DATA["exchange"]))
                result = None
            else:
                raise ValueError(f"Unsupported command: {kind}")
            self.changed()
            return {"snapshot": self.snapshot(), "result": result}
