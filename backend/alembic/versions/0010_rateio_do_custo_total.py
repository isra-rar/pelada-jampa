"""cobrança por rateio do custo total da pelada (campo + goleiros/juízes contratados)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-07 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.core.db


revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('match_series', schema=None) as batch_op:
        batch_op.add_column(sa.Column('split_cost', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('field_cost', sa.Numeric(precision=10, scale=2), nullable=True))

    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.add_column(sa.Column('split_cost', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('field_cost', sa.Numeric(precision=10, scale=2), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.drop_column('field_cost')
        batch_op.drop_column('split_cost')

    with op.batch_alter_table('match_series', schema=None) as batch_op:
        batch_op.drop_column('field_cost')
        batch_op.drop_column('split_cost')

