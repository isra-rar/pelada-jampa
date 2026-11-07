"""espaços (quadras): agenda, vínculo com grupos e aluguel

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-06 13:05:12.442735
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.core.db


revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('venues',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('address', sa.String(length=300), nullable=True),
    sa.Column('phone', sa.String(length=30), nullable=True),
    sa.Column('link_code', sa.String(length=16), nullable=False),
    sa.Column('opening_hours', sa.JSON(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=False),
    sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('venues', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_venues_link_code'), ['link_code'], unique=True)

    op.create_table('courts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('venue_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('modality', sa.Enum('society', 'futsal', 'campo', 'outra', name='modality', native_enum=False, length=24), nullable=False),
    sa.Column('hourly_price', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['venue_id'], ['venues.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('courts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_courts_venue_id'), ['venue_id'], unique=False)

    op.create_table('venue_groups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('venue_id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'active', 'rejected', 'ended', name='venuelinkstatus', native_enum=False, length=24), nullable=False),
    sa.Column('origin', sa.Enum('venue', 'request', name='venuelinkorigin', native_enum=False, length=24), nullable=False),
    sa.Column('rent_hourly_price', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('message', sa.String(length=300), nullable=True),
    sa.Column('requested_by_id', sa.Integer(), nullable=False),
    sa.Column('decided_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('decided_at', app.core.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('ended_at', app.core.db.UTCDateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['decided_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['group_id'], ['football_groups.id'], ),
    sa.ForeignKeyConstraint(['requested_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['venue_id'], ['venues.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('venue_groups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_venue_groups_group_id'), ['group_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_venue_groups_venue_id'), ['venue_id'], unique=False)

    op.create_table('venue_staff',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('venue_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.Enum('owner', 'manager', name='venuerole', native_enum=False, length=24), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['venue_id'], ['venues.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('venue_id', 'user_id', name='uq_venue_staff_user')
    )
    with op.batch_alter_table('venue_staff', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_venue_staff_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_venue_staff_venue_id'), ['venue_id'], unique=False)

    op.create_table('court_blocks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('court_id', sa.Integer(), nullable=False),
    sa.Column('starts_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('ends_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('reason', sa.String(length=200), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=False),
    sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['court_id'], ['courts.id'], ),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('court_blocks', schema=None) as batch_op:
        batch_op.create_index('ix_court_blocks_court_start', ['court_id', 'starts_at'], unique=False)

    op.create_table('rent_charges',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('venue_id', sa.Integer(), nullable=False),
    sa.Column('court_id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('status', sa.Enum('pending', 'paid', 'exempt', 'cancelled', name='rentstatus', native_enum=False, length=24), nullable=False),
    sa.Column('reported_paid_at', app.core.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('paid_at', app.core.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('method', sa.String(length=40), nullable=True),
    sa.Column('notes', sa.String(length=300), nullable=True),
    sa.Column('confirmed_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['confirmed_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['court_id'], ['courts.id'], ),
    sa.ForeignKeyConstraint(['group_id'], ['football_groups.id'], ),
    sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ),
    sa.ForeignKeyConstraint(['venue_id'], ['venues.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('match_id', name='uq_rent_charge_match')
    )
    with op.batch_alter_table('rent_charges', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_rent_charges_group_id'), ['group_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_rent_charges_venue_id'), ['venue_id'], unique=False)

    with op.batch_alter_table('match_series', schema=None) as batch_op:
        batch_op.add_column(sa.Column('court_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_match_series_court_id', 'courts', ['court_id'], ['id'])

    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.add_column(sa.Column('court_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_matches_court_id'), ['court_id'], unique=False)
        batch_op.create_foreign_key('fk_matches_court_id', 'courts', ['court_id'], ['id'])



def downgrade() -> None:
    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.drop_constraint('fk_matches_court_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_matches_court_id'))
        batch_op.drop_column('court_id')

    with op.batch_alter_table('match_series', schema=None) as batch_op:
        batch_op.drop_constraint('fk_match_series_court_id', type_='foreignkey')
        batch_op.drop_column('court_id')

    with op.batch_alter_table('rent_charges', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rent_charges_venue_id'))
        batch_op.drop_index(batch_op.f('ix_rent_charges_group_id'))

    op.drop_table('rent_charges')
    with op.batch_alter_table('court_blocks', schema=None) as batch_op:
        batch_op.drop_index('ix_court_blocks_court_start')

    op.drop_table('court_blocks')
    with op.batch_alter_table('venue_staff', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_venue_staff_venue_id'))
        batch_op.drop_index(batch_op.f('ix_venue_staff_user_id'))

    op.drop_table('venue_staff')
    with op.batch_alter_table('venue_groups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_venue_groups_venue_id'))
        batch_op.drop_index(batch_op.f('ix_venue_groups_group_id'))

    op.drop_table('venue_groups')
    with op.batch_alter_table('courts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_courts_venue_id'))

    op.drop_table('courts')
    with op.batch_alter_table('venues', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_venues_link_code'))

    op.drop_table('venues')
