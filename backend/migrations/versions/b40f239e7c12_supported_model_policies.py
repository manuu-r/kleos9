"""Normalize unsupported model choices to the configured Gemini model.

Revision ID: b40f239e7c12
Revises: 5f2e8ff0368e
"""
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "b40f239e7c12"
down_revision = "5f2e8ff0368e"
branch_labels = None
depends_on = None


def normalize_policies(connection, gemini_model):
    metadata = sa.MetaData()
    projects = sa.Table("projects", metadata, autoload_with=connection)
    events = sa.Table("events", metadata, autoload_with=connection)
    rows = connection.execute(sa.select(
        projects.c.id, projects.c.tenant_id, projects.c.model_policy, projects.c.policy_revision,
        projects.c.revision, projects.c.event_sequence).where(
        projects.c.deleted_at.is_(None)).with_for_update()).mappings().all()
    for row in rows:
        policy = row["model_policy"] or {}
        changed = [lane for lane, choice in policy.items()
                   if choice.get("provider") not in {"meta", "google"}]
        if not changed:
            continue
        policy = {**policy, **{lane: {"provider": "google", "model": gemini_model} for lane in changed}}
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
    # Gemini was removed from the settings later; this historical migration pins its last default.
    normalize_policies(op.get_bind(), "gemini-3.8-flash")


def downgrade():
    # Removed provider choices are intentionally not restored on schema rollback.
    pass
