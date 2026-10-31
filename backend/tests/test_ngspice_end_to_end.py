"""Opt-in real numeric solver + temporary ledger/API; no generated host code."""

import importlib.util
import json
import math
import os
from pathlib import Path

import pytest
from sqlalchemy import select
from test_safety_and_recovery import native_job

from kleos.circuit_simulation import canonical_sha256
from kleos.db import Artifact, NativeJob, Project
from kleos.native_runner import run_job

pytestmark = pytest.mark.skipif(os.getenv("KLEOS_TEST_DIRECT_NGSPICE") != "1",
                                reason="Explicit direct numeric solver fallback opt-in required")
ROOT = Path(__file__).resolve().parents[1]


def trusted_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "docker" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("kind,bad", [("rc", False), ("divider", False), ("led", False),
                                    ("power-on", False), ("led", True), ("unknown", False)])
def test_real_solver_to_validation_storage_and_authorized_api(platform, tmp_path, monkeypatch, kind, bad):
    import base64
    import sys
    monkeypatch.syspath_prepend(str(ROOT / "docker"))
    fixture = trusted_module("ngspice_fixture")
    # Module imports only trusted numeric fixture/deck code, never candidate programs.
    smoke = trusted_module("smoke_ngspice")
    interfaces = fixture.specimen(kind, bad)
    project_id, _, _, worker = native_job(platform, "validate")
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        project.interfaces = interfaces
        inputs = []
        for a in session.scalars(select(Artifact).where(Artifact.project_id == project_id)):
            inputs.append({"id": a.id, "sha256": a.sha256, "name": a.name, "lane": a.lane,
                           "contentBase64": base64.b64encode(platform.store.get(a.key)).decode()})
        job = session.get(NativeJob, "native-fixture")
        job.request = {**job.request, "schemaVersion": 1, "purpose": "validate", "inputArtifacts": inputs,
                       "interfaces": interfaces, "requirements": project.requirements}
        request = job.request
    commands = []

    def execute(args, work, timeout):
        assert args[0] == "ngspice", "Direct fallback may execute only deterministic numeric decks"
        commands.append(args)
        return smoke.direct_execute(args, work, timeout)

    work = tmp_path / "native"
    work.mkdir()
    result = run_job(request, work, execute)
    worker.finish_native("native-fixture", result)
    worker.finish_native("native-fixture", result)  # Idempotent callback, no duplicate evidence.
    response = platform.client.get(f"/api/v1/projects/{project_id}/circuit-simulation")
    assert response.status_code == 200
    view = response.json()
    assert view["status"] == "available"
    payload = view["result"]
    states = {c["name"]: c["status"] for c in payload["checks"]}
    assert all(c["evidenceKind"] == "digital" for c in payload["checks"])
    assert payload["physicalAcceptance"] is False
    if kind == "unknown":
        assert set(states.values()) == {"UNKNOWN"} and commands == []
    else:
        assert states["circuit_dc_rails"] == "PASS"
        assert states["circuit_startup"] == "PASS"
        assert states["circuit_led_current"] == ("FAIL" if bad else "NOT_APPLICABLE" if kind in {"rc", "divider"} else "PASS")
        assert states["circuit_total_current"] == "PASS"
        assert len(payload["waveform"]["timeSeconds"]) == 501
        assert payload["resultsSha256"] == canonical_sha256({k: v for k, v in payload.items() if k != "resultsSha256"})
    if kind == "divider":
        nominal = payload["checks"][0]["details"]["measurements"][0]
        assert nominal["voltageV"] == pytest.approx(2.2, abs=1e-6)
    if kind == "led" and not bad:
        current = payload["checks"][2]["details"]["measurements"][0]["peakCurrentMa"] / 1000
        assert 1.25 < current * 1000 < 1.27
        diode_voltage = 2 * (1.380649e-23 * 300.15 / 1.602176634e-19) * math.log1p(current / 1e-20)
        assert current * 1000 + diode_voltage == pytest.approx(3.3, abs=0.002)
    if kind == "rc":
        # Exact ideal response to a finite 1 us ramp, not an instantaneous step.
        rail = payload["waveform"]["signals"][0]
        t = payload["waveform"]["timeSeconds"][5]
        tau, ramp = 0.001, 1e-6
        at_ramp_end = 3.3 * (1 - tau / ramp * (1 - math.exp(-ramp / tau)))
        reference = 3.3 + (at_ramp_end - 3.3) * math.exp(-(t - ramp) / tau)
        assert rail["values"][5] == pytest.approx(reference, abs=0.005)
    download = platform.client.get(f'/api/v1/artifacts/{view["artifactId"]}/content')
    assert download.status_code == 200 and download.json() == payload
    workspace = platform.client.get(f"/api/v1/projects/{project_id}/workspace").json()
    assert {c["name"]: c["status"] for c in workspace["checks"] if c["name"] in states} == states
    assert len([a for a in workspace["artifacts"] if a["name"] == "circuit-waveform.json"]) == 1
    report_path = os.getenv("KLEOS_NGSPICE_REPORT")
    if report_path:
        path = Path(report_path)
        report = json.loads(path.read_text()) if path.exists() else {"date": "2026-11-01", "cases": {}}
        report.update({"mode": "real direct ngspice; temporary SQLite/API; model/project setup fixtures",
                       "python": sys.version.split()[0], "imageSmoke": "NOT_RUN: Docker daemon unavailable",
                       "fullDeviceRelease": "NOT_QUALIFIED: other engineering lanes and PCB correspondence remain UNKNOWN"})
        report["cases"][kind + ("-bad" if bad else "")] = {
            "toolVersion": payload["toolVersion"], "checks": states, "resultsSha256": payload["resultsSha256"],
            "artifactSha256": view["artifactSha256"], "decks": payload["decks"], "commands": len(commands),
            "samples": len(payload["waveform"]["timeSeconds"]), "nativeOverallStatus": result.status}
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        if kind == "power-on":
            path.with_name("ngspice-power-on-sample.json").write_text(json.dumps(payload, sort_keys=True) + "\n")
