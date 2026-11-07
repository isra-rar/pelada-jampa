"""Perfil esportivo: atributos, média, Tier e histórico (PRD §5)."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError
from app.domain import rating as R
from app.domain.enums import Position, RatingKind
from app.models import FootballGroup, GroupMember, PlayerRating, PlayerRatingHistory
from app.services import audit, platform


def keys_for(group: FootballGroup, kind: RatingKind) -> tuple[str, ...]:
    return R.attributes_for(kind, group.goalkeeper_rating_mode)


def params_for(group: FootballGroup) -> R.RatingParams:
    return R.RatingParams(
        match_weight=group.rating_match_weight,
        provisional_match_weight=group.provisional_match_weight,
        min_evaluators_full_weight=group.min_evaluators_full_weight,
    )


def get_rating(db: Session, member_id: int, kind: RatingKind) -> PlayerRating | None:
    return db.scalar(select(PlayerRating).where(PlayerRating.member_id == member_id, PlayerRating.kind == kind))


def _history(db: Session, rating: PlayerRating, *, reason: str, before: dict | None, avg_before, tier_before,
             match_id=None, match_scores=None, n=0, weight=0.0, actor_user_id=None) -> None:
    db.add(
        PlayerRatingHistory(
            rating_id=rating.id,
            member_id=rating.member_id,
            kind=rating.kind,
            match_id=match_id,
            reason=reason,
            attributes_before=before,
            attributes_after=dict(rating.attributes),
            match_scores=match_scores,
            average_before=avg_before,
            average_after=rating.average,
            tier_before=tier_before,
            tier_after=rating.tier,
            evaluations_count=n,
            weight_applied=weight,
            actor_user_id=actor_user_id,
        )
    )


def ensure_rating(db: Session, group: FootballGroup, member: GroupMember, kind: RatingKind) -> PlayerRating:
    keys = keys_for(group, kind)
    rating = get_rating(db, member.id, kind)
    if rating is None:
        attrs = R.initial_attributes(keys, group.initial_rating)
        avg = R.compute_average(attrs, keys)
        rating = PlayerRating(
            member_id=member.id,
            kind=kind,
            attributes=attrs,
            average=avg,
            tier=R.compute_tier(avg, platform.tier_thresholds(db)),
            is_provisional=True,
            updated_at=utcnow(),
        )
        db.add(rating)
        db.flush()
        _history(db, rating, reason="initial", before=None, avg_before=None, tier_before=None)
    elif set(rating.attributes) != set(keys):
        # o grupo trocou o modo de avaliação de goleiros: reinicia preservando o nível médio
        before, avg_before, tier_before = dict(rating.attributes), rating.average, rating.tier
        rating.attributes = R.initial_attributes(keys, rating.average)
        rating.average = R.compute_average(rating.attributes, keys)
        rating.tier = R.compute_tier(rating.average, platform.tier_thresholds(db))
        rating.updated_at = utcnow()
        _history(db, rating, reason="mode_change", before=before, avg_before=avg_before, tier_before=tier_before)
    return rating


def ensure_member_ratings(db: Session, group: FootballGroup, member: GroupMember) -> None:
    ensure_rating(db, group, member, RatingKind.FIELD)
    if member.preferred_position == Position.GOALKEEPER:
        ensure_rating(db, group, member, RatingKind.GOALKEEPER)


def to_out(rating: PlayerRating) -> dict:
    return {
        "kind": rating.kind,
        "attributes": rating.attributes,
        "attributes_display": {k: R.display_average(v) for k, v in rating.attributes.items()},
        "labels": {k: R.ATTRIBUTE_LABELS[k] for k in rating.attributes},
        "average": rating.average,
        "average_display": R.display_average(rating.average),
        "tier": rating.tier,
        "tier_label": R.TIER_LABELS[rating.tier],
        "is_provisional": rating.is_provisional,
        "valid_evaluations_count": rating.valid_evaluations_count,
        "matches_evaluated_count": rating.matches_evaluated_count,
        "updated_at": rating.updated_at,
    }


def set_provisional(
    db: Session,
    group: FootballGroup,
    member: GroupMember,
    kind: RatingKind,
    attributes: dict[str, float],
    actor_user_id: int,
    reason: str | None,
) -> PlayerRating:
    """Administrador define a nota inicial provisória. Só é permitido enquanto o perfil é provisório."""
    rating = ensure_rating(db, group, member, kind)
    if not rating.is_provisional:
        raise BusinessRuleError(
            "Perfil já consolidado por avaliações; a nota só muda por novas avaliações",
            code="rating_consolidated",
        )
    keys = keys_for(group, kind)
    try:
        clean = R.validate_scores(attributes, keys)
    except ValueError as exc:
        raise BusinessRuleError(str(exc), code="invalid_attributes") from None
    before, avg_before, tier_before = dict(rating.attributes), rating.average, rating.tier
    rating.attributes = clean
    rating.average = R.compute_average(clean, keys)
    rating.tier = R.compute_tier(rating.average, platform.tier_thresholds(db))
    rating.updated_at = utcnow()
    _history(db, rating, reason="admin_provisional", before=before, avg_before=avg_before, tier_before=tier_before,
             actor_user_id=actor_user_id)
    audit.record(
        db,
        action="rating.provisional_set",
        entity_type="player_rating",
        entity_id=rating.id,
        actor_user_id=actor_user_id,
        group_id=group.id,
        before={"attributes": before, "average": avg_before, "tier": tier_before},
        after={"attributes": clean, "average": rating.average, "tier": rating.tier},
        reason=reason,
    )
    return rating


def apply_match_result(
    db: Session,
    group: FootballGroup,
    member: GroupMember,
    kind: RatingKind,
    match_id: int,
    score_sets: list[dict[str, float]],
) -> PlayerRating | None:
    """Atualiza a nota com as avaliações válidas da partida. Sem avaliações, nada muda (sem notas fictícias)."""
    n = len(score_sets)
    if n == 0:
        return None
    rating = ensure_rating(db, group, member, kind)
    keys = keys_for(group, kind)
    match_scores = R.consolidate(score_sets, keys)
    new_attrs, weight = R.update_attributes(
        rating.attributes, match_scores, n, rating.is_provisional, keys, params_for(group)
    )
    before, avg_before, tier_before = dict(rating.attributes), rating.average, rating.tier
    rating.attributes = new_attrs
    rating.average = R.compute_average(new_attrs, keys)
    rating.tier = R.compute_tier(rating.average, platform.tier_thresholds(db))
    rating.valid_evaluations_count += n
    rating.matches_evaluated_count += 1
    if rating.valid_evaluations_count >= group.min_evaluations_to_consolidate:
        rating.is_provisional = False
    rating.updated_at = utcnow()
    _history(db, rating, reason="match", before=before, avg_before=avg_before, tier_before=tier_before,
             match_id=match_id, match_scores=match_scores, n=n, weight=weight)
    return rating


def history(db: Session, member_id: int, kind: RatingKind | None, page: int, size: int):
    stmt = select(PlayerRatingHistory).where(PlayerRatingHistory.member_id == member_id)
    if kind:
        stmt = stmt.where(PlayerRatingHistory.kind == kind)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(PlayerRatingHistory.created_at.desc(), PlayerRatingHistory.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return list(items), total


def explanation(db: Session, group: FootballGroup) -> dict:
    return {
        "formula": (
            "nota_nova = nota_anterior × (1 − peso) + nota_da_partida × peso; "
            "peso reduzido proporcionalmente quando há menos avaliadores que o mínimo"
        ),
        "match_weight": group.rating_match_weight,
        "provisional_match_weight": group.provisional_match_weight,
        "min_evaluators_full_weight": group.min_evaluators_full_weight,
        "min_evaluations_to_consolidate": group.min_evaluations_to_consolidate,
        "tiers": {t: f"{R.TIER_LABELS[t]} (média ≥ {m:.2f})" for t, m in platform.tier_thresholds(db)},
    }
