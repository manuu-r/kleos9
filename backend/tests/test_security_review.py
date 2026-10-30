"""Regressions for authorization changes during long-lived client connections."""

import asyncio
import os

import pytest
from sqlalchemy import select
from test_control_plane import command
from test_postgres import postgres  # noqa: F401
from test_safety_and_recovery import native_job

from kleos import api, domain
from kleos.auth import Principal
from kleos.contracts import CheckResult, Command, DomainError, JobResult
from kleos.db import Artifact, Membership, NativeJob, Project
from kleos.domain import emit, get_project


def test_sse_rechecks_project_authorization_after_organization_role_downgrade(platform, monkeypatch):
    project_id = platform.control.command(
        platform.principal, Command(type="project.create", payload={"brief": "Owner's private watch"}),
        "create-private-watch",
    )["projectId"]
    with platform.db.transaction() as session:
        session.add(Membership(subject="temporary-admin", tenant_id="org-a", role="admin"))

    class ConnectedRequest:
        headers = {}
        checks = 0

        async def is_disconnected(self):
            self.checks += 1
            return self.checks > 32

    reauthenticated = []

    def downgrade_at_heartbeat(_request):
        with platform.db.transaction() as session:
            membership = session.scalar(select(Membership).where(Membership.subject == "temporary-admin"))
            membership.role = "member"
            project = get_project(session, project_id)
            emit(session, project, "project.privateUpdate", {"secret": "private-after-downgrade"})
        principal = Principal("temporary-admin", "org-a", "member")
        reauthenticated.append(principal)
        return principal

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(api, "authenticate", downgrade_at_heartbeat)
    monkeypatch.setattr(api.asyncio, "sleep", no_sleep)
    endpoint = next(route.endpoint for route in platform.client.app.routes
                    if getattr(route, "path", "") == "/api/v1/projects/{project_id}/events")

    async def collect():
        response = await endpoint(project_id, ConnectedRequest(),
                                  Principal("temporary-admin", "org-a", "admin"), after=0, once=False)
        return "".join([part async for part in response.body_iterator])

    body = asyncio.run(collect())
    assert len(reauthenticated) == 1
    assert "event: auth_expired" in body
    assert "private-after-downgrade" not in body


def test_replacing_interfaces_cannot_reuse_old_digital_validation(platform):
    project_id, _, _, worker = native_job(platform, "validate")
    with platform.db.transaction() as session:
        project = get_project(session, project_id)
        project.state = {"confirmed": True}
        project.interfaces = {"mechanical": {"externalMm": {"x": 40, "y": 40, "z": 10}}}
        job = session.get(NativeJob, "native-fixture")
        job.request = {**job.request, "requirements": project.requirements, "interfaces": project.interfaces}
        emit(session, project, "fixture.interfaces", {})
    worker.finish_native("native-fixture", JobResult(status="succeeded", checks=[
        CheckResult(name=name, status="PASS")
        for name in ["interfaces", "mechanics", "electronics", "firmware", "application"]
    ]))
    command(platform, project_id, "project.acceptRelease")
    with platform.db.transaction() as session:
        previous_release = session.get(Project, project_id).accepted_release_id
    try:
        result = command(platform, project_id, "project.solveInterfaces")
    except DomainError:
        # Freezing established interfaces until explicit invalidation is also safe.
        return
    task_id = worker.admit_run(result["runId"])[0]
    attempt_id = worker.begin_attempt(task_id)
    worker.finish_attempt(attempt_id, {
        "interfaces": {"mechanical": {"externalMm": {"x": 4, "y": 4, "z": 1}}},
        "summary": "A much smaller enclosure",
    })
    worker.finish_run(result["runId"])
    with pytest.raises(DomainError):
        command(platform, project_id, "project.acceptRelease")
    with platform.db.transaction() as session:
        assert session.get(Project, project_id).accepted_release_id == previous_release


def test_reconfirming_new_requirements_invalidates_previous_checks(platform):
    project_id, _, _, worker = native_job(platform, "validate")
    with platform.db.transaction() as session:
        project = get_project(session, project_id)
        project.state = {"confirmed": True}
        project.requirements = {"screen": "small"}
        job = session.get(NativeJob, "native-fixture")
        job.request = {**job.request, "requirements": project.requirements, "interfaces": project.interfaces}
        emit(session, project, "fixture.requirements", {})
    worker.finish_native("native-fixture", JobResult(status="succeeded", checks=[
        CheckResult(name=name, status="PASS")
        for name in ["interfaces", "mechanics", "electronics", "firmware", "application"]
    ]))
    command(platform, project_id, "project.acceptRelease")
    research = command(platform, project_id, "project.research")
    task_id = worker.admit_run(research["runId"])[0]
    attempt_id = worker.begin_attempt(task_id)
    worker.finish_attempt(attempt_id, {
        "summary": "New accepted display requirement",
        "requirements": {"screen": "must not overwrite existing", "displayRefreshHz": 60},
    })
    worker.finish_run(research["runId"])
    command(platform, project_id, "project.confirm")
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        assert project.engineering_revision == 2
        assert project.requirements == {"screen": "small", "displayRefreshHz": 60}
    with pytest.raises(DomainError):
        command(platform, project_id, "project.acceptRelease")


def test_generator_job_cannot_submit_independent_release_checks(platform):
    project_id, _, _, worker = native_job(platform, "build")
    worker.finish_native("native-fixture", JobResult(status="succeeded", checks=[
        CheckResult(name=name, status="PASS")
        for name in ["interfaces", "mechanics", "electronics", "firmware", "application"]
    ]))
    assert platform.control.workspace(platform.principal, project_id)["checks"] == []
    with pytest.raises(DomainError):
        command(platform, project_id, "project.acceptRelease")


@pytest.mark.skipif(not os.getenv("KLEOS_TEST_POSTGRES_URL"), reason="PostgreSQL opt-in required")
def test_workspace_snapshot_and_cursor_share_one_committed_baseline(postgres, monkeypatch):  # noqa: F811
    created = postgres.control.command(postgres.principal, Command(
        type="project.create", payload={"brief": "Consistent workspace"}), "create")
    project_id = created["projectId"]
    original = domain.get_project
    committed = []

    def publish_after_snapshot_begins(session, project_id, *args, **kwargs):
        project = original(session, project_id, *args, **kwargs)
        if kwargs.get("lock") is False and not committed:
            with postgres.db.transaction() as writer:
                current = original(writer, project_id)
                writer.add(Artifact(tenant_id=current.tenant_id, project_id=project_id,
                                    revision=current.engineering_revision, lane="mechanics",
                                    name="enclosure.py", key="new-candidate", sha256="a" * 64,
                                    size=1, media_type="text/plain"))
                emit(writer, current, "artifact.created", {"name": "enclosure.py"})
            committed.append(True)
        return project

    monkeypatch.setattr(domain, "get_project", publish_after_snapshot_begins)
    snapshot = postgres.control.workspace(postgres.principal, project_id)
    assert committed
    assert snapshot["eventCursor"] == 1
    assert snapshot["revision"] == created["revision"]
    assert snapshot["artifacts"] == []
    current = postgres.control.workspace(postgres.principal, project_id)
    assert current["eventCursor"] == 2
    assert current["revision"] == created["revision"] + 1
    assert [artifact["name"] for artifact in current["artifacts"]] == ["enclosure.py"]
    assert [event["sequence"] for event in postgres.control.events(
        postgres.principal, project_id, snapshot["eventCursor"])] == [2]
