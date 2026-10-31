"""Settle completed model calls from priced usage, retaining unknown-cost holds."""
import math

from sqlalchemy import select

from .db import DailyBudget, Invocation
from .domain import emit, get_project, lock_platform


def priced_usage(usage):
    value = usage.get('estimatedUsd')
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def invocation_debit(reserved, usage):
    amount = priced_usage(usage)
    if amount is not None:
        return amount, 'priced_usage'
    search = usage.get('searchUsd')
    search = search if isinstance(search, (int, float)) and not isinstance(search, bool) and math.isfinite(search) else 0
    return max(reserved, search), 'conservative_reservation'


def reconcile_model_usage(db, project_id, *, apply=False):
    """Correct one project's completed calls once; never release uncertain calls."""
    with db.transaction() as session:
        lock_platform(session)
        project = get_project(session, project_id)
        calls = session.scalars(select(Invocation).where(
            Invocation.project_id == project_id, Invocation.status == 'succeeded')).all()
        changes = []
        for call in calls:
            usage = call.usage or {}
            amount = priced_usage(usage)
            old = usage.get('allowanceDebitUsd')
            if usage.get('costBasis') != 'conservative_reservation' or amount is None:
                continue
            if not isinstance(old, (int, float)) or isinstance(old, bool) or not math.isfinite(old) or old < amount:
                continue
            daily = session.get(DailyBudget, usage.get('reservationDay'))
            if daily is None:
                raise ValueError('Missing original daily ledger; reconcile manually')
            changes.append({'invocationId': call.id, 'previousDebitUsd': old, 'debitUsd': amount})
            if apply:
                delta = old - amount
                if project.spent_usd + 1e-8 < delta or daily.spent + 1e-8 < delta:
                    raise ValueError('Ledger totals disagree with recorded debits; reconcile manually')
                project.spent_usd = max(0, project.spent_usd - delta)
                daily.spent = max(0, daily.spent - delta)
                call.estimated_usd = amount
                call.usage = {**usage, 'costBasis': 'priced_usage', 'allowanceDebitUsd': amount,
                              'previousAllowanceDebitUsd': old}
                emit(session, project, 'model.usageReconciled', changes[-1])
        return {'projectId': project_id, 'applied': apply, 'calls': len(changes),
                'correctionUsd': sum(c['previousDebitUsd'] - c['debitUsd'] for c in changes)}
