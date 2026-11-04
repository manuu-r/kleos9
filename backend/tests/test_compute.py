"""Allowance conservation across clean stops, lost owners and human waits."""
import copy
import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from kleos.contracts.models import Actor, DomainError
from kleos.orchestration.engine import RunScope
from kleos.projects import compute

START = 1_800_000_000_000


def fixture(limit=120, lifetime_ms=7*86400_000):
    scope = RunScope(actor=Actor(tenant_id='tenant', user_id='owner'), project_id='project', run_id='run', revision=1,
        compute_budget_seconds=limit, deadline_at=datetime.fromtimestamp((START+lifetime_ms)/1000, UTC))
    return SimpleNamespace(data={'scope': scope.model_dump(mode='json')}), scope


def test_waiting_time_is_not_active_work_and_each_clean_dispatch_settles_once():
    run, scope = fixture()
    compute.begin(run, scope, 'first', now=START)
    compute.finish(run, scope, 'first', now=START+11000)
    assert run.data['activeBudget']['committedMs'] == 11000
    first = copy.deepcopy(run.data)
    compute.finish(run, scope, 'first', now=START+86400_000)
    assert run.data == first
    compute.begin(run, scope, 'second', now=START+86400_000)
    compute.finish(run, scope, 'second', now=START+86400_000+9000)
    assert run.data['activeBudget']['committedMs'] == 20000
    assert run.data['activeBudget']['lease'] is None


def test_renewal_reserves_only_bounded_contiguous_work_and_clean_finish_refunds_unused():
    run, scope = fixture()
    compute.begin(run, scope, 'owner', now=START)
    compute.renew(run, scope, 'owner', now=START+14000)
    assert run.data['activeBudget']['committedMs'] == 30000
    compute.renew(run, scope, 'owner', now=START+16000)
    assert run.data['activeBudget']['committedMs'] == 60000
    assert run.data['activeBudget']['lease']['untilMs'] == START+60000
    compute.renew(run, scope, 'owner', now=START+16000)
    assert run.data['activeBudget']['committedMs'] == 60000
    compute.finish(run, scope, 'owner', now=START+17000)
    assert run.data['activeBudget']['committedMs'] == 17000


def test_final_fraction_remains_usable_but_cannot_renew_past_allowance():
    run, scope = fixture(limit=35)
    compute.begin(run, scope, 'owner', now=START)
    compute.renew(run, scope, 'owner', now=START+20000)
    compute.renew(run, scope, 'owner', now=START+30000)
    assert run.data['activeBudget']['committedMs'] == 35000
    compute.assert_active(run, scope, 'owner', now=START+34999)
    with pytest.raises(DomainError) as error:
        compute.assert_active(run, scope, 'owner', now=START+35000)
    assert error.value.code == 'compute_budget_exhausted'
    compute.finish(run, scope, 'owner', now=START+36000)
    with pytest.raises(DomainError, match='exhausted'):
        compute.begin(run, scope, 'retry', now=START+36001)


def test_grant_is_also_bounded_by_original_lifetime():
    run, scope = fixture(limit=120, lifetime_ms=1500)
    compute.begin(run, scope, 'owner', now=START)
    assert run.data['activeBudget']['committedMs'] == 1500
    compute.finish(run, scope, 'owner', now=START+1000)
    assert run.data['activeBudget']['committedMs'] == 1000
    with pytest.raises(DomainError, match='exhausted'):
        compute.begin(run, scope, 'late', now=START+1500)


def test_restart_keeps_abandoned_reservation_and_fences_old_owner_refund():
    run, scope = fixture()
    compute.begin(run, scope, 'lost-worker', now=START)
    compute.begin(run, scope, 'replacement', now=START+10000)
    assert run.data['activeBudget']['committedMs'] == 60000
    previous = copy.deepcopy(run.data)
    compute.finish(run, scope, 'lost-worker', now=START+10500)
    assert run.data == previous
    with pytest.raises(DomainError) as error:
        compute.assert_active(run, scope, 'lost-worker', now=START+10500)
    assert error.value.code == 'worker_fenced'
    compute.finish(run, scope, 'replacement', now=START+11000)
    assert run.data['activeBudget']['committedMs'] == 31000


def test_duplicate_begin_does_not_reset_or_reserve_another_grant():
    run, scope = fixture()
    compute.begin(run, scope, 'same', now=START)
    compute.begin(run, scope, 'same', now=START+20000)
    assert run.data['activeBudget']['committedMs'] == 30000
    assert run.data['activeBudget']['lease']['untilMs'] == START+30000
    with pytest.raises(DomainError, match='expired'):
        compute.begin(run, scope, 'same', now=START+30000)


def test_rollback_within_lease_cannot_refund_already_observed_work():
    run, scope = fixture()
    compute.begin(run, scope, 'owner', now=START)
    compute.renew(run, scope, 'owner', now=START+16000)
    compute.assert_active(run, scope, 'owner', now=START+20000)
    with pytest.raises(DomainError) as error:
        compute.assert_active(run, scope, 'owner', now=START+19000)
    assert error.value.code == 'compute_clock_reversed'
    compute.finish(run, scope, 'owner', now=START+19000)
    assert run.data['activeBudget']['committedMs'] == 60000


def test_sub_tick_dispatch_charges_one_millisecond():
    run, scope = fixture()
    compute.begin(run, scope, 'owner', now=START)
    compute.finish(run, scope, 'owner', now=START)
    assert run.data['activeBudget']['committedMs'] == 1


@pytest.mark.parametrize('change', ['disabled', 'larger', 'smaller', 'deadline'])
def test_persisted_scope_cannot_be_replaced_to_reset_allowance(change):
    run, scope = fixture()
    compute.begin(run, scope, 'owner', now=START)
    modified = scope.model_copy(update={'compute_budget_seconds': {'disabled': None, 'larger': 240, 'smaller': 60}.get(change, 120),
        'deadline_at': scope.deadline_at+timedelta(days=1) if change == 'deadline' else scope.deadline_at})
    before = copy.deepcopy(run.data)
    for action in (compute.begin, compute.assert_active, compute.renew, compute.finish):
        with pytest.raises(DomainError) as error:
            action(run, modified, 'owner', now=START+100)
        assert error.value.code == 'compute_budget_conflict'
    assert run.data == before


def test_historical_scope_without_budget_stays_deadline_only():
    run, scope = fixture(limit=None)
    run.data['scope'].pop('compute_budget_seconds')
    for action in (compute.begin, compute.assert_active, compute.renew, compute.finish):
        action(run, scope, 'owner', now=START)
    assert 'activeBudget' not in run.data
    with pytest.raises(DomainError):
        compute.begin(run, scope.model_copy(update={'compute_budget_seconds': 120}), 'owner', now=START)
    run.data['activeBudget'] = {'committedMs': 30000}
    with pytest.raises(DomainError):
        compute.assert_active(run, scope, 'owner', now=START)


@pytest.mark.parametrize('field,value', [('committedMs', -1), ('committedMs', True), ('limitMs', 999999),
    ('deadlineMs', START), ('lease', {'owner': 'x'}), ('lease', 'invalid')])
def test_corrupt_budget_fails_closed_without_mutating_records(field, value):
    run, scope = fixture()
    compute.begin(run, scope, 'owner', now=START)
    run.data['activeBudget'][field] = value
    before = copy.deepcopy(run.data)
    with pytest.raises(DomainError) as error:
        compute.finish(run, scope, 'owner', now=START+1000)
    assert error.value.code == 'compute_budget_conflict' and run.data == before


@pytest.mark.parametrize('field,value', [('untilMs', START), ('reservedMs', 120001), ('observedAtMs', START-1),
    ('startedAtMs', True), ('owner', None)])
def test_corrupt_grant_cannot_create_time_credit(field, value):
    run, scope = fixture()
    compute.begin(run, scope, 'owner', now=START)
    run.data['activeBudget']['lease'][field] = value
    with pytest.raises(DomainError) as error:
        compute.finish(run, scope, 'owner', now=START)
    assert error.value.code == 'compute_budget_conflict'


def test_many_clean_dispatches_conserve_cumulative_active_time():
    run, scope = fixture(limit=7200, lifetime_ms=30*86400_000)
    clock, expected = START, 0
    randomizer = random.Random(41)
    for index in range(100):
        duration = randomizer.randint(1, 27000)
        compute.begin(run, scope, str(index), now=clock)
        compute.renew(run, scope, str(index), now=clock+duration//2)
        compute.finish(run, scope, str(index), now=clock+duration)
        expected += duration
        assert run.data['activeBudget']['committedMs'] == expected
        clock += duration + randomizer.randint(0, 6*3600_000)
