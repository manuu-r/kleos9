import json
import math

import pytest
from sqlalchemy import select
from test_circuit_simulation import elements, example
from test_control_plane import command
from test_safety_and_recovery import native_job

from kleos.circuit_simulation import canonical_sha256, plan_simulation
from kleos.circuit_simulation_runtime import CHECK_NAMES, WAVEFORM_NAME, parse_table, simulate_circuit
from kleos.contracts import CheckResult, DomainError, GeneratedFile, JobResult
from kleos.db import Artifact, NativeJob, Project, Validation
from kleos.native_runner import UnsupportedTool, validate_candidates


def rc_execute(args, work, timeout):
    """Fixture output only; ordinary tests never need a local solver."""
    assert timeout <= 15
    if args == ["ngspice", "--version"]:
        return 0, "** ngspice-39 : fixture only"
    source = (work / "circuit.cir").read_text()
    r = float(elements(source, "R")[0][3])
    c = float(elements(source, "C")[0][3])
    plan = plan_simulation(example("rc"))
    vin, out = plan.metadata["nodes"]["VIN"], plan.metadata["nodes"]["OUT"]
    vectors = plan.metadata["vectors"]
    header = "time " + " ".join(vectors) + "\n"
    rows = []
    for index in range(1001):
        t = index * 0.0001
        voltage = 3.3 * (1 - math.exp(-t / (r * c)))
        values = {f"v({vin})": 3.3 if index else 0, f"v({out})": voltage,
                  "i(V1)": -(3.3 - voltage) / r if index else 0}
        rows.append(" ".join(map(str, [t, *[values[v] for v in vectors]])))
    for filename in ("transient.dat", "transient-raw.dat"):
        (work / filename).write_text(header + "\n".join(rows) + "\n")
    values = {f"v({vin})": 3.3, f"v({out})": 3.3, "i(V1)": 0}
    (work / "operating.dat").write_text(header + " ".join(map(str, [3.3, *[values[v] for v in vectors]])) + "\n")
    return 0, "fixture"


def test_fixture_runtime_hashes_band_and_independent_checks(tmp_path):
    checks, body = simulate_circuit(example("rc"), tmp_path / "sim", rc_execute)
    # The fixture's accepted 2.4 V ceiling is exceeded by a 3.3 V RC rail.
    assert [c.status for c in checks] == ["FAIL", "PASS", "NOT_APPLICABLE", "PASS"]
    payload = json.loads(body)
    expected = payload.pop("resultsSha256")
    assert expected == canonical_sha256(payload)
    wave = payload["waveform"]
    assert len(wave["timeSeconds"]) == 501
    rail = wave["signals"][0]
    assert rail["low"][1] < rail["values"][1] < rail["high"][1]
    assert len(payload["decks"]) == 5
    assert all(c.details["advisory"] and c.details["required"] is False for c in checks)


def test_raw_current_spike_cannot_be_hidden_by_linearization_or_downsampling(tmp_path):
    def spike(args, work, timeout):
        result = rc_execute(args, work, timeout)
        if "--version" not in args:
            path = work / "transient-raw.dat"
            lines = path.read_text().splitlines()
            lines.insert(2, "0.00005 0.1 3.3 -1")
            path.write_text("\n".join(lines) + "\n")
        return result
    checks, body = simulate_circuit(example("rc"), tmp_path / "sim", spike)
    assert checks[-1].status == "FAIL"
    assert checks[-1].details["peakCurrentMa"] == 1000
    supply = next(s for s in json.loads(body)["waveform"]["signals"] if s["name"] == "Supply current")
    assert max(supply["high"]) == 1000


@pytest.mark.parametrize("problem,status", [("absent", "UNKNOWN"), ("timeout", "ERROR"), ("version", "UNKNOWN"),
                                         ("exit", "ERROR"), ("nan", "ERROR"), ("symlink", "ERROR")])
def test_runtime_failures_preserve_status_and_do_not_publish_partial_waveforms(tmp_path, problem, status):
    def failed(args, work, timeout):
        if problem == "absent":
            raise UnsupportedTool("missing tool")
        if problem == "timeout":
            raise TimeoutError("bounded deadline")
        if problem == "version":
            return 0, "ngspice-999"
        if problem == "exit":
            return 1, "failed"
        result = rc_execute(args, work, timeout)
        if "--version" not in args:
            path = work / "operating.dat"
            if problem == "nan":
                path.write_text("time v(1) v(2) i(V1)\n3.3 nan 3.3 0\n")
            else:
                path.unlink()
                path.symlink_to(work / "transient.dat")
        return result
    checks, body = simulate_circuit(example("rc"), tmp_path / "sim", failed)
    assert all(c.status == status for c in checks)
    assert json.loads(body)["waveform"]["signals"] == []


@pytest.mark.parametrize("rows", ["time v(1)\n0 0\n0 1\n0.1 1", "time v(1)\n0 0\n0.01 1\n0.02 1",
    "time v(2)\n0 0\n0.05 1\n0.1 1", "time v(1)\n0 0\n0.05 inf\n0.1 1",
    "time v(1)\n" + "0 0\n" * 5001])
def test_invalid_table_rejected(tmp_path, rows):
    path = tmp_path / "response.dat"
    path.write_text(rows)
    with pytest.raises(ValueError):
        parse_table(path, ["v(1)"], transient=True)


def test_sparse_and_oversized_output_is_not_evidence(tmp_path):
    path = tmp_path / "response.dat"
    path.write_text("time v(1)\n0 0\n0.05 1\n0.1 1\n")
    with pytest.raises(ValueError, match="maximum timestep"):
        parse_table(path, ["v(1)"], transient=True)
    path.write_bytes(b"0" * 2_000_001)
    with pytest.raises(ValueError, match="oversized"):
        parse_table(path, ["v(1)"], transient=True)


def test_no_model_never_executes(tmp_path):
    interfaces = example("led")
    interfaces["circuit"]["components"][-1]["value"] = "LED"
    def forbidden(*_args):
        pytest.fail("Unsupported parts must not run")
    checks, _ = simulate_circuit(interfaces, tmp_path / "sim", forbidden)
    assert all(c.status == "UNKNOWN" for c in checks)


@pytest.mark.parametrize("version", ["ngspice-390", "ngspice-470", "ngspice-47evil"])
def test_similar_version_prefix_is_not_accepted(tmp_path, version):
    def execute(*_args):
        return 0, version
    checks, _ = simulate_circuit(example("rc"), tmp_path / "sim", execute)
    assert all(c.status == "UNKNOWN" for c in checks)


def test_advisory_fail_does_not_fail_validation_but_required_fail_still_does(tmp_path, monkeypatch):
    from kleos import circuit_simulation_runtime, native_runner
    check = CheckResult(name=CHECK_NAMES[0], status="FAIL", details={"required": False, "scopeExcluded": True})
    monkeypatch.setattr(circuit_simulation_runtime, "simulate_circuit", lambda *args: ([check], "{}"))
    result = validate_candidates({"interfaces": {}, "inputArtifacts": []}, tmp_path)
    assert result.status == "unsupported"
    monkeypatch.setattr(native_runner, "validate_interfaces", lambda *args: CheckResult(name="interfaces", status="FAIL"))
    assert validate_candidates({"interfaces": {}, "inputArtifacts": []}, tmp_path).status == "failed"


def publish(platform, *, result_status="succeeded", stale=False):
    project_id, _, _, worker = native_job(platform, "validate")
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        project.state = {"confirmed": True}
        job = session.get(NativeJob, "native-fixture")
        job.request = {**job.request, "requirements": project.requirements, "interfaces": project.interfaces}
    checks = [CheckResult(name=name, status="PASS") for name in
              ("interfaces", "mechanics", "electronics", "firmware", "application")]
    checks.append(CheckResult(name=CHECK_NAMES[0], status="FAIL" if result_status == "succeeded" else "PASS",
                              details={"required": False, "scopeExcluded": True}))
    if stale:
        with platform.db.transaction() as session:
            session.get(Project, project_id).interfaces = {"changed": True}
    worker.finish_native("native-fixture", JobResult(status=result_status, checks=checks,
        files=[GeneratedFile(path=WAVEFORM_NAME, content="{}", media_type="application/json")]))
    return project_id, worker


def test_waveform_evidence_preserves_release_closure_and_advisory_failure(platform):
    project_id, _ = publish(platform)
    with platform.db.transaction() as session:
        artifacts = session.scalars(select(Artifact).where(Artifact.project_id == project_id)).all()
        evidence = next(a for a in artifacts if a.name == WAVEFORM_NAME)
        assert evidence.status == "evidence" and evidence.provenance["artifactRole"] == "validationEvidence"
        validation = session.scalar(select(Validation).where(Validation.name == CHECK_NAMES[0]))
        assert evidence.id not in validation.artifact_hashes
        assert validation.details["waveformSha256"] == evidence.sha256
    command(platform, project_id, "project.acceptRelease")


def test_simulation_pass_survives_other_lane_unsupported_result(platform):
    publish(platform, result_status="unsupported")
    with platform.db.transaction() as session:
        checks = session.scalars(select(Validation)).all()
        assert [(c.name, c.status) for c in checks] == [(CHECK_NAMES[0], "PASS")]


def test_stale_result_cannot_publish_waveform(platform):
    with pytest.raises(DomainError):
        publish(platform, stale=True)
    with platform.db.transaction() as session:
        assert not session.scalar(select(Artifact).where(Artifact.name == WAVEFORM_NAME))
