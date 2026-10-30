import pytest
from pydantic import ValidationError
from test_control_plane import start_research
from test_execution import execution  # noqa: F401
from test_memory_learning import learning_env  # noqa: F401

from kleos.config import Settings
from kleos.contracts import DomainError
from kleos.db import DailyBudget, now
from kleos.learning import ComputeLedger
from kleos.operations import report


def seed_spend(db):
    with db.transaction() as session:
        session.add(DailyBudget(day=now().date().isoformat(), spent=1000, reserved=0))


def assert_tracked(db):
    with db.transaction() as session:
        budget = session.get(DailyBudget, now().date().isoformat())
        assert budget.spent == 1000 and budget.reserved == 1


def test_explicit_unlimited_dotenv_preserves_zero_and_invalid_value_semantics(tmp_path):
    env = tmp_path / '.env'
    env.write_text('KLEOS_DAILY_BUDGET_USD=unlimited\n')
    assert Settings(_env_file=env).daily_budget_usd is None
    assert Settings(_env_file=env, daily_budget_usd=0).daily_budget_usd == 0
    for value in (-1, 'nan', 'infinity', 'typo'):
        with pytest.raises(ValidationError):
            Settings(_env_file=env, daily_budget_usd=value)


@pytest.mark.parametrize('unlimited', [False, True])
def test_model_daily_ceiling_can_be_removed_without_erasing_usage(platform, unlimited):
    _, _, task_id, worker = start_research(platform)
    platform.settings.daily_budget_usd = None if unlimited else 40
    seed_spend(platform.db)
    attempt_id = worker.begin_attempt(task_id)
    if unlimited:
        worker.reserve_invocation(attempt_id)
        assert_tracked(platform.db)
    else:
        with pytest.raises(DomainError, match='Daily allowance exhausted'):
            worker.reserve_invocation(attempt_id)


@pytest.mark.parametrize('unlimited', [False, True])
def test_native_daily_ceiling_can_be_removed_without_erasing_usage(execution, unlimited):  # noqa: F811
    execution.settings.daily_budget_usd = None if unlimited else 40
    seed_spend(execution.db)
    if unlimited:
        execution.submit('attempt', 'build')
        assert_tracked(execution.db)
    else:
        with pytest.raises(DomainError, match='Daily native compute allowance exhausted'):
            execution.submit('attempt', 'build')


@pytest.mark.parametrize('kind', ['embedding', 'modal', 'training'])
@pytest.mark.parametrize('unlimited', [False, True])
def test_compute_daily_ceiling_can_be_removed_without_erasing_usage(learning_env, kind, unlimited):  # noqa: F811
    db, settings, _, user = learning_env
    settings.daily_budget_usd = None if unlimited else 40
    seed_spend(db)
    ledger = ComputeLedger(db, settings)
    if unlimited:
        ledger.begin(user, 'project-1', 'daily-limit-fixture', kind, {})
        assert_tracked(db)
    else:
        with pytest.raises(DomainError, match='Compute allowance exhausted'):
            ledger.begin(user, 'project-1', 'daily-limit-fixture', kind, {})


def test_uncapped_daily_metrics_keep_usage_without_false_zero_remaining(platform, monkeypatch):
    platform.settings.daily_budget_usd = None
    seed_spend(platform.db)
    values = {}
    monkeypatch.setattr('kleos.operations.metric', lambda name, value, *args, **kwargs: values.update({name: value}))
    report(platform.db, platform.settings)
    assert values['DailyAllowanceUsedUsd'] == 1000
    assert values['DailyAllowanceLimited'] == 0
    assert 'DailyAllowanceRemainingUsd' not in values
