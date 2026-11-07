"""jogo ao vivo (placar em tempo real com cronômetro)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-06 01:30:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.core.db


revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('match_games', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'status', sa.Enum('live', 'finished', name='gamestatus', native_enum=False, length=10),
            nullable=False, server_default='finished'))
        batch_op.add_column(sa.Column('started_at', app.core.db.UTCDateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('ended_at', app.core.db.UTCDateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('duration_seconds', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('match_games', schema=None) as batch_op:
        batch_op.drop_column('duration_seconds')
        batch_op.drop_column('ended_at')
        batch_op.drop_column('started_at')
        batch_op.drop_column('status')
