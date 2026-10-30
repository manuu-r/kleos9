from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kleos.api import create_app
from kleos.auth import Principal, issue_invite
from kleos.config import Settings
from kleos.db import Base, Database, Membership, Organization
from kleos.domain import ControlPlane
from kleos.storage import ArtifactStore


@pytest.fixture
def platform(tmp_path):
    settings = Settings(
        environment="test", auth_mode="development", development_no_login=False, development_token="test-token-not-for-deployment",
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}", local_artifact_dir=tmp_path / "artifacts",
        project_budget_usd=10, daily_budget_usd=100,
    )
    db = Database(settings.database_url)
    Base.metadata.create_all(db.engine)
    with db.transaction() as session:
        session.add_all([Organization(id="org-a", name="A"), Organization(id="org-b", name="B")])
        session.flush()
        session.add(Membership(subject="development", tenant_id="org-a", role="owner"))
    store = ArtifactStore(settings)
    control = ControlPlane(db, settings, store)
    principal = Principal("development", "org-a", "owner")
    app = create_app(settings, db, store)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test-token-not-for-deployment"
        yield SimpleNamespace(settings=settings, db=db, store=store, control=control,
                              principal=principal, client=client)
    db.close()


@pytest.fixture
def auth(tmp_path):
    settings = Settings(
        _env_file=None, environment="test", auth_mode="password", database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        local_artifact_dir=tmp_path / "artifacts", project_budget_usd=10, daily_budget_usd=100,
        setup_token="setup-token-for-tests-only",
    )
    db = Database(settings.database_url)
    Base.metadata.create_all(db.engine)
    with db.transaction() as session:
        session.add(Organization(id="org-a", name="A"))
    app = create_app(settings, db, ArtifactStore(settings))

    def invite(role="member", **kwargs):
        with db.transaction() as session:
            return issue_invite(session, "org-a", role, **kwargs)

    with TestClient(app, base_url="https://testserver") as client:
        yield type("Auth", (), {"client": client, "db": db, "invite": staticmethod(invite), "app": app})
    db.close()
