"""suspensão de grupos pela administração da plataforma

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-06 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.core.db


revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('football_groups', schema=None) as batch_op:
        batch_op.add_column(sa.Column('suspended_at', app.core.db.UTCDateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('suspension_reason', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('football_groups', schema=None) as batch_op:
        batch_op.drop_column('suspension_reason')
        batch_op.drop_column('suspended_at')
