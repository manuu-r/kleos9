from datetime import timedelta

import pytest
from sqlalchemy import select
from test_checkpoint_retention import CheckpointClient
from test_control_plane import FakeModels, command, create, engine, start_research

from kleos.contracts import AgentOutput, DomainError
from kleos.db import Attempt, DailyBudget, Invocation, Outbox, Project, Task, now
from kleos.maintenance import Maintenance
from kleos.worker import delete_with_retry


def test_explicit_research_turns_each_have_a_bounded_attempt_allowance(platform):
    project = create(platform)["projectId"]
    worker = engine(platform)
    for turn in range(platform.settings.max_attempts + 2):
        receipt = command(platform, project, "project.research")
        task = worker.admit_run(receipt["runId"])[0]
        attempt = worker.begin_attempt(task)
        assert attempt and worker.begin_attempt(task) == attempt
        worker.finish_attempt(attempt, AgentOutput(summary="Research complete").model_dump())
        worker.finish_run(receipt["runId"])
    receipt = command(platform, project, "project.research")
    task = worker.admit_run(receipt["runId"])[0]
    for _ in range(platform.settings.max_attempts):
        attempt = engine(platform).begin_attempt(task)
        assert attempt
        with platform.db.transaction() as session:
            session.get(Attempt, attempt).status = "failed"
    assert engine(platform).begin_attempt(task) is None


@pytest.mark.asyncio
async def test_failed_sdk_calls_release_live_capacity_but_hold_money_and_cannot_replay(platform):
    class Throttled(Exception):
        status_code = 429
    class Models(FakeModels):
        async def generate(self, *args):
            self.calls += 1
            raise Throttled("redacted provider body")
    models = Models()
    worker = engine(platform, models)
    for index in range(platform.settings.max_model_calls + 1):
        project = create(platform, f"Research {index}")["projectId"]
        receipt = command(platform, project, "project.research", key=f"research-{index}")
        task = worker.admit_run(receipt["runId"])[0]
        await worker.run_task(task)
        worker.finish_run(receipt["runId"])
    with platform.db.transaction() as session:
        calls = session.scalars(select(Invocation)).all()
        assert len(calls) == platform.settings.max_model_calls + 1
        assert all(call.status == "uncertain" for call in calls)
        call_id, attempt_id, project_id = calls[0].id, calls[0].attempt_id, calls[0].project_id
        held = session.get(Project, project_id).reserved_usd
        assert held == platform.settings.invocation_reservation_usd
        assert session.scalar(select(DailyBudget)).reserved == pytest.approx(held * len(calls))
        # Make the same operation fresh to distinguish the uncertainty check
        # from the independent task fencing guard.
        attempt = session.get(Attempt, attempt_id)
        attempt.status = "running"
        session.get(Task, attempt.task_id).status = "running"
    with pytest.raises(DomainError) as error:
        worker.reserve_invocation(attempt_id)
    assert error.value.code == "invocation_uncertain"
    assert models.calls == platform.settings.max_model_calls + 1
    # A late recorded response can settle once, without another provider call.
    worker.complete_invocation(call_id, {"summary": "late result"}, {}, "{}")
    worker.complete_invocation(call_id, {}, {}, "{}")
    with platform.db.transaction() as session:
        assert session.get(Invocation, call_id).status == "succeeded"
        project = session.get(Project, project_id)
        assert project.reserved_usd == 0 and project.spent_usd == held


def test_legacy_pending_failed_calls_are_reclassified_but_running_calls_remain_live(platform):
    project, _, task_id, worker = start_research(platform)
    attempt_id = worker.begin_attempt(task_id)
    call_id, _ = worker.reserve_invocation(attempt_id)
    maintenance = Maintenance(platform.db, platform.settings, platform.store, checkpoint_client=CheckpointClient())
    assert maintenance.reclassify_failed_invocations() == 0
    worker.block_task(task_id, attempt_id, "Legacy interrupted SDK call")
    assert maintenance.reclassify_failed_invocations() == 1
    assert maintenance.reclassify_failed_invocations() == 0
    with platform.db.transaction() as session:
        assert session.get(Invocation, call_id).status == "uncertain"
        assert session.get(Project, project).reserved_usd == platform.settings.invocation_reservation_usd
    response = platform.client.post(f"/api/v1/invocations/{call_id}/reconcile", json={
        "note": "Provider confirms no recoverable response; conservatively debit reservation"})
    assert response.status_code == 200
    assert platform.client.post(f"/api/v1/invocations/{call_id}/reconcile", json={
        "note": "Duplicate operator reconciliation must not debit twice"}).status_code == 200


@pytest.mark.asyncio
async def test_deletion_has_bounded_retries_and_records_exhaustion():
    calls, sleeps, failures = [], [], []
    async def purge(project):
        calls.append(project)
        if len(calls) < 3:
            raise ConnectionError("private connection details")
        return {"status": "purged"}
    async def sleep(delay):
        sleeps.append(delay)
    async def failure(*args):
        failures.append(args)
    assert await delete_with_retry("project", purge, sleep, failure) == {"status": "purged"}
    assert sleeps == [1, 2]
    assert failures == [("project", "ConnectionError", 1, False), ("project", "ConnectionError", 2, False)]
    async def unavailable(project):
        raise TimeoutError("private")
    failures.clear()
    assert await delete_with_retry("project", unavailable, sleep, failure) == {"status": "retry_pending"}
    assert len(failures) == 5 and failures[-1] == ("project", "TimeoutError", 5, True)


def test_failed_deletion_schedules_one_fresh_workflow_and_resumes_after_storage_recovers(platform, monkeypatch):
    project = create(platform)["projectId"]
    maintenance = Maintenance(platform.db, platform.settings, platform.store, checkpoint_client=CheckpointClient())
    maintenance.request_delete(platform.principal, project, platform.control.workspace(platform.principal, project)["revision"])
    maintenance.mark_deletion_started(project)
    def fail(_prefix):
        raise ConnectionError("storage unavailable")
    original = maintenance.purge_prefix
    monkeypatch.setattr(maintenance, "purge_prefix", fail)
    with pytest.raises(ConnectionError):
        maintenance.purge_project(project)
    maintenance.record_deletion_failure(project, "ConnectionError", 5, True)
    assert maintenance.schedule_deletion_retries() == 0
    assert maintenance.schedule_deletion_retries(at=now() + timedelta(minutes=6)) == 1
    assert maintenance.schedule_deletion_retries(at=now() + timedelta(minutes=6)) == 0
    with platform.db.transaction() as session:
        rows = session.scalars(select(Outbox).where(Outbox.kind == "delete_project")).all()
        assert len(rows) == 2 and rows[0].key != rows[1].key
        assert session.get(Project, project).state["deletion"]["status"] == "queued"
    monkeypatch.setattr(maintenance, "purge_prefix", original)
    assert maintenance.purge_project(project)["status"] == "purged"
    assert maintenance.schedule_deletion_retries(at=now() + timedelta(days=1)) == 0


def test_expired_deletion_lease_recovers_interrupted_worker(platform):
    project = create(platform)["projectId"]
    maintenance = Maintenance(platform.db, platform.settings, platform.store, checkpoint_client=CheckpointClient())
    maintenance.request_delete(platform.principal, project, platform.control.workspace(platform.principal, project)["revision"])
    maintenance.mark_deletion_started(project)
    assert maintenance.schedule_deletion_retries() == 0
    assert maintenance.schedule_deletion_retries(at=now() + timedelta(seconds=max(
        300, platform.settings.worker_timeout_seconds) + 1)) == 1


def test_uncertain_embeddings_release_only_live_capacity_not_financial_holds(platform):
    from kleos.learning import ComputeLedger
    platform.settings.max_model_calls = 1
    project, _, task, worker = start_research(platform)
    ledger = ComputeLedger(platform.db, platform.settings)
    first, _ = ledger.begin(platform.principal, project, "first", "embedding", {})
    ledger.uncertain(first)
    with pytest.raises(DomainError) as error:
        ledger.begin(platform.principal, project, "first", "embedding", {})
    assert error.value.code == "compute_uncertain"
    second, _ = ledger.begin(platform.principal, project, "second", "embedding", {})
    attempt = worker.begin_attempt(task)
    with pytest.raises(DomainError) as error:
        worker.reserve_invocation(attempt)
    assert error.value.code == "provider_capacity"
    ledger.uncertain(second)
    invocation, _ = worker.reserve_invocation(attempt)
    with platform.db.transaction() as session:
        assert session.get(Invocation, invocation).status == "pending"
        assert session.get(Project, project).reserved_usd == 3 * platform.settings.invocation_reservation_usd
    with pytest.raises(DomainError) as error:
        ledger.begin(platform.principal, project, "third", "embedding", {})
    assert error.value.code == "model_capacity"


def test_only_stale_delivered_legacy_deletions_receive_one_recovery_workflow(platform):
    project = create(platform)["projectId"]
    maintenance = Maintenance(platform.db, platform.settings, platform.store, checkpoint_client=CheckpointClient())
    revision = platform.control.workspace(platform.principal, project)["revision"]
    maintenance.request_delete(platform.principal, project, revision)
    # A stale request still awaiting delivery must not create duplicate work.
    later = now() + timedelta(hours=2)
    assert maintenance.schedule_deletion_retries(at=later) == 0
    with platform.db.transaction() as session:
        original = session.scalar(select(Outbox).where(Outbox.kind == "delete_project"))
        original.delivered_at = now()
        session.get(Project, project).deleted_at = now() - timedelta(hours=2)
    # Recently dispatched work gets the full grace period even when queued.
    assert maintenance.schedule_deletion_retries() == 0
    assert maintenance.schedule_deletion_retries(at=later) == 1
    with platform.db.transaction() as session:
        retry = session.scalar(select(Outbox).where(Outbox.key == f"delete-project:{project}:retry:1"))
        assert retry is not None
        retry.delivered_at = later
        deletion = session.get(Project, project).state["deletion"]
        assert deletion["status"] == "queued" and deletion["retryGeneration"] == 1
    # This legacy migration is one-shot; the newer workflow takes a lease when
    # it starts and uses ordinary failure/lease recovery thereafter.
    assert maintenance.schedule_deletion_retries(at=later + timedelta(hours=2)) == 0
