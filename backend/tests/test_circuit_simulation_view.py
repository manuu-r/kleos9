import json

import pytest
from sqlalchemy import select
from test_circuit_simulation import example
from test_circuit_simulation_runtime import rc_execute
from test_safety_and_recovery import native_job

from kleos.auth import Principal
from kleos.circuit_simulation_runtime import WAVEFORM_NAME, simulate_circuit
from kleos.circuit_simulation_view import CircuitSimulationView
from kleos.contracts import DomainError, GeneratedFile, JobResult
from kleos.db import Artifact, NativeJob, Project, ProjectMembership


def published_simulation(platform, tmp_path):
    project_id, _, _, worker = native_job(platform, "validate")
    interfaces = example("rc")
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        project.interfaces = interfaces
        job = session.get(NativeJob, "native-fixture")
        job.request = {**job.request, "requirements": project.requirements, "interfaces": interfaces}
    checks, body = simulate_circuit(interfaces, tmp_path / "sim", rc_execute)
    worker.finish_native("native-fixture", JobResult(status="unsupported", checks=checks,
        files=[GeneratedFile(path=WAVEFORM_NAME, content=body, media_type="application/json")]))
    return project_id


def test_authorized_current_waveform_and_artifact_download(platform, tmp_path):
    project_id = published_simulation(platform, tmp_path)
    response = platform.client.get(f"/api/v1/projects/{project_id}/circuit-simulation")
    assert response.status_code == 200
    view = response.json()
    assert view["status"] == "available"
    assert len(view["result"]["waveform"]["timeSeconds"]) == 501
    artifact = platform.client.get(f'/api/v1/artifacts/{view["artifactId"]}/download').json()
    assert artifact["sha256"] == view["artifactSha256"]
    downloaded = platform.client.get(artifact["url"]).json()
    assert downloaded == view["result"]
    assert downloaded["physicalAcceptance"] is False


@pytest.mark.parametrize("change", ["interfaces", "revision", "closure", "corrupt", "role", "status"])
def test_stale_or_untrusted_waveform_never_shown_as_current(platform, tmp_path, change):
    project_id = published_simulation(platform, tmp_path)
    service = CircuitSimulationView(platform.db, platform.store)
    assert service.read(platform.principal, project_id)["status"] == "available"
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        evidence = session.scalar(select(Artifact).where(Artifact.name == WAVEFORM_NAME))
        if change == "interfaces":
            project.interfaces = {**project.interfaces, "changed": True}
        elif change == "revision":
            project.engineering_revision += 1
        elif change == "closure":
            candidate = session.scalar(select(Artifact).where(Artifact.status == "candidate"))
            candidate.sha256 = "f" * 64
        elif change == "role":
            evidence.provenance = {**evidence.provenance, "artifactRole": "output"}
        elif change == "status":
            evidence.status = "candidate"
        else:
            platform.store._path(evidence.key).write_text(json.dumps({"forged": True}))
    assert service.read(platform.principal, project_id)["status"] == "unavailable"


def test_tenant_and_revoked_project_membership(platform, tmp_path):
    project_id = published_simulation(platform, tmp_path)
    service = CircuitSimulationView(platform.db, platform.store)
    with pytest.raises(DomainError):
        service.read(Principal("other", "org-b", "owner"), project_id)
    with platform.db.transaction() as session:
        membership = ProjectMembership(project_id=project_id, subject="reader", role="viewer")
        session.add(membership)
        session.flush()
        ident = membership.id
    reader = Principal("reader", "org-a", "member")
    assert service.read(reader, project_id)["status"] == "available"
    with platform.db.transaction() as session:
        session.delete(session.get(ProjectMembership, ident))
    with pytest.raises(DomainError):
        service.read(reader, project_id)


def test_no_evidence_is_explicitly_unavailable(platform):
    project_id, _, _, _ = native_job(platform, "validate")
    result = platform.client.get(f"/api/v1/projects/{project_id}/circuit-simulation")
    assert result.json()["status"] == "unavailable"
    assert result.json()["result"] is None
