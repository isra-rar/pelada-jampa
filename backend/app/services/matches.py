"""Peladas (ocorrências) e recorrência (PRD §3)."""

import calendar
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError, Conflict
from app.domain.enums import (
    AttendanceStatus,
    GroupRole,
    MatchStatus,
    MemberStatus,
    NotificationType,
    Recurrence,
)
from app.models import FootballGroup, GroupMember, Match, MatchPlayer, MatchSeries, User
from app.schemas.match import MatchCreate, MatchUpdate, SeriesCreate, SeriesUpdate
from app.services import audit, notifications, venues
from app.services.groups import group_admin_user_ids

DEFAULT_CONFIRMATION_HOURS = 2

_MATCH_AUDIT_FIELDS = [
    "name", "description", "modality", "location", "address", "court_id", "starts_at", "ends_at", "price", "slots",
    "field_players_per_team", "goalkeepers_per_team", "confirmation_deadline", "cancellation_deadline",
    "evaluation_window_hours", "waitlist_auto_promote", "responsible_admin_ids", "status",
]


# ------------------------------------------------------------------ validações


def _validate_schedule(starts_at: datetime, ends_at: datetime, confirmation: datetime, cancellation: datetime | None):
    if ends_at <= starts_at:
        raise BusinessRuleError("O término deve ser posterior ao início", code="invalid_schedule")
    if confirmation > starts_at:
        raise BusinessRuleError("O prazo de confirmação deve ser anterior ao início", code="invalid_deadline")
    if cancellation is not None and cancellation > starts_at:
        raise BusinessRuleError("O prazo de cancelamento deve ser anterior ao início", code="invalid_deadline")


def _validate_admins(db: Session, group_id: int, user_ids: list[int]) -> list[int]:
    if not user_ids:
        return []
    found = set(
        db.scalars(
            select(GroupMember.user_id).where(
                GroupMember.group_id == group_id,
                GroupMember.user_id.in_(user_ids),
                GroupMember.role == GroupRole.ADMIN,
                GroupMember.status == MemberStatus.ACTIVE,
            )
        ).all()
    )
    missing = [u for u in user_ids if u not in found]
    if missing:
        raise BusinessRuleError(f"Usuários não são administradores do grupo: {missing}", code="invalid_admins")
    return list(dict.fromkeys(user_ids))


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        raise BusinessRuleError("Datas devem incluir fuso horário (ex.: 2026-11-10T20:00:00-03:00)",
                                code="naive_datetime")
    return dt


# ------------------------------------------------------------------ ocorrências


def active_member_ids(db: Session, group_id: int) -> list[GroupMember]:
    return list(
        db.scalars(
            select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.status == MemberStatus.ACTIVE)
        ).all()
    )


def create_match(
    db: Session, group: FootballGroup, data: MatchCreate, actor: User, *, series: MatchSeries | None = None,
    commit: bool = True,
) -> Match:
    starts_at = _aware(data.starts_at)
    ends_at = _aware(data.ends_at) or starts_at + timedelta(minutes=data.duration_minutes or 0)
    confirmation = _aware(data.confirmation_deadline) or starts_at - timedelta(hours=DEFAULT_CONFIRMATION_HOURS)
    cancellation = _aware(data.cancellation_deadline)
    _validate_schedule(starts_at, ends_at, confirmation, cancellation)
    location, address = data.location, data.address
    if data.court_id is not None:
        # quadra de espaço vinculado: reserva o horário (falha se ocupado, bloqueado ou fora do funcionamento)
        court = venues.court_for_group(db, group, data.court_id)
        venues.check_slot(db, court, starts_at, ends_at)
        label, venue_address = venues.court_label(db, court)
        location, address = location or label, address or venue_address
    match = Match(
        group_id=group.id,
        series_id=series.id if series else None,
        name=data.name,
        description=data.description,
        modality=data.modality,
        location=location,
        address=address,
        court_id=data.court_id,
        starts_at=starts_at,
        ends_at=ends_at,
        price=data.price,
        slots=data.slots,
        field_players_per_team=data.field_players_per_team,
        goalkeepers_per_team=data.goalkeepers_per_team,
        confirmation_deadline=confirmation,
        cancellation_deadline=cancellation,
        evaluation_window_hours=data.evaluation_window_hours,
        waitlist_auto_promote=(
            data.waitlist_auto_promote if data.waitlist_auto_promote is not None else group.waitlist_auto_promote
        ),
        responsible_admin_ids=_validate_admins(db, group.id, data.responsible_admin_ids) or [actor.id],
        status=MatchStatus.DRAFT,
        created_by_id=actor.id,
    )
    db.add(match)
    db.flush()
    audit.record(db, action="match.created", entity_type="match", entity_id=match.id, actor_user_id=actor.id,
                 group_id=group.id, after=audit.snapshot(match, _MATCH_AUDIT_FIELDS))
    if data.publish:
        publish_match(db, group, match, actor, commit=False)
    if commit:
        db.commit()
    return match


def publish_match(db: Session, group: FootballGroup, match: Match, actor: User, *, commit: bool = True) -> Match:
    if match.status != MatchStatus.DRAFT:
        raise BusinessRuleError("Apenas peladas em rascunho podem ser publicadas", code="invalid_status")
    match.status = MatchStatus.PUBLISHED
    match.published_at = utcnow()
    members = active_member_ids(db, group.id)
    existing = set(db.scalars(select(MatchPlayer.member_id).where(MatchPlayer.match_id == match.id)).all())
    for m in members:
        if m.id not in existing:
            db.add(MatchPlayer(match_id=match.id, member_id=m.id, status=AttendanceStatus.PENDING))
    when = notifications.when(match.starts_at)
    notifications.notify(
        db, [m.user_id for m in members], NotificationType.MATCH_CREATED, f"Nova pelada: {match.name}",
        f"{when} em {match.location}. Confirme sua presença até "
        f"{notifications.when(match.confirmation_deadline)}.",
        group_id=group.id, match_id=match.id,
    )
    audit.record(db, action="match.published", entity_type="match", entity_id=match.id, actor_user_id=actor.id,
                 group_id=group.id)
    if commit:
        db.commit()
    return match


def update_match(db: Session, group: FootballGroup, match: Match, data: MatchUpdate, actor: User) -> Match:
    from app.services import attendance  # evita import circular

    if match.status in (MatchStatus.FINISHED, MatchStatus.CANCELLED):
        raise BusinessRuleError("Peladas encerradas ou canceladas não podem ser alteradas", code="invalid_status")
    before = audit.snapshot(match, _MATCH_AUDIT_FIELDS)
    changes = data.model_dump(exclude_unset=True)
    for key in ("starts_at", "ends_at", "confirmation_deadline", "cancellation_deadline"):
        if key in changes:
            _aware(changes[key])
    if "responsible_admin_ids" in changes and changes["responsible_admin_ids"] is not None:
        changes["responsible_admin_ids"] = _validate_admins(db, group.id, changes["responsible_admin_ids"])
    court_changed = "court_id" in changes and changes["court_id"] != match.court_id
    old_times = (match.starts_at, match.ends_at)
    for key, value in changes.items():
        if value is None and key not in ("description", "address", "cancellation_deadline", "court_id"):
            continue
        setattr(match, key, value)
    _validate_schedule(match.starts_at, match.ends_at, match.confirmation_deadline, match.cancellation_deadline)
    if match.court_id is not None and (court_changed or (match.starts_at, match.ends_at) != old_times):
        court = venues.court_for_group(db, group, match.court_id)
        venues.check_slot(db, court, match.starts_at, match.ends_at, exclude_match_id=match.id)
        if court_changed and "location" not in changes:
            match.location, venue_address = venues.court_label(db, court)
            match.address = changes.get("address") or venue_address
    db.flush()
    after = audit.snapshot(match, _MATCH_AUDIT_FIELDS)
    if before != after:
        audit.record(db, action="match.updated", entity_type="match", entity_id=match.id, actor_user_id=actor.id,
                     group_id=group.id, before=before, after=after)
    if match.status == MatchStatus.PUBLISHED and match.slots > before["slots"]:
        attendance.promote_waitlist(db, match)
    db.commit()
    return match


def cancel_match(db: Session, group: FootballGroup, match: Match, reason: str | None, actor: User) -> Match:
    if match.status in (MatchStatus.FINISHED, MatchStatus.CANCELLED):
        raise BusinessRuleError("Pelada já encerrada ou cancelada", code="invalid_status")
    match.status = MatchStatus.CANCELLED
    match.cancelled_at = utcnow()
    match.cancel_reason = reason
    rows = db.execute(
        select(GroupMember.user_id)
        .join(MatchPlayer, MatchPlayer.member_id == GroupMember.id)
        .where(MatchPlayer.match_id == match.id, MatchPlayer.status != AttendanceStatus.DECLINED)
    ).scalars().all()
    notifications.notify(db, rows, NotificationType.MATCH_CANCELLED, f"Pelada cancelada: {match.name}",
                         reason or "A pelada foi cancelada pelo administrador.", group_id=group.id,
                         match_id=match.id)
    audit.record(db, action="match.cancelled", entity_type="match", entity_id=match.id, actor_user_id=actor.id,
                 group_id=group.id, reason=reason)
    db.commit()
    return match


def list_matches(
    db: Session,
    group_id: int,
    *,
    status: MatchStatus | list[MatchStatus] | None,
    upcoming: bool | None,
    include_drafts: bool,
    page: int,
    size: int,
) -> tuple[list[Match], int]:
    stmt = select(Match).where(Match.group_id == group_id)
    if status:
        stmt = stmt.where(Match.status.in_(status if isinstance(status, list) else [status]))
    if not include_drafts:
        stmt = stmt.where(Match.status != MatchStatus.DRAFT)
    order = Match.starts_at.desc()
    if upcoming is True:
        stmt = stmt.where(Match.ends_at >= utcnow())
        order = Match.starts_at.asc()
    elif upcoming is False:
        stmt = stmt.where(Match.ends_at < utcnow())
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(order, Match.id).offset((page - 1) * size).limit(size)).all()
    return list(items), total


def next_match(db: Session, group_id: int) -> Match | None:
    return db.scalar(
        select(Match)
        .where(Match.group_id == group_id, Match.status == MatchStatus.PUBLISHED, Match.ends_at >= utcnow())
        .order_by(Match.starts_at)
        .limit(1)
    )


def attendance_counts(db: Session, match: Match) -> dict:
    rows = db.execute(
        select(MatchPlayer.status, func.count()).where(MatchPlayer.match_id == match.id).group_by(MatchPlayer.status)
    ).all()
    counts = {s.value: 0 for s in AttendanceStatus}
    for status, n in rows:
        counts[AttendanceStatus(status).value] = n
    offered = db.scalar(
        select(func.count()).where(
            MatchPlayer.match_id == match.id,
            MatchPlayer.status == AttendanceStatus.WAITLIST,
            MatchPlayer.promotion_offered_at.is_not(None),
        )
    ) or 0
    counts["available_slots"] = max(0, match.slots - counts["confirmed"] - offered)
    return counts


def match_out(db: Session, match: Match, member: GroupMember | None = None) -> dict:
    from app.schemas.match import MatchOut

    data = MatchOut.model_validate(match).model_dump()
    data["counts"] = attendance_counts(db, match)
    if member is not None:
        data["my_status"] = db.scalar(
            select(MatchPlayer.status).where(MatchPlayer.match_id == match.id, MatchPlayer.member_id == member.id)
        )
    return data


# ------------------------------------------------------------------ recorrência


def add_months(dt: datetime, months: int, day: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return dt.replace(year=year, month=month, day=min(day, last_day))


def next_occurrence(series: MatchSeries, previous: datetime | None) -> datetime:
    if previous is None:
        return series.first_starts_at
    if series.recurrence == Recurrence.WEEKLY:
        return previous + timedelta(days=7)
    if series.recurrence == Recurrence.BIWEEKLY:
        return previous + timedelta(days=14)
    return add_months(previous, 1, series.first_starts_at.day)


def create_series(db: Session, group: FootballGroup, data: SeriesCreate, actor: User) -> tuple[MatchSeries, list]:
    _aware(data.first_starts_at)
    values = data.model_dump(exclude={"generate_count"})
    values["responsible_admin_ids"] = _validate_admins(db, group.id, data.responsible_admin_ids) or [actor.id]
    if data.court_id is not None:
        label, venue_address = venues.court_label(db, venues.court_for_group(db, group, data.court_id))
        values["location"] = data.location or label
        values["address"] = data.address or venue_address
    series = MatchSeries(group_id=group.id, created_by_id=actor.id, **values)
    db.add(series)
    db.flush()
    audit.record(db, action="series.created", entity_type="match_series", entity_id=series.id,
                 actor_user_id=actor.id, group_id=group.id, after=values)
    matches = generate_occurrences(db, group, series, data.generate_count, actor, commit=False) if data.generate_count else []
    db.commit()
    return series, matches


def update_series(db: Session, group: FootballGroup, series: MatchSeries, data: SeriesUpdate, actor: User):
    """Altera apenas o padrão das próximas ocorrências; partidas já geradas não mudam."""
    fields = list(SeriesUpdate.model_fields)
    before = audit.snapshot(series, fields)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("responsible_admin_ids") is not None:
        changes["responsible_admin_ids"] = _validate_admins(db, group.id, changes["responsible_admin_ids"])
    if changes.get("court_id") is not None and changes["court_id"] != series.court_id:
        label, venue_address = venues.court_label(db, venues.court_for_group(db, group, changes["court_id"]))
        changes.setdefault("location", label)
        changes["address"] = changes.get("address") or venue_address
    for key, value in changes.items():
        if value is None and key not in ("description", "address", "cancellation_deadline_hours", "court_id"):
            continue
        setattr(series, key, value)
    audit.record(db, action="series.updated", entity_type="match_series", entity_id=series.id,
                 actor_user_id=actor.id, group_id=group.id, before=before, after=audit.snapshot(series, fields))
    db.commit()
    return series


def generate_occurrences(
    db: Session, group: FootballGroup, series: MatchSeries, count: int, actor: User, *, commit: bool = True,
    skip_conflicts: bool = False,
) -> list[Match]:
    """Gera as próximas ocorrências. Com quadra, cada data reserva o horário; `skip_conflicts` (agendador) pula
    datas indisponíveis em vez de falhar."""
    if not series.is_active:
        raise BusinessRuleError("Recorrência inativa", code="series_inactive")
    created: list[Match] = []
    previous = series.last_generated_starts_at
    now = utcnow()
    attempts = 0
    while len(created) < count and attempts < count * 4 + 8:
        attempts += 1
        starts_at = next_occurrence(series, previous)
        previous = starts_at
        if starts_at <= now:
            continue  # pula datas que já passaram
        data = MatchCreate(
            name=series.name,
            description=series.description,
            modality=series.modality,
            location=series.location,
            address=series.address,
            court_id=series.court_id,
            price=series.price,
            slots=series.slots,
            field_players_per_team=series.field_players_per_team,
            goalkeepers_per_team=series.goalkeepers_per_team,
            evaluation_window_hours=series.evaluation_window_hours,
            responsible_admin_ids=series.responsible_admin_ids or [],
            starts_at=starts_at,
            duration_minutes=series.duration_minutes,
            confirmation_deadline=starts_at - timedelta(hours=series.confirmation_deadline_hours),
            cancellation_deadline=(
                starts_at - timedelta(hours=series.cancellation_deadline_hours)
                if series.cancellation_deadline_hours is not None
                else None
            ),
            publish=True,
        )
        try:
            created.append(create_match(db, group, data, actor, series=series, commit=False))
        except Conflict as exc:
            if not skip_conflicts:
                raise
            # horário indisponível na quadra: registra e segue para a próxima data
            audit.record(db, action="series.slot_skipped", entity_type="match_series", entity_id=series.id,
                         actor_user_id=None, group_id=group.id,
                         after={"starts_at": starts_at, "reason": exc.details.get("reason")})
        series.last_generated_starts_at = starts_at
    if commit:
        db.commit()
    return created


def auto_generate_due(db: Session) -> int:
    """Tarefa do agendador: mantém `auto_generate_ahead` ocorrências futuras em cada recorrência ativa.

    Ocorrências canceladas não contam (o próximo horário é gerado no lugar). Idempotente: a contagem é
    refeita a cada execução e `last_generated_starts_at` impede datas repetidas.
    """
    now = utcnow()
    rows = db.execute(
        select(MatchSeries, FootballGroup)
        .join(FootballGroup, FootballGroup.id == MatchSeries.group_id)
        .where(MatchSeries.is_active.is_(True), MatchSeries.auto_generate_ahead > 0,
               FootballGroup.is_active.is_(True))
        .with_for_update(of=MatchSeries)
    ).all()
    total = 0
    for series, group in rows:
        upcoming = db.scalar(
            select(func.count()).select_from(Match).where(
                Match.series_id == series.id, Match.starts_at > now, Match.status != MatchStatus.CANCELLED,
            )
        ) or 0
        missing = series.auto_generate_ahead - upcoming
        if missing <= 0:
            continue
        # quem criou a recorrência pode ter deixado de ser administrador: usa só administradores atuais
        admins = set(group_admin_user_ids(db, group.id))
        if not admins:
            continue
        responsible = [u for u in (series.responsible_admin_ids or []) if u in admins]
        if responsible != list(series.responsible_admin_ids or []):
            series.responsible_admin_ids = responsible
        actor_id = series.created_by_id if series.created_by_id in admins else (responsible or sorted(admins))[0]
        created = generate_occurrences(db, group, series, missing, db.get(User, actor_id), commit=False,
                                       skip_conflicts=True)
        for match in created:
            audit.record(db, action="series.auto_generated", entity_type="match", entity_id=match.id,
                         actor_user_id=None, group_id=group.id, after={"series_id": series.id,
                                                                      "starts_at": match.starts_at})
        total += len(created)
    db.commit()
    return total


def list_series(db: Session, group_id: int) -> list[MatchSeries]:
    return list(db.scalars(select(MatchSeries).where(MatchSeries.group_id == group_id).order_by(MatchSeries.id)).all())


def get_series(db: Session, group_id: int, series_id: int) -> MatchSeries:
    from app.core.errors import NotFound

    series = db.get(MatchSeries, series_id)
    if series is None or series.group_id != group_id:
        raise NotFound("Recorrência não encontrada")
    return series
