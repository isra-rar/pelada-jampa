"""nota inicial confirmada pelo admin

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-14 12:00:00

- player_ratings.initial_confirmed: o admin já salvou a nota inicial do jogador. Quem já teve a nota
  definida pelo admin (histórico admin_provisional) entra como confirmado.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0016'
down_revision: str | None = '0015'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('player_ratings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('initial_confirmed', sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
    op.execute(
        "UPDATE player_ratings SET initial_confirmed = TRUE WHERE id IN "
        "(SELECT rating_id FROM player_rating_history WHERE reason = 'admin_provisional')"
    )


def downgrade() -> None:
    with op.batch_alter_table('player_ratings', schema=None) as batch_op:
        batch_op.drop_column('initial_confirmed')
