"""Delete-build UI uses the existing revision-fenced erasure endpoint."""
import pytest
from sqlalchemy import select
from test_control_plane import command, create, engine

from kleos.contracts import AgentOutput, DomainError
from kleos.db import Membership, Outbox, Project, Run, Task
from kleos.maintenance import Maintenance


def test_delete_build_immediately_hides_it_and_fences_running_research(platform):
    ident = create(platform, "A private desk monitor")["projectId"]
    receipt = command(platform, ident, "project.research")
    worker = engine(platform)
    task = worker.admit_run(receipt["runId"])[0]
    attempt = worker.begin_attempt(task)
    revision = platform.control.workspace(platform.principal, ident)["revision"]
    response = platform.client.delete(f"/api/v1/projects/{ident}?expectedRevision={revision}")
    assert response.status_code == 202
    assert response.json() == {"projectId": ident, "status": "deletion_queued", "deleted": True}
    assert all(p["id"] != ident for p in platform.client.get('/api/v1/projects').json()["projects"])
    assert platform.client.get(f'/api/v1/projects/{ident}/workspace').status_code == 404
    with platform.db.transaction() as session:
        assert session.get(Run, receipt["runId"]).status == "cancelled"
        assert session.get(Task, task).status == "cancelled"
        assert session.scalar(select(Outbox).where(Outbox.key == f'cancel:{receipt["runId"]}'))
        assert session.scalar(select(Outbox).where(Outbox.key == f'delete-project:{ident}'))
    with pytest.raises(DomainError):
        worker.finish_attempt(attempt, AgentOutput(summary="Late response must not publish").model_dump())


def test_delete_build_removes_private_requirement_answers_and_keeps_neighbor(platform):
    ident = create(platform, "My private brief")["projectId"]
    neighbor = create(platform, "Keep this build")["projectId"]
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        project.state = {"proposal": {"summary": "private concept"},
                         "clarificationAnswers": {"name": {"values": ["private answer"]}}}
    revision = platform.control.workspace(platform.principal, ident)["revision"]
    assert platform.client.delete(f"/api/v1/projects/{ident}?expectedRevision={revision}").status_code == 202
    Maintenance(platform.db, platform.settings, platform.store).purge_project(ident)
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        assert project.brief == "" and project.requirements == {}
        assert "proposal" not in project.state and "clarificationAnswers" not in project.state
        assert project.status == "deleted"
    assert platform.client.get(f'/api/v1/projects/{neighbor}/workspace').status_code == 200


def test_delete_build_cannot_cross_tenants_or_be_called_by_readonly_member(platform):
    ident = create(platform)["projectId"]
    with platform.db.transaction() as session:
        membership = session.scalar(select(Membership).where(Membership.tenant_id == 'org-a'))
        membership.role = 'member'
    assert platform.client.delete(f'/api/v1/projects/{ident}?expectedRevision=1').status_code == 403
    with platform.db.transaction() as session:
        membership = session.scalar(select(Membership).where(Membership.tenant_id == 'org-a'))
        membership.role = 'owner'
        session.add(Membership(subject='development', tenant_id='org-b', role='owner'))
    response = platform.client.delete(f'/api/v1/projects/{ident}?expectedRevision=1',
                                      headers={'X-Organization-Id': 'org-b'})
    assert response.status_code == 404
    with platform.db.transaction() as session:
        assert session.get(Project, ident).deleted_at is None
