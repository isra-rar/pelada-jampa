"""Aluguel das quadras (controle manual, sem processar pagamento) e painel do dono do espaço.

Cada pelada realizada numa quadra gera um aluguel para o grupo: preço por hora combinado no vínculo (ou o da
quadra) × duração. O lançamento é feito de forma preguiçosa e idempotente (`sync_rent`), pelo agendador e
sempre que o aluguel ou o painel são consultados.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError, NotFound
from app.domain.enums import (
    AttendanceStatus,
    BookingStatus,
    MatchStatus,
    NotificationType,
    RentStatus,
    VenueLinkStatus,
)
from app.models import Court, CourtBlock, FootballGroup, Match, MatchPlayer, RentCharge, User, Venue, VenueGroup
from app.schemas.venue import RentChargeUpdate
from app.services import audit, notifications
from app.services.stats import BR_TZ
from app.services.venues import courts_of, group_admin_ids, opening_window, staff_user_ids

_CENT = Decimal("0.01")


def _brl(amount: Decimal) -> str:
    return f"R$ {Decimal(amount):.2f}".replace(".", ",")
_HELD = (MatchStatus.PUBLISHED, MatchStatus.FINISHED)


def _local_range(since: date, until: date) -> tuple[datetime, datetime]:
    start = datetime.combine(since, time.min, BR_TZ).astimezone(timezone.utc)
    end = datetime.combine(until + timedelta(days=1), time.min, BR_TZ).astimezone(timezone.utc)
    return start, end


def _month_bounds(month: str | None) -> tuple[str, date, date]:
    today = utcnow().astimezone(BR_TZ).date()
    if month:
        try:
            year, mon = (int(x) for x in month.split("-"))
            first = date(year, mon, 1)
        except ValueError:
            raise BusinessRuleError("Mês no formato AAAA-MM", code="invalid_month") from None
    else:
        first = today.replace(day=1)
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return first.strftime("%Y-%m"), first, nxt - timedelta(days=1)


# ------------------------------------------------------------------ lançamento


def rent_amount(db: Session, group_id: int, court: Court, minutes: int) -> Decimal:
    """Aluguel de uma pelada: preço por hora combinado com o grupo (ou o da quadra) × duração."""
    link = db.scalar(
        select(VenueGroup).where(VenueGroup.group_id == group_id, VenueGroup.venue_id == court.venue_id,
                                 VenueGroup.status.in_((VenueLinkStatus.ACTIVE, VenueLinkStatus.ENDED)))
        .order_by(VenueGroup.id.desc()).limit(1)
    )
    hourly = link.rent_hourly_price if link and link.rent_hourly_price is not None else court.hourly_price
    return (Decimal(hourly) * Decimal(minutes) / Decimal(60)).quantize(_CENT)


def sync_rent(db: Session, *, venue_id: int | None = None, group_id: int | None = None) -> int:
    """Lança o aluguel das peladas que já começaram e cancela o das que foram canceladas. Não faz commit."""
    stmt = (
        select(Match, Court)
        .join(Court, Court.id == Match.court_id)
        .outerjoin(RentCharge, RentCharge.match_id == Match.id)
        .where(RentCharge.id.is_(None), Match.status.in_(_HELD), Match.starts_at <= utcnow(),
               Match.booking_status == BookingStatus.CONFIRMED)  # horário pendente não gera aluguel
    )
    if venue_id is not None:
        stmt = stmt.where(Court.venue_id == venue_id)
    if group_id is not None:
        stmt = stmt.where(Match.group_id == group_id)
    created = 0
    for match, court in db.execute(stmt).all():
        amount = rent_amount(db, match.group_id, court, match.duration_minutes)
        db.add(RentCharge(venue_id=court.venue_id, court_id=court.id, group_id=match.group_id, match_id=match.id,
                          amount=amount, status=RentStatus.PENDING if amount > 0 else RentStatus.EXEMPT))
        created += 1
    cancelled = select(RentCharge).join(Match, Match.id == RentCharge.match_id).where(
        RentCharge.status == RentStatus.PENDING, Match.status == MatchStatus.CANCELLED)
    if venue_id is not None:
        cancelled = cancelled.where(RentCharge.venue_id == venue_id)
    for charge in db.scalars(cancelled).all():
        charge.status = RentStatus.CANCELLED
    db.flush()
    return created


def sync_rent_job(db: Session) -> int:
    """Tarefa do agendador."""
    created = sync_rent(db)
    db.commit()
    return created


# ------------------------------------------------------------------ consulta


def _charges_query():
    return (
        select(RentCharge, Venue.name, Court.name, FootballGroup.name, Match.name, Match.starts_at)
        .join(Venue, Venue.id == RentCharge.venue_id)
        .join(Court, Court.id == RentCharge.court_id)
        .join(FootballGroup, FootballGroup.id == RentCharge.group_id)
        .join(Match, Match.id == RentCharge.match_id)
    )


def _out(row) -> dict:
    c, venue_name, court_name, group_name, match_name, starts_at = row
    return {
        "id": c.id, "venue_id": c.venue_id, "venue_name": venue_name, "court_id": c.court_id,
        "court_name": court_name, "group_id": c.group_id, "group_name": group_name, "match_id": c.match_id,
        "match_name": match_name, "match_starts_at": starts_at, "amount": c.amount, "status": c.status,
        "reported_paid_at": c.reported_paid_at, "paid_at": c.paid_at, "method": c.method, "notes": c.notes,
    }


def list_rent(db: Session, venue: Venue, *, month: str | None, status: RentStatus | None,
              group_id: int | None) -> dict:
    sync_rent(db, venue_id=venue.id)
    db.commit()
    label, first, last = _month_bounds(month)
    start, end = _local_range(first, last)
    stmt = _charges_query().where(RentCharge.venue_id == venue.id, Match.starts_at >= start, Match.starts_at < end)
    if group_id is not None:
        stmt = stmt.where(RentCharge.group_id == group_id)
    rows = db.execute(stmt.order_by(Match.starts_at, RentCharge.id)).all()
    totals = defaultdict(lambda: Decimal("0"))
    groups: dict[int, dict] = {}
    for row in rows:
        c, group_name = row[0], row[3]
        totals[c.status] += c.amount
        g = groups.setdefault(c.group_id, {"group_id": c.group_id, "group_name": group_name, "charges": 0,
                                           "amount": Decimal("0"), "paid": Decimal("0"), "pending": Decimal("0"),
                                           "reported": 0})
        if c.status == RentStatus.CANCELLED:
            continue
        g["charges"] += 1
        g["amount"] += c.amount
        if c.status == RentStatus.PAID:
            g["paid"] += c.amount
        elif c.status == RentStatus.PENDING:
            g["pending"] += c.amount
            g["reported"] += 1 if c.reported_paid_at else 0
    items = [_out(r) for r in rows if status is None or r[0].status == status]
    return {
        "month": label,
        "total": totals[RentStatus.PAID] + totals[RentStatus.PENDING],
        "paid": totals[RentStatus.PAID], "pending": totals[RentStatus.PENDING], "exempt": totals[RentStatus.EXEMPT],
        "items": items,
        "groups": sorted(groups.values(), key=lambda g: (-g["pending"], g["group_name"])),
    }


def get_charge(db: Session, charge_id: int) -> RentCharge:
    charge = db.get(RentCharge, charge_id)
    if charge is None:
        raise NotFound("Aluguel não encontrado")
    return charge


def charge_out(db: Session, charge: RentCharge) -> dict:
    return _out(db.execute(_charges_query().where(RentCharge.id == charge.id)).one())


_FIELDS = ["amount", "status", "paid_at", "method", "notes"]


def _set_status(charge: RentCharge, status: RentStatus, actor: User) -> None:
    charge.status = status
    if status == RentStatus.PAID:
        charge.paid_at = charge.paid_at or utcnow()
        charge.confirmed_by_id = actor.id
    else:
        charge.paid_at = None
        charge.confirmed_by_id = None


def update_charge(db: Session, venue: Venue, charge: RentCharge, data: RentChargeUpdate, actor: User) -> RentCharge:
    if charge.venue_id != venue.id:
        raise NotFound("Aluguel não encontrado")
    before = audit.snapshot(charge, _FIELDS)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("status") is not None and changes["status"] != charge.status:
        _set_status(charge, changes["status"], actor)
    for key in ("amount", "method", "notes"):
        if key in changes and (changes[key] is not None or key != "amount"):
            setattr(charge, key, changes[key])
    after = audit.snapshot(charge, _FIELDS)
    if before != after:
        audit.record(db, action="rent.updated", entity_type="rent_charge", entity_id=charge.id,
                     actor_user_id=actor.id, group_id=charge.group_id, before=before, after=after)
        if charge.status == RentStatus.PAID and before["status"] != RentStatus.PAID:
            _notify_paid(db, venue, [charge])
    db.commit()
    return charge


def settle(db: Session, venue: Venue, charge_ids: list[int], method: str | None, actor: User) -> list[RentCharge]:
    charges = db.scalars(select(RentCharge).where(RentCharge.id.in_(charge_ids),
                                                  RentCharge.venue_id == venue.id)).all()
    if len(charges) != len(set(charge_ids)):
        raise NotFound("Aluguel não encontrado")
    paid = []
    for charge in charges:
        if charge.status == RentStatus.PAID:
            continue  # idempotente
        if charge.status != RentStatus.PENDING:
            raise BusinessRuleError("Só aluguéis pendentes podem ser quitados", code="invalid_status")
        _set_status(charge, RentStatus.PAID, actor)
        charge.method = method or charge.method
        audit.record(db, action="rent.settled", entity_type="rent_charge", entity_id=charge.id,
                     actor_user_id=actor.id, group_id=charge.group_id, after={"amount": charge.amount,
                                                                             "method": charge.method})
        paid.append(charge)
    if paid:
        _notify_paid(db, venue, paid)
    db.commit()
    return list(charges)


def _notify_paid(db: Session, venue: Venue, charges: list[RentCharge]) -> None:
    by_group: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for c in charges:
        by_group[c.group_id] += c.amount
    for group_id, amount in by_group.items():
        notifications.notify(db, group_admin_ids(db, group_id), NotificationType.VENUE,
                             f"{venue.name} confirmou o aluguel",
                             f"Pagamento de {_brl(amount)} registrado.", group_id=group_id)


def group_rent(db: Session, group: FootballGroup) -> dict:
    sync_rent(db, group_id=group.id)
    db.commit()
    base = _charges_query().where(RentCharge.group_id == group.id)
    pending = db.execute(base.where(RentCharge.status == RentStatus.PENDING).order_by(Match.starts_at)).all()
    recent = db.execute(
        base.where(RentCharge.status != RentStatus.PENDING).order_by(Match.starts_at.desc()).limit(20)
    ).all()
    return {
        "pending_amount": sum((r[0].amount for r in pending), Decimal("0")),
        "pending_count": len(pending),
        "items": [_out(r) for r in [*pending, *recent]],
    }


def report_paid(db: Session, group: FootballGroup, charge: RentCharge, actor: User) -> RentCharge:
    """O grupo avisa o espaço que pagou; só o espaço confirma."""
    if charge.group_id != group.id:
        raise NotFound("Aluguel não encontrado")
    if charge.status != RentStatus.PENDING:
        raise BusinessRuleError("Este aluguel não está pendente", code="invalid_status")
    if charge.reported_paid_at is None:
        charge.reported_paid_at = utcnow()
        venue = db.get(Venue, charge.venue_id)
        audit.record(db, action="rent.reported", entity_type="rent_charge", entity_id=charge.id,
                     actor_user_id=actor.id, group_id=group.id)
        notifications.notify(db, staff_user_ids(db, venue.id), NotificationType.VENUE,
                             f"{group.name} informou que pagou o aluguel",
                             f"{_brl(charge.amount)}. Confira e confirme na aba Aluguel.")
    db.commit()
    return charge


# ------------------------------------------------------------------ financeiro (dono)


def _shift_month(first: date, delta: int) -> date:
    index = first.year * 12 + (first.month - 1) + delta
    return date(index // 12, index % 12 + 1, 1)


def finance_overview(db: Session, venue: Venue, months: int) -> dict:
    """Visão consolidada do dono: receita de aluguel por mês e por quadra, recebido × pendente, idade da
    inadimplência e previsão das próximas peladas. Só valores e nomes de grupos; nada de atletas."""
    sync_rent(db, venue_id=venue.id)
    db.commit()
    today = utcnow().astimezone(BR_TZ).date()
    current = today.replace(day=1)
    first = _shift_month(current, -(months - 1))
    start, end = _local_range(first, _shift_month(current, 1) - timedelta(days=1))

    rows = db.execute(
        select(RentCharge, Court.name, Match.starts_at, Match.ends_at)
        .join(Court, Court.id == RentCharge.court_id)
        .join(Match, Match.id == RentCharge.match_id)
        .where(RentCharge.venue_id == venue.id, Match.starts_at >= start, Match.starts_at < end,
               RentCharge.status != RentStatus.CANCELLED)
    ).all()

    zero = Decimal("0")
    series = {
        _shift_month(first, i).strftime("%Y-%m"): {"month": _shift_month(first, i).strftime("%Y-%m"), "matches": 0,
                                                    "expected": zero, "received": zero, "pending": zero,
                                                    "exempt": zero}
        for i in range(months)
    }
    courts: dict[int, dict] = {}
    methods: dict[str, dict] = {}
    billed = 0
    for charge, court_name, starts_at, ends_at in rows:
        bucket = series[starts_at.astimezone(BR_TZ).strftime("%Y-%m")]
        bucket["matches"] += 1
        court = courts.setdefault(charge.court_id, {"court_id": charge.court_id, "name": court_name, "matches": 0,
                                                    "hours": 0.0, "expected": zero, "received": zero})
        court["matches"] += 1
        court["hours"] += (ends_at - starts_at).total_seconds() / 3600
        if charge.status == RentStatus.EXEMPT:
            bucket["exempt"] += charge.amount
            continue
        billed += 1
        bucket["expected"] += charge.amount
        court["expected"] += charge.amount
        if charge.status == RentStatus.PAID:
            bucket["received"] += charge.amount
            court["received"] += charge.amount
            label = charge.method or "não informado"
            m = methods.setdefault(label, {"method": label, "count": 0, "amount": zero})
            m["count"] += 1
            m["amount"] += charge.amount
        elif charge.status == RentStatus.PENDING:
            bucket["pending"] += charge.amount

    pending_rows = db.execute(
        select(RentCharge.group_id, FootballGroup.name, RentCharge.amount, RentCharge.reported_paid_at,
               Match.starts_at)
        .join(FootballGroup, FootballGroup.id == RentCharge.group_id)
        .join(Match, Match.id == RentCharge.match_id)
        .where(RentCharge.venue_id == venue.id, RentCharge.status == RentStatus.PENDING)
    ).all()
    aging = {"upto_30": zero, "from_31_to_60": zero, "over_60": zero}
    debtors: dict[int, dict] = {}
    for group_id, group_name, amount, reported_at, starts_at in pending_rows:
        age = (today - starts_at.astimezone(BR_TZ).date()).days
        aging["upto_30" if age <= 30 else "from_31_to_60" if age <= 60 else "over_60"] += amount
        d = debtors.setdefault(group_id, {"group_id": group_id, "group_name": group_name, "pending_amount": zero,
                                          "charges": 0, "oldest_match_at": starts_at, "reported": 0})
        d["pending_amount"] += amount
        d["charges"] += 1
        d["oldest_match_at"] = min(d["oldest_match_at"], starts_at)
        d["reported"] += 1 if reported_at else 0

    court_ids = [c.id for c in courts_of(db, venue.id)]
    upcoming = db.execute(
        select(Match, Court).join(Court, Court.id == Match.court_id)
        .outerjoin(RentCharge, RentCharge.match_id == Match.id)
        .where(Court.venue_id == venue.id, RentCharge.id.is_(None), Match.status == MatchStatus.PUBLISHED,
               Match.starts_at > utcnow(), Match.booking_status == BookingStatus.CONFIRMED)
    ).all() if court_ids else []
    upcoming_amount = sum((rent_amount(db, m.group_id, c, m.duration_minutes) for m, c in upcoming), zero)

    monthly = list(series.values())
    expected = sum((m["expected"] for m in monthly), zero)
    received = sum((m["received"] for m in monthly), zero)
    return {
        "months": months,
        "series": monthly,
        "expected": expected, "received": received,
        "pending": sum((m["pending"] for m in monthly), zero),
        "exempt": sum((m["exempt"] for m in monthly), zero),
        "collection_rate": _rate(float(received), float(expected)),
        "average_per_match": (expected / billed).quantize(_CENT) if billed else zero,
        "overdue_total": sum(aging.values(), zero), "aging": aging,
        "upcoming_matches": len(upcoming), "upcoming_expected": upcoming_amount,
        "by_court": sorted(({**c, "hours": round(c["hours"], 1)} for c in courts.values()),
                           key=lambda c: -c["expected"]),
        "by_method": sorted(methods.values(), key=lambda m: -m["amount"]),
        "debtors": sorted(debtors.values(), key=lambda d: -d["pending_amount"]),
    }


def export_rent_csv(db: Session, venue: Venue, month: str | None, actor: User) -> tuple[str, str, int]:
    """Aluguel do mês em CSV (mesmo formato do financeiro dos grupos). A exportação é auditada."""
    from app.services import finance_export  # evita import circular

    sync_rent(db, venue_id=venue.id)
    label, first, last = _month_bounds(month)
    start, end = _local_range(first, last)
    rows = db.execute(
        _charges_query().where(RentCharge.venue_id == venue.id, Match.starts_at >= start, Match.starts_at < end)
        .order_by(Match.starts_at, RentCharge.id)
    ).all()
    status_labels = {RentStatus.PENDING: "Pendente", RentStatus.PAID: "Pago", RentStatus.EXEMPT: "Isento",
                     RentStatus.CANCELLED: "Cancelado"}
    content = finance_export._csv(
        ["Data da pelada", "Grupo", "Quadra", "Pelada", "Valor", "Situação", "Grupo informou pagamento em",
         "Pago em", "Forma de pagamento", "Observações"],
        [[starts_at.astimezone(BR_TZ).date(), group_name, court_name, match_name, c.amount,
          status_labels.get(c.status, c.status), c.reported_paid_at, c.paid_at, c.method, c.notes]
         for c, _venue, court_name, group_name, match_name, starts_at in rows],
    )
    audit.record(db, action="venue.finance_exported", entity_type="venue", entity_id=venue.id,
                 actor_user_id=actor.id, group_id=None, after={"month": label, "rows": len(rows)})
    db.commit()
    return content, f"aluguel-{finance_export._slug(venue.name)}-{label}.csv", len(rows)


# ------------------------------------------------------------------ painel


def _overlap_hours(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    seconds = (min(a_end, b_end) - max(a_start, b_start)).total_seconds()
    return max(0.0, seconds / 3600)


def _rate(num: float, den: float) -> float | None:
    return round(num / den, 4) if den > 0 else None


def dashboard(db: Session, venue: Venue, since: date | None, until: date | None) -> dict:
    sync_rent(db, venue_id=venue.id)
    db.commit()
    _, first, last = _month_bounds(None)
    since, until = since or first, until or last
    if until < since or (until - since).days > 366:
        raise BusinessRuleError("Período inválido (máximo de um ano)", code="invalid_period")
    start, end = _local_range(since, until)
    now = utcnow()
    courts = courts_of(db, venue.id, active_only=True)
    all_court_ids = [c.id for c in courts_of(db, venue.id)]

    open_per_court = 0.0
    day = since
    while day <= until:
        window = opening_window(venue, day)
        if window:
            open_per_court += (window[1] - window[0]).total_seconds() / 3600
        day += timedelta(days=1)

    matches = db.execute(
        select(Match, FootballGroup.name).join(FootballGroup, FootballGroup.id == Match.group_id).where(
            Match.court_id.in_(all_court_ids), Match.starts_at < end, Match.ends_at > start)
    ).all() if all_court_ids else []
    blocks = db.scalars(select(CourtBlock).where(
        CourtBlock.court_id.in_(all_court_ids), CourtBlock.starts_at < end, CourtBlock.ends_at > start,
    )).all() if all_court_ids else []

    court_rows, total_open, total_booked, total_blocked = [], 0.0, 0.0, 0.0
    for court in courts:
        booked = sum(_overlap_hours(m.starts_at, m.ends_at, start, end) for m, _ in matches
                     if m.court_id == court.id and m.status != MatchStatus.CANCELLED)
        blocked = sum(_overlap_hours(b.starts_at, b.ends_at, start, end) for b in blocks if b.court_id == court.id)
        total_open += open_per_court
        total_booked += booked
        total_blocked += blocked
        court_rows.append({"court_id": court.id, "name": court.name, "open_hours": round(open_per_court, 1),
                           "booked_hours": round(booked, 1), "blocked_hours": round(blocked, 1),
                           "occupancy_rate": _rate(booked, open_per_court - blocked)})

    in_period = [(m, g) for m, g in matches if start <= m.starts_at < end]
    held = [(m, g) for m, g in in_period if m.status in _HELD and m.starts_at <= now]
    upcoming = [m for m, _ in in_period if m.status != MatchStatus.CANCELLED and m.starts_at > now]
    cancelled = [(m, g) for m, g in in_period if m.status == MatchStatus.CANCELLED]

    filled = dict(db.execute(
        select(MatchPlayer.match_id, func.count()).where(
            MatchPlayer.match_id.in_([m.id for m, _ in held]),
            MatchPlayer.status.in_((AttendanceStatus.CONFIRMED, AttendanceStatus.PRESENT)),
            MatchPlayer.is_referee.is_(False),
        ).group_by(MatchPlayer.match_id)
    ).all()) if held else {}

    rent_rows = db.execute(
        select(RentCharge.status, func.coalesce(func.sum(RentCharge.amount), 0))
        .join(Match, Match.id == RentCharge.match_id)
        .where(RentCharge.venue_id == venue.id, Match.starts_at >= start, Match.starts_at < end)
        .group_by(RentCharge.status)
    ).all()
    rent = {s: Decimal(v) for s, v in rent_rows}
    received = rent.get(RentStatus.PAID, Decimal("0"))
    pending_period = rent.get(RentStatus.PENDING, Decimal("0"))

    overdue = db.execute(
        select(RentCharge.group_id, FootballGroup.name, func.sum(RentCharge.amount), func.count(),
               func.min(Match.starts_at), func.count(RentCharge.reported_paid_at))
        .join(FootballGroup, FootballGroup.id == RentCharge.group_id)
        .join(Match, Match.id == RentCharge.match_id)
        .where(RentCharge.venue_id == venue.id, RentCharge.status == RentStatus.PENDING)
        .group_by(RentCharge.group_id, FootballGroup.name)
    ).all()
    overdue_groups = sorted(
        ({"group_id": gid, "group_name": name, "pending_amount": Decimal(amount), "charges": n,
          "oldest_match_at": oldest, "reported": reported} for gid, name, amount, n, oldest, reported in overdue),
        key=lambda g: -g["pending_amount"],
    )
    pending_by_group = {g["group_id"]: g["pending_amount"] for g in overdue_groups}

    soon = db.execute(
        select(Match, FootballGroup.name, Court.name)
        .join(FootballGroup, FootballGroup.id == Match.group_id).join(Court, Court.id == Match.court_id)
        .where(Match.court_id.in_(all_court_ids), Match.status == MatchStatus.PUBLISHED,
               Match.starts_at > now, Match.starts_at <= now + timedelta(days=7))
        .order_by(Match.starts_at)
    ).all() if all_court_ids else []
    confirmed_soon = dict(db.execute(
        select(MatchPlayer.match_id, func.count()).where(
            MatchPlayer.match_id.in_([m.id for m, _, _ in soon]), MatchPlayer.status == AttendanceStatus.CONFIRMED,
            MatchPlayer.is_referee.is_(False),
        ).group_by(MatchPlayer.match_id)
    ).all()) if soon else {}
    at_risk = [
        {"match_id": m.id, "match_name": m.name, "group_id": m.group_id, "group_name": g, "court_name": c,
         "starts_at": m.starts_at,
         "confirmed": confirmed_soon.get(m.id, 0), "slots": m.slots}
        for m, g, c in soon if confirmed_soon.get(m.id, 0) * 2 < m.slots
    ]

    activity: dict[int, dict] = {}
    for m, name in [*held, *cancelled]:
        a = activity.setdefault(m.group_id, {"group_id": m.group_id, "group_name": name, "matches_held": 0,
                                             "matches_cancelled": 0, "_fill": [], "rent_pending": Decimal("0")})
        if m.status == MatchStatus.CANCELLED:
            a["matches_cancelled"] += 1
        else:
            a["matches_held"] += 1
            a["_fill"].append(min(1.0, filled.get(m.id, 0) / m.slots) if m.slots else 0.0)
    for a in activity.values():
        fills = a.pop("_fill")
        a["fill_rate"] = round(sum(fills) / len(fills), 4) if fills else None
        a["rent_pending"] = pending_by_group.get(a["group_id"], Decimal("0"))

    links = dict(db.execute(
        select(VenueGroup.status, func.count()).where(VenueGroup.venue_id == venue.id).group_by(VenueGroup.status)
    ).all())
    return {
        "since": since, "until": until,
        "open_hours": round(total_open, 1), "booked_hours": round(total_booked, 1),
        "occupancy_rate": _rate(total_booked, total_open - total_blocked),
        "matches_held": len(held), "matches_upcoming": len(upcoming), "matches_cancelled": len(cancelled),
        "cancelled_hours": round(sum((m.ends_at - m.starts_at).total_seconds() / 3600 for m, _ in cancelled), 1),
        "rent_expected": received + pending_period, "rent_received": received, "rent_pending": pending_period,
        "rent_overdue_total": sum((g["pending_amount"] for g in overdue_groups), Decimal("0")),
        "active_groups": links.get(VenueLinkStatus.ACTIVE, 0),
        "pending_requests": links.get(VenueLinkStatus.PENDING, 0),
        "courts": court_rows, "at_risk": at_risk, "overdue_groups": overdue_groups,
        "groups": sorted(activity.values(), key=lambda a: (-a["matches_held"], a["group_name"])),
    }
