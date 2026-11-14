"""Descoberta de peladas abertas e comunidades públicas; prévia pública do convite (v3.0).

Um grupo só aparece aqui se o administrador ligar `discoverable` (e não estiver suspenso/inativo). Suas peladas
futuras e publicadas aparecem na busca de peladas abertas quando `discover_matches` está ligado e o prazo de
confirmação não terminou. Quem chega pela descoberta entra no grupo conforme `public_join_requires_approval`.
"""

from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import NotFound
from app.domain.enums import AttendanceStatus, MatchStatus, MemberStatus, Modality
from app.models import FootballGroup, GroupMember, Match, MatchPlayer, User
from app.services import attendance, groups, matches, notifications, payments
from app.services.access import get_membership
from app.services.stats import local_bounds

UPCOMING_LIMIT = 10


def _visible_group():
    return (
        FootballGroup.discoverable.is_(True),
        FootballGroup.is_active.is_(True),
        FootballGroup.suspended_at.is_(None),
    )


def _occupied_subquery():
    """Vagas ocupadas por pelada: confirmados + vagas oferecidas à lista de espera (mesma regra da confirmação)."""
    return (
        select(MatchPlayer.match_id, func.count().label("occupied"))
        .where(MatchPlayer.is_referee.is_(False), or_(
            MatchPlayer.status == AttendanceStatus.CONFIRMED,
            and_(MatchPlayer.status == AttendanceStatus.WAITLIST, MatchPlayer.promotion_offered_at.is_not(None)),
        ))
        .group_by(MatchPlayer.match_id)
        .subquery()
    )


def _like(text: str) -> str:
    return f"%{text.strip().lower()}%"


def _open_matches_stmt():
    now = utcnow()
    occ = _occupied_subquery()
    occupied = func.coalesce(occ.c.occupied, 0)
    stmt = (
        select(Match, FootballGroup, occupied.label("occupied"))
        .join(FootballGroup, FootballGroup.id == Match.group_id)
        .outerjoin(occ, occ.c.match_id == Match.id)
        .where(
            *_visible_group(),
            FootballGroup.discover_matches.is_(True),
            Match.status == MatchStatus.PUBLISHED,
            Match.starts_at > now,
            Match.confirmation_deadline > now,
        )
    )
    return stmt, occupied


def community_ref(group: FootballGroup) -> dict:
    return {
        "id": group.id, "name": group.name, "city": group.city, "neighborhood": group.neighborhood,
        "join_requires_approval": group.public_join_requires_approval,
    }


def match_card(db: Session, match: Match, group: FootballGroup, occupied: int) -> dict:
    return {
        "id": match.id, "name": match.name, "modality": match.modality, "location": match.location,
        "address": match.address, "starts_at": match.starts_at, "ends_at": match.ends_at, "price": match.price,
        "split_cost": match.split_cost, "price_estimate": matches.per_player_if_full(db, match),
        "slots": match.slots, "confirmed": occupied, "available_slots": max(0, match.slots - occupied),
        "confirmation_deadline": match.confirmation_deadline,
        "field_players_per_team": match.field_players_per_team,
        "goalkeepers_per_team": match.goalkeepers_per_team, "group": community_ref(group),
    }


def search_matches(
    db: Session, *, q: str | None, city: str | None, modality: Modality | None, since: date | None,
    until: date | None, with_spots: bool, page: int, size: int,
) -> tuple[list[dict], int]:
    stmt, occupied = _open_matches_stmt()
    if q:
        like = _like(q)
        stmt = stmt.where(or_(
            func.lower(Match.name).like(like), func.lower(Match.location).like(like),
            func.lower(func.coalesce(Match.address, "")).like(like), func.lower(FootballGroup.name).like(like),
            func.lower(func.coalesce(FootballGroup.neighborhood, "")).like(like),
        ))
    if city:
        stmt = stmt.where(func.lower(func.coalesce(FootballGroup.city, "")).like(_like(city)))
    if modality:
        stmt = stmt.where(Match.modality == modality)
    start, end = local_bounds(since, until)
    if start is not None:
        stmt = stmt.where(Match.starts_at >= start)
    if end is not None:
        stmt = stmt.where(Match.starts_at < end)
    if with_spots:
        stmt = stmt.where(Match.slots > occupied)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(Match.starts_at, Match.id).offset((page - 1) * size).limit(size)).all()
    return [match_card(db, m, g, int(n)) for m, g, n in rows], total


def _members_count(db: Session, group_ids: list[int]) -> dict[int, int]:
    if not group_ids:
        return {}
    rows = db.execute(
        select(GroupMember.group_id, func.count())
        .join(User, User.id == GroupMember.user_id)
        .where(GroupMember.group_id.in_(group_ids), GroupMember.status == MemberStatus.ACTIVE,
               User.deleted_at.is_(None))
        .group_by(GroupMember.group_id)
    ).all()
    return {gid: n for gid, n in rows}


def _upcoming_by_group(db: Session, group_ids: list[int]) -> dict[int, list[tuple[Match, int]]]:
    if not group_ids:
        return {}
    stmt, _ = _open_matches_stmt()
    rows = db.execute(stmt.where(FootballGroup.id.in_(group_ids)).order_by(Match.starts_at, Match.id)).all()
    out: dict[int, list[tuple[Match, int]]] = {}
    for m, g, n in rows:
        out.setdefault(g.id, []).append((m, int(n)))
    return out


def _next_ref(items: list[tuple[Match, int]]) -> dict | None:
    if not items:
        return None
    m, n = items[0]
    return {"id": m.id, "starts_at": m.starts_at, "location": m.location, "available_slots": max(0, m.slots - n)}


def community_card(group: FootballGroup, members: int, upcoming: list[tuple[Match, int]]) -> dict:
    return {
        "id": group.id, "name": group.name, "description": group.description, "modality": group.modality,
        "city": group.city, "neighborhood": group.neighborhood, "members_count": members,
        "join_requires_approval": group.public_join_requires_approval,
        "open_matches": len(upcoming), "next_match": _next_ref(upcoming),
        "public_ranking_token": group.public_token if group.public_ranking_enabled else None,
    }


def search_groups(
    db: Session, *, q: str | None, city: str | None, modality: Modality | None, page: int, size: int
) -> tuple[list[dict], int]:
    stmt = select(FootballGroup).where(*_visible_group())
    if q:
        like = _like(q)
        stmt = stmt.where(or_(
            func.lower(FootballGroup.name).like(like),
            func.lower(func.coalesce(FootballGroup.description, "")).like(like),
            func.lower(func.coalesce(FootballGroup.neighborhood, "")).like(like),
        ))
    if city:
        stmt = stmt.where(func.lower(func.coalesce(FootballGroup.city, "")).like(_like(city)))
    if modality:
        stmt = stmt.where(FootballGroup.modality == modality)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(FootballGroup.name, FootballGroup.id).offset((page - 1) * size).limit(size)).all()
    ids = [g.id for g in items]
    members = _members_count(db, ids)
    upcoming = _upcoming_by_group(db, ids)
    return [community_card(g, members.get(g.id, 0), upcoming.get(g.id, [])) for g in items], total


def cities(db: Session) -> list[dict]:
    rows = db.execute(
        select(FootballGroup.city, func.count())
        .where(*_visible_group(), FootballGroup.city.is_not(None))
        .group_by(FootballGroup.city)
        .order_by(func.count().desc(), FootballGroup.city)
    ).all()
    return [{"city": c, "communities": n} for c, n in rows if c]


# ------------------------------------------------------------------ páginas públicas


def _viewer(db: Session, user: User | None, group: FootballGroup, match: Match | None = None) -> dict | None:
    if user is None:
        return None
    member = get_membership(db, group.id, user.id)
    if member is None:
        return {"member_status": None, "member_id": None, "participation_status": None}
    participation = None
    if match is not None and member.status == MemberStatus.ACTIVE:
        participation = db.scalar(
            select(MatchPlayer.status).where(MatchPlayer.match_id == match.id, MatchPlayer.member_id == member.id)
        )
    return {"member_status": member.status, "member_id": member.id, "participation_status": participation}


def get_community(db: Session, group_id: int) -> FootballGroup:
    group = db.scalar(select(FootballGroup).where(FootballGroup.id == group_id, *_visible_group()))
    if group is None:
        raise NotFound("Comunidade não encontrada")
    return group


def community_detail(db: Session, group_id: int, user: User | None) -> dict:
    group = get_community(db, group_id)
    upcoming = _upcoming_by_group(db, [group.id]).get(group.id, [])
    card = community_card(group, _members_count(db, [group.id]).get(group.id, 0), upcoming)
    return {
        **card,
        "created_at": group.created_at,
        "upcoming_matches": [match_card(db, m, group, n) for m, n in upcoming[:UPCOMING_LIMIT]],
        "viewer": _viewer(db, user, group),
    }


def get_open_match(db: Session, match_id: int) -> tuple[Match, FootballGroup, int]:
    stmt, _ = _open_matches_stmt()
    row = db.execute(stmt.where(Match.id == match_id)).first()
    if row is None:
        raise NotFound("Pelada não encontrada ou não está mais aberta")
    return row[0], row[1], int(row[2])


def match_detail(db: Session, match_id: int, user: User | None) -> dict:
    match, group, occupied = get_open_match(db, match_id)
    return {
        **match_card(db, match, group, occupied),
        "description": match.description,
        "duration_minutes": match.duration_minutes,
        "viewer": _viewer(db, user, group, match),
    }


def invite_preview(db: Session, token: str, user: User | None) -> dict:
    group = groups.find_by_invite(db, token)
    if group is None or not group.is_active or group.suspended_at is not None:
        raise NotFound("Convite inválido ou expirado", code="invalid_invite")
    # próxima pelada publicada (mesmo que o grupo não esteja na descoberta: quem tem o convite foi chamado)
    now = utcnow()
    nxt = db.scalar(
        select(Match).where(Match.group_id == group.id, Match.status == MatchStatus.PUBLISHED, Match.starts_at > now)
        .order_by(Match.starts_at).limit(1)
    )
    next_ref = None
    if nxt is not None:
        next_ref = {"id": nxt.id, "starts_at": nxt.starts_at, "location": nxt.location,
                    "available_slots": matches.attendance_counts(db, nxt)["available_slots"]}
    return {
        "group_id": group.id, "name": group.name, "description": group.description, "modality": group.modality,
        "city": group.city, "neighborhood": group.neighborhood,
        "members_count": _members_count(db, [group.id]).get(group.id, 0),
        "requires_approval": group.require_join_approval, "next_match": next_ref,
        "viewer": _viewer(db, user, group),
    }


# ------------------------------------------------------------------ entrar pela descoberta


def join_community(db: Session, user: User, group_id: int) -> GroupMember:
    group = get_community(db, group_id)
    member = groups.join_public(db, user, group)
    db.commit()
    return member


def join_match(db: Session, user: User, match_id: int, as_goalkeeper: bool) -> dict:
    """"Quero jogar": entra no grupo (ou pede para entrar) e, se já puder, confirma presença na pelada."""
    match, group, _ = get_open_match(db, match_id)
    member = get_membership(db, group.id, user.id)
    if member is None or member.status != MemberStatus.ACTIVE:
        when = notifications.when(match.starts_at)
        member = groups.join_public(
            db, user, group,
            request_text=f"{user.name} quer jogar em {match.name} ({when}) e pediu para entrar em {group.name}.",
        )
        db.commit()
    if member.status != MemberStatus.ACTIVE:
        return {
            "group_id": group.id, "member_id": member.id, "member_status": member.status, "confirmation": None,
            "message": "Pedido enviado ao organizador. Quando ele aprovar, você recebe um aviso para confirmar "
                       "presença na pelada.",
        }
    mp, warnings, debts = attendance.confirm(db, group, match, member, as_goalkeeper)
    positions = {p.id: i + 1 for i, p in enumerate(attendance.waitlist(db, match))}
    confirmation = {
        "participation": attendance.participation_out(mp, waitlist_position=positions.get(mp.id)),
        "warnings": warnings,
        "pending_debts": [payments.debt_out(p) for p in debts],
    }
    message = (
        "Presença confirmada!" if mp.status == AttendanceStatus.CONFIRMED
        else "Vagas esgotadas: você entrou na lista de espera."
    )
    return {"group_id": group.id, "member_id": member.id, "member_status": member.status,
            "confirmation": confirmation, "message": message}
