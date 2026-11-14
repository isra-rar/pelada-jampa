"""código curto do link de convidado

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-13 20:00:00

- match_guest_links: o link da pelada para convidado sem conta passa a ser um código curto (8 caracteres)
  por pelada e por quem compartilhou, no lugar do token assinado longo.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'match_guest_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(length=16), nullable=False),
        sa.Column('match_id', sa.Integer(), sa.ForeignKey('matches.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sharer_member_id', sa.Integer(), sa.ForeignKey('group_members.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('match_id', 'sharer_member_id', name='uq_guest_link_match_sharer'),
    )
    op.create_index('ix_match_guest_links_code', 'match_guest_links', ['code'], unique=True)
    op.create_index('ix_match_guest_links_match_id', 'match_guest_links', ['match_id'])


def downgrade() -> None:
    op.drop_index('ix_match_guest_links_match_id', table_name='match_guest_links')
    op.drop_index('ix_match_guest_links_code', table_name='match_guest_links')
    op.drop_table('match_guest_links')
