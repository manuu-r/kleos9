"""Durable bounded repository download and source-publication receipts."""
import sqlalchemy as sa
from alembic import op

revision = '0006_repository_imports'
down_revision = '0005_external_waits'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('kleos2_repository_imports',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(100), nullable=False),
        sa.Column('user_id', sa.String(100), nullable=False),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('idempotency_key', sa.String(128), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.UniqueConstraint('tenant_id', 'user_id', 'idempotency_key'))
    op.create_index('ix_kleos2_repository_imports_project_id', 'kleos2_repository_imports', ['project_id'])


def downgrade():
    op.drop_index('ix_kleos2_repository_imports_project_id', table_name='kleos2_repository_imports')
    op.drop_table('kleos2_repository_imports')
