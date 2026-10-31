import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_control_plane import command, create, engine, start_research
from test_execution import execution as execution  # noqa: F401
from test_native_retry import rebuild as rebuild  # noqa: F401
from test_native_retry import start as start_rebuild
from test_safety_and_recovery import native_job

from kleos.config import Settings
from kleos.contracts import DomainError, JobResult
from kleos.db import Attempt, DailyBudget, Invocation, Outbox, Project, Task
from kleos.learning import ComputeLedger


def test_unlimited_usage_is_explicit_and_cannot_enter_cloud_environments():
    assert not Settings(_env_file=None).development_unlimited_usage
    assert Settings(_env_file=None, environment="development", development_unlimited_usage=True).development_unlimited_usage
    for environment in ("staging", "production"):
        with pytest.raises(ValidationError, match="restricted to development/test"):
            Settings(_env_file=None, environment=environment, development_unlimited_usage=True)


def enable(platform):
    platform.settings.development_unlimited_usage = True
    platform.settings.project_budget_usd = platform.settings.daily_budget_usd = 0


def test_unlimited_hosted_usage_preserves_costs_and_exposes_no_fictitious_balance(platform):
    enable(platform)
    project, _, task, worker = start_research(platform)
    attempt = worker.begin_attempt(task)
    assert worker.attempt_input(attempt)[0]["remainingAllowanceUsd"] is None
    invocation, _ = worker.reserve_invocation(attempt)
    worker.complete_invocation(invocation, {"summary": "result"}, {"estimatedUsd": 2.5}, "{}")
    view = platform.control.workspace(platform.principal, project)["project"]["budget"]
    assert view == {"allowanceUsd": None, "unlimited": True, "spentEstimateUsd": 2.5, "reservedUsd": 0}
    assert platform.client.get(f"/api/v1/projects/{project}/workspace").json()["project"]["budget"] == view
    with platform.db.transaction() as session:
        assert session.scalar(select(DailyBudget)).spent == 2.5
        assert session.get(Project, project).budget_usd == 0
        assert session.get(Invocation, invocation).status == "succeeded"


def test_unlimited_project_admission_keeps_inference_concurrency(platform):
    enable(platform)
    platform.settings.max_active_projects = platform.settings.max_org_projects = platform.settings.max_model_calls = 1
    worker = engine(platform)
    tasks = []
    for number in range(3):
        project = create(platform, f"Concurrent dev project {number}")["projectId"]
        receipt = command(platform, project, "project.research", key=f"dev-research-{number}")
        admitted = worker.admit_run(receipt["runId"])
        assert admitted
        tasks.append(admitted[0])
    worker.reserve_invocation(worker.begin_attempt(tasks[0]))
    with pytest.raises(DomainError) as error:
        worker.reserve_invocation(worker.begin_attempt(tasks[1]))
    assert error.value.code == "provider_capacity"


def test_manual_engineering_turns_are_unlimited_but_each_automatic_chain_is_finite(platform):
    enable(platform)
    project = create(platform)["projectId"]
    with platform.db.transaction() as session:
        p = session.get(Project, project)
        p.state = {**p.state, "confirmed": True}
        p.interfaces = {"mechanical": {"units": "mm"}}
    worker = engine(platform)
    for _ in range(6):
        receipt = command(platform, project, "project.generate", {"lane": "firmware"})
        task = worker.admit_run(receipt["runId"])[0]
        for _ in range(3):
            attempt = worker.begin_attempt(task)
            assert attempt
            with platform.db.transaction() as session:
                session.get(Attempt, attempt).status = "failed"
        assert worker.begin_attempt(task) is None
        worker.finish_run(receipt["runId"])


def test_unlimited_compute_admission_retains_replay_and_cost_guards(platform):
    enable(platform)
    project = create(platform)["projectId"]
    ledger = ComputeLedger(platform.db, platform.settings)
    operation, _ = ledger.begin(platform.principal, project, "embedding", "embedding", {})
    ledger.uncertain(operation)
    with pytest.raises(DomainError) as error:
        ledger.begin(platform.principal, project, "embedding", "embedding", {})
    assert error.value.code == "compute_uncertain"
    ledger.begin(platform.principal, project, "another", "embedding", {})
    with platform.db.transaction() as session:
        assert session.get(Project, project).reserved_usd == 2 * platform.settings.invocation_reservation_usd
        assert session.scalar(select(DailyBudget)).reserved == 2 * platform.settings.invocation_reservation_usd


def test_unlimited_native_work_still_records_reservations(execution):  # noqa: F811
    execution.settings.development_unlimited_usage = True
    execution.settings.daily_budget_usd = 0
    with execution.db.transaction() as session:
        session.get(Project, "project").budget_usd = 0
    job = execution.submit("attempt", "build")
    assert job
    with execution.db.transaction() as session:
        assert session.get(Project, "project").reserved_usd == execution.settings.native_job_reservation_usd


async def test_unlimited_manual_native_rebuilds_do_not_trigger_automatic_inference(rebuild):  # noqa: F811
    execution, worker, _, _, _, _, _ = rebuild
    execution.settings.development_unlimited_usage = True
    execution.settings.daily_budget_usd = 0
    with execution.db.transaction() as session:
        session.get(Project, "project").budget_usd = 0
    for number in range(5):
        _, _, _, _, job = await start_rebuild(rebuild, f"unlimited-rebuild-{number}")
        worker.finish_native(job.id, JobResult(status="failed", measurements={"failureKind": "engineering"},
                                              limitations=["Explicit native failure fixture"]))
    with execution.db.transaction() as session:
        assert not session.scalars(select(Invocation)).all()
        assert not session.scalars(select(Outbox).where(Outbox.key.like("repair:%"))).all()


def test_zero_budget_does_not_suppress_the_bounded_automatic_repair_path(platform):
    enable(platform)
    _, task, _, worker = native_job(platform)
    worker.finish_native("native-fixture", JobResult(status="failed", measurements={"failureKind": "engineering"},
                                                    limitations=["Compiler fixture"]))
    with platform.db.transaction() as session:
        assert session.get(Task, task).status == "queued"
        assert session.scalar(select(Outbox).where(Outbox.key.like("repair:%")))


def test_a_fallback_answer_is_recorded_against_the_model_that_gave_it(platform):
    enable(platform)
    _, _, task, worker = start_research(platform)
    attempt = worker.begin_attempt(task)
    invocation, _ = worker.reserve_invocation(attempt)
    fallback = {"provider": "meta", "model": "muse-spark-1.3",
                "fallbackFrom": {"provider": "modal", "model": "open", "reason": "rate_limited"}}
    worker.complete_invocation(invocation, {"summary": "result"}, fallback, "{}")
    with platform.db.transaction() as session:
        for row in (session.get(Invocation, invocation), session.get(Attempt, attempt)):
            assert (row.provider, row.model) == ("meta", "muse-spark-1.3")
