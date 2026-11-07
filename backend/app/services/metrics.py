"""Indicadores de sucesso do produto (PRD §21), por grupo e para a plataforma.

Períodos usam datas locais (horário de Brasília), inclusivas. A retenção compara o período pedido com o
período imediatamente anterior de mesmo tamanho.
"""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError
from app.domain.enums import AttendanceStatus as S
from app.domain.enums import DrawStatus, EvaluationStatus, MatchStatus, PaymentStatus
from app.models import Draw, Evaluation, FootballGroup, GroupMember, Match, MatchPlayer, Payment, User
from app.services.payments import outstanding
from app.services.stats import BR_TZ, local_bounds

GROUP_DEFAULT_DAYS = 90
PLATFORM_DEFAULT_DAYS = 30
_HELD = (MatchStatus.PUBLISHED, MatchStatus.FINISHED)
_OCCUPYING = (S.CONFIRMED, S.PRESENT, S.ABSENT)
_RESPONDED = (S.CONFIRMED, S.DECLINED, S.WAITLIST, S.CANCELLED)


def _rate(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def period(since: date | None, until: date | None, default_days: int) -> tuple[date, date]:
    until = until or utcnow().astimezone(BR_TZ).date()
    since = since or until - timedelta(days=default_days - 1)
    if since > until:
        raise BusinessRuleError("A data inicial deve ser anterior à final", code="invalid_period")
    if (until - since).days > 366 * 3:
        raise BusinessRuleError("Período máximo de 3 anos", code="invalid_period")
    return since, until


def _previous(since: date, until: date) -> tuple[date, date]:
    days = (until - since).days + 1
    return since - timedelta(days=days), since - timedelta(days=1)


def _held_matches(db: Session, group_id: int, start: datetime, end: datetime) -> list[Match]:
    """Peladas realizadas no período: publicadas ou encerradas que já começaram."""
    return list(db.scalars(
        select(Match).where(
            Match.group_id == group_id, Match.status.in_(_HELD),
            Match.starts_at >= start, Match.starts_at < min(end, utcnow()),
        )
    ).all())


def _active_members(db: Session, group_id: int | None, start: datetime, end: datetime) -> set[int]:
    stmt = (
        select(MatchPlayer.member_id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .where(Match.status.in_(_HELD), MatchPlayer.status == S.PRESENT,
               Match.starts_at >= start, Match.starts_at < end)
    )
    if group_id is not None:
        stmt = stmt.where(Match.group_id == group_id)
    return set(db.scalars(stmt).all())


def _evaluation_metrics(db: Session, matches: list[Match], by_match: dict[int, list[MatchPlayer]]) -> dict:
    present = {m.id: {mp.member_id for mp in by_match[m.id] if mp.status == S.PRESENT} for m in matches}
    opened = [m for m in matches if m.evaluation_status != EvaluationStatus.NOT_OPEN and len(present[m.id]) >= 2]
    expected = sum(len(present[m.id]) * (len(present[m.id]) - 1) for m in opened)
    rows = db.execute(
        select(Evaluation.match_id, Evaluation.evaluator_member_id, Evaluation.created_at)
        .where(Evaluation.match_id.in_([m.id for m in opened]))
    ).all() if opened else []
    # só conta quem jogou (administradores que avaliam sem ter jogado ficam fora da taxa)
    rows = [r for r in rows if r.evaluator_member_id in present[r.match_id]]
    # tempo para concluir: da abertura da janela até a última avaliação enviada por cada avaliador
    last_sent: dict[tuple[int, int], datetime] = {}
    for r in rows:
        key = (r.match_id, r.evaluator_member_id)
        last_sent[key] = max(last_sent.get(key, r.created_at), r.created_at)
    opens = {m.id: m.evaluation_opens_at for m in opened}
    hours = [
        (sent - opens[mid]).total_seconds() / 3600
        for (mid, _), sent in last_sent.items()
        if opens[mid] is not None and sent >= opens[mid]
    ]
    return {
        "evaluation_matches": len(opened),
        "evaluations_expected": expected,
        "evaluations_submitted": len(rows),
        "evaluation_completion_rate": _rate(len(rows), expected),
        "avg_evaluation_hours": round(mean(hours), 2) if hours else None,
    }


def _finance_metrics(db: Session, group_id: int, start: datetime, end: datetime) -> dict:
    pays = db.scalars(
        select(Payment).join(Match, Match.id == Payment.match_id)
        .where(Payment.group_id == group_id, Match.starts_at >= start, Match.starts_at < end,
               Payment.status.in_((PaymentStatus.PENDING, PaymentStatus.PAID)))
    ).unique().all()
    paid = sum(1 for p in pays if p.status == PaymentStatus.PAID)
    debts = [outstanding(p) for p in pays if outstanding(p) > 0]
    return {
        "charges": len(pays),
        "charges_paid": paid,
        "payment_rate": _rate(paid, len(pays)),
        "open_debts": len(debts),
        "open_debts_amount": sum(debts, Decimal("0.00")),
    }


def _draw_metrics(db: Session, match_ids: list[int]) -> dict:
    rows = db.execute(
        select(Draw.match_id, Draw.status, Draw.metrics, Draw.manually_adjusted).where(Draw.match_id.in_(match_ids))
    ).all() if match_ids else []
    published = [r for r in rows if r.status == DrawStatus.PUBLISHED]
    per_match = Counter(r.match_id for r in rows)
    return {
        "matches_with_published_draw": len({r.match_id for r in published}),
        "avg_average_difference": _avg([float(r.metrics.get("average_difference", 0.0)) for r in published]),
        "avg_strength_difference": _avg([float(r.metrics.get("strength_difference", 0.0)) for r in published]),
        "draws_per_match": round(mean(per_match.values()), 2) if per_match else None,
        "manual_adjust_rate": _rate(sum(1 for r in published if r.manually_adjusted), len(published)),
    }


def group_metrics(db: Session, group_id: int, since: date | None, until: date | None) -> dict:
    since, until = period(since, until, GROUP_DEFAULT_DAYS)
    start, end = local_bounds(since, until)
    prev_start, prev_end = local_bounds(*_previous(since, until))

    matches = _held_matches(db, group_id, start, end)
    ids = [m.id for m in matches]
    cancelled = db.scalar(
        select(func.count()).select_from(Match).where(
            Match.group_id == group_id, Match.status == MatchStatus.CANCELLED,
            Match.starts_at >= start, Match.starts_at < end,
        )
    ) or 0

    rows = list(db.scalars(select(MatchPlayer).where(MatchPlayer.match_id.in_(ids))).all()) if ids else []
    by_match: dict[int, list[MatchPlayer]] = defaultdict(list)
    for mp in rows:
        by_match[mp.match_id].append(mp)
    responded = sum(1 for mp in rows if mp.status in _RESPONDED or mp.was_confirmed or mp.responded_at is not None)
    occupancy = [
        min(1.0, sum(1 for mp in by_match[m.id] if mp.status in _OCCUPYING) / m.slots) for m in matches if m.slots
    ]

    active = _active_members(db, group_id, start, end)
    previous = _active_members(db, group_id, prev_start, prev_end)
    return {
        "group_id": group_id,
        "since": since,
        "until": until,
        "matches": len(matches),
        "cancelled_matches": cancelled,
        "invited": len(rows),
        "responded": responded,
        "response_rate": _rate(responded, len(rows)),
        "occupancy_rate": _avg(occupancy),
        "confirmations": sum(1 for mp in rows if mp.was_confirmed),
        "cancellations_after_confirmation": sum(1 for mp in rows if mp.status == S.CANCELLED),
        "late_cancellations": sum(1 for mp in rows if mp.status == S.CANCELLED and mp.late_cancellation),
        "no_shows": sum(1 for mp in rows if mp.status == S.ABSENT and mp.was_confirmed),
        **_evaluation_metrics(db, matches, by_match),
        **_finance_metrics(db, group_id, start, end),
        **_draw_metrics(db, ids),
        "active_players": len(active),
        "previous_active_players": len(previous),
        "retained_players": len(active & previous),
        "retention_rate": _rate(len(active & previous), len(previous)),
    }


def _active_groups(db: Session, start: datetime, end: datetime) -> set[int]:
    return set(db.scalars(
        select(Match.group_id).where(Match.status.in_(_HELD), Match.starts_at >= start,
                                     Match.starts_at < min(end, utcnow()))
    ).all())


def platform_metrics(db: Session, since: date | None, until: date | None) -> dict:
    since, until = period(since, until, PLATFORM_DEFAULT_DAYS)
    start, end = local_bounds(since, until)
    prev_start, prev_end = local_bounds(*_previous(since, until))

    def count(stmt) -> int:
        return db.scalar(stmt) or 0

    active_groups = _active_groups(db, start, end)
    previous_groups = _active_groups(db, prev_start, prev_end)
    member_ids = _active_members(db, None, start, end)
    players = set(db.scalars(select(GroupMember.user_id).where(GroupMember.id.in_(member_ids))).all()) if member_ids else set()
    return {
        "since": since,
        "until": until,
        "users_total": count(select(func.count()).select_from(User).where(User.deleted_at.is_(None))),
        "users_active": count(select(func.count()).select_from(User).where(User.deleted_at.is_(None),
                                                                           User.is_active.is_(True))),
        "users_new": count(select(func.count()).select_from(User).where(User.created_at >= start,
                                                                        User.created_at < end)),
        "users_deleted": count(select(func.count()).select_from(User).where(User.deleted_at >= start,
                                                                            User.deleted_at < end)),
        "groups_total": count(select(func.count()).select_from(FootballGroup)),
        "groups_active": len(active_groups),
        "groups_previous_active": len(previous_groups),
        "groups_retained": len(active_groups & previous_groups),
        "group_retention_rate": _rate(len(active_groups & previous_groups), len(previous_groups)),
        "matches": count(select(func.count()).select_from(Match).where(
            Match.status.in_(_HELD), Match.starts_at >= start, Match.starts_at < min(end, utcnow()))),
        "players_active": len(players),
    }
