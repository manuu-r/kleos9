"""Shared compute reservations and independent post-run verification jobs."""
import sqlalchemy as sa
from alembic import op

revision = '0004_capacity_verification'
down_revision = '0003_print_preparation'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('kleos2_capacity_pools',
        sa.Column('id', sa.String(100), primary_key=True),
        sa.Column('limits', sa.JSON(), nullable=False))
    op.create_table('kleos2_capacity_reservations',
        sa.Column('key', sa.String(160), primary_key=True),
        sa.Column('pool_id', sa.String(100), nullable=False),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('resources', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('reserved_at', sa.Float(), nullable=False),
        sa.Column('released_at', sa.Float(), nullable=True),
        sa.Column('proof', sa.JSON(), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False))
    for column in ('pool_id', 'project_id', 'status'):
        op.create_index('ix_kleos2_capacity_reservations_' + column, 'kleos2_capacity_reservations', [column])
    op.create_table('kleos2_verification_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('run_id', sa.String(36), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('lease_until', sa.Float(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.UniqueConstraint('run_id', 'request_hash'))
    for column in ('project_id', 'run_id', 'status'):
        op.create_index('ix_kleos2_verification_jobs_' + column, 'kleos2_verification_jobs', [column])


def downgrade():
    for column in ('project_id', 'run_id', 'status'):
        op.drop_index('ix_kleos2_verification_jobs_' + column, table_name='kleos2_verification_jobs')
    op.drop_table('kleos2_verification_jobs')
    for column in ('pool_id', 'project_id', 'status'):
        op.drop_index('ix_kleos2_capacity_reservations_' + column, table_name='kleos2_capacity_reservations')
    op.drop_table('kleos2_capacity_reservations')
    op.drop_table('kleos2_capacity_pools')
