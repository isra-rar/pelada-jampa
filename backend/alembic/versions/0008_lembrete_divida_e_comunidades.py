"""lembrete periódico de pendências e comunidades públicas (descoberta)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-06 21:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.core.db


revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('football_groups', schema=None) as batch_op:
        batch_op.add_column(sa.Column('debt_reminder_days', sa.Integer(), nullable=False, server_default='3'))
        batch_op.add_column(sa.Column('discoverable', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('discover_matches', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(
            sa.Column('public_join_requires_approval', sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(sa.Column('city', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('neighborhood', sa.String(length=80), nullable=True))
        batch_op.create_index(batch_op.f('ix_football_groups_discoverable'), ['discoverable'], unique=False)

    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_reminder_at', app.core.db.UTCDateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.drop_column('last_reminder_at')

    with op.batch_alter_table('football_groups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_football_groups_discoverable'))
        batch_op.drop_column('neighborhood')
        batch_op.drop_column('city')
        batch_op.drop_column('public_join_requires_approval')
        batch_op.drop_column('discover_matches')
        batch_op.drop_column('discoverable')
        batch_op.drop_column('debt_reminder_days')
