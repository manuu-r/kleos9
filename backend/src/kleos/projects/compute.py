"""Cumulative active-work reservations; waiting checkpoints consume no worker time.

This is an allowance ledger on the authoritative objective, not a workflow engine.
A crash retains the outstanding small grant. Only a cleanly stopped owner may
return its unused time. Callers hold the project row lock for every mutation.
"""
from datetime import UTC, datetime

from kleos.contracts.models import DomainError

QUANTUM_MS = 30_000


def now_ms():
    return int(datetime.now(UTC).timestamp() * 1000)


def _conflict():
    raise DomainError('Active-work allowance changed or is invalid', 'compute_budget_conflict')


def _deadline(value):
    try:
        date = datetime.fromisoformat(value) if isinstance(value, str) else value
        if date.tzinfo is None:
            _conflict()
        return int(date.timestamp() * 1000)
    except (AttributeError, TypeError, ValueError, OverflowError):
        _conflict()


def _budget(run, scope):
    limit = scope.compute_budget_seconds
    deadline = _deadline(scope.deadline_at)
    pinned = run.data.get('scope')
    if pinned is not None:
        if not isinstance(pinned, dict):
            _conflict()
        pinned_limit = pinned.get('compute_budget_seconds', pinned.get('computeBudgetSeconds'))
        if pinned_limit != limit or _deadline(pinned.get('deadline_at', pinned.get('deadlineAt'))) != deadline:
            _conflict()
    raw = run.data.get('activeBudget')
    if limit is None:
        # Historical scopes stay deadline-only. A budgeted scope cannot opt out.
        if raw is not None:
            _conflict()
        return None
    if type(limit) is not int or not 1 <= limit <= 7200:
        _conflict()
    if raw is None:
        return {'limitMs': limit * 1000, 'committedMs': 0, 'deadlineMs': deadline, 'lease': None}
    if not isinstance(raw, dict):
        _conflict()
    budget = dict(raw)
    if (type(budget.get('limitMs')) is not int or budget['limitMs'] != limit * 1000
            or type(budget.get('committedMs')) is not int or not 0 <= budget['committedMs'] <= budget['limitMs']
            or budget.get('deadlineMs') != deadline):
        _conflict()
    lease = budget.get('lease')
    if lease is not None:
        if (not isinstance(lease, dict) or not isinstance(lease.get('owner'), str) or not 1 <= len(lease['owner']) <= 128
                or any(type(lease.get(key)) is not int for key in
                    ('startedAtMs', 'untilMs', 'reservedMs', 'observedAtMs'))
                or not 0 <= lease['startedAtMs'] <= lease['observedAtMs'] < lease['untilMs'] <= deadline
                or lease['untilMs'] - lease['startedAtMs'] != lease['reservedMs']
                or not 1 <= lease['reservedMs'] <= budget['committedMs']):
            _conflict()
        budget['lease'] = dict(lease)
    return budget


def _time(now):
    value = now_ms() if now is None else now
    if type(value) is not int or value < 0:
        _conflict()
    return value


def _observe(budget, owner, now):
    lease = budget.get('lease')
    if not lease or lease['owner'] != owner:
        raise DomainError('Active-work grant belongs to another worker', 'worker_fenced')
    if now < lease['observedAtMs']:
        raise DomainError('Active-work clock moved backwards', 'compute_clock_reversed')
    if now >= lease['untilMs']:
        raise DomainError('Active-work grant expired', 'compute_budget_exhausted')
    lease['observedAtMs'] = now
    return lease


def begin(run, scope, owner, *, now=None):
    budget = _budget(run, scope)
    if budget is None:
        return
    now = _time(now)
    if not isinstance(owner, str) or not 1 <= len(owner) <= 128:
        _conflict()
    lease = budget.get('lease')
    if lease and lease['owner'] == owner:
        _observe(budget, owner, now)
        run.data = {**run.data, 'activeBudget': budget}
        return
    remaining = min(budget['limitMs'] - budget['committedMs'], budget['deadlineMs'] - now)
    if remaining <= 0:
        raise DomainError('Active-work allowance or objective lifetime exhausted', 'compute_budget_exhausted')
    grant = min(QUANTUM_MS, remaining)
    # Do not refund an abandoned prior owner: its last grant may have been used.
    budget.update(committedMs=budget['committedMs'] + grant,
                  lease={'owner': owner, 'startedAtMs': now, 'untilMs': now + grant,
                         'reservedMs': grant, 'observedAtMs': now})
    run.data = {**run.data, 'activeBudget': budget}


def assert_active(run, scope, owner, *, now=None):
    budget = _budget(run, scope)
    if budget is None:
        return
    _observe(budget, owner, _time(now))
    run.data = {**run.data, 'activeBudget': budget}


def renew(run, scope, owner, *, now=None):
    budget = _budget(run, scope)
    if budget is None:
        return
    now = _time(now)
    lease = _observe(budget, owner, now)
    if lease['untilMs'] - now > QUANTUM_MS // 2:
        run.data = {**run.data, 'activeBudget': budget}
        return
    grant = min(QUANTUM_MS, budget['limitMs'] - budget['committedMs'],
                budget['deadlineMs'] - lease['untilMs'])
    if grant <= 0:
        run.data = {**run.data, 'activeBudget': budget}
        return  # The last reserved fraction remains usable until its exact expiry.
    lease.update(untilMs=lease['untilMs'] + grant, reservedMs=lease['reservedMs'] + grant)
    budget.update(committedMs=budget['committedMs'] + grant, lease=lease)
    run.data = {**run.data, 'activeBudget': budget}


def finish(run, scope, owner, *, now=None):
    budget = _budget(run, scope)
    if budget is None:
        return
    now = _time(now)
    lease = budget.get('lease')
    if not lease or lease.get('owner') != owner:
        return
    # A backwards clock cannot manufacture a refund. At least one millisecond
    # is charged for a completed dispatch even when it finishes within one tick.
    unused = max(0, lease['untilMs'] - max(now, lease['startedAtMs'] + 1)) if now >= lease['observedAtMs'] else 0
    budget.update(committedMs=budget['committedMs'] - min(unused, lease['reservedMs']), lease=None)
    run.data = {**run.data, 'activeBudget': budget}
