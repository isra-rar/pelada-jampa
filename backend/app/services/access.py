"""Controle de acesso por grupo e papel (isolamento de dados entre grupos)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, NotFound
from app.domain.enums import MemberStatus
from app.models import Draw, FootballGroup, GroupMember, Match, User


def get_group(db: Session, group_id: int) -> FootballGroup:
    group = db.get(FootballGroup, group_id)
    if group is None:
        raise NotFound("Grupo não encontrado")
    return group


def get_membership(db: Session, group_id: int, user_id: int) -> GroupMember | None:
    return db.scalar(select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id))


def require_member(db: Session, group_id: int, user: User) -> GroupMember:
    member = get_membership(db, group_id, user.id)
    if member is None or member.status != MemberStatus.ACTIVE:
        # não revela a existência do grupo para quem não participa
        raise NotFound("Grupo não encontrado")
    return member


def require_admin(db: Session, group_id: int, user: User) -> GroupMember:
    member = require_member(db, group_id, user)
    if not member.is_admin:
        raise Forbidden("Apenas administradores da pelada podem realizar esta ação")
    return member


def get_member_in_group(db: Session, group_id: int, member_id: int) -> GroupMember:
    member = db.get(GroupMember, member_id)
    if member is None or member.group_id != group_id:
        raise NotFound("Jogador não encontrado neste grupo")
    return member


def get_match(db: Session, match_id: int, *, lock: bool = False) -> Match:
    if lock:
        match = db.scalar(select(Match).where(Match.id == match_id).with_for_update())
    else:
        match = db.get(Match, match_id)
    if match is None:
        raise NotFound("Pelada não encontrada")
    return match


def match_for_member(db: Session, match_id: int, user: User) -> tuple[Match, GroupMember]:
    match = get_match(db, match_id)
    try:
        member = require_member(db, match.group_id, user)
    except NotFound:
        raise NotFound("Pelada não encontrada") from None
    return match, member


def match_for_admin(db: Session, match_id: int, user: User) -> tuple[Match, GroupMember]:
    match, member = match_for_member(db, match_id, user)
    if not member.is_admin:
        raise Forbidden("Apenas administradores da pelada podem realizar esta ação")
    return match, member


def draw_for_member(db: Session, draw_id: int, user: User) -> tuple[Draw, Match, GroupMember]:
    draw = db.get(Draw, draw_id)
    if draw is None:
        raise NotFound("Sorteio não encontrado")
    match, member = match_for_member(db, draw.match_id, user)
    return draw, match, member
