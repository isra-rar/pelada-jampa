"""Espaços esportivos (o dono da quadra): cadastro, equipe, quadras, reserva de horários na agenda e vínculo
com os grupos de pelada.

O grupo continua sendo administrado pelos próprios administradores; o espaço enxerga só o que precisa para a
agenda e o aluguel (nada de notas, avaliações ou finanças dos jogadores). Horários de funcionamento são locais
(horário de Brasília).
"""

import secrets
import string
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError, Conflict, Forbidden, NotFound
from app.domain.enums import (
    AttendanceStatus,
    BookingStatus,
    GroupRole,
    MatchStatus,
    MemberStatus,
    NotificationType,
    Recurrence,
    RentStatus,
    VenueLinkOrigin,
    VenueLinkStatus,
    VenueRole,
)
from app.models import (
    Court,
    CourtBlock,
    FootballGroup,
    GroupMember,
    Match,
    MatchPlayer,
    MatchSeries,
    RentCharge,
    User,
    Venue,
    VenueGroup,
    VenueStaff,
)
from app.models.venue import default_opening_hours
from app.schemas.group import GroupCreate
from app.schemas.match import SeriesCreate
from app.schemas.venue import (
    AdminVenueCreate,
    AdminVenueUpdate,
    BlockIn,
    CourtIn,
    CourtUpdate,
    StaffIn,
    StaffUpdate,
    VenueGroupAdminIn,
    VenueGroupCreate,
    VenuePeladaCreate,
    VenueUpdate,
)
from app.services import audit, notifications, users
from app.services.stats import BR_TZ

_LIVE = (VenueLinkStatus.PENDING, VenueLinkStatus.ACTIVE)
_CODE_ALPHABET = string.ascii_uppercase + string.digits


# ------------------------------------------------------------------ acesso


def get_venue(db: Session, venue_id: int) -> Venue:
    venue = db.get(Venue, venue_id)
    if venue is None:
        raise NotFound("Espaço não encontrado")
    return venue


def require_staff(db: Session, venue_id: int, user: User, *, owner: bool = False) -> tuple[Venue, VenueStaff]:
    staff = db.scalar(
        select(VenueStaff).where(VenueStaff.venue_id == venue_id, VenueStaff.user_id == user.id,
                                 VenueStaff.is_active.is_(True))
    )
    if staff is None:
        raise NotFound("Espaço não encontrado")  # não revela o espaço para quem não é da equipe
    if owner and staff.role != VenueRole.OWNER:
        raise Forbidden("Apenas o dono do espaço pode fazer isso")
    return get_venue(db, venue_id), staff


def staff_user_ids(db: Session, venue_id: int) -> list[int]:
    return list(db.scalars(
        select(VenueStaff.user_id).where(VenueStaff.venue_id == venue_id, VenueStaff.is_active.is_(True))
    ).all())


def group_admin_ids(db: Session, group_id: int) -> list[int]:
    return list(db.scalars(
        select(GroupMember.user_id).where(GroupMember.group_id == group_id, GroupMember.role == GroupRole.ADMIN,
                                          GroupMember.status == MemberStatus.ACTIVE)
    ).all())


def _record(db: Session, action: str, entity_type: str, entity_id: int, actor: User | None, *,
            group_id: int | None = None, before: dict | None = None, after: dict | None = None,
            reason: str | None = None) -> None:
    audit.record(db, action=action, entity_type=entity_type, entity_id=entity_id,
                 actor_user_id=actor.id if actor else None, group_id=group_id, before=before, after=after,
                 reason=reason)


# ------------------------------------------------------------------ cadastro


def _new_code(db: Session) -> str:
    while True:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        if not db.scalar(select(Venue.id).where(Venue.link_code == code)):
            return code


def create_venue(db: Session, actor: User, data: AdminVenueCreate) -> Venue:
    """Super-Admin cadastra o espaço e define o dono (DE). Quem ainda não tem conta passa a ter acesso ao criar a
    conta com o mesmo e-mail."""
    venue = Venue(
        name=data.name, description=data.description, address=data.address, phone=data.phone,
        opening_hours=data.opening_hours if data.opening_hours is not None else default_opening_hours(),
        link_code=_new_code(db), created_by_id=actor.id,
    )
    db.add(venue)
    db.flush()
    owner = users.get_or_create_placeholder(db, data.owner_email, data.owner_name)
    db.add(VenueStaff(venue_id=venue.id, user_id=owner.id, role=VenueRole.OWNER))
    _record(db, "venue.created", "venue", venue.id, actor, after={"name": venue.name, "owner_user_id": owner.id})
    notifications.notify(db, [owner.id], NotificationType.VENUE, f"Você é o dono de {venue.name}",
                         "Monte a agenda, crie as peladas e defina quem administra cada uma em Meu espaço.")
    db.commit()
    return venue


def list_my_venues(db: Session, user: User) -> list[tuple[Venue, VenueStaff]]:
    rows = db.execute(
        select(Venue, VenueStaff).join(VenueStaff, VenueStaff.venue_id == Venue.id)
        .where(VenueStaff.user_id == user.id, VenueStaff.is_active.is_(True))
        .order_by(Venue.name)
    ).all()
    return [(v, s) for v, s in rows]


def courts_of(db: Session, venue_id: int, *, active_only: bool = False) -> list[Court]:
    stmt = select(Court).where(Court.venue_id == venue_id)
    if active_only:
        stmt = stmt.where(Court.is_active.is_(True))
    return list(db.scalars(stmt.order_by(Court.name, Court.id)).all())


def venue_out(db: Session, venue: Venue, staff: VenueStaff) -> dict:
    return {
        "id": venue.id, "name": venue.name, "description": venue.description, "address": venue.address,
        "phone": venue.phone, "opening_hours": venue.opening_hours, "is_active": venue.is_active,
        "link_code": venue.link_code, "created_at": venue.created_at, "courts": courts_of(db, venue.id),
        "my_role": staff.role,
    }


def venue_public_out(db: Session, venue: Venue) -> dict:
    return {
        "id": venue.id, "name": venue.name, "address": venue.address, "phone": venue.phone,
        "opening_hours": venue.opening_hours, "courts": courts_of(db, venue.id, active_only=True),
    }


def directory(db: Session, search: str | None) -> list[dict]:
    """Espaços ativos com quadra, para o administrador de um grupo marcar a pelada (o horário fica pendente até o
    dono confirmar, se o grupo não for vinculado ao espaço)."""
    stmt = select(Venue).where(Venue.is_active.is_(True), Venue.id.in_(
        select(Court.venue_id).where(Court.is_active.is_(True))))
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(func.lower(Venue.name).like(like) | func.lower(func.coalesce(Venue.address, "")).like(like))
    return [venue_public_out(db, v) for v in db.scalars(stmt.order_by(Venue.name).limit(50)).all()]


# ------------------------------------------------------------------ gestão (Super-Admin)


def _owners(db: Session, venue_id: int) -> list[VenueStaff]:
    return list(db.scalars(select(VenueStaff).where(
        VenueStaff.venue_id == venue_id, VenueStaff.role == VenueRole.OWNER, VenueStaff.is_active.is_(True),
    ).order_by(VenueStaff.id)).all())


def admin_venue_out(db: Session, venue: Venue) -> dict:
    court_ids = [c.id for c in courts_of(db, venue.id)]
    return {
        "id": venue.id, "name": venue.name, "address": venue.address, "phone": venue.phone,
        "is_active": venue.is_active, "created_at": venue.created_at,
        "owners": [{"staff_id": s.id, "user_id": s.user_id, "name": s.user.name, "email": s.user.email,
                    "claimed": s.user.is_claimed} for s in _owners(db, venue.id)],
        "courts": sum(1 for c in courts_of(db, venue.id) if c.is_active),
        "active_groups": db.scalar(select(func.count()).select_from(VenueGroup).where(
            VenueGroup.venue_id == venue.id, VenueGroup.status == VenueLinkStatus.ACTIVE)) or 0,
        "pending_bookings": db.scalar(select(func.count()).select_from(Match).where(
            Match.court_id.in_(court_ids), Match.booking_status == BookingStatus.PENDING,
            Match.status != MatchStatus.CANCELLED)) or 0 if court_ids else 0,
    }


def admin_list_venues(db: Session, search: str | None, page: int, size: int) -> tuple[list[dict], int]:
    stmt = select(Venue)
    if search:
        like = f"%{search.lower()}%"
        owner_match = select(VenueStaff.venue_id).join(User, User.id == VenueStaff.user_id).where(
            VenueStaff.role == VenueRole.OWNER, VenueStaff.is_active.is_(True),
            func.lower(User.name).like(like) | func.lower(User.email).like(like))
        stmt = stmt.where(func.lower(Venue.name).like(like) | Venue.id.in_(owner_match))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Venue.id.desc()).offset((page - 1) * size).limit(size)).all()
    return [admin_venue_out(db, v) for v in rows], total


def admin_update_venue(db: Session, venue: Venue, data: AdminVenueUpdate, actor: User) -> Venue:
    fields = ["name", "address", "phone", "is_active"]
    before = audit.snapshot(venue, fields)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("is_active") is False and venue.is_active and _future_bookings(
        db, [c.id for c in courts_of(db, venue.id)]
    ):
        raise BusinessRuleError("Há peladas marcadas nas quadras. Combine com o dono antes de desativar.",
                                code="venue_in_use")
    for key, value in changes.items():
        if value is None and key not in ("address", "phone"):
            continue
        setattr(venue, key, value)
    _record(db, "platform.venue_updated", "venue", venue.id, actor, before=before,
            after=audit.snapshot(venue, fields))
    db.commit()
    return venue


def admin_add_owner(db: Session, venue: Venue, email: str, name: str, actor: User) -> VenueStaff:
    """Define mais um dono (DE) do espaço. Quem já é da equipe como funcionário vira dono."""
    user = users.get_or_create_placeholder(db, email, name)
    staff = db.scalar(select(VenueStaff).where(VenueStaff.venue_id == venue.id, VenueStaff.user_id == user.id))
    if staff is not None and staff.is_active and staff.role == VenueRole.OWNER:
        raise Conflict("Esta pessoa já é dona do espaço", code="already_owner")
    if staff is None:
        staff = VenueStaff(venue_id=venue.id, user_id=user.id)
        db.add(staff)
    staff.role = VenueRole.OWNER
    staff.is_active = True
    db.flush()
    _record(db, "platform.venue_owner_added", "venue_staff", staff.id, actor, after={"user_id": user.id})
    notifications.notify(db, [user.id], NotificationType.VENUE, f"Você é o dono de {venue.name}",
                         "Monte a agenda, crie as peladas e defina quem administra cada uma em Meu espaço.")
    db.commit()
    return staff


def admin_remove_owner(db: Session, venue: Venue, staff_id: int, actor: User) -> None:
    staff = get_staff(db, venue, staff_id)
    if staff.role != VenueRole.OWNER or not staff.is_active:
        raise NotFound("Dono não encontrado neste espaço")
    if _active_owners(db, venue.id) <= 1:
        raise BusinessRuleError("O espaço precisa de ao menos um dono", code="last_owner")
    staff.is_active = False
    _record(db, "platform.venue_owner_removed", "venue_staff", staff.id, actor, before={"user_id": staff.user_id})
    db.commit()


def _future_bookings(db: Session, court_ids: list[int]) -> int:
    if not court_ids:
        return 0
    return db.scalar(select(func.count()).where(
        Match.court_id.in_(court_ids), Match.status != MatchStatus.CANCELLED, Match.ends_at > utcnow(),
    )) or 0


def update_venue(db: Session, venue: Venue, staff: VenueStaff, data: VenueUpdate, actor: User) -> Venue:
    fields = ["name", "description", "address", "phone", "opening_hours", "is_active"]
    before = audit.snapshot(venue, fields)
    changes = data.model_dump(exclude_unset=True)
    if "is_active" in changes and changes["is_active"] is not None and changes["is_active"] != venue.is_active:
        if staff.role != VenueRole.OWNER:
            raise Forbidden("Apenas o dono pode desativar ou reativar o espaço")
        if not changes["is_active"] and _future_bookings(db, [c.id for c in courts_of(db, venue.id)]):
            raise BusinessRuleError("Há peladas marcadas nas quadras. Combine com os grupos antes de desativar.",
                                    code="venue_in_use")
    if changes.get("opening_hours") is not None and changes["opening_hours"] != venue.opening_hours:
        _check_hours_keep_bookings(db, venue, changes["opening_hours"])
    for key, value in changes.items():
        if value is None and key not in ("description", "address", "phone"):
            continue
        setattr(venue, key, value)
    after = audit.snapshot(venue, fields)
    if before != after:
        _record(db, "venue.updated", "venue", venue.id, actor, before=before, after=after)
    db.commit()
    return venue


def _check_hours_keep_bookings(db: Session, venue: Venue, hours: dict) -> None:
    """Novo horário de funcionamento não pode deixar de fora peladas já marcadas."""
    court_ids = [c.id for c in courts_of(db, venue.id)]
    if not court_ids:
        return
    probe = Venue(opening_hours=hours)
    outside = []
    for match in db.scalars(select(Match).where(
        Match.court_id.in_(court_ids), Match.status != MatchStatus.CANCELLED, Match.starts_at > utcnow(),
    ).order_by(Match.starts_at)).all():
        window = opening_window(probe, match.starts_at.astimezone(BR_TZ).date())
        if window is None or match.starts_at < window[0] or match.ends_at > window[1]:
            outside.append(match)
    if outside:
        first = outside[0].starts_at.astimezone(BR_TZ).strftime("%d/%m %H:%M")
        raise BusinessRuleError(
            f"{len(outside)} pelada(s) marcada(s) ficariam fora do novo horário (a primeira em {first}). "
            "Combine com os grupos antes de mudar.",
            code="hours_conflict", details={"matches": [m.id for m in outside]},
        )


def rotate_code(db: Session, venue: Venue, actor: User) -> Venue:
    venue.link_code = _new_code(db)
    _record(db, "venue.code_rotated", "venue", venue.id, actor)
    db.commit()
    return venue


# ------------------------------------------------------------------ quadras


def get_court(db: Session, venue: Venue, court_id: int) -> Court:
    court = db.get(Court, court_id)
    if court is None or court.venue_id != venue.id:
        raise NotFound("Quadra não encontrada")
    return court


def add_court(db: Session, venue: Venue, data: CourtIn, actor: User) -> Court:
    court = Court(venue_id=venue.id, **data.model_dump())
    db.add(court)
    db.flush()
    _record(db, "court.created", "court", court.id, actor, after=data.model_dump())
    db.commit()
    return court


def update_court(db: Session, venue: Venue, court: Court, data: CourtUpdate, actor: User) -> Court:
    fields = ["name", "modality", "hourly_price", "is_active"]
    before = audit.snapshot(court, fields)
    changes = data.model_dump(exclude_none=True)
    if changes.get("is_active") is False and court.is_active and _future_bookings(db, [court.id]):
        raise BusinessRuleError("Há peladas marcadas nesta quadra", code="court_in_use")
    for key, value in changes.items():
        setattr(court, key, value)
    _record(db, "court.updated", "court", court.id, actor, before=before, after=audit.snapshot(court, fields))
    db.commit()
    return court


# ------------------------------------------------------------------ equipe


def staff_out(s: VenueStaff) -> dict:
    return {"id": s.id, "user_id": s.user_id, "name": s.user.name, "email": s.user.email, "role": s.role,
            "is_active": s.is_active, "created_at": s.created_at}


def list_staff(db: Session, venue: Venue) -> list[VenueStaff]:
    return list(db.scalars(
        select(VenueStaff).where(VenueStaff.venue_id == venue.id).order_by(VenueStaff.role.desc(), VenueStaff.id)
    ).all())


def _active_owners(db: Session, venue_id: int) -> int:
    return db.scalar(select(func.count()).where(
        VenueStaff.venue_id == venue_id, VenueStaff.role == VenueRole.OWNER, VenueStaff.is_active.is_(True),
    )) or 0


def add_staff(db: Session, venue: Venue, data: StaffIn, actor: User) -> VenueStaff:
    user = users.get_or_create_placeholder(db, data.email, data.name)
    staff = db.scalar(select(VenueStaff).where(VenueStaff.venue_id == venue.id, VenueStaff.user_id == user.id))
    if staff is not None and staff.is_active:
        raise Conflict("Esta pessoa já faz parte da equipe", code="already_staff")
    if staff is None:
        staff = VenueStaff(venue_id=venue.id, user_id=user.id)
        db.add(staff)
    staff.role = data.role
    staff.is_active = True
    db.flush()
    _record(db, "venue.staff_added", "venue_staff", staff.id, actor, after={"user_id": user.id, "role": data.role})
    notifications.notify(db, [user.id], NotificationType.VENUE, f"Você entrou na equipe de {venue.name}",
                         "Agora você acompanha a agenda, os grupos e o aluguel do espaço.")
    db.commit()
    return staff


def update_staff(db: Session, venue: Venue, staff: VenueStaff, data: StaffUpdate, actor: User) -> VenueStaff:
    before = {"role": staff.role, "is_active": staff.is_active}
    losing_owner = staff.role == VenueRole.OWNER and staff.is_active and (
        data.role == VenueRole.MANAGER or data.is_active is False
    )
    if losing_owner and _active_owners(db, venue.id) <= 1:
        raise BusinessRuleError("O espaço precisa de ao menos um dono", code="last_owner")
    if data.role is not None:
        staff.role = data.role
    if data.is_active is not None:
        staff.is_active = data.is_active
    _record(db, "venue.staff_updated", "venue_staff", staff.id, actor, before=before,
            after={"role": staff.role, "is_active": staff.is_active})
    db.commit()
    return staff


def get_staff(db: Session, venue: Venue, staff_id: int) -> VenueStaff:
    staff = db.get(VenueStaff, staff_id)
    if staff is None or staff.venue_id != venue.id:
        raise NotFound("Pessoa não encontrada na equipe")
    return staff


# ------------------------------------------------------------------ horários


def opening_window(venue: Venue, day: date) -> tuple[datetime, datetime] | None:
    """Abertura e fechamento (UTC) do espaço num dia local; None se fechado."""
    interval = (venue.opening_hours or {}).get(str(day.weekday()))
    if not interval:
        return None

    def at(hhmm: str) -> datetime:
        if hhmm == "24:00":
            return datetime.combine(day + timedelta(days=1), time.min, BR_TZ).astimezone(timezone.utc)
        h, m = (int(x) for x in hhmm.split(":"))
        return datetime.combine(day, time(h, m), BR_TZ).astimezone(timezone.utc)

    return at(interval[0]), at(interval[1])


def _unavailable(message: str, reason: str, **details) -> Conflict:
    return Conflict(message, code="court_unavailable", details={"reason": reason, **details})


def check_slot(db: Session, court: Court, starts_at: datetime, ends_at: datetime, *,
               exclude_match_id: int | None = None) -> None:
    """Garante que a quadra está livre e o espaço aberto no horário (pelada não ocupa horário já reservado)."""
    venue = db.get(Venue, court.venue_id)
    if not venue.is_active or not court.is_active:
        raise _unavailable("Quadra indisponível", "inactive")
    window = opening_window(venue, starts_at.astimezone(BR_TZ).date())
    if window is None or starts_at < window[0] or ends_at > window[1]:
        raise _unavailable(
            "O espaço está fechado nesse horário", "closed",
            opens_at=window[0].isoformat() if window else None, closes_at=window[1].isoformat() if window else None,
        )
    stmt = select(Match).where(
        Match.court_id == court.id, Match.status != MatchStatus.CANCELLED,
        Match.starts_at < ends_at, Match.ends_at > starts_at,
    )
    if exclude_match_id is not None:
        stmt = stmt.where(Match.id != exclude_match_id)
    other = db.scalar(stmt.limit(1))
    if other is not None:
        raise _unavailable("A quadra já está reservada nesse horário", "booked",
                           starts_at=other.starts_at.isoformat(), ends_at=other.ends_at.isoformat())
    block = db.scalar(select(CourtBlock).where(
        CourtBlock.court_id == court.id, CourtBlock.starts_at < ends_at, CourtBlock.ends_at > starts_at,
    ).limit(1))
    if block is not None:
        raise _unavailable(f"Horário bloqueado pelo espaço: {block.reason}", "blocked",
                           starts_at=block.starts_at.isoformat(), ends_at=block.ends_at.isoformat())


def active_link(db: Session, group_id: int) -> VenueGroup | None:
    return db.scalar(select(VenueGroup).where(VenueGroup.group_id == group_id,
                                              VenueGroup.status == VenueLinkStatus.ACTIVE))


def court_for_group(db: Session, group: FootballGroup, court_id: int) -> Court:
    """Quadra do espaço vinculado ao grupo (recorrências só reservam no espaço vinculado)."""
    court = db.get(Court, court_id)
    link = active_link(db, group.id)
    if court is None or link is None or court.venue_id != link.venue_id:
        raise BusinessRuleError("Quadra não pertence ao espaço vinculado ao grupo", code="court_not_linked")
    return court


def court_for_booking(db: Session, group: FootballGroup, court_id: int) -> tuple[Court, BookingStatus]:
    """Quadra de qualquer espaço ativo para uma pelada avulsa. No espaço vinculado ao grupo o horário já entra
    confirmado; em outro espaço fica pendente até o dono (DE) confirmar data e horário."""
    court = db.get(Court, court_id)
    venue = db.get(Venue, court.venue_id) if court is not None else None
    if court is None or venue is None or not venue.is_active or not court.is_active:
        raise BusinessRuleError("Quadra indisponível", code="court_not_found")
    link = active_link(db, group.id)
    linked = link is not None and link.venue_id == court.venue_id
    return court, BookingStatus.CONFIRMED if linked else BookingStatus.PENDING


def notify_booking_request(db: Session, group: FootballGroup, match: Match) -> None:
    court = db.get(Court, match.court_id)
    venue = db.get(Venue, court.venue_id)
    notifications.notify(db, staff_user_ids(db, venue.id), NotificationType.VENUE,
                         f"{group.name} pediu horário em {venue.name}",
                         f"{notifications.when(match.starts_at)} na {court.name}. Confirme ou recuse na Agenda.")


def court_label(db: Session, court: Court) -> tuple[str, str | None]:
    venue = db.get(Venue, court.venue_id)
    return f"{venue.name} · {court.name}"[:160], venue.address


# ------------------------------------------------------------------ bloqueios


def add_block(db: Session, venue: Venue, data: BlockIn, actor: User) -> CourtBlock:
    court = get_court(db, venue, data.court_id)
    if data.starts_at.tzinfo is None or data.ends_at.tzinfo is None:
        raise BusinessRuleError("Datas devem incluir fuso horário", code="naive_datetime")
    if data.ends_at <= data.starts_at:
        raise BusinessRuleError("O término deve ser posterior ao início", code="invalid_schedule")
    booked = db.scalar(select(Match).where(
        Match.court_id == court.id, Match.status != MatchStatus.CANCELLED,
        Match.starts_at < data.ends_at, Match.ends_at > data.starts_at,
    ).limit(1))
    if booked is not None:
        raise Conflict("Há pelada marcada nesse horário. Combine com o grupo antes de bloquear.",
                       code="block_conflicts_match", details={"match_id": booked.id})
    block = CourtBlock(court_id=court.id, starts_at=data.starts_at, ends_at=data.ends_at, reason=data.reason,
                       created_by_id=actor.id)
    db.add(block)
    db.flush()
    _record(db, "court.blocked", "court_block", block.id, actor, after=data.model_dump())
    db.commit()
    return block


def delete_block(db: Session, venue: Venue, block_id: int, actor: User) -> None:
    block = db.get(CourtBlock, block_id)
    if block is None or db.get(Court, block.court_id).venue_id != venue.id:
        raise NotFound("Bloqueio não encontrado")
    _record(db, "court.unblocked", "court_block", block.id, actor,
            before={"court_id": block.court_id, "starts_at": block.starts_at, "reason": block.reason})
    db.delete(block)
    db.commit()


# ------------------------------------------------------------------ agenda


def _subtract(start: datetime, end: datetime, busy: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    free, cursor = [], start
    for b_start, b_end in sorted(busy):
        if b_end <= cursor or b_start >= end:
            continue
        if b_start > cursor:
            free.append((cursor, b_start))
        cursor = max(cursor, b_end)
    if cursor < end:
        free.append((cursor, end))
    return [(a, b) for a, b in free if (b - a) >= timedelta(minutes=30)]


def calendar(db: Session, venue: Venue, start: date, days: int, *, viewer_group_id: int | None = None) -> dict:
    """Agenda por dia e quadra: peladas, bloqueios e horários livres. Na visão de um grupo (`viewer_group_id`) as
    peladas dos outros grupos aparecem só como "Ocupado"."""
    courts = courts_of(db, venue.id, active_only=True)
    court_ids = [c.id for c in courts]
    range_start = datetime.combine(start, time.min, BR_TZ).astimezone(timezone.utc)
    range_end = datetime.combine(start + timedelta(days=days), time.min, BR_TZ).astimezone(timezone.utc)
    matches = db.execute(
        select(Match, FootballGroup.name).join(FootballGroup, FootballGroup.id == Match.group_id).where(
            Match.court_id.in_(court_ids), Match.status != MatchStatus.CANCELLED,
            Match.starts_at < range_end, Match.ends_at > range_start,
        )
    ).all() if court_ids else []
    blocks = db.scalars(select(CourtBlock).where(
        CourtBlock.court_id.in_(court_ids), CourtBlock.starts_at < range_end, CourtBlock.ends_at > range_start,
    )).all() if court_ids else []
    confirmed = dict(db.execute(
        select(MatchPlayer.match_id, func.count()).where(
            MatchPlayer.match_id.in_([m.id for m, _ in matches]),
            MatchPlayer.status.in_((AttendanceStatus.CONFIRMED, AttendanceStatus.PRESENT)),
            MatchPlayer.is_referee.is_(False),  # juiz tem vaga própria, fora das vagas de jogador
        ).group_by(MatchPlayer.match_id)
    ).all()) if matches else {}

    now = utcnow()
    # o que já passou não está livre: hoje, a agenda livre começa na próxima meia hora
    now = (now + timedelta(minutes=(30 - now.minute % 30) % 30)).replace(second=0, microsecond=0)
    schedule = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        day_start = datetime.combine(day, time.min, BR_TZ).astimezone(timezone.utc)
        day_end = day_start + timedelta(days=1)
        window = opening_window(venue, day)
        per_court = []
        for court in courts:
            items, busy = [], []
            for m, group_name in matches:
                if m.court_id != court.id or not (m.starts_at < day_end and m.ends_at > day_start):
                    continue
                mine = viewer_group_id is not None and m.group_id == viewer_group_id
                visible = viewer_group_id is None or mine
                items.append({
                    "kind": "match", "id": m.id, "starts_at": m.starts_at, "ends_at": m.ends_at,
                    "title": m.name if visible else "Ocupado",
                    "group_id": m.group_id if visible else None, "group_name": group_name if visible else None,
                    "match_status": m.status if visible else None,
                    "confirmed": confirmed.get(m.id, 0) if visible else None, "slots": m.slots if visible else None,
                    "series_id": m.series_id if visible else None, "mine": mine,
                    "booking_status": m.booking_status if visible else None,
                })
                busy.append((m.starts_at, m.ends_at))
            for b in blocks:
                if b.court_id != court.id or not (b.starts_at < day_end and b.ends_at > day_start):
                    continue
                items.append({"kind": "block", "id": b.id, "starts_at": b.starts_at, "ends_at": b.ends_at,
                              "title": b.reason})
                busy.append((b.starts_at, b.ends_at))
            items.sort(key=lambda i: i["starts_at"])
            free_from = max(window[0], now) if window else None
            free = _subtract(free_from, window[1], busy) if window and free_from < window[1] else []
            per_court.append({"court_id": court.id, "items": items,
                              "free": [{"starts_at": a, "ends_at": b} for a, b in free]})
        schedule.append({"date": day, "opens_at": window[0] if window else None,
                         "closes_at": window[1] if window else None, "courts": per_court})
    return {"start": start, "days": days, "courts": courts, "schedule": schedule}


# ------------------------------------------------------------------ vínculo com grupos


def get_link(db: Session, venue: Venue, link_id: int) -> VenueGroup:
    link = db.get(VenueGroup, link_id)
    if link is None or link.venue_id != venue.id:
        raise NotFound("Grupo não encontrado neste espaço")
    return link


def link_out(db: Session, link: VenueGroup) -> dict:
    venue = db.get(Venue, link.venue_id)
    group = db.get(FootballGroup, link.group_id)
    # contato dos administradores só enquanto o pedido está aberto ou o vínculo ativo: grupos recusados ou
    # que já saíram do espaço deixam de ser visíveis para o dono
    admins = db.execute(
        select(User).join(GroupMember, GroupMember.user_id == User.id).where(
            GroupMember.group_id == group.id, GroupMember.role == GroupRole.ADMIN,
            GroupMember.status == MemberStatus.ACTIVE,
        ).order_by(User.name)
    ).scalars().all() if link.status in _LIVE else []
    active_members = db.scalar(select(func.count()).where(
        GroupMember.group_id == group.id, GroupMember.status == MemberStatus.ACTIVE)) or 0
    court_ids = [c.id for c in courts_of(db, venue.id)]
    next_match_at = db.scalar(select(func.min(Match.starts_at)).where(
        Match.group_id == group.id, Match.court_id.in_(court_ids), Match.status != MatchStatus.CANCELLED,
        Match.starts_at > utcnow(),
    )) if court_ids else None
    rent_pending = db.scalar(select(func.coalesce(func.sum(RentCharge.amount), 0)).where(
        RentCharge.venue_id == venue.id, RentCharge.group_id == group.id, RentCharge.status == RentStatus.PENDING,
    ))
    return {
        "id": link.id, "venue_id": venue.id, "venue_name": venue.name, "group_id": group.id,
        "group_name": group.name, "status": link.status, "origin": link.origin,
        "rent_hourly_price": link.rent_hourly_price, "message": link.message, "created_at": link.created_at,
        "decided_at": link.decided_at, "ended_at": link.ended_at,
        "admins": [{"name": u.name, "email": u.email, "phone": u.phone} for u in admins],
        "active_members": active_members, "next_match_at": next_match_at, "rent_pending": rent_pending or 0,
    }


def list_links(db: Session, venue: Venue, status: VenueLinkStatus | None) -> list[VenueGroup]:
    stmt = select(VenueGroup).where(VenueGroup.venue_id == venue.id)
    stmt = stmt.where(VenueGroup.status == status) if status else stmt.where(VenueGroup.status.in_(_LIVE))
    return list(db.scalars(stmt.order_by(VenueGroup.status.desc(), VenueGroup.id.desc())).all())


def _live_link(db: Session, group_id: int) -> VenueGroup | None:
    return db.scalar(select(VenueGroup).where(VenueGroup.group_id == group_id, VenueGroup.status.in_(_LIVE)))


def create_group(db: Session, venue: Venue, data: VenueGroupCreate, actor: User, *,
                 commit: bool = True) -> VenueGroup:
    """O espaço cria a pelada e define quem a administra (o administrador cuida do dia a dia)."""
    from app.services import groups  # evita import circular

    admin_user = users.get_or_create_placeholder(db, data.admin_email, data.admin_name)
    group, _ = groups.create_group(
        db, admin_user, GroupCreate(name=data.name, description=data.description, modality=data.modality),
        actor=actor, commit=False,
    )
    link = VenueGroup(venue_id=venue.id, group_id=group.id, status=VenueLinkStatus.ACTIVE,
                      origin=VenueLinkOrigin.VENUE, rent_hourly_price=data.rent_hourly_price,
                      requested_by_id=actor.id, decided_by_id=actor.id, decided_at=utcnow())
    db.add(link)
    db.flush()
    _record(db, "venue.group_created", "venue_group", link.id, actor, group_id=group.id,
            after={"venue_id": venue.id, "admin_user_id": admin_user.id})
    notifications.notify(db, [admin_user.id], NotificationType.MEMBERSHIP, f"Você administra {group.name}",
                         f"{venue.name} criou a pelada e definiu você como administrador.", group_id=group.id)
    if commit:
        db.commit()
    return link


def request_link(db: Session, group: FootballGroup, actor: User, code: str, message: str | None) -> VenueGroup:
    venue = db.scalar(select(Venue).where(Venue.link_code == code.strip().upper(), Venue.is_active.is_(True)))
    if venue is None:
        raise NotFound("Código de espaço inválido", code="invalid_venue_code")
    if _live_link(db, group.id) is not None:
        raise Conflict("O grupo já tem um espaço vinculado ou um pedido em andamento", code="already_linked")
    link = VenueGroup(venue_id=venue.id, group_id=group.id, status=VenueLinkStatus.PENDING,
                      origin=VenueLinkOrigin.REQUEST, message=message, requested_by_id=actor.id)
    db.add(link)
    db.flush()
    _record(db, "venue.link_requested", "venue_group", link.id, actor, group_id=group.id,
            after={"venue_id": venue.id, "venue_name": venue.name})
    notifications.notify(db, staff_user_ids(db, venue.id), NotificationType.VENUE,
                         f"{group.name} quer jogar em {venue.name}",
                         message or "Um grupo pediu vínculo com o espaço. Aprove ou recuse na aba Grupos.")
    db.commit()
    return link


def decide_link(db: Session, venue: Venue, link: VenueGroup, approve: bool, actor: User) -> VenueGroup:
    if link.status != VenueLinkStatus.PENDING:
        raise BusinessRuleError("O pedido já foi respondido", code="invalid_status")
    if approve and active_link(db, link.group_id) is not None:
        raise Conflict("O grupo já está vinculado a outro espaço", code="already_linked")
    link.status = VenueLinkStatus.ACTIVE if approve else VenueLinkStatus.REJECTED
    link.decided_by_id = actor.id
    link.decided_at = utcnow()
    group = db.get(FootballGroup, link.group_id)
    if approve:
        _confirm_pending_of_group(db, venue, group.id)
    _record(db, "venue.link_approved" if approve else "venue.link_rejected", "venue_group", link.id, actor,
            group_id=group.id, after={"venue_id": venue.id})
    notifications.notify(
        db, group_admin_ids(db, group.id), NotificationType.VENUE,
        f"{venue.name} {'aprovou' if approve else 'recusou'} o vínculo",
        "Agora dá para marcar as peladas nas quadras do espaço." if approve
        else "O pedido de vínculo do grupo foi recusado.",
        group_id=group.id,
    )
    db.commit()
    return link


def end_link(db: Session, link: VenueGroup, actor: User, *, by_venue: bool) -> VenueGroup:
    """Encerra o vínculo (ou cancela o pedido). As próximas peladas do grupo deixam de ocupar as quadras; o
    histórico e o aluguel já lançado continuam."""
    if link.status not in _LIVE:
        raise BusinessRuleError("O vínculo já foi encerrado", code="invalid_status")
    if not by_venue and link.origin == VenueLinkOrigin.VENUE and link.status == VenueLinkStatus.ACTIVE:
        raise Forbidden("Esta pelada foi criada pelo espaço: só o espaço pode encerrar o vínculo",
                        code="venue_owned_group")
    venue = db.get(Venue, link.venue_id)
    group = db.get(FootballGroup, link.group_id)
    was_active = link.status == VenueLinkStatus.ACTIVE
    link.status = VenueLinkStatus.ENDED
    link.ended_at = utcnow()
    released = 0
    if was_active:
        court_ids = [c.id for c in courts_of(db, venue.id)]
        for match in db.scalars(select(Match).where(
            Match.group_id == group.id, Match.court_id.in_(court_ids), Match.status != MatchStatus.CANCELLED,
            Match.starts_at > utcnow(),
        )).all():
            match.court_id = None  # o local (texto) continua; o horário sai da agenda do espaço
            match.booking_status = None
            released += 1
        for series in db.scalars(select(MatchSeries).where(MatchSeries.group_id == group.id,
                                                           MatchSeries.court_id.in_(court_ids))).all():
            series.court_id = None
    _record(db, "venue.link_ended", "venue_group", link.id, actor, group_id=group.id,
            after={"venue_id": venue.id, "by_venue": by_venue, "released_matches": released})
    if was_active:
        if by_venue:
            notifications.notify(db, group_admin_ids(db, group.id), NotificationType.VENUE,
                                 f"{venue.name} encerrou o vínculo",
                                 "As próximas peladas do grupo não estão mais reservadas nas quadras do espaço.",
                                 group_id=group.id)
        else:
            notifications.notify(db, staff_user_ids(db, venue.id), NotificationType.VENUE,
                                 f"{group.name} saiu de {venue.name}",
                                 "Os horários reservados pelo grupo foram liberados na agenda.")
    db.commit()
    return link


def update_link(db: Session, link: VenueGroup, rent_hourly_price, actor: User) -> VenueGroup:
    before = {"rent_hourly_price": link.rent_hourly_price}
    link.rent_hourly_price = rent_hourly_price
    _record(db, "venue.link_updated", "venue_group", link.id, actor, group_id=link.group_id, before=before,
            after={"rent_hourly_price": rent_hourly_price})
    db.commit()
    return link


def set_group_admin(db: Session, venue: Venue, link: VenueGroup, data: VenueGroupAdminIn, actor: User) -> VenueGroup:
    """Pelada criada pelo espaço: o espaço escolhe (ou troca) quem administra."""
    from app.services import groups  # evita import circular

    if link.status != VenueLinkStatus.ACTIVE or link.origin != VenueLinkOrigin.VENUE:
        raise Forbidden("Só é possível definir o administrador de peladas criadas pelo espaço",
                        code="not_venue_owned")
    group = db.get(FootballGroup, link.group_id)
    user = users.get_or_create_placeholder(db, data.email, data.name)
    member = db.scalar(select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.user_id == user.id))
    if member is None:
        member = GroupMember(group_id=group.id, user_id=user.id)
        db.add(member)
    was_active = member.status == MemberStatus.ACTIVE if member.id else False
    member.role = GroupRole.ADMIN
    member.status = MemberStatus.ACTIVE
    db.flush()
    if not was_active:
        groups.on_member_activated(db, group, member)
    demoted = []
    if data.replace:
        for other in db.scalars(select(GroupMember).where(
            GroupMember.group_id == group.id, GroupMember.role == GroupRole.ADMIN,
            GroupMember.status == MemberStatus.ACTIVE, GroupMember.id != member.id,
        )).all():
            other.role = GroupRole.PLAYER
            demoted.append(other.user_id)
    _record(db, "venue.group_admin_set", "group_member", member.id, actor, group_id=group.id,
            after={"user_id": user.id, "replaced_user_ids": demoted})
    notifications.notify(db, [user.id], NotificationType.MEMBERSHIP, f"Você administra {group.name}",
                         f"{venue.name} definiu você como administrador da pelada.", group_id=group.id)
    if demoted:
        notifications.notify(db, demoted, NotificationType.MEMBERSHIP, f"Você não administra mais {group.name}",
                             f"{venue.name} definiu outra pessoa para administrar a pelada.", group_id=group.id)
    db.commit()
    return link


def group_venue(db: Session, group: FootballGroup, member: GroupMember) -> dict:
    link = _live_link(db, group.id)
    if link is None:
        return {}
    venue = db.get(Venue, link.venue_id)
    return {
        "link_id": link.id, "status": link.status, "origin": link.origin,
        "venue": venue_public_out(db, venue),
        "rent_hourly_price": link.rent_hourly_price if member.is_admin else None,
    }


def group_link_for_admin(db: Session, group: FootballGroup) -> VenueGroup:
    link = _live_link(db, group.id)
    if link is None:
        raise NotFound("O grupo não tem espaço vinculado", code="no_venue")
    return link


# ------------------------------------------------------------------ horários pedidos por grupos sem vínculo


def _venue_match(db: Session, venue: Venue, match_id: int) -> Match:
    match = db.get(Match, match_id)
    court = db.get(Court, match.court_id) if match is not None and match.court_id is not None else None
    if court is None or court.venue_id != venue.id:
        raise NotFound("Pelada não encontrada na agenda do espaço")
    return match


def booking_out(db: Session, match: Match) -> dict:
    group = db.get(FootballGroup, match.group_id)
    court = db.get(Court, match.court_id) if match.court_id is not None else None
    pending = match.booking_status == BookingStatus.PENDING
    admins = db.execute(
        select(User).join(GroupMember, GroupMember.user_id == User.id).where(
            GroupMember.group_id == group.id, GroupMember.role == GroupRole.ADMIN,
            GroupMember.status == MemberStatus.ACTIVE,
        ).order_by(User.name)
    ).scalars().all() if pending else []  # contato só enquanto o pedido está aberto
    confirmed = db.scalar(select(func.count()).select_from(MatchPlayer).where(
        MatchPlayer.match_id == match.id, MatchPlayer.is_referee.is_(False),
        MatchPlayer.status.in_((AttendanceStatus.CONFIRMED, AttendanceStatus.PRESENT)))) or 0
    return {
        "match_id": match.id, "match_name": match.name, "group_id": group.id, "group_name": group.name,
        "court_id": court.id if court else None, "court_name": court.name if court else None,
        "starts_at": match.starts_at, "ends_at": match.ends_at, "booking_status": match.booking_status,
        "confirmed": confirmed, "slots": match.slots, "created_at": match.created_at,
        "admins": [{"name": u.name, "email": u.email, "phone": u.phone} for u in admins],
    }


def list_bookings(db: Session, venue: Venue) -> list[Match]:
    """Horários pendentes (pedidos de grupos sem vínculo) das próximas peladas."""
    court_ids = [c.id for c in courts_of(db, venue.id)]
    if not court_ids:
        return []
    return list(db.scalars(select(Match).where(
        Match.court_id.in_(court_ids), Match.booking_status == BookingStatus.PENDING,
        Match.status != MatchStatus.CANCELLED, Match.ends_at > utcnow(),
    ).order_by(Match.starts_at)).all())


def _confirm_pending_of_group(db: Session, venue: Venue, group_id: int) -> int:
    """Grupo passou a ser vinculado ao espaço: os outros horários pendentes dele no espaço ficam confirmados."""
    court_ids = [c.id for c in courts_of(db, venue.id)]
    pending = db.scalars(select(Match).where(
        Match.group_id == group_id, Match.court_id.in_(court_ids), Match.booking_status == BookingStatus.PENDING,
    )).all() if court_ids else []
    for match in pending:
        match.booking_status = BookingStatus.CONFIRMED
    return len(pending)


def decide_booking(db: Session, venue: Venue, match_id: int, approve: bool, actor: User,
                   reason: str | None = None) -> Match:
    """O DE confirma ou recusa o horário pedido. Confirmar vincula o grupo ao espaço (se ele não tiver outro
    espaço); recusar tira a quadra da pelada, que continua valendo para os jogadores."""
    match = _venue_match(db, venue, match_id)
    if match.booking_status != BookingStatus.PENDING:
        raise BusinessRuleError("Este horário já foi respondido", code="invalid_status")
    if match.status == MatchStatus.CANCELLED:
        raise BusinessRuleError("A pelada foi cancelada", code="invalid_status")
    group = db.get(FootballGroup, match.group_id)
    court = db.get(Court, match.court_id)
    when = notifications.when(match.starts_at)
    if approve:
        match.booking_status = BookingStatus.CONFIRMED
        live = _live_link(db, group.id)
        if live is None:
            db.add(VenueGroup(venue_id=venue.id, group_id=group.id, status=VenueLinkStatus.ACTIVE,
                              origin=VenueLinkOrigin.REQUEST, requested_by_id=match.created_by_id,
                              decided_by_id=actor.id, decided_at=utcnow()))
        elif live.venue_id == venue.id and live.status == VenueLinkStatus.PENDING:
            live.status = VenueLinkStatus.ACTIVE
            live.decided_by_id = actor.id
            live.decided_at = utcnow()
        db.flush()
        if active_link(db, group.id) is not None and active_link(db, group.id).venue_id == venue.id:
            _confirm_pending_of_group(db, venue, group.id)
        title, body = (f"{venue.name} confirmou o horário",
                       f"{match.name}: {when} na {court.name} está reservado.")
    else:
        match.booking_status = BookingStatus.REJECTED
        match.court_id = None  # o local (texto) continua; o horário sai da agenda do espaço
        title, body = (f"{venue.name} recusou o horário",
                       f"{match.name} ({when}) continua marcada, mas sem a quadra."
                       + (f" Motivo: {reason}" if reason else " Combine outro horário ou local."))
    _record(db, "venue.booking_confirmed" if approve else "venue.booking_rejected", "match", match.id, actor,
            group_id=group.id, after={"venue_id": venue.id, "court_id": court.id}, reason=reason)
    notifications.notify(db, group_admin_ids(db, group.id), NotificationType.VENUE, title, body,
                         group_id=group.id, match_id=match.id)
    db.commit()
    return match


# ------------------------------------------------------------------ pelada semanal criada pelo DE


def create_weekly_pelada(db: Session, venue: Venue, data: VenuePeladaCreate, actor: User) -> VenueGroup:
    """O DE marca um horário fixo semanal numa quadra: cria a pelada (grupo), define o administrador e gera as
    próximas semanas já confirmadas na agenda."""
    from app.services import matches  # evita import circular

    court = get_court(db, venue, data.court_id)
    if data.first_starts_at.tzinfo is None:
        raise BusinessRuleError("Datas devem incluir fuso horário", code="naive_datetime")
    check_slot(db, court, data.first_starts_at, data.first_starts_at + timedelta(minutes=data.duration_minutes))
    link = create_group(db, venue, VenueGroupCreate(
        name=data.name, description=data.description, modality=data.modality or court.modality,
        admin_email=data.admin_email, admin_name=data.admin_name, rent_hourly_price=data.rent_hourly_price,
    ), actor, commit=False)
    group = db.get(FootballGroup, link.group_id)
    admin_user = users.get_by_email(db, data.admin_email)
    teams = data.num_teams or 2
    series_in = SeriesCreate(
        name=data.name, description=data.description, modality=data.modality or court.modality,
        court_id=court.id, price=data.price, slots=data.slots, num_teams=teams,
        field_players_per_team=max(1, data.slots // teams - 1), goalkeepers_per_team=1,
        responsible_admin_ids=[admin_user.id], recurrence=Recurrence.WEEKLY,
        first_starts_at=data.first_starts_at, duration_minutes=data.duration_minutes,
        generate_count=0, auto_generate_ahead=4,
    )
    series, _ = matches.create_series(db, group, series_in, actor)
    # semanas já ocupadas por bloqueio ou outra pelada são puladas (o agendador mantém as próximas criadas)
    matches.generate_occurrences(db, group, series, 4, actor, skip_conflicts=True)
    return link
