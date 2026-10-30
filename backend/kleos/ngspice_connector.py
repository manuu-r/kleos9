"""Alternative digital engineering adapter: a fixed RC circuit simulated by real ngspice.

Run behind an access-controlled connector gateway in an isolated CPU container. This example
accepts numeric circuit parameters only, never caller-supplied SPICE, scripts or filenames.
"""

import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import Field, model_validator

from .contracts import DomainError, WireModel


class RCParameters(WireModel):
    resistance_ohms: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    capacitance_farads: float = Field(gt=0, le=1, allow_inf_nan=False)
    voltage: float = Field(gt=0, le=50, allow_inf_nan=False)
    duration_seconds: float = Field(gt=0, le=10, allow_inf_nan=False)
    step_seconds: float = Field(gt=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def bounded_steps(self):
        if not 2 <= self.duration_seconds / self.step_seconds <= 5000:
            raise ValueError("Use 2–5000 bounded simulation steps")
        if self.resistance_ohms * self.capacitance_farads < self.step_seconds / 100:
            raise ValueError("The RC time constant is below this qualified scenario's resolution")
        return self


class SimulationRequest(WireModel):
    schema_version: Literal["1"]
    operation_id: str = Field(min_length=1, max_length=64)
    capability: Literal["electronics.simulate"]
    payload: RCParameters


def simulate_rc(parameters, binary=None, runner=subprocess.run):
    binary = binary or shutil.which("ngspice")
    if not binary:
        return {"status": "UNKNOWN", "executionMode": "digital_simulation", "tool": "ngspice",
                "limitations": ["ngspice is not installed in this adapter environment"], "physicalAcceptance": False}
    p = parameters
    netlist = f"""* Kleos qualified RC step response
V1 in 0 DC {p.voltage:.12g}
R1 in out {p.resistance_ohms:.12g}
C1 out 0 {p.capacitance_farads:.12g} IC=0
.control
tran {p.step_seconds:.12g} {p.duration_seconds:.12g} uic
wrdata response.dat v(out)
quit
.endc
.end
"""
    with tempfile.TemporaryDirectory(prefix="kleos-ngspice-") as directory:
        root = Path(directory)
        (root / "circuit.cir").write_text(netlist)
        try:
            result = runner([str(binary), "-n", "-b", "circuit.cir"], cwd=directory, timeout=15, capture_output=True,
                            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C"}, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return {"status": "UNKNOWN", "executionMode": "digital_simulation", "tool": "ngspice",
                    "limitations": ["ngspice exceeded its 15-second scenario limit"], "physicalAcceptance": False}
        output = root / "response.dat"
        if result.returncode or not output.exists() or output.stat().st_size > 2_000_000:
            return {"status": "UNKNOWN", "executionMode": "digital_simulation", "tool": "ngspice",
                    "limitations": ["ngspice did not produce a bounded successful response"], "physicalAcceptance": False}
        samples = []
        for line in output.read_text().splitlines():
            values = line.split()
            if len(values) != 2:
                raise DomainError("Unsupported ngspice output format", 502)
            timestamp, voltage = map(float, values)
            if not math.isfinite(timestamp) or not math.isfinite(voltage):
                raise DomainError("Non-finite ngspice measurement", 502)
            samples.append((timestamp, voltage))
        if not samples:
            raise DomainError("ngspice returned no measurements", 502)
        final_time, final_voltage = samples[-1]
        reference = p.voltage * (1 - math.exp(-final_time / (p.resistance_ohms * p.capacitance_farads)))
        return {"status": "succeeded", "executionMode": "digital_simulation", "tool": "ngspice",
                "inputSha256": hashlib.sha256(netlist.encode()).hexdigest(),
                "measurements": {"finalTimeSeconds": final_time, "finalVoltage": final_voltage,
                                 "analyticReferenceVoltage": reference, "absoluteErrorVolts": abs(final_voltage - reference),
                                 "sampleCount": len(samples)},
                "source": netlist, "physicalAcceptance": False,
                "limitations": ["Qualified ideal RC topology only; no component tolerances or physical verification",
                                "Candidate measurements require independent Kleos validation"]}


class NgspiceAdapter:
    def __init__(self, receipt_path, simulator=simulate_rc):
        self.receipt_path, self.simulator = str(receipt_path), simulator
        self.capacity = threading.BoundedSemaphore(2)
        with sqlite3.connect(self.receipt_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT)")

    def invoke(self, request):
        fingerprint = hashlib.sha256(request.payload.model_dump_json().encode()).hexdigest()
        with sqlite3.connect(self.receipt_path) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT fingerprint, result FROM receipts WHERE id=?", (request.operation_id,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise DomainError("Operation ID was reused with different input", 409)
                if previous[1]:
                    return json.loads(previous[1])
                raise DomainError("Simulation is running or interrupted; reconcile before a new operation", 409)
            if not self.capacity.acquire(blocking=False):
                raise DomainError("Simulation capacity is full", 429)
            try:
                db.execute("INSERT INTO receipts(id,fingerprint) VALUES (?,?)", (request.operation_id, fingerprint))
            except Exception:
                self.capacity.release()
                raise
        try:
            result = self.simulator(request.payload)
            with sqlite3.connect(self.receipt_path) as db:
                db.execute("UPDATE receipts SET result=? WHERE id=?", (json.dumps(result), request.operation_id))
            return result
        finally:
            self.capacity.release()


def create_app(receipt_path=None, simulator=simulate_rc):
    app = FastAPI(title="Kleos ngspice connector example")
    # Receipts need a persistent mounted directory in a deployed adapter.
    adapter = NgspiceAdapter(receipt_path or os.environ.get("KLEOS_CONNECTOR_RECEIPTS", "/tmp/kleos-ngspice.sqlite"), simulator)

    @app.exception_handler(DomainError)
    async def failed(_request, exc):
        return JSONResponse(status_code=exc.status, content={"message": exc.message, "code": exc.code})

    @app.post("/v1/invoke")
    def invoke(request: SimulationRequest):
        return adapter.invoke(request)

    return app
