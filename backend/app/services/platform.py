"""Configurações globais da plataforma (PRD §2): limites dos Tiers e padrões de grupos novos."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.domain import rating as R
from app.models import PlatformSettings, PlayerRating, PlayerRatingHistory, User
from app.models.group import _default_draw_weights
from app.schemas.platform import PlatformSettingsUpdate
from app.services import audit

_SETTINGS_ID = 1
_CACHE_KEY = "platform_settings"

DEFAULT_GROUP_DEFAULTS: dict = {
    "initial_rating": 2.5,
    "min_evaluations_to_consolidate": 5,
    "rating_match_weight": 0.30,
    "provisional_match_weight": 0.50,
    "min_evaluators_full_weight": 3,
    "outlier_threshold": 2.0,
    "exclude_outliers": True,
    "draw_weights": _default_draw_weights(),
}


def _row(db: Session) -> PlatformSettings | None:
    return db.get(PlatformSettings, _SETTINGS_ID)


def _effective(db: Session) -> dict:
    """Valores em vigor (padrões de fábrica sobrepostos pelo que foi salvo). Cache por sessão."""
    cached = db.info.get(_CACHE_KEY)
    if cached is not None:
        return cached
    row = _row(db)
    thresholds = R.thresholds_to_dict(R.TIER_THRESHOLDS)
    defaults = {**DEFAULT_GROUP_DEFAULTS, "draw_weights": dict(DEFAULT_GROUP_DEFAULTS["draw_weights"])}
    if row is not None:
        thresholds.update(row.tier_thresholds or {})
        stored = dict(row.group_defaults or {})
        if "draw_weights" in stored:
            defaults["draw_weights"].update(stored.pop("draw_weights") or {})
        defaults.update(stored)
    value = {
        "tier_thresholds": thresholds,
        "group_defaults": defaults,
        "updated_at": row.updated_at if row is not None else None,
    }
    db.info[_CACHE_KEY] = value
    return value


def tier_thresholds(db: Session) -> R.Thresholds:
    return R.thresholds_from_dict(_effective(db)["tier_thresholds"])


def group_defaults(db: Session) -> dict:
    defaults = _effective(db)["group_defaults"]
    return {**defaults, "draw_weights": dict(defaults["draw_weights"])}


def settings_out(db: Session, recalculated: int | None = None) -> dict:
    eff = _effective(db)
    return {
        "tier_thresholds": eff["tier_thresholds"],
        "tier_labels": R.TIER_LABELS,
        "group_defaults": eff["group_defaults"],
        "updated_at": eff["updated_at"],
        "recalculated_ratings": recalculated,
    }


def recalculate_tiers(db: Session, thresholds: R.Thresholds, actor_user_id: int) -> int:
    """Reclassifica todas as notas com os novos limites. Médias não mudam; cada Tier alterado gera histórico."""
    changed = 0
    for rating in db.scalars(select(PlayerRating)).all():
        new_tier = R.compute_tier(rating.average, thresholds)
        if new_tier == rating.tier:
            continue
        before = rating.tier
        rating.tier = new_tier
        db.add(PlayerRatingHistory(
            rating_id=rating.id,
            member_id=rating.member_id,
            kind=rating.kind,
            match_id=None,
            reason="tier_thresholds",
            attributes_before=dict(rating.attributes),
            attributes_after=dict(rating.attributes),
            match_scores=None,
            average_before=rating.average,
            average_after=rating.average,
            tier_before=before,
            tier_after=new_tier,
            evaluations_count=0,
            weight_applied=0.0,
            actor_user_id=actor_user_id,
        ))
        changed += 1
    return changed


def update(db: Session, data: PlatformSettingsUpdate, actor: User) -> dict:
    before = settings_out(db)
    row = _row(db)
    if row is None:
        row = PlatformSettings(id=_SETTINGS_ID, tier_thresholds={}, group_defaults={})
        db.add(row)
    recalculated = None
    if data.tier_thresholds is not None and data.tier_thresholds != before["tier_thresholds"]:
        row.tier_thresholds = data.tier_thresholds
        recalculated = recalculate_tiers(db, R.thresholds_from_dict(data.tier_thresholds), actor.id)
    if data.group_defaults is not None:
        changes = data.group_defaults.model_dump(exclude_none=True)
        stored = dict(row.group_defaults or {})
        if "draw_weights" in changes:
            changes["draw_weights"] = {**before["group_defaults"]["draw_weights"], **changes["draw_weights"]}
        stored.update(changes)
        row.group_defaults = stored
    row.updated_by_id = actor.id
    row.updated_at = utcnow()
    db.info.pop(_CACHE_KEY, None)
    after = settings_out(db, recalculated)
    audit.record(
        db, action="platform.settings_updated", entity_type="platform_settings", entity_id=_SETTINGS_ID,
        actor_user_id=actor.id, group_id=None,
        before={"tier_thresholds": before["tier_thresholds"], "group_defaults": before["group_defaults"]},
        after={"tier_thresholds": after["tier_thresholds"], "group_defaults": after["group_defaults"],
               "recalculated_ratings": recalculated},
        reason=data.reason,
    )
    db.commit()
    db.info.pop(_CACHE_KEY, None)
    return settings_out(db, recalculated)
