"""External observation correlation for LangGraph interruptions."""
import sqlalchemy as sa
from alembic import op

revision = '0005_external_waits'
down_revision = '0004_capacity_verification'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('kleos2_external_waits',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('run_id', sa.String(36), nullable=False),
        sa.Column('task_id', sa.String(100), nullable=False),
        sa.Column('job_id', sa.String(36), nullable=False),
        sa.Column('kind', sa.String(30), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('deadline_at', sa.Float(), nullable=False),
        sa.Column('next_poll_at', sa.Float(), nullable=False),
        sa.Column('last_sequence', sa.Integer(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.UniqueConstraint('run_id', 'task_id', 'kind', 'job_id'))
    for column in ('project_id', 'run_id', 'task_id', 'job_id', 'status', 'next_poll_at'):
        op.create_index('ix_kleos2_external_waits_' + column, 'kleos2_external_waits', [column])
    op.create_table('kleos2_external_wait_events',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('wait_id', sa.String(36), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False))
    op.create_index('ix_kleos2_external_wait_events_wait_id', 'kleos2_external_wait_events', ['wait_id'])


def downgrade():
    op.drop_index('ix_kleos2_external_wait_events_wait_id', table_name='kleos2_external_wait_events')
    op.drop_table('kleos2_external_wait_events')
    for column in ('project_id', 'run_id', 'task_id', 'job_id', 'status', 'next_poll_at'):
        op.drop_index('ix_kleos2_external_waits_' + column, table_name='kleos2_external_waits')
    op.drop_table('kleos2_external_waits')
