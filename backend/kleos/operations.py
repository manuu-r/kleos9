"""Low-cardinality operational gauges derived from authoritative ledgers."""
from datetime import UTC

from sqlalchemy import func, select

from .db import Attempt, DailyBudget, Invocation, NativeJob, Outbox, Project, Run, Source, Validation, now
from .telemetry import metric


def report(db, settings):
    with db.transaction(read_only=True) as session:
        oldest = session.scalar(select(func.min(Run.created_at)).where(Run.status == "queued"))
        age = max(0, (now()-oldest.replace(tzinfo=UTC)).total_seconds()) if oldest else 0
        values = {
            "QueueAgeSeconds": age,
            "OutboxPending": session.scalar(select(func.count(Outbox.id)).where(Outbox.delivered_at.is_(None))),
            "OutboxFailures": session.scalar(select(func.coalesce(func.sum(Outbox.failures), 0))),
            "PendingModelCalls": session.scalar(select(func.count(Invocation.id)).where(Invocation.status == "pending")),
            "UncertainModelCalls": session.scalar(select(func.count(Invocation.id)).where(Invocation.status == "uncertain")),
            "BlockedRuns": session.scalar(select(func.count(Run.id)).where(Run.status == "blocked")),
            "RepairAttempts": session.scalar(select(func.count(Attempt.id)).where(Attempt.number > 1)),
            "IndexBacklog": session.scalar(select(func.count(Source.id)).join(Project, Project.id == Source.project_id).where(
                Source.indexed_at.is_(None), Source.revoked.is_(False), Project.deleted_at.is_(None),
                Source.revision == Project.engineering_revision)),
            "ActiveNativeJobs": session.scalar(select(func.count(NativeJob.id)).where(
                NativeJob.status.in_(["dispatching", "submitted", "running"]))),
        }
        checks = session.execute(select(Validation.status, func.count(Validation.id)).join(
            Project, Project.id == Validation.project_id).where(Project.deleted_at.is_(None),
                Validation.revision == Project.engineering_revision).group_by(Validation.status)).all()
        coverage = dict(checks)
        values["UnknownChecks"] = coverage.get("UNKNOWN", 0)
        values["FailedChecks"] = coverage.get("FAIL", 0) + coverage.get("ERROR", 0)
        values["ValidationPassPercent"] = 100*coverage.get("PASS", 0)/max(1, sum(coverage.values()))
        budget = session.get(DailyBudget, now().date().isoformat())
        values["DailyAllowanceUsedUsd"] = budget.spent+budget.reserved if budget else 0
        values["DailyAllowanceLimited"] = int(not settings.development_unlimited_usage and settings.daily_budget_usd is not None)
        if not settings.development_unlimited_usage and settings.daily_budget_usd is not None:
            values["DailyAllowanceRemainingUsd"] = max(0, settings.daily_budget_usd-values["DailyAllowanceUsedUsd"])
    for name, value in values.items():
        metric(name, value, "Seconds" if name.endswith("Seconds") else "Percent" if name.endswith("Percent") else "Count",
               Environment=settings.environment)
