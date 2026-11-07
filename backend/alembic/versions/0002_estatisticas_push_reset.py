"""estatisticas push reset

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-05 20:56:09.494591
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.core.db


revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('password_reset_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('used_at', app.core.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('password_reset_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_password_reset_tokens_token_hash'), ['token_hash'], unique=True)
        batch_op.create_index(batch_op.f('ix_password_reset_tokens_user_id'), ['user_id'], unique=False)

    op.create_table('push_subscriptions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('endpoint', sa.Text(), nullable=False),
    sa.Column('endpoint_hash', sa.String(length=64), nullable=False),
    sa.Column('keys', sa.JSON(), nullable=False),
    sa.Column('user_agent', sa.String(length=300), nullable=True),
    sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('last_used_at', app.core.db.UTCDateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('push_subscriptions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_push_subscriptions_endpoint_hash'), ['endpoint_hash'], unique=True)
        batch_op.create_index(batch_op.f('ix_push_subscriptions_user_id'), ['user_id'], unique=False)

    op.create_table('match_games',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('home_name', sa.String(length=60), nullable=False),
    sa.Column('away_name', sa.String(length=60), nullable=False),
    sa.Column('home_team_index', sa.Integer(), nullable=True),
    sa.Column('away_team_index', sa.Integer(), nullable=True),
    sa.Column('home_member_ids', sa.JSON(), nullable=False),
    sa.Column('away_member_ids', sa.JSON(), nullable=False),
    sa.Column('home_score', sa.Integer(), nullable=False),
    sa.Column('away_score', sa.Integer(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=False),
    sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('match_id', 'sequence', name='uq_game_match_seq')
    )
    with op.batch_alter_table('match_games', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_match_games_match_id'), ['match_id'], unique=False)

    op.create_table('match_goals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('game_id', sa.Integer(), nullable=False),
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('scorer_member_id', sa.Integer(), nullable=True),
    sa.Column('assist_member_id', sa.Integer(), nullable=True),
    sa.Column('own_goal', sa.Boolean(), nullable=False),
    sa.Column('minute', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['assist_member_id'], ['group_members.id'], ),
    sa.ForeignKeyConstraint(['game_id'], ['match_games.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['scorer_member_id'], ['group_members.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('match_goals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_match_goals_assist_member_id'), ['assist_member_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_match_goals_game_id'), ['game_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_match_goals_match_id'), ['match_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_match_goals_scorer_member_id'), ['scorer_member_id'], unique=False)

    with op.batch_alter_table('football_groups', schema=None) as batch_op:
        batch_op.add_column(sa.Column('charge_no_shows', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('charge_late_cancellations', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('public_ranking_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('public_token', sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint('uq_groups_public_token', ['public_token'])

    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reported_paid_at', app.core.db.UTCDateTime(timezone=True), nullable=True))

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('password_changed_at', app.core.db.UTCDateTime(timezone=True), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('password_changed_at')

    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.drop_column('reported_paid_at')

    with op.batch_alter_table('football_groups', schema=None) as batch_op:
        batch_op.drop_constraint('uq_groups_public_token', type_='unique')
        batch_op.drop_column('public_token')
        batch_op.drop_column('public_ranking_enabled')
        batch_op.drop_column('charge_late_cancellations')
        batch_op.drop_column('charge_no_shows')

    with op.batch_alter_table('match_goals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_match_goals_scorer_member_id'))
        batch_op.drop_index(batch_op.f('ix_match_goals_match_id'))
        batch_op.drop_index(batch_op.f('ix_match_goals_game_id'))
        batch_op.drop_index(batch_op.f('ix_match_goals_assist_member_id'))

    op.drop_table('match_goals')
    with op.batch_alter_table('match_games', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_match_games_match_id'))

    op.drop_table('match_games')
    with op.batch_alter_table('push_subscriptions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_push_subscriptions_user_id'))
        batch_op.drop_index(batch_op.f('ix_push_subscriptions_endpoint_hash'))

    op.drop_table('push_subscriptions')
    with op.batch_alter_table('password_reset_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_password_reset_tokens_user_id'))
        batch_op.drop_index(batch_op.f('ix_password_reset_tokens_token_hash'))

    op.drop_table('password_reset_tokens')
