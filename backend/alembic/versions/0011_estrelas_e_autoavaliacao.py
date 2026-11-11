"""nível em estrelas (0 a 5) no lugar dos Tiers e quiz de autoavaliação ao entrar no grupo

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-11 10:00:00

- player_ratings.tier (S/A/B/C/D) vira player_ratings.stars (0 a 5), recalculado pela média com os limites
  padrão (média arredondada: 4,5+ = 5★, 3,5+ = 4★, 2,5+ = 3★, 1,5+ = 2★, 0,5+ = 1★).
- player_rating_history.tier_before/tier_after viram stars_before/stars_after (pelas médias registradas).
- platform_settings.tier_thresholds vira star_thresholds (volta aos padrões: os limites antigos eram de Tier).
- O peso "tier" do sorteio passa a se chamar "stars" (grupos e padrões da plataforma).
- group_members ganha os campos do quiz de autoavaliação (respostas, nível calculado, nível declarado pelo
  jogador, confirmação do administrador e lembretes). Quem já está no grupo também responde, exceto quem já
  tem perfil consolidado pelas avaliações.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.core.db

revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# limites padrão (cópia fixa: a migração não deve mudar se o código mudar depois)
_STARS = ((5, 4.5), (4, 3.5), (3, 2.5), (2, 1.5), (1, 0.5))
_TIERS = (("S", 4.5), ("A", 3.75), ("B", 3.0), ("C", 2.0))


def _stars(average) -> int | None:
    if average is None:
        return None
    value = round(float(average), 6)
    return next((s for s, m in _STARS if value >= m), 0)


def _tier(average) -> str | None:
    if average is None:
        return None
    value = round(float(average), 6)
    return next((t for t, m in _TIERS if value >= m), "D")


def _rename_weight(weights: dict | None, old: str, new: str) -> dict | None:
    if not weights or old not in weights:
        return weights
    out = {k: v for k, v in weights.items() if k != old}
    out.setdefault(new, weights[old])
    return out


def _convert_weights(old: str, new: str) -> None:
    bind = op.get_bind()
    groups = sa.table('football_groups', sa.column('id', sa.Integer), sa.column('draw_weights', sa.JSON))
    for gid, weights in bind.execute(sa.select(groups.c.id, groups.c.draw_weights)).all():
        renamed = _rename_weight(weights, old, new)
        if renamed is not weights:
            bind.execute(groups.update().where(groups.c.id == gid).values(draw_weights=renamed))
    platform = sa.table('platform_settings', sa.column('id', sa.Integer), sa.column('group_defaults', sa.JSON))
    for pid, defaults in bind.execute(sa.select(platform.c.id, platform.c.group_defaults)).all():
        if defaults and isinstance(defaults.get('draw_weights'), dict) and old in defaults['draw_weights']:
            new_defaults = {**defaults, 'draw_weights': _rename_weight(defaults['draw_weights'], old, new)}
            bind.execute(platform.update().where(platform.c.id == pid).values(group_defaults=new_defaults))


def upgrade() -> None:
    bind = op.get_bind()

    with op.batch_alter_table('group_members', schema=None) as batch_op:
        batch_op.add_column(sa.Column('self_assessment', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('self_assessed_at', app.core.db.UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column('self_assessment_kind', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('quiz_stars', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('declared_stars', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('confirmed_stars', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('level_reviewed_at', app.core.db.UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column('quiz_reminded_at', app.core.db.UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column('quiz_reminders_count', sa.Integer(), nullable=False, server_default='0'))

    # --- notas atuais
    with op.batch_alter_table('player_ratings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('stars', sa.Integer(), nullable=True))
    ratings = sa.table('player_ratings', sa.column('id', sa.Integer), sa.column('average', sa.Float),
                       sa.column('stars', sa.Integer))
    for rid, average in bind.execute(sa.select(ratings.c.id, ratings.c.average)).all():
        bind.execute(ratings.update().where(ratings.c.id == rid).values(stars=_stars(average)))
    with op.batch_alter_table('player_ratings', schema=None) as batch_op:
        batch_op.alter_column('stars', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('tier')

    # --- histórico
    with op.batch_alter_table('player_rating_history', schema=None) as batch_op:
        batch_op.add_column(sa.Column('stars_before', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('stars_after', sa.Integer(), nullable=True))
    history = sa.table('player_rating_history', sa.column('id', sa.Integer), sa.column('reason', sa.String),
                       sa.column('average_before', sa.Float), sa.column('average_after', sa.Float),
                       sa.column('stars_before', sa.Integer), sa.column('stars_after', sa.Integer))
    rows = bind.execute(sa.select(history.c.id, history.c.average_before, history.c.average_after)).all()
    for hid, before, after in rows:
        bind.execute(history.update().where(history.c.id == hid)
                     .values(stars_before=_stars(before), stars_after=_stars(after)))
    bind.execute(history.update().where(history.c.reason == 'tier_thresholds').values(reason='star_thresholds'))
    with op.batch_alter_table('player_rating_history', schema=None) as batch_op:
        batch_op.alter_column('stars_after', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('tier_before')
        batch_op.drop_column('tier_after')

    # --- configurações da plataforma e pesos do sorteio
    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('star_thresholds', sa.JSON(), nullable=True))
    platform = sa.table('platform_settings', sa.column('star_thresholds', sa.JSON))
    bind.execute(platform.update().values(star_thresholds={}))
    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.alter_column('star_thresholds', existing_type=sa.JSON(), nullable=False)
        batch_op.drop_column('tier_thresholds')
    _convert_weights('tier', 'stars')


def downgrade() -> None:
    bind = op.get_bind()
    _convert_weights('stars', 'tier')

    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tier_thresholds', sa.JSON(), nullable=True))
    platform = sa.table('platform_settings', sa.column('tier_thresholds', sa.JSON))
    bind.execute(platform.update().values(tier_thresholds={}))
    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.alter_column('tier_thresholds', existing_type=sa.JSON(), nullable=False)
        batch_op.drop_column('star_thresholds')

    with op.batch_alter_table('player_rating_history', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tier_before', sa.String(length=1), nullable=True))
        batch_op.add_column(sa.Column('tier_after', sa.String(length=1), nullable=True))
    history = sa.table('player_rating_history', sa.column('id', sa.Integer), sa.column('reason', sa.String),
                       sa.column('average_before', sa.Float), sa.column('average_after', sa.Float),
                       sa.column('tier_before', sa.String), sa.column('tier_after', sa.String))
    rows = bind.execute(sa.select(history.c.id, history.c.average_before, history.c.average_after)).all()
    for hid, before, after in rows:
        bind.execute(history.update().where(history.c.id == hid)
                     .values(tier_before=_tier(before), tier_after=_tier(after)))
    bind.execute(history.update().where(history.c.reason == 'star_thresholds').values(reason='tier_thresholds'))
    with op.batch_alter_table('player_rating_history', schema=None) as batch_op:
        batch_op.alter_column('tier_after', existing_type=sa.String(length=1), nullable=False)
        batch_op.drop_column('stars_after')
        batch_op.drop_column('stars_before')

    with op.batch_alter_table('player_ratings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tier', sa.String(length=1), nullable=True))
    ratings = sa.table('player_ratings', sa.column('id', sa.Integer), sa.column('average', sa.Float),
                       sa.column('tier', sa.String))
    for rid, average in bind.execute(sa.select(ratings.c.id, ratings.c.average)).all():
        bind.execute(ratings.update().where(ratings.c.id == rid).values(tier=_tier(average)))
    with op.batch_alter_table('player_ratings', schema=None) as batch_op:
        batch_op.alter_column('tier', existing_type=sa.String(length=1), nullable=False)
        batch_op.drop_column('stars')

    with op.batch_alter_table('group_members', schema=None) as batch_op:
        batch_op.drop_column('quiz_reminders_count')
        batch_op.drop_column('quiz_reminded_at')
        batch_op.drop_column('level_reviewed_at')
        batch_op.drop_column('confirmed_stars')
        batch_op.drop_column('declared_stars')
        batch_op.drop_column('quiz_stars')
        batch_op.drop_column('self_assessment_kind')
        batch_op.drop_column('self_assessed_at')
        batch_op.drop_column('self_assessment')
