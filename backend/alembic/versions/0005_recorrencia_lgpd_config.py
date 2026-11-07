"""geração automática da recorrência, exclusão de conta (LGPD) e configurações globais

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-06 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.core.db


revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('match_series', schema=None) as batch_op:
        batch_op.add_column(sa.Column('auto_generate_ahead', sa.Integer(), nullable=False, server_default='1'))

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('deleted_at', app.core.db.UTCDateTime(timezone=True), nullable=True))

    op.create_table(
        'platform_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tier_thresholds', sa.JSON(), nullable=False),
        sa.Column('group_defaults', sa.JSON(), nullable=False),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('platform_settings')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('deleted_at')
    with op.batch_alter_table('match_series', schema=None) as batch_op:
        batch_op.drop_column('auto_generate_ahead')
