import pytest
from sqlalchemy import select
from test_control_plane import start_research

from kleos.db import DailyBudget, Invocation, Project
from kleos.usage_accounting import invocation_debit, reconcile_model_usage


@pytest.mark.parametrize('estimate,expected,basis', [
    (.10, .10, 'priced_usage'), (0, 0, 'priced_usage'), (2, 2, 'priced_usage'),
    (None, 1, 'conservative_reservation'), (-1, 1, 'conservative_reservation'),
    (float('nan'), 1, 'conservative_reservation'), ('0.1', 1, 'conservative_reservation'),
])
def test_settlement_uses_valid_priced_usage(estimate, expected, basis):
    assert invocation_debit(1, {'estimatedUsd': estimate}) == (expected, basis)


def test_completion_releases_unused_reservation_and_is_idempotent(platform):
    project, _, task, worker = start_research(platform)
    call, _ = worker.reserve_invocation(worker.begin_attempt(task))
    worker.complete_invocation(call, {'summary': 'fixture'}, {'estimatedUsd': .1}, '{}')
    worker.complete_invocation(call, {}, {'estimatedUsd': .1}, '{}')
    with platform.db.transaction() as session:
        p = session.get(Project, project)
        daily = session.scalar(select(DailyBudget))
        invocation = session.get(Invocation, call)
        assert p.spent_usd == daily.spent == .1
        assert p.reserved_usd == daily.reserved == 0
        assert invocation.usage['costBasis'] == 'priced_usage'
        assert invocation.usage['allowanceDebitUsd'] == .1


def test_historical_reconciliation_is_previewable_and_idempotent(platform):
    project, _, task, worker = start_research(platform)
    call, _ = worker.reserve_invocation(worker.begin_attempt(task))
    worker.complete_invocation(call, {'summary': 'fixture'}, {}, '{}')
    with platform.db.transaction() as session:
        invocation = session.get(Invocation, call)
        invocation.usage = {**invocation.usage, 'estimatedUsd': .1}
        invocation.estimated_usd = .1
    preview = reconcile_model_usage(platform.db, project)
    assert preview['calls'] == 1 and preview['correctionUsd'] == .9
    with platform.db.transaction() as session:
        assert session.get(Project, project).spent_usd == 1
    reconcile_model_usage(platform.db, project, apply=True)
    assert reconcile_model_usage(platform.db, project, apply=True)['calls'] == 0
    with platform.db.transaction() as session:
        assert session.get(Project, project).spent_usd == pytest.approx(.1)
        assert session.scalar(select(DailyBudget)).spent == pytest.approx(.1)
        assert session.get(Invocation, call).usage['previousAllowanceDebitUsd'] == 1


def test_uncertain_calls_are_not_refunded(platform):
    project, _, task, worker = start_research(platform)
    call, _ = worker.reserve_invocation(worker.begin_attempt(task))
    with platform.db.transaction() as session:
        invocation = session.get(Invocation, call)
        invocation.status = 'uncertain'
        invocation.usage = {**invocation.usage, 'costBasis': 'conservative_reservation',
                            'estimatedUsd': .1, 'allowanceDebitUsd': 1}
    assert reconcile_model_usage(platform.db, project, apply=True)['calls'] == 0
    with platform.db.transaction() as session:
        assert session.get(Project, project).reserved_usd == 1
