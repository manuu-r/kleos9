"""Ledger tests use disposable SQLite only, never customer projects or provider calls."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select

from kleos.contracts import DomainError
from kleos.db import (
    Attempt,
    Base,
    Database,
    Invocation,
    NativeJob,
    NativeSessionStep,
    Organization,
    Project,
    Run,
    Task,
    Validation,
    now,
)
from kleos.native_sessions import NativeSessions, register_session_routes


@pytest.fixture
def ledger(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'session.sqlite'}")
    Base.metadata.create_all(db.engine)
    settings = SimpleNamespace(native_sessions_enabled=True, native_session_max_steps=4, native_session_max_seconds=900)
    with db.transaction() as s:
        s.add(Organization(id="org", name="Fixtures"))
        s.add(Project(id="p", tenant_id="org", owner_id="owner", name="P", brief="Fixture"))
        s.flush()
        s.add(Run(id="r", tenant_id="org", project_id="p", command_id="c", kind="project.generate",
                  revision=1, status="running"))
        s.flush()
        s.add(Task(id="t", tenant_id="org", project_id="p", run_id="r", lane="mechanics",
                   kind="generate", revision=1, fence=2, status="running"))
        s.flush()
        s.add(Attempt(id="a", tenant_id="org", task_id="t", number=1, fence=2,
                      policy_revision=1, provider="fixture", model="fixture", context={}))
        s.flush()
        s.add(Invocation(id="i", tenant_id="org", project_id="p", attempt_id="a", operation="model",
                         provider="fixture", model="fixture", status="pending", reserved_usd=1))
        s.add(NativeJob(id="j", tenant_id="org", task_id="t", attempt_id="a", purpose="session",
                        status="running", token_hash="fixture", request={}, expires_at=now() + timedelta(hours=1)))
    service = NativeSessions(db, settings)
    yield SimpleNamespace(db=db, service=service, settings=settings)
    db.close()


def queue(ledger, key="call1", action="read", args=None):
    return ledger.service.queue("a", "j", key, action, args or {"path": "design.py"})


def test_replay_and_conflict(ledger):
    first = queue(ledger)
    assert queue(ledger)["id"] == first["id"]
    with pytest.raises(DomainError, match="different contents"):
        queue(ledger, args={"path": "other.py"})
    assert ledger.service.read(first["id"])["sequence"] == 1
    assert ledger.service.next_local("j")["step"]["id"] == first["id"]
    assert ledger.service.next_local("j")["step"]["id"] == first["id"]
    value = {"status": "succeeded", "result": {"text": "source", "measurements": {
        "provisionalApprovedDesign": {"status": "PASS"}}}}
    complete = ledger.service.complete_local("j", first["id"], value)
    assert complete["status"] == "succeeded"
    assert ledger.service.complete_local("j", first["id"], value) == complete
    with pytest.raises(DomainError, match="conflicts"):
        ledger.service.complete_local("j", first["id"], {"status": "failed", "result": {}})
    assert ledger.service.next_local("j") == {"step": None}
    with ledger.db.transaction() as s:
        assert list(s.scalars(select(Validation))) == []


def test_order_and_reserved_finish_slot(ledger):
    steps = [queue(ledger, f"call{i}") for i in range(3)]
    with pytest.raises(DomainError, match="step limit"):
        queue(ledger, "call4")
    finish = queue(ledger, "finish", "finish", {})
    with pytest.raises(DomainError, match="already finishing"):
        queue(ledger, "afterfinish")
    with pytest.raises(DomainError, match="earlier"):
        ledger.service.complete_local("j", finish["id"], {"status": "succeeded", "result": {}})
    for step in [*steps, finish]:
        ledger.service.complete_local("j", step["id"], {"status": "succeeded", "result": {}})
    assert ledger.service.next_local("j")["step"] is None


@pytest.mark.parametrize("table,id,attribute,value", [
    (NativeJob, "j", "purpose", "build"),
    (NativeJob, "j", "status", "cancelled"),
    (Task, "t", "fence", 3),
    (Task, "t", "tenant_id", "other"),
    (Project, "p", "engineering_revision", 2),
    (Project, "p", "deleted_at", now()),
    (Run, "r", "status", "cancelled"),
    (Attempt, "a", "status", "succeeded"),
    (NativeJob, "j", "created_at", now() - timedelta(minutes=16)),
    (NativeJob, "j", "expires_at", now() - timedelta(seconds=1)),
])
def test_freshness_and_scope(ledger, table, id, attribute, value):
    step = queue(ledger)
    with ledger.db.transaction() as s:
        setattr(s.get(table, id), attribute, value)
    for action in (lambda: queue(ledger, "later"), lambda: ledger.service.next_local("j"),
                   lambda: ledger.service.complete_local("j", step["id"], {"status": "succeeded", "result": {}})):
        with pytest.raises(DomainError):
            action()


def test_requires_live_reservation_and_correct_attempt(ledger):
    with ledger.db.transaction() as s:
        s.get(Invocation, "i").status = "uncertain"
    with pytest.raises(DomainError, match="reserved"):
        queue(ledger)
    with pytest.raises(DomainError, match="attempt"):
        ledger.service.queue("other", "j", "c", "read", {})


@pytest.mark.parametrize("action", ["exec", "validate", "approve", "publish", "shell"])
def test_fixed_capabilities(ledger, action):
    with pytest.raises(DomainError, match="Unsupported"):
        queue(ledger, action=action)


@pytest.mark.parametrize("result", [{"checks": [{"status": "PASS"}]}, {"validations": []},
                                    {"acceptedRelease": {}}, {"releaseAuthority": True}])
def test_no_validation_or_release_authority(ledger, result):
    step = queue(ledger)
    with pytest.raises(DomainError, match="authority"):
        ledger.service.complete_local("j", step["id"], {"status": "succeeded", "result": result})


def test_bounded_finite_json_and_cross_job_step(ledger):
    with pytest.raises(DomainError, match="2 MiB"):
        queue(ledger, args={"text": "a" * (2 * 1024 * 1024)})
    with pytest.raises(DomainError, match="finite"):
        queue(ledger, args={"number": float("nan")})
    step = queue(ledger)
    with pytest.raises(DomainError, match="2 MiB"):
        ledger.service.complete_local("j", step["id"], {"status": "succeeded", "result": {
            "imageBase64": "a" * (2 * 1024 * 1024)}})
    with pytest.raises(DomainError, match="not found"):
        ledger.service.complete_local("j", "unrelated", {"status": "succeeded", "result": {}})


def test_routes_require_authorization_and_recheck_live_state(ledger):
    class Auth:
        def authorize(self, job_id, token):
            if job_id != "j" or token != "scoped-token":
                raise DomainError("Invalid native job credential", 401)
    app = FastAPI()

    @app.exception_handler(DomainError)
    async def error(_request, exc):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    register_session_routes(app, Auth(), ledger.service)
    step = queue(ledger)
    with TestClient(app) as client:
        url = "/api/v1/internal/jobs/j/session"
        assert client.get(url + "/next").status_code == 401
        headers = {"Authorization": "Bearer scoped-token"}
        assert client.get(url + "/next", headers=headers).json()["step"]["id"] == step["id"]
        result_url = url + f"/{step['id']}/result"
        assert client.post(result_url, headers=headers, json={"status": "PASS"}).status_code == 422
        assert client.post(result_url, headers=headers, json={"status": "succeeded", "result": {}}).status_code == 200
        with ledger.db.transaction() as s:
            s.get(Task, "t").fence += 1
        assert client.get(url + "/next", headers=headers).status_code == 409


def test_unique_constraints_exist(ledger):
    # PostgreSQL serializes job row allocation; uniqueness remains the final DB guard.
    table = NativeSessionStep.__table__
    keys = {tuple(col.name for col in constraint.columns) for constraint in table.constraints}
    assert ("job_id", "request_key") in keys
    assert ("job_id", "sequence") in keys


def test_trusted_finish_after_model_completion_and_disable(ledger):
    with ledger.db.transaction() as s:
        s.get(Invocation, "i").status = "succeeded"
    finish = queue(ledger, "finish", "finish", {})
    assert finish["action"] == "finish"
    ledger.settings.native_sessions_enabled = False
    with pytest.raises(DomainError, match="disabled"):
        ledger.service.next_local("j")


def test_finish_handshake_allows_waiting_poll_and_result(ledger):
    finish = queue(ledger, "finish", "finish", {})
    with ledger.db.transaction() as s:
        s.get(Task, "t").status = "waiting"
        s.get(Attempt, "a").status = "waiting"
    assert ledger.service.next_local("j")["step"]["id"] == finish["id"]
    assert ledger.service.complete_local("j", finish["id"], {"status": "succeeded", "result": {}})["status"] == "succeeded"


def test_session_retention_scrubs_content_and_call_key(ledger, tmp_path):
    from kleos.config import Settings
    from kleos.maintenance import Maintenance
    from kleos.storage import ArtifactStore

    step = queue(ledger, "private-sensitive-request-key", args={"path": "private-name.py"})
    ledger.service.complete_local("j", step["id"], {"status": "succeeded", "result": {"text": "private source"}})
    settings = Settings(_env_file=None, environment="test", local_artifact_dir=tmp_path / "artifacts")
    store = ArtifactStore(settings)
    with ledger.db.transaction() as s:
        s.get(NativeJob, "j").status = "succeeded"
        s.get(NativeSessionStep, step["id"]).created_at = now() - timedelta(days=31)
    maint = Maintenance(ledger.db, settings, store)
    assert maint.retention()["nativeSessionSteps"] == 1
    assert maint.retention()["nativeSessionSteps"] == 0
    with ledger.db.transaction() as s:
        row = s.get(NativeSessionStep, step["id"])
        assert row.arguments == {}
        assert row.result == {"retentionExpired": True}
        assert row.request_key == f"expired:{row.id}"
        assert row.request_hash and row.result_hash


def test_live_session_retention_does_not_erase_inflight_context(ledger, tmp_path):
    from kleos.config import Settings
    from kleos.maintenance import Maintenance
    from kleos.storage import ArtifactStore

    step = queue(ledger)
    with ledger.db.transaction() as s:
        s.get(NativeSessionStep, step["id"]).created_at = now() - timedelta(days=31)
    settings = Settings(_env_file=None, environment="test", local_artifact_dir=tmp_path / "artifacts")
    assert Maintenance(ledger.db, settings, ArtifactStore(settings)).retention()["nativeSessionSteps"] == 0
    assert ledger.service.read(step["id"])["arguments"] == {"path": "design.py"}


def test_project_erasure_scrubs_session_content(ledger, tmp_path):
    from kleos.auth import Principal
    from kleos.config import Settings
    from kleos.db import Membership
    from kleos.maintenance import Maintenance
    from kleos.storage import ArtifactStore

    step = queue(ledger, "private-sensitive-call", args={"path": "private.py"})
    with ledger.db.transaction() as s:
        s.add(Membership(subject="owner", tenant_id="org", role="owner"))
    settings = Settings(_env_file=None, environment="test", local_artifact_dir=tmp_path / "artifacts")
    maint = Maintenance(ledger.db, settings, ArtifactStore(settings))
    with ledger.db.transaction() as s:
        revision = s.get(Project, "p").revision
    maint.request_delete(Principal("owner", "org", "owner"), "p", revision)
    maint.purge_project("p")
    with ledger.db.transaction() as s:
        row = s.get(NativeSessionStep, step["id"])
        assert row.arguments == {}
        assert row.result == {"contentDeleted": True}
        assert row.status == "cancelled"
        assert row.request_key == f"purged:{row.id}"
        assert row.job_id == "j" and row.attempt_id == "a" and row.sequence == 1
    with pytest.raises(DomainError):
        ledger.service.next_local("j")


def test_migration_round_trip_matches_ledger_model(ledger):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    path = Path(__file__).parents[1] / "migrations/versions/e91d4b2f608a_native_session_steps.py"
    spec = importlib.util.spec_from_file_location("session_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with ledger.db.engine.begin() as connection:
        NativeSessionStep.__table__.drop(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        inspector = inspect(connection)
        assert {column["name"] for column in inspector.get_columns("native_session_steps")} == {
            column.name for column in NativeSessionStep.__table__.columns}
        assert {tuple(item["column_names"]) for item in inspector.get_unique_constraints("native_session_steps")} == {
            ("job_id", "request_key"), ("job_id", "sequence")}
        assert all(fk["options"]["ondelete"] == "CASCADE"
                   for fk in inspector.get_foreign_keys("native_session_steps"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert "native_session_steps" not in inspect(connection).get_table_names()


def test_session_events_are_metadata_only_and_replays_do_not_duplicate(ledger):
    from kleos.db import Event

    step = queue(ledger, args={"path": "private.py", "content": "private source"})
    queue(ledger, args={"path": "private.py", "content": "private source"})
    value = {"status": "succeeded", "result": {"content": "private output", "images": [
        {"contentBase64": "secret-image"}]}}
    ledger.service.complete_local("j", step["id"], value)
    ledger.service.complete_local("j", step["id"], value)
    with ledger.db.transaction() as s:
        events = list(s.scalars(select(Event).order_by(Event.sequence)))
        assert [event.type for event in events] == ["native.session.queued", "native.session.completed"]
        assert events[0].data == {"jobId": "j", "stepId": step["id"], "action": "read", "sequence": 1}
        assert events[1].data == {**events[0].data, "status": "succeeded"}


def test_run_inspector_omits_native_source_and_image_bodies(ledger):
    import hashlib
    import json

    from kleos.auth import Principal
    from kleos.domain import ControlPlane

    step = queue(ledger, args={"path": "design.py", "content": "private source"})
    ledger.service.complete_local("j", step["id"], {"status": "succeeded", "result": {
        "content": "private output", "logs": "x" * 3000, "apiKey": "private-key",
        "images": [{"name": "front.png", "mediaType": "image/png", "sha256": "hash",
                    "contentBase64": "secret-image"}]}})
    control = ControlPlane.__new__(ControlPlane)
    control.db = ledger.db
    view = control.inspect_run(Principal("owner", "org", "owner"), "r")
    summary = view["tasks"][0]["nativeJobs"][0]["sessionSteps"][0]
    encoded = json.dumps(summary)
    assert summary["arguments"] == {"path": "design.py", "contentSha256": hashlib.sha256(b"private source").hexdigest()}
    assert summary["result"]["images"] == [{"name": "front.png", "mediaType": "image/png", "sha256": "hash"}]
    assert len(summary["result"]["logs"]) == 2000
    assert summary["result"]["apiKey"] == "[redacted]"
    assert not any(text in encoded for text in ["private source", "private output", "private-key", "secret-image", "contentBase64", "requestKey"])
    with pytest.raises(DomainError):
        control.inspect_run(Principal("outsider", "other-org", "owner"), "r")


def test_settings_increase_never_extends_pinned_session_step_ceiling(ledger):
    with ledger.db.transaction() as s:
        s.get(NativeJob, "j").request = {"sessionLimits": {"maxSteps": 3, "maxSeconds": 60}}
    ledger.settings.native_session_max_steps = 64
    ledger.settings.native_session_max_seconds = 1800
    queue(ledger, "one")
    queue(ledger, "two")
    with pytest.raises(DomainError, match="step limit"):
        queue(ledger, "three")
    assert queue(ledger, "finish", "finish", {})["sequence"] == 3


def test_pinned_original_lifetime_survives_worker_limit_increase(ledger):
    with ledger.db.transaction() as s:
        job = s.get(NativeJob, "j")
        job.request = {"sessionLimits": {"maxSteps": 32, "maxSeconds": 60}}
        job.created_at = now() - timedelta(seconds=61)
    ledger.settings.native_session_max_seconds = 1800
    with pytest.raises(DomainError, match="deadline"):
        ledger.service.next_local("j")


def test_current_operator_reductions_can_stop_previously_larger_sessions(ledger):
    with ledger.db.transaction() as s:
        s.get(NativeJob, "j").request = {"sessionLimits": {"maxSteps": 32, "maxSeconds": 900}}
    ledger.settings.native_session_max_steps = 2
    queue(ledger, "one")
    with pytest.raises(DomainError, match="step limit"):
        queue(ledger, "two")
    with ledger.db.transaction() as s:
        s.get(NativeJob, "j").created_at = now() - timedelta(seconds=31)
    ledger.settings.native_session_max_seconds = 30
    with pytest.raises(DomainError, match="deadline"):
        ledger.service.next_local("j")
