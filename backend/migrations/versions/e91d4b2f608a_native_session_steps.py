"""Persist bounded native engineering session tool steps.

Revision ID: e91d4b2f608a
Revises: d8e2f4a61c37
"""
import sqlalchemy as sa
from alembic import op

revision = "e91d4b2f608a"
down_revision = "d8e2f4a61c37"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "native_session_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("native_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("job_id", "request_key"),
        sa.UniqueConstraint("job_id", "sequence"),
    )
    for field in ("job_id", "attempt_id", "tenant_id"):
        op.create_index(f"ix_native_session_steps_{field}", "native_session_steps", [field])


def downgrade():
    op.drop_table("native_session_steps")
