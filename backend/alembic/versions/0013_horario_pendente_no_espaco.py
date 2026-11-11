"""horário pendente na agenda do espaço

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-11 18:00:00

- matches.booking_status: pelada marcada numa quadra por grupo sem vínculo fica pendente até o dono do espaço
  (DE) confirmar data e horário; recusada, a pelada continua sem a quadra. As peladas que já ocupam uma quadra
  ficam confirmadas.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'booking_status',
            sa.Enum('pending', 'confirmed', 'rejected', name='bookingstatus', native_enum=False, length=12),
            nullable=True,
        ))
    op.execute("UPDATE matches SET booking_status = 'confirmed' WHERE court_id IS NOT NULL")


def downgrade() -> None:
    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.drop_column('booking_status')
