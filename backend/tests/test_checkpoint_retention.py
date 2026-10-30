from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_memory_learning import learning_env as learning_env

from kleos.checkpoint_retention import CheckpointRetention
from kleos.db import CommandReceipt, Outbox, Project, Run, Source, Task, now
from kleos.maintenance import Maintenance


class CheckpointClient:
    def __init__(self, *rows):
        self.rows = {row.workflow_id: row for row in rows}
        self.deleted = []
        self.calls = 0
        self.before_read = None

    def list_workflows(self, **filters):
        assert filters.pop("load_input") is False and filters.pop("load_output") is False
        self.calls += 1
        if self.before_read:
            self.before_read(self, filters)
        rows = list(self.rows.values())
        for field, value in filters.items():
            if field == "workflow_ids":
                rows = [row for row in rows if row.workflow_id in value]
            elif field != "limit":
                rows = [row for row in rows if getattr(row, field) == value]
        return rows[:filters.get("limit", len(rows))]

    def delete_workflows(self, identifiers, delete_children):
        assert delete_children is False
        self.deleted.extend(identifiers)
        for identifier in identifiers:
            del self.rows[identifier]


def row(identifier, status="SUCCESS", name="engineering_run", parent=None, fork=None, age_days=40):
    timestamp = int((now() - timedelta(days=age_days)).timestamp() * 1000)
    return SimpleNamespace(workflow_id=identifier, status=status, name=name, parent_workflow_id=parent,
                           forked_from=fork, application_name="kleos", shard_key=None,
                           completed_at=timestamp, updated_at=timestamp)


def schedule(db, workflow="run:fixture", reason="project_deletion"):
    with db.transaction() as session:
        CheckpointRetention.schedule(session, "project-1", workflow, "engineering_run", reason)


def test_terminal_family_and_forks_deleted_without_loading_private_outputs(learning_env):
    db, settings, _, _ = learning_env
    schedule(db)
    client = CheckpointClient(row("run:fixture"), row("child", parent="run:fixture"),
                              row("fork", fork="child"), row("grandchild", parent="fork"), row("unrelated"))
    report = CheckpointRetention(db, settings, client).sweep()
    assert report == {"checkpointCleanups": 1, "checkpointDeferred": 0}
    assert set(client.deleted) == {"run:fixture", "child", "fork", "grandchild"}
    assert set(client.rows) == {"unrelated"}


@pytest.mark.parametrize("status", ["PENDING", "ENQUEUED", "DELAYED", "NEW_UNKNOWN_STATE"])
def test_active_or_unknown_descendant_protects_entire_workflow_family(learning_env, status):
    db, settings, _, _ = learning_env
    schedule(db)
    client = CheckpointClient(row("run:fixture"), row("child", status=status, parent="run:fixture"))
    assert CheckpointRetention(db, settings, client).sweep()["checkpointDeferred"] == 1
    assert client.deleted == []


def test_missing_root_still_protects_existing_active_descendant(learning_env):
    db, settings, _, _ = learning_env
    schedule(db)
    client = CheckpointClient(row("child", status="PENDING", parent="run:fixture"))
    assert CheckpointRetention(db, settings, client).sweep()["checkpointDeferred"] == 1
    assert client.deleted == []


def test_completed_child_cannot_be_erased_while_ancestor_can_replay(learning_env):
    db, settings, _, _ = learning_env
    schedule(db)
    client = CheckpointClient(row("run:fixture", parent="running-parent"), row("running-parent", status="PENDING"))
    assert CheckpointRetention(db, settings, client).sweep()["checkpointDeferred"] == 1
    assert client.deleted == []


def test_changed_family_or_resumed_workflow_is_rechecked_before_delete(learning_env):
    db, settings, _, _ = learning_env
    schedule(db)
    client = CheckpointClient(row("run:fixture"))
    def resume(client, filters):
        if client.calls == 4:
            client.rows["run:fixture"].status = "PENDING"
    client.before_read = resume
    assert CheckpointRetention(db, settings, client).sweep()["checkpointDeferred"] == 1
    assert client.deleted == []


@pytest.mark.parametrize("field,value", [("application_name", "other"), ("name", "other_function"), ("shard_key", "other")])
def test_mismatched_workflow_ownership_is_never_deleted(learning_env, field, value):
    db, settings, _, _ = learning_env
    schedule(db)
    workflow = row("run:fixture")
    setattr(workflow, field, value)
    client = CheckpointClient(workflow)
    assert CheckpointRetention(db, settings, client).sweep()["checkpointDeferred"] == 1
    assert client.deleted == []


def test_recent_cancellation_waits_for_bounded_inflight_steps_to_settle(learning_env):
    db, settings, _, _ = learning_env
    schedule(db)
    client = CheckpointClient(row("run:fixture", status="CANCELLED", age_days=0))
    cleaner = CheckpointRetention(db, settings, client)
    assert cleaner.sweep()["checkpointDeferred"] == 1
    assert cleaner.sweep(at=now() + timedelta(seconds=settings.worker_timeout_seconds + 120))["checkpointCleanups"] == 1


def test_checkpoint_retention_uses_completion_age_and_keeps_active_work(learning_env):
    db, settings, _, _ = learning_env
    with db.transaction() as session:
        for identifier in ("old", "recent", "active"):
            session.add(Run(id=identifier, tenant_id="org", project_id="project-1", command_id="fixture",
                            kind="research", revision=1, status="succeeded"))
            session.add(Outbox(key=f"run:{identifier}", kind="run", payload={"runId": identifier},
                               delivered_at=now() - timedelta(days=40)))
    cleaner = CheckpointRetention(db, settings, CheckpointClient(
        row("run:old"), row("run:recent", age_days=1), row("run:active", status="PENDING")))
    cleaner.schedule_expired(now(), 250)
    assert cleaner.sweep() == {"checkpointCleanups": 1, "checkpointDeferred": 2}
    assert cleaner.client.deleted == ["run:old"]


def test_explicit_erasure_preserves_workflow_ownership_and_defers_final_status(learning_env):
    db, settings, store, user = learning_env
    with db.transaction() as session:
        session.add(Run(id="fixture", tenant_id="org", project_id="project-1", command_id="fixture",
                        kind="research", revision=1, status="running"))
        session.add(Task(id="fixture", tenant_id="org", project_id="project-1", run_id="fixture",
                         lane="research", kind="research", revision=1, status="running"))
        session.add(Source(id="source", tenant_id="org", project_id="project-1", revision=1,
                           uri="https://example.com", sha256="fixture", text="private fixture", authority="research"))
        session.add(Outbox(key="index:source", kind="index_source", payload={"sourceId": "source"}))
        session.add(CommandReceipt(tenant_id="org", actor_id="owner", idempotency_key="old-command",
                                    fingerprint="fixture", type="project.create", project_id="project-1", response={}))
    client = CheckpointClient(row("run:fixture", status="PENDING"),
                              row("task:fixture", name="engineering_task", parent="run:fixture"),
                              row("index:source", name="index_source"))
    index = SimpleNamespace(delete_points=lambda ids: None)
    maintenance = Maintenance(db, settings, store, memory=index, checkpoint_client=client)
    maintenance.request_delete(user, "project-1", 1)
    with db.transaction() as session:
        suppressed = session.scalar(select(Outbox).where(Outbox.key == "index:source"))
        assert suppressed.delivered_at and suppressed.payload["suppressedByProjectDeletion"]
    assert maintenance.purge_project("project-1")["status"] == "checkpoint_cleanup_pending"
    with db.transaction() as session:
        assert session.get(Source, "source") is None
        assert session.query(CommandReceipt).count() == 1
        assert session.get(Project, "project-1").state["deletion"]["applicationPurged"]
        assert session.scalar(select(Outbox).where(Outbox.kind == "checkpoint_cleanup",
            Outbox.payload["workflowId"].as_string() == "index:source")) is not None
    client.rows["run:fixture"].status = "ERROR"
    maintenance.checkpoints.sweep(project_id="project-1")
    with db.transaction() as session:
        assert session.get(Project, "project-1").state["deletion"]["status"] == "purged"
    assert not client.rows


def test_dbos_failure_never_claims_checkpoint_erasure_or_captures_error_body(learning_env):
    db, settings, _, _ = learning_env
    schedule(db)
    client = CheckpointClient()
    def unavailable(client, filters):
        raise RuntimeError("secret provider text must never be retained")
    client.before_read = unavailable
    assert CheckpointRetention(db, settings, client).sweep()["checkpointDeferred"] == 1
    with db.transaction() as session:
        record = session.scalar(select(Outbox).where(Outbox.kind == "checkpoint_cleanup"))
        assert record.payload["result"]["errorType"] == "RuntimeError"
        assert "secret" not in str(record.payload)
