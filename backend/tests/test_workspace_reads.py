"""Warm workspace reads retain fresh access checks without a second database round trip."""

import os

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, event, select
from sqlalchemy.engine import make_url
from test_postgres import postgres  # noqa: F401

from kleos import auth
from kleos.api import create_app
from kleos.auth import Principal, authenticate
from kleos.contracts import Command
from kleos.db import Membership, Project, ProjectMembership, now
from kleos.domain import emit


def create_project(platform):
    return platform.control.command(platform.principal, Command(
        type="project.create", payload={"brief": "Workspace read authorization fixture"}), "workspace-fixture")["projectId"]


def read(platform, project_id, **kwargs):
    return platform.client.get(f"/api/v1/projects/{project_id}/workspace", **kwargs)


def test_warm_snapshot_uses_one_fresh_metadata_query(platform):
    project_id = create_project(platform)
    first = read(platform, project_id)
    assert first.status_code == 200
    statements = []

    def capture(_connection, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(platform.db.engine, "before_cursor_execute", capture)
    try:
        cached = read(platform, project_id)
    finally:
        event.remove(platform.db.engine, "before_cursor_execute", capture)
    assert cached.json() == first.json()
    assert len(statements) == 1
    assert "memberships" in statements[0] and "project_memberships" in statements[0]
    assert "projects.revision" in statements[0]
    assert "projects.brief" not in statements[0]  # No repeated full project/context hydration.


@pytest.mark.parametrize("change,expected", [
    ("organization_revoked", 403), ("role_downgraded", 404),
    ("project_revoked", 404), ("deleted", 404), ("tenant_changed", 404),
])
def test_cached_snapshot_never_bypasses_current_authorization(platform, change, expected):
    project_id = create_project(platform)
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        project.owner_id = "someone-else"
        member = session.scalar(select(Membership).where(Membership.subject == "development"))
        if change == "project_revoked":
            member.role = "viewer"
            session.add(ProjectMembership(project_id=project_id, subject="development", role="viewer"))
    assert read(platform, project_id).status_code == 200
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        if change == "organization_revoked":
            session.execute(delete(Membership).where(Membership.subject == "development"))
        elif change == "role_downgraded":
            session.scalar(select(Membership).where(Membership.subject == "development")).role = "member"
        elif change == "project_revoked":
            session.execute(delete(ProjectMembership).where(ProjectMembership.project_id == project_id))
        elif change == "deleted":
            project.deleted_at = now()
        elif change == "tenant_changed":
            project.tenant_id = "org-b"
    assert read(platform, project_id).status_code == expected


def test_changed_revision_refreshes_snapshot_and_cursor(platform):
    project_id = create_project(platform)
    before = read(platform, project_id).json()
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        project.name = "Newly committed name"
        emit(session, project, "project.updated", {})
    after = read(platform, project_id).json()
    assert after["project"]["name"] == "Newly committed name"
    assert after["revision"] == before["revision"] + 1
    assert after["eventCursor"] == before["eventCursor"] + 1


def test_project_path_does_not_implicitly_choose_organization(platform):
    project_id = create_project(platform)
    assert read(platform, project_id).status_code == 200
    with platform.db.transaction() as session:
        session.add(Membership(subject="development", tenant_id="org-b", role="owner"))
    assert read(platform, project_id).json()["code"] == "organization_required"
    assert read(platform, project_id, headers={"X-Organization-Id": "org-a"}).status_code == 200
    assert read(platform, project_id, headers={"X-Organization-Id": "org-b"}).status_code == 404
    assert read(platform, project_id, headers={"X-Organization-Id": "missing"}).status_code == 403


@pytest.mark.parametrize("org_role,project_owner,project_member,expected", [
    ("owner", False, None, 200), ("admin", False, None, 200),
    ("member", True, None, 200), ("viewer", True, None, 200),
    ("member", False, "viewer", 200), ("viewer", False, "member", 200),
    ("member", False, None, 404), ("viewer", False, None, 404),
])
def test_joined_authorization_matches_project_read_rules(platform, org_role, project_owner, project_member, expected):
    project_id = create_project(platform)
    with platform.db.transaction() as session:
        session.scalar(select(Membership).where(Membership.subject == "development")).role = org_role
        session.get(Project, project_id).owner_id = "development" if project_owner else "someone-else"
        if project_member:
            session.add(ProjectMembership(project_id=project_id, subject="development", role=project_member))
    assert read(platform, project_id).status_code == expected


def test_dependency_override_still_requires_project_authorization(platform):
    project_id = create_project(platform)
    assert read(platform, project_id).status_code == 200
    platform.client.app.dependency_overrides[authenticate] = lambda: Principal("stranger", "org-a", "member")
    try:
        assert read(platform, project_id).status_code == 404
    finally:
        platform.client.app.dependency_overrides.clear()


@pytest.mark.parametrize("warm", [False, True])
def test_commit_after_metadata_read_never_mixes_snapshot_revisions(platform, monkeypatch, warm):
    project_id = create_project(platform)
    before = read(platform, project_id).json() if warm else None
    original = auth.resolve_workspace_access
    committed = []

    def update_after_metadata(*args, **kwargs):
        access = original(*args, **kwargs)
        if not committed:
            with platform.db.transaction() as writer:
                project = writer.get(Project, project_id)
                project.name = "Concurrent committed revision"
                emit(writer, project, "project.updated", {})
            committed.append(True)
        return access

    monkeypatch.setattr(auth, "resolve_workspace_access", update_after_metadata)
    response = read(platform, project_id)
    assert response.status_code == 200
    snapshot = response.json()
    if warm:
        # A body from the exact revision observed at the authorization point is valid.
        assert snapshot == before
    else:
        # With no cached body, the independent repeatable-read fallback sees the newer commit.
        assert snapshot["project"]["name"] == "Concurrent committed revision"
        assert snapshot["eventCursor"] == 2
    latest = read(platform, project_id).json()
    assert latest["project"]["name"] == "Concurrent committed revision"
    assert latest["eventCursor"] == 2


@pytest.mark.skipif(not os.getenv("KLEOS_TEST_POSTGRES_URL"), reason="PostgreSQL opt-in required")
def test_postgres_warm_http_read_checks_access_in_one_query(postgres):  # noqa: F811
    target = make_url(postgres.settings.database_url).database
    assert target.startswith("kleos_test_")
    assert target != make_url(os.environ["KLEOS_TEST_POSTGRES_URL"]).database
    settings = postgres.settings.model_copy(update={
        "auth_mode": "development", "development_token": SecretStr("isolated-workspace-fixture-token"),
        "otlp_endpoint": None,
    })
    project_id = postgres.control.command(postgres.principal, Command(
        type="project.create", payload={"brief": "Postgres workspace fixture"}), "workspace-fixture")["projectId"]
    with postgres.db.transaction() as session:
        session.add(Membership(subject="development", tenant_id="org", role="viewer"))
        session.add(ProjectMembership(subject="development", project_id=project_id, role="viewer"))
    with TestClient(create_app(settings, postgres.db, postgres.store)) as client:
        client.headers["Authorization"] = "Bearer isolated-workspace-fixture-token"
        path = f"/api/v1/projects/{project_id}/workspace"
        first = client.get(path)
        assert first.status_code == 200
        statements = []

        def capture(_connection, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        event.listen(postgres.db.engine, "before_cursor_execute", capture)
        try:
            cached = client.get(path)
        finally:
            event.remove(postgres.db.engine, "before_cursor_execute", capture)
        assert cached.json() == first.json()
        assert len(statements) == 1
        with postgres.db.transaction() as session:
            session.execute(delete(ProjectMembership).where(ProjectMembership.project_id == project_id))
        assert client.get(path).status_code == 404
