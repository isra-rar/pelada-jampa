"""goleiros e juízes de aluguel: anúncio, agenda, contratação e vaga de juiz na pelada

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-06 23:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.core.db


revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('pro_profiles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('is_goalkeeper', sa.Boolean(), nullable=False),
    sa.Column('is_referee', sa.Boolean(), nullable=False),
    sa.Column('goalkeeper_price', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('referee_price', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('max_duration_minutes', sa.Integer(), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.Column('city', sa.String(length=80), nullable=True),
    sa.Column('neighborhood', sa.String(length=80), nullable=True),
    sa.Column('modalities', sa.JSON(), nullable=False),
    sa.Column('contact_phone', sa.String(length=30), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', name='uq_pro_profiles_user')
    )
    with op.batch_alter_table('pro_profiles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pro_profiles_user_id'), ['user_id'], unique=False)

    op.create_table('pro_availability',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('profile_id', sa.Integer(), nullable=False),
    sa.Column('weekday', sa.Integer(), nullable=True),
    sa.Column('date', sa.Date(), nullable=True),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('end_time', sa.Time(), nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['pro_profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('pro_availability', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pro_availability_profile_id'), ['profile_id'], unique=False)

    op.create_table('match_hires',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('pro_profile_id', sa.Integer(), nullable=False),
    sa.Column('pro_user_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.Enum('goalkeeper', 'referee', name='hirerole', native_enum=False, length=12), nullable=False),
    sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('status', sa.Enum('pending', 'accepted', 'declined', 'cancelled', 'expired', name='hirestatus', native_enum=False, length=12), nullable=False),
    sa.Column('message', sa.String(length=300), nullable=True),
    sa.Column('response_note', sa.String(length=300), nullable=True),
    sa.Column('requested_by_id', sa.Integer(), nullable=False),
    sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('responded_at', app.core.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', app.core.db.UTCDateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['group_id'], ['football_groups.id'], ),
    sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['pro_profile_id'], ['pro_profiles.id'], ),
    sa.ForeignKeyConstraint(['pro_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['requested_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('match_hires', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_match_hires_group_id'), ['group_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_match_hires_match_id'), ['match_id'], unique=False)
        batch_op.create_index('ix_match_hires_pro_status', ['pro_profile_id', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_match_hires_pro_user_id'), ['pro_user_id'], unique=False)

    with op.batch_alter_table('match_players', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_referee', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('hire_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_match_players_hire_id', 'match_hires', ['hire_id'], ['id'])

    with op.batch_alter_table('match_series', schema=None) as batch_op:
        batch_op.add_column(sa.Column('referee_slots', sa.Integer(), nullable=False, server_default='0'))

    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.add_column(sa.Column('referee_slots', sa.Integer(), nullable=False, server_default='0'))



def downgrade() -> None:
    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.drop_column('referee_slots')

    with op.batch_alter_table('match_series', schema=None) as batch_op:
        batch_op.drop_column('referee_slots')

    with op.batch_alter_table('match_players', schema=None) as batch_op:
        batch_op.drop_constraint('fk_match_players_hire_id', type_='foreignkey')
        batch_op.drop_column('hire_id')
        batch_op.drop_column('is_referee')

    with op.batch_alter_table('match_hires', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_match_hires_pro_user_id'))
        batch_op.drop_index('ix_match_hires_pro_status')
        batch_op.drop_index(batch_op.f('ix_match_hires_match_id'))
        batch_op.drop_index(batch_op.f('ix_match_hires_group_id'))

    op.drop_table('match_hires')
    with op.batch_alter_table('pro_availability', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pro_availability_profile_id'))

    op.drop_table('pro_availability')
    with op.batch_alter_table('pro_profiles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pro_profiles_user_id'))

    op.drop_table('pro_profiles')
