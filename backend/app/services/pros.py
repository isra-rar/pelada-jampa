"""Comércio de goleiros e juízes: anúncio, agenda, busca e contratação para peladas.

Fluxo: o administrador da pelada envia um pedido (valor por pelada, padrão o do anúncio); o profissional aceita
ou recusa. Aceito, ele entra na pelada como goleiro (vai para o sorteio) ou juiz (vaga de juiz). Quem não é do
grupo entra como convidado (`MemberStatus.GUEST`): participa só das peladas para as quais foi contratado, sem
acesso ao grupo. O contratado não paga a pelada; o valor dele é rateado entre os jogadores presentes na
cobrança. O pagamento ao profissional é combinado por fora do app.
"""

from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError, Conflict, NotFound
from app.domain.enums import AttendanceStatus as S
from app.domain.enums import (
    GroupRole,
    HireRole,
    HireStatus,
    MatchStatus,
    MemberStatus,
    Modality,
    NotificationType,
)
from app.models import FootballGroup, GroupMember, Match, MatchHire, MatchPlayer, ProAvailability, ProProfile, User
from app.schemas.pro import HireCreate, ProProfileIn
from app.services import attendance, audit, notifications
from app.services.access import get_membership
from app.services.groups import group_admin_user_ids
from app.services.stats import BR_TZ

PRO_URL = "/profissional"
ROLE_LABELS = {HireRole.GOALKEEPER: "goleiro", HireRole.REFEREE: "juiz"}
ROLE_ACTION = {HireRole.GOALKEEPER: "jogar no gol", HireRole.REFEREE: "apitar"}
_ACTIVE_HIRE = (HireStatus.PENDING, HireStatus.ACCEPTED)
SEARCH_LIMIT = 1000


# ------------------------------------------------------------------ anúncio


def my_profile(db: Session, user: User) -> ProProfile | None:
    return db.scalar(select(ProProfile).where(ProProfile.user_id == user.id))


def upsert_profile(db: Session, user: User, data: ProProfileIn) -> ProProfile:
    profile = my_profile(db, user)
    if profile is None:
        profile = ProProfile(user_id=user.id)
        db.add(profile)
    values = data.model_dump(exclude={"availability"})
    values["modalities"] = [m.value for m in data.modalities]
    for key, value in values.items():
        setattr(profile, key, value)
    if not data.is_goalkeeper:
        profile.goalkeeper_price = None
    if not data.is_referee:
        profile.referee_price = None
    if data.availability is not None:
        profile.availability.clear()
        db.flush()
        for slot in data.availability:
            profile.availability.append(ProAvailability(**slot.model_dump()))
    db.commit()
    db.refresh(profile)
    return profile


def _today() -> date:
    return utcnow().astimezone(BR_TZ).date()


def _jobs_done(db: Session, profile_ids: list[int]) -> dict[int, int]:
    if not profile_ids:
        return {}
    rows = db.execute(
        select(MatchHire.pro_profile_id, func.count())
        .join(Match, Match.id == MatchHire.match_id)
        .where(MatchHire.pro_profile_id.in_(profile_ids), MatchHire.status == HireStatus.ACCEPTED,
               Match.status == MatchStatus.FINISHED)
        .group_by(MatchHire.pro_profile_id)
    ).all()
    return {pid: n for pid, n in rows}


def card_out(profile: ProProfile, jobs: int, available: bool | None = None) -> dict:
    today = _today()
    return {
        "id": profile.id,
        "name": profile.user.name,
        "photo_url": profile.user.photo_url,
        "is_goalkeeper": profile.is_goalkeeper,
        "is_referee": profile.is_referee,
        "goalkeeper_price": profile.goalkeeper_price,
        "referee_price": profile.referee_price,
        "max_duration_minutes": profile.max_duration_minutes,
        "description": profile.description,
        "city": profile.city,
        "neighborhood": profile.neighborhood,
        "modalities": profile.modalities or [],
        "jobs_done": jobs,
        "availability": [
            {"id": a.id, "weekday": a.weekday, "date": a.date, "start_time": a.start_time, "end_time": a.end_time}
            for a in profile.availability
            if a.date is None or a.date >= today
        ],
        "available": available,
    }


def public_card(db: Session, profile: ProProfile) -> dict:
    return card_out(profile, _jobs_done(db, [profile.id]).get(profile.id, 0))


def profile_out(db: Session, profile: ProProfile) -> dict:
    return {
        **card_out(profile, _jobs_done(db, [profile.id]).get(profile.id, 0)),
        "contact_phone": profile.contact_phone,
        "is_active": profile.is_active,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


# ------------------------------------------------------------------ agenda


def match_window(match: Match) -> tuple[date, time, time]:
    """Data e horário locais (Brasília) da pelada. Se passar da meia-noite, o fim vira 23:59."""
    start = match.starts_at.astimezone(BR_TZ)
    end = match.ends_at.astimezone(BR_TZ)
    end_time = end.time() if end.date() == start.date() else time(23, 59)
    return start.date(), start.time().replace(second=0, microsecond=0), end_time


def covers(profile: ProProfile, day: date, start: time | None = None, end: time | None = None) -> bool:
    """A agenda cobre o dia (e, se informado, o horário inteiro)?"""
    for slot in profile.availability:
        same_day = slot.date == day if slot.date is not None else slot.weekday == day.weekday()
        if not same_day:
            continue
        if start is None or (slot.start_time <= start and (end is None or slot.end_time >= end)):
            return True
    return False


def booked(db: Session, profile_id: int, starts_at: datetime, ends_at: datetime,
           exclude_match_id: int | None = None) -> bool:
    """Já tem pelada aceita que se sobrepõe ao horário?"""
    stmt = (
        select(MatchHire.id)
        .join(Match, Match.id == MatchHire.match_id)
        .where(MatchHire.pro_profile_id == profile_id, MatchHire.status == HireStatus.ACCEPTED,
               Match.status != MatchStatus.CANCELLED, Match.starts_at < ends_at, Match.ends_at > starts_at)
    )
    if exclude_match_id is not None:
        stmt = stmt.where(Match.id != exclude_match_id)
    return db.scalar(stmt.limit(1)) is not None


def _price(profile: ProProfile, role: HireRole):
    return profile.goalkeeper_price if role == HireRole.GOALKEEPER else profile.referee_price


def _offers(profile: ProProfile, role: HireRole) -> bool:
    return profile.is_goalkeeper if role == HireRole.GOALKEEPER else profile.is_referee


# ------------------------------------------------------------------ busca


def search(
    db: Session, *, role: HireRole, city: str | None, modality: Modality | None, day: date | None,
    start: time | None, end: time | None, max_price, q: str | None, page: int, size: int,
) -> tuple[list[dict], int]:
    stmt = (
        select(ProProfile)
        .join(User, User.id == ProProfile.user_id)
        .where(ProProfile.is_active.is_(True), User.is_active.is_(True), User.deleted_at.is_(None))
    )
    stmt = stmt.where(ProProfile.is_goalkeeper.is_(True) if role == HireRole.GOALKEEPER
                      else ProProfile.is_referee.is_(True))
    if city:
        stmt = stmt.where(func.lower(func.coalesce(ProProfile.city, "")).like(f"%{city.strip().lower()}%"))
    profiles = db.scalars(stmt.limit(SEARCH_LIMIT)).unique().all()

    if modality:
        profiles = [p for p in profiles if not p.modalities or modality.value in p.modalities]
    if max_price is not None:
        profiles = [p for p in profiles if _price(p, role) is not None and _price(p, role) <= max_price]
    if q:
        needle = q.strip().lower()
        profiles = [
            p for p in profiles
            if needle in " ".join(filter(None, [p.user.name, p.description, p.neighborhood, p.city])).lower()
        ]

    availability: dict[int, bool | None] = {p.id: None for p in profiles}
    if day is not None:
        window = None
        if start is not None and end is not None and end > start:
            window = (datetime.combine(day, start, BR_TZ), datetime.combine(day, end, BR_TZ))
        for p in profiles:
            ok = covers(p, day, start, end)
            if ok and window is not None:
                ok = not booked(db, p.id, *window)
            availability[p.id] = ok
        profiles = [p for p in profiles if availability[p.id]]

    jobs = _jobs_done(db, [p.id for p in profiles])
    profiles.sort(key=lambda p: (_price(p, role) is None, _price(p, role) or 0, -jobs.get(p.id, 0), p.user.name))
    total = len(profiles)
    page_items = profiles[(page - 1) * size: page * size]
    return [card_out(p, jobs.get(p.id, 0), availability[p.id]) for p in page_items], total


def get_public(db: Session, pro_id: int) -> ProProfile:
    profile = db.get(ProProfile, pro_id)
    if profile is None or not profile.is_active or profile.user.deleted_at is not None or not profile.user.is_active:
        raise NotFound("Profissional não encontrado")
    return profile


# ------------------------------------------------------------------ pedidos


def _when(match: Match) -> str:
    return notifications.when(match.starts_at)


def _flags(profile: ProProfile, match: Match) -> tuple[bool, bool]:
    day, start, end = match_window(match)
    outside = not covers(profile, day, start, end)
    too_long = match.duration_minutes > profile.max_duration_minutes
    return outside, too_long


def hire_out(db: Session, hire: MatchHire, *, viewer: str) -> dict:
    """`viewer`: "admin" (organizador) ou "pro". O telefone de cada lado só aparece com o pedido aceito."""
    match, profile = hire.match, hire.profile
    group = db.get(FootballGroup, hire.group_id)
    organizer = db.get(User, hire.requested_by_id)
    accepted = hire.status == HireStatus.ACCEPTED
    outside, too_long = _flags(profile, match)
    return {
        "id": hire.id,
        "match_id": match.id,
        "group_id": hire.group_id,
        "group_name": group.name if group else "",
        "match_name": match.name,
        "starts_at": match.starts_at,
        "ends_at": match.ends_at,
        "location": match.location,
        "address": match.address,
        "modality": match.modality,
        "role": hire.role,
        "price": hire.price,
        "status": hire.status,
        "message": hire.message,
        "response_note": hire.response_note,
        "created_at": hire.created_at,
        "responded_at": hire.responded_at,
        "pro_id": profile.id,
        "pro": {
            "name": profile.user.name,
            "photo_url": profile.user.photo_url,
            "phone": (profile.contact_phone or profile.user.phone) if accepted and viewer == "admin" else None,
        },
        "organizer": {
            "name": organizer.name if organizer else "",
            "photo_url": organizer.photo_url if organizer else None,
            "phone": organizer.phone if accepted and viewer == "pro" and organizer else None,
        },
        "outside_availability": outside,
        "over_max_duration": too_long,
    }


def _require_open(match: Match) -> None:
    if match.status != MatchStatus.PUBLISHED:
        raise BusinessRuleError("A pelada precisa estar publicada", code="match_not_open")
    if utcnow() >= match.starts_at:
        raise BusinessRuleError("A pelada já começou", code="match_started")


def request_hire(db: Session, group: FootballGroup, match: Match, data: HireCreate, actor: User) -> MatchHire:
    _require_open(match)
    profile = db.get(ProProfile, data.pro_id)
    if profile is None or not profile.is_active or profile.user.deleted_at is not None:
        raise NotFound("Profissional não encontrado")
    if not _offers(profile, data.role):
        raise BusinessRuleError(f"Este profissional não se anuncia como {ROLE_LABELS[data.role]}",
                                code="role_not_offered")
    if profile.user_id == actor.id:
        raise BusinessRuleError("Você não pode contratar a si mesmo", code="self_hire")
    if data.role == HireRole.GOALKEEPER and match.goalkeepers_per_team == 0:
        raise BusinessRuleError("Esta pelada não tem goleiros (0 por time)", code="no_goalkeepers")
    open_hire = db.scalar(
        select(MatchHire.id).where(MatchHire.match_id == match.id, MatchHire.pro_profile_id == profile.id,
                                   MatchHire.status.in_(_ACTIVE_HIRE))
    )
    if open_hire:
        raise Conflict("Já existe um pedido para este profissional nesta pelada", code="hire_exists")
    if booked(db, profile.id, match.starts_at, match.ends_at, exclude_match_id=match.id):
        raise BusinessRuleError("O profissional já tem outra pelada nesse horário", code="pro_unavailable")
    price = data.price if data.price is not None else _price(profile, data.role)
    if price is None:
        raise BusinessRuleError("Informe o valor", code="price_required")
    if data.role == HireRole.REFEREE:
        if match.referee_slots == 0:
            match.referee_slots = 1  # contratar juiz abre a vaga de juiz na pelada
        if attendance.referees_taken(db, match) >= match.referee_slots:
            raise BusinessRuleError("A vaga de juiz já está preenchida", code="referee_full")

    hire = MatchHire(
        match_id=match.id, group_id=group.id, pro_profile_id=profile.id, pro_user_id=profile.user_id,
        role=data.role, price=price, message=data.message, requested_by_id=actor.id,
    )
    db.add(hire)
    db.flush()
    notifications.notify(
        db, [profile.user_id], NotificationType.HIRE, f"Pedido para {ROLE_ACTION[data.role]}",
        f"{group.name} quer você para {ROLE_ACTION[data.role]} em {match.name} ({_when(match)}, {match.location}) "
        f"por R$ {price:.2f}. Responda em Goleiro/juiz de aluguel.",
        url=PRO_URL,
    )
    audit.record(db, action="hire.requested", entity_type="match_hire", entity_id=hire.id, actor_user_id=actor.id,
                 group_id=group.id, after={"match_id": match.id, "pro_id": profile.id, "role": data.role,
                                           "price": str(price)})
    db.commit()
    db.refresh(hire)
    return hire


def list_for_match(db: Session, match: Match) -> list[MatchHire]:
    return list(db.scalars(
        select(MatchHire).where(MatchHire.match_id == match.id).order_by(MatchHire.created_at.desc())
    ).all())


def all_for_pro(db: Session, user: User) -> list[MatchHire]:
    """Todos os pedidos do profissional (exportação de dados)."""
    stmt = select(MatchHire).join(Match, Match.id == MatchHire.match_id).where(MatchHire.pro_user_id == user.id)
    return list(db.scalars(stmt.order_by(Match.starts_at.desc())).unique().all())


def list_for_pro(db: Session, user: User, status: HireStatus | None = None, *, page: int = 1,
                 size: int = 20) -> tuple[list[MatchHire], int]:
    stmt = select(MatchHire).join(Match, Match.id == MatchHire.match_id).where(MatchHire.pro_user_id == user.id)
    if status is not None:
        stmt = stmt.where(MatchHire.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(Match.starts_at.desc(), MatchHire.id.desc())
                       .offset((page - 1) * size).limit(size)).unique().all()
    return list(items), total


def _get_for_pro(db: Session, user: User, hire_id: int) -> MatchHire:
    hire = db.get(MatchHire, hire_id)
    if hire is None or hire.pro_user_id != user.id:
        raise NotFound("Pedido não encontrado")
    return hire


def get_for_match(db: Session, match: Match, hire_id: int) -> MatchHire:
    hire = db.get(MatchHire, hire_id)
    if hire is None or hire.match_id != match.id:
        raise NotFound("Pedido não encontrado")
    return hire


def _guest_member(db: Session, group: FootballGroup, user: User) -> GroupMember:
    """Vínculo do profissional com o grupo: membro ativo continua como está; quem é de fora vira convidado."""
    member = get_membership(db, group.id, user.id)
    if member is None:
        member = GroupMember(group_id=group.id, user_id=user.id, role=GroupRole.PLAYER, status=MemberStatus.GUEST)
        db.add(member)
        db.flush()
    elif member.status == MemberStatus.REMOVED:
        member.status = MemberStatus.GUEST
    elif member.status not in (MemberStatus.ACTIVE, MemberStatus.GUEST):
        raise BusinessRuleError(
            "Você tem um vínculo pendente ou inativo com este grupo. Fale com o organizador.",
            code="member_conflict",
        )
    return member


def accept(db: Session, user: User, hire_id: int, note: str | None) -> MatchHire:
    hire = _get_for_pro(db, user, hire_id)
    if hire.status == HireStatus.ACCEPTED:
        return hire
    if hire.status != HireStatus.PENDING:
        raise BusinessRuleError("Este pedido não está mais aberto", code="hire_closed")
    match = db.scalar(select(Match).where(Match.id == hire.match_id).with_for_update())
    if match.status != MatchStatus.PUBLISHED or utcnow() >= match.starts_at:
        hire.status = HireStatus.EXPIRED
        db.commit()
        raise BusinessRuleError("A pelada não está mais aberta", code="hire_closed")
    if booked(db, hire.pro_profile_id, match.starts_at, match.ends_at, exclude_match_id=match.id):
        raise BusinessRuleError("Você já tem outra pelada aceita nesse horário", code="pro_unavailable")
    group = db.get(FootballGroup, hire.group_id)
    member = _guest_member(db, group, user)
    if hire.role == HireRole.REFEREE:
        attendance._require_referee_slot(db, match, member.id)

    mp = attendance.get_participation(db, match, member.id)
    if mp is None:
        mp = MatchPlayer(match_id=match.id, member_id=member.id, status=S.PENDING)
        db.add(mp)
    now = utcnow()
    mp.status = S.CONFIRMED
    mp.confirmed_at = mp.responded_at = now
    mp.was_confirmed = True
    mp.promotion_offered_at = mp.waitlist_seq = mp.waitlist_joined_at = None
    mp.blocked = False
    mp.is_goalkeeper = hire.role == HireRole.GOALKEEPER
    mp.is_referee = hire.role == HireRole.REFEREE
    db.flush()
    mp.hire_id = hire.id
    hire.status = HireStatus.ACCEPTED
    hire.responded_at = now
    hire.response_note = note
    notifications.notify(
        db, group_admin_user_ids(db, group.id), NotificationType.HIRE, f"{user.name} aceitou o pedido",
        f"{user.name} vai {ROLE_ACTION[hire.role]} em {match.name} ({_when(match)}) por R$ {hire.price:.2f}, "
        "rateado entre os jogadores presentes.",
        group_id=group.id, match_id=match.id,
    )
    audit.record(db, action="hire.accepted", entity_type="match_hire", entity_id=hire.id, actor_user_id=user.id,
                 group_id=group.id)
    db.commit()
    db.refresh(hire)
    return hire


def decline(db: Session, user: User, hire_id: int, note: str | None) -> MatchHire:
    hire = _get_for_pro(db, user, hire_id)
    if hire.status == HireStatus.DECLINED:
        return hire
    if hire.status != HireStatus.PENDING:
        raise BusinessRuleError("Este pedido não está mais aberto", code="hire_closed")
    hire.status = HireStatus.DECLINED
    hire.responded_at = utcnow()
    hire.response_note = note
    notifications.notify(
        db, group_admin_user_ids(db, hire.group_id), NotificationType.HIRE, f"{user.name} recusou o pedido",
        f"{user.name} não pode {ROLE_ACTION[hire.role]} em {hire.match.name}."
        + (f" Motivo: {note}" if note else ""),
        group_id=hire.group_id, match_id=hire.match_id,
    )
    audit.record(db, action="hire.declined", entity_type="match_hire", entity_id=hire.id, actor_user_id=user.id,
                 group_id=hire.group_id)
    db.commit()
    return hire


def _release(db: Session, hire: MatchHire) -> None:
    """Tira o contratado da pelada (pedido aceito que foi cancelado). Não faz commit."""
    mp = db.scalar(select(MatchPlayer).where(MatchPlayer.hire_id == hire.id))
    if mp is None:
        return
    mp.status = S.CANCELLED
    mp.cancelled_at = utcnow()
    mp.hire_id = None
    mp.is_goalkeeper = mp.is_referee = False
    attendance.promote_waitlist(db, hire.match)


def _cancel(db: Session, hire: MatchHire, actor: User, reason: str | None) -> None:
    if hire.status not in _ACTIVE_HIRE:
        raise BusinessRuleError("Este pedido não está mais aberto", code="hire_closed")
    if hire.status == HireStatus.ACCEPTED:
        if utcnow() >= hire.match.starts_at:
            raise BusinessRuleError("A pelada já começou", code="match_started")
        _release(db, hire)
    hire.status = HireStatus.CANCELLED
    hire.cancelled_at = utcnow()
    hire.response_note = reason or hire.response_note
    audit.record(db, action="hire.cancelled", entity_type="match_hire", entity_id=hire.id, actor_user_id=actor.id,
                 group_id=hire.group_id, reason=reason)


def cancel_by_admin(db: Session, match: Match, hire_id: int, actor: User, reason: str | None) -> MatchHire:
    hire = get_for_match(db, match, hire_id)
    _cancel(db, hire, actor, reason)
    notifications.notify(
        db, [hire.pro_user_id], NotificationType.HIRE, "Pedido cancelado pelo organizador",
        f"O pedido para {ROLE_ACTION[hire.role]} em {match.name} ({_when(match)}) foi cancelado."
        + (f" Motivo: {reason}" if reason else ""),
        url=PRO_URL,
    )
    db.commit()
    return hire


def cancel_by_pro(db: Session, user: User, hire_id: int, reason: str | None) -> MatchHire:
    hire = _get_for_pro(db, user, hire_id)
    _cancel(db, hire, user, reason)
    notifications.notify(
        db, group_admin_user_ids(db, hire.group_id), NotificationType.HIRE, f"{user.name} desistiu",
        f"{user.name} não vai mais {ROLE_ACTION[hire.role]} em {hire.match.name} ({_when(hire.match)})."
        + (f" Motivo: {reason}" if reason else ""),
        group_id=hire.group_id, match_id=hire.match_id,
    )
    db.commit()
    return hire


def cancel_for_match(db: Session, match: Match, reason: str | None) -> int:
    """Pelada cancelada: pedidos abertos e aceitos são cancelados e os profissionais avisados. Sem commit."""
    hires = db.scalars(
        select(MatchHire).where(MatchHire.match_id == match.id, MatchHire.status.in_(_ACTIVE_HIRE))
    ).all()
    for hire in hires:
        hire.status = HireStatus.CANCELLED
        hire.cancelled_at = utcnow()
    if hires:
        notifications.notify(
            db, [h.pro_user_id for h in hires], NotificationType.HIRE, f"Pelada cancelada: {match.name}",
            f"A pelada de {_when(match)} foi cancelada pelo organizador." + (f" Motivo: {reason}" if reason else ""),
            url=PRO_URL,
        )
    return len(hires)


def retire_profile(db: Session, user: User) -> None:
    """Exclusão de conta (LGPD): o anúncio sai do ar sem dados pessoais e os pedidos abertos são cancelados.
    Sem commit."""
    profile = my_profile(db, user)
    if profile is not None:
        profile.is_active = False
        profile.description = profile.contact_phone = profile.city = profile.neighborhood = None
        profile.availability.clear()
    for hire in db.scalars(
        select(MatchHire).where(MatchHire.pro_user_id == user.id, MatchHire.status.in_(_ACTIVE_HIRE))
    ).all():
        if hire.status == HireStatus.ACCEPTED and utcnow() < hire.match.starts_at:
            _release(db, hire)
        hire.status = HireStatus.CANCELLED
        hire.cancelled_at = utcnow()
        notifications.notify(
            db, group_admin_user_ids(db, hire.group_id), NotificationType.HIRE, "Contratado indisponível",
            f"O {ROLE_LABELS[hire.role]} contratado para {hire.match.name} ({_when(hire.match)}) encerrou a conta.",
            group_id=hire.group_id, match_id=hire.match_id,
        )


def expire_due(db: Session) -> int:
    """Pedidos sem resposta de peladas que já começaram (ou saíram do ar) expiram. Tarefa do agendador."""
    rows = db.scalars(
        select(MatchHire).join(Match, Match.id == MatchHire.match_id).where(
            MatchHire.status == HireStatus.PENDING,
            (Match.starts_at <= utcnow()) | (Match.status != MatchStatus.PUBLISHED),
        )
    ).unique().all()
    for hire in rows:
        hire.status = HireStatus.EXPIRED
    db.commit()
    return len(rows)


# ------------------------------------------------------------------ contratar a partir do anúncio


def bookable_matches(db: Session, user: User, profile: ProProfile) -> list[dict]:
    """Próximas peladas publicadas dos grupos que o usuário administra, com a situação do profissional."""
    now = utcnow()
    rows = db.execute(
        select(Match, FootballGroup)
        .join(FootballGroup, FootballGroup.id == Match.group_id)
        .join(GroupMember, GroupMember.group_id == Match.group_id)
        .where(GroupMember.user_id == user.id, GroupMember.status == MemberStatus.ACTIVE,
               GroupMember.role == GroupRole.ADMIN, FootballGroup.suspended_at.is_(None),
               Match.status == MatchStatus.PUBLISHED, Match.starts_at > now)
        .order_by(Match.starts_at)
        .limit(30)
    ).all()
    requested = set(db.scalars(
        select(MatchHire.match_id).where(MatchHire.pro_profile_id == profile.id, MatchHire.status.in_(_ACTIVE_HIRE))
    ).all())
    out = []
    for match, group in rows:
        roles = [r for r in (HireRole.GOALKEEPER, HireRole.REFEREE)
                 if _offers(profile, r) and (r != HireRole.GOALKEEPER or match.goalkeepers_per_team > 0)]
        outside, too_long = _flags(profile, match)
        out.append({
            "match_id": match.id, "group_id": group.id, "group_name": group.name, "name": match.name,
            "starts_at": match.starts_at, "ends_at": match.ends_at, "location": match.location, "roles": roles,
            "available": not outside and not booked(db, profile.id, match.starts_at, match.ends_at,
                                                    exclude_match_id=match.id),
            "over_max_duration": too_long,
            "already_requested": match.id in requested,
        })
    return out
