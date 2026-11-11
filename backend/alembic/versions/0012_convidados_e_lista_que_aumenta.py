"""convidados dos membros e lista de espera que aumenta a pelada

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-11 14:00:00

- group_members.invited_by_member_id: convidado (status guest, sem conta própria) e quem o trouxe.
- payments.payer_member_id: quem paga, quando não é o próprio jogador (convidado: quem trouxe).
- matches/match_series: times previstos, limite da lista de espera e aumento único da pelada quando a espera
  lota (as vagas crescem do tamanho da espera e os times passam a `expanded_num_teams`).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.core.db

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _team_columns(batch_op) -> None:
    batch_op.add_column(sa.Column('num_teams', sa.Integer(), nullable=True))
    batch_op.add_column(sa.Column('waitlist_limit', sa.Integer(), nullable=True))
    batch_op.add_column(sa.Column('expand_when_waitlist_full', sa.Boolean(), nullable=False,
                                  server_default=sa.false()))
    batch_op.add_column(sa.Column('expanded_num_teams', sa.Integer(), nullable=True))


def _drop_team_columns(batch_op) -> None:
    batch_op.drop_column('expanded_num_teams')
    batch_op.drop_column('expand_when_waitlist_full')
    batch_op.drop_column('waitlist_limit')
    batch_op.drop_column('num_teams')


def upgrade() -> None:
    with op.batch_alter_table('group_members', schema=None) as batch_op:
        batch_op.add_column(sa.Column('invited_by_member_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_group_members_invited_by_member_id'), ['invited_by_member_id'],
                              unique=False)
        batch_op.create_foreign_key('fk_group_members_invited_by', 'group_members', ['invited_by_member_id'],
                                    ['id'])

    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payer_member_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_payments_payer_member_id'), ['payer_member_id'], unique=False)
        batch_op.create_foreign_key('fk_payments_payer_member', 'group_members', ['payer_member_id'], ['id'])

    with op.batch_alter_table('matches', schema=None) as batch_op:
        _team_columns(batch_op)
        batch_op.add_column(sa.Column('expanded_at', app.core.db.UTCDateTime(), nullable=True))

    with op.batch_alter_table('match_series', schema=None) as batch_op:
        _team_columns(batch_op)


def downgrade() -> None:
    with op.batch_alter_table('match_series', schema=None) as batch_op:
        _drop_team_columns(batch_op)

    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.drop_column('expanded_at')
        _drop_team_columns(batch_op)

    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.drop_constraint('fk_payments_payer_member', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_payments_payer_member_id'))
        batch_op.drop_column('payer_member_id')

    with op.batch_alter_table('group_members', schema=None) as batch_op:
        batch_op.drop_constraint('fk_group_members_invited_by', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_group_members_invited_by_member_id'))
        batch_op.drop_column('invited_by_member_id')
