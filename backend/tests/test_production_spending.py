import pytest
from sqlalchemy import select
from test_control_plane import command, create, engine, start_research
from test_execution import execution  # noqa: F401

from kleos.config import Settings
from kleos.db import DailyBudget, Invocation, Project
from kleos.learning import ComputeLedger
from kleos.operations import report


def test_production_can_disable_only_spending_caps():
    settings = Settings(_env_file=None, environment='production', storage_backend='s3', spending_limits_enabled=False)
    assert settings.unlimited_spending
    assert not settings.development_unlimited_usage
    assert Settings(_env_file=None).spending_limits_enabled


def test_uncapped_usage_and_metrics(platform, monkeypatch):
    platform.settings.spending_limits_enabled = False
    platform.settings.project_budget_usd = platform.settings.daily_budget_usd = 0
    project, _, task, worker = start_research(platform)
    attempt = worker.begin_attempt(task)
    assert worker.attempt_input(attempt)[0]['remainingAllowanceUsd'] is None
    invocation, _ = worker.reserve_invocation(attempt)
    worker.complete_invocation(invocation, {'summary': 'fixture'}, {'estimatedUsd': .05}, '{}')
    budget = platform.control.workspace(platform.principal, project)['project']['budget']
    assert budget['unlimited'] and budget['allowanceUsd'] is None
    with platform.db.transaction() as session:
        assert session.get(Invocation, invocation).estimated_usd == .05
        assert session.get(Project, project).spent_usd == 1
        assert session.scalar(select(DailyBudget)).spent == 1
    metrics = {}
    monkeypatch.setattr('kleos.operations.metric', lambda name, value, *a, **kw: metrics.update({name: value}))
    report(platform.db, platform.settings)
    assert metrics['DailyAllowanceLimited'] == 0
    assert 'DailyAllowanceRemainingUsd' not in metrics


def test_uncapped_spending_keeps_project_concurrency(platform):
    platform.settings.spending_limits_enabled = False
    platform.settings.max_active_projects = platform.settings.max_org_projects = 1
    worker = engine(platform)
    for number in range(2):
        project = create(platform, f'Project {number}')['projectId']
        run = command(platform, project, 'project.research', key=f'research-{number}')['runId']
        assert bool(worker.admit_run(run)) == (number == 0)


@pytest.mark.parametrize('kind', ['embedding', 'modal', 'training'])
def test_uncapped_compute_records_reservations(platform, kind):
    platform.settings.spending_limits_enabled = False
    platform.settings.project_budget_usd = platform.settings.daily_budget_usd = 0
    project = create(platform)['projectId']
    ComputeLedger(platform.db, platform.settings).begin(platform.principal, project, 'fixture', kind, {})
    with platform.db.transaction() as session:
        assert session.get(Project, project).reserved_usd == 1
        assert session.scalar(select(DailyBudget)).reserved == 1


def test_uncapped_native_work_records_reservations(execution):  # noqa: F811
    execution.settings.spending_limits_enabled = False
    execution.settings.daily_budget_usd = 0
    with execution.db.transaction() as session:
        session.get(Project, 'project').budget_usd = 0
    assert execution.submit('attempt', 'build')
    with execution.db.transaction() as session:
        assert session.get(Project, 'project').reserved_usd == execution.settings.native_job_reservation_usd
