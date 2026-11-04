"""Retain frontend draft fields separately from immutable external effect inputs."""

import sqlalchemy as sa
from alembic import op

revision = "0002_external_presentation"
down_revision = "0001_modular_backend"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "kleos2_external_jobs", sa.Column("presentation", sa.JSON(), nullable=False, server_default="{}")
    )


def downgrade():
    op.drop_column("kleos2_external_jobs", "presentation")
