"""Durable isolated MCP dispatch identities and cleanup evidence."""
import sqlalchemy as sa
from alembic import op

revision = '0007_mcp_sessions'
down_revision = '0006_repository_imports'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('kleos2_mcp_sessions',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('run_id', sa.String(36), nullable=False),
        sa.Column('actor_id', sa.String(100), nullable=False),
        sa.Column('tenant_id', sa.String(100), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('request', sa.JSON(), nullable=False),
        sa.Column('container_id', sa.String(64), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('finished_at', sa.Float(), nullable=True),
        sa.Column('termination', sa.JSON(), nullable=False))
    for column in ('project_id', 'run_id', 'status'):
        op.create_index(f'ix_kleos2_mcp_sessions_{column}', 'kleos2_mcp_sessions', [column])


def downgrade():
    for column in ('status', 'run_id', 'project_id'):
        op.drop_index(f'ix_kleos2_mcp_sessions_{column}', table_name='kleos2_mcp_sessions')
    op.drop_table('kleos2_mcp_sessions')
