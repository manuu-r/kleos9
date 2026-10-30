"""Move lane choices for providers the registry no longer offers to the Muse models.

Revision ID: d8e2f4a61c37
Revises: c7a1d3e94b52
"""
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "d8e2f4a61c37"
down_revision = "c7a1d3e94b52"
branch_labels = None
depends_on = None

CODE_LANES = {"firmware", "application"}


def muse_policies(connection, spark_model, wonder_model):
    metadata = sa.MetaData()
    projects = sa.Table("projects", metadata, autoload_with=connection)
    events = sa.Table("events", metadata, autoload_with=connection)
    rows = connection.execute(sa.select(
        projects.c.id, projects.c.tenant_id, projects.c.model_policy, projects.c.policy_revision,
        projects.c.revision, projects.c.event_sequence).where(
        projects.c.deleted_at.is_(None)).with_for_update()).mappings().all()
    for row in rows:
        policy = row["model_policy"] or {}
        changed = [lane for lane, choice in policy.items() if choice.get("provider") not in {"meta", "modal", "google"}]
        if not changed:
            continue
        policy = {**policy, **{lane: {"provider": "meta", "model": wonder_model if lane in CODE_LANES else spark_model}
                               for lane in changed}}
        policy_revision = row["policy_revision"] + 1
        sequence = row["event_sequence"] + 1
        connection.execute(projects.update().where(projects.c.id == row["id"]).values(
            model_policy=policy, policy_revision=policy_revision,
            revision=row["revision"] + 1, event_sequence=sequence))
        connection.execute(events.insert().values(
            id=str(uuid4()), created_at=datetime.now(UTC), tenant_id=row["tenant_id"],
            project_id=row["id"], sequence=sequence, type="modelPolicy.changed",
            data={"reason": "provider_catalog_updated", "policyRevision": policy_revision,
                  "appliesTo": "next_attempt", "changedLanes": changed, "modelPolicy": policy},
            trace_id=None))


def upgrade():
    from kleos.config import get_settings

    settings = get_settings()
    muse_policies(op.get_bind(), settings.meta_model, settings.meta_wonder_model)


def downgrade():
    # Retired provider choices are intentionally not restored on schema rollback.
    pass
