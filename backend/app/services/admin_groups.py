"""Gestão de grupos pela administração da plataforma (PRD §2): consulta, suspensão/reativação e suporte a
grupos sem administrador.

Grupo suspenso fica inativo (convite, ranking público e geração automática param) e somente leitura para os
membros; só a administração da plataforma reativa. Toda ação fica na auditoria do próprio grupo.
"""

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError
from app.domain.enums import GroupRole, MatchStatus, MemberStatus, NotificationType
from app.models import AuditLog, FootballGroup, GroupMember, Match, MatchSeries, User
from app.schemas.admin import AdminGroupStatus
from app.services import audit, notifications

_STATUS_FIELDS = ["is_active", "suspended_at", "suspension_reason"]
_HELD = (MatchStatus.PUBLISHED, MatchStatus.FINISHED)


def _member_counts():
    """Subconsulta com membros e administradores ativos por grupo."""
    active = GroupMember.status == MemberStatus.ACTIVE
    return (
        select(
            GroupMember.group_id.label("group_id"),
            func.sum(case((active, 1), else_=0)).label("members"),
            func.sum(case((active & (GroupMember.role == GroupRole.ADMIN), 1), else_=0)).label("admins"),
        )
        .group_by(GroupMember.group_id)
        .subquery()
    )


def _last_matches():
    """Subconsulta com a última pelada realizada (já começou e não foi cancelada) por grupo."""
    return (
        select(Match.group_id.label("group_id"), func.max(Match.starts_at).label("last_match_at"))
        .where(Match.status.in_(_HELD), Match.starts_at <= utcnow())
        .group_by(Match.group_id)
        .subquery()
    )


def list_groups(
    db: Session, *, search: str | None, status: AdminGroupStatus | None, page: int, size: int
) -> tuple[list[dict], int]:
    counts, last = _member_counts(), _last_matches()
    members = func.coalesce(counts.c.members, 0)
    admins = func.coalesce(counts.c.admins, 0)
    stmt = (
        select(FootballGroup, members.label("members"), admins.label("admins"), last.c.last_match_at)
        .outerjoin(counts, counts.c.group_id == FootballGroup.id)
        .outerjoin(last, last.c.group_id == FootballGroup.id)
    )
    if search:
        stmt = stmt.where(func.lower(FootballGroup.name).like(f"%{search.strip().lower()}%"))
    if status == "active":
        stmt = stmt.where(FootballGroup.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(FootballGroup.is_active.is_(False), FootballGroup.suspended_at.is_(None))
    elif status == "suspended":
        stmt = stmt.where(FootballGroup.suspended_at.is_not(None))
    elif status == "no_admin":  # tem gente ativa, mas ninguém para administrar
        stmt = stmt.where(members > 0, admins == 0)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(FootballGroup.id.desc()).offset((page - 1) * size).limit(size)).all()
    return [_row(g, m, a, lm) for g, m, a, lm in rows], total


def _row(group: FootballGroup, members: int, admins: int, last_match_at) -> dict:
    return {
        "id": group.id,
        "name": group.name,
        "modality": group.modality,
        "is_active": group.is_active,
        "suspended_at": group.suspended_at,
        "created_at": group.created_at,
        "members": int(members or 0),
        "admins": int(admins or 0),
        "last_match_at": last_match_at,
    }


def detail(db: Session, group: FootballGroup) -> dict:
    by_status = dict(
        db.execute(
            select(GroupMember.status, func.count()).where(GroupMember.group_id == group.id).group_by(GroupMember.status)
        ).all()
    )
    active_members = db.execute(
        select(GroupMember, User)
        .join(User, User.id == GroupMember.user_id)
        .where(GroupMember.group_id == group.id, GroupMember.status == MemberStatus.ACTIVE)
        .order_by(case((GroupMember.role == GroupRole.ADMIN, 0), else_=1), User.name)
    ).all()
    now = utcnow()
    held = db.scalar(
        select(func.count()).where(Match.group_id == group.id, Match.status.in_(_HELD), Match.starts_at <= now)
    ) or 0
    by_match_status = dict(
        db.execute(
            select(Match.status, func.count()).where(Match.group_id == group.id).group_by(Match.status)
        ).all()
    )
    upcoming = db.scalar(
        select(func.count()).where(Match.group_id == group.id, Match.status.in_(_HELD), Match.starts_at > now)
    ) or 0
    next_match_at = db.scalar(
        select(func.min(Match.starts_at)).where(
            Match.group_id == group.id, Match.status.in_(_HELD), Match.starts_at > now
        )
    )
    last_match_at = db.scalar(
        select(func.max(Match.starts_at)).where(
            Match.group_id == group.id, Match.status.in_(_HELD), Match.starts_at <= now
        )
    )
    active_series = db.scalar(
        select(func.count()).where(MatchSeries.group_id == group.id, MatchSeries.is_active.is_(True))
    ) or 0
    creator = db.get(User, group.created_by_id)
    history = db.execute(
        select(AuditLog, User.name)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .where(AuditLog.group_id == group.id, AuditLog.action.like("platform.%"))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(30)
    ).all()
    admins = sum(1 for m, _ in active_members if m.role == GroupRole.ADMIN)
    return {
        **_row(group, len(active_members), admins, last_match_at),
        "description": group.description,
        "suspension_reason": group.suspension_reason,
        "creator_name": creator.name if creator else None,
        "creator_email": creator.email if creator else None,
        "pending_requests": by_status.get(MemberStatus.PENDING, 0),
        "inactive_members": by_status.get(MemberStatus.INACTIVE, 0),
        "removed_members": by_status.get(MemberStatus.REMOVED, 0),
        "matches_upcoming": upcoming,
        "matches_held": held,
        "matches_cancelled": by_match_status.get(MatchStatus.CANCELLED, 0),
        "next_match_at": next_match_at,
        "active_series": active_series,
        "active_members": [
            {"member_id": m.id, "user_id": u.id, "name": u.name, "email": u.email, "role": m.role,
             "joined_at": m.joined_at}
            for m, u in active_members
        ],
        "history": [
            {"id": e.id, "action": e.action, "actor_name": name, "reason": e.reason, "created_at": e.created_at}
            for e, name in history
        ],
    }


def _active_member_user_ids(db: Session, group_id: int) -> list[int]:
    return list(db.scalars(
        select(GroupMember.user_id).where(GroupMember.group_id == group_id, GroupMember.status == MemberStatus.ACTIVE)
    ).all())


def suspend(db: Session, group: FootballGroup, admin: User, reason: str) -> FootballGroup:
    if group.suspended_at is not None:
        raise BusinessRuleError("O grupo já está suspenso", code="already_suspended")
    reason = reason.strip()
    before = audit.snapshot(group, _STATUS_FIELDS)
    group.is_active = False
    group.suspended_at = utcnow()
    group.suspension_reason = reason
    audit.record(db, action="platform.group_suspended", entity_type="group", entity_id=group.id,
                 actor_user_id=admin.id, group_id=group.id, before=before,
                 after=audit.snapshot(group, _STATUS_FIELDS), reason=reason)
    notifications.notify(
        db, _active_member_user_ids(db, group.id), NotificationType.GROUP_STATUS, f"{group.name} foi suspenso",
        f"A administração da plataforma suspendeu o grupo. Motivo: {reason}. "
        "Ele continua disponível para consulta, mas sem alterações.",
        group_id=group.id,
    )
    db.commit()
    return group


def reactivate(db: Session, group: FootballGroup, admin: User, reason: str | None) -> FootballGroup:
    if group.is_active and group.suspended_at is None:
        raise BusinessRuleError("O grupo já está ativo", code="already_active")
    member_ids = _active_member_user_ids(db, group.id)
    if not member_ids:
        raise BusinessRuleError("O grupo não tem membros ativos para ser reativado", code="group_without_members")
    was_suspended = group.suspended_at is not None
    before = audit.snapshot(group, _STATUS_FIELDS)
    group.is_active = True
    group.suspended_at = None
    group.suspension_reason = None
    audit.record(db, action="platform.group_reactivated", entity_type="group", entity_id=group.id,
                 actor_user_id=admin.id, group_id=group.id, before=before,
                 after=audit.snapshot(group, _STATUS_FIELDS), reason=(reason or "").strip() or None)
    notifications.notify(
        db, member_ids, NotificationType.GROUP_STATUS, f"{group.name} foi reativado",
        "A suspensão foi encerrada e o grupo voltou ao normal." if was_suspended
        else "A administração da plataforma reativou o grupo.",
        group_id=group.id,
    )
    db.commit()
    return group


def set_member_role(
    db: Session, group: FootballGroup, member: GroupMember, role: GroupRole, admin: User, reason: str | None
) -> GroupMember:
    """Suporte: define quem administra o grupo (ex.: o único administrador saiu ou perdeu o acesso)."""
    if member.status != MemberStatus.ACTIVE:
        raise BusinessRuleError("Só membros ativos podem ter o papel alterado", code="member_not_active")
    if member.role == role:
        return member
    if role == GroupRole.PLAYER:
        admins = db.scalar(select(func.count()).where(
            GroupMember.group_id == group.id, GroupMember.role == GroupRole.ADMIN,
            GroupMember.status == MemberStatus.ACTIVE,
        )) or 0
        if admins <= 1:
            raise BusinessRuleError("O grupo precisa de ao menos um administrador ativo", code="last_admin")
    before = {"role": member.role}
    member.role = role
    audit.record(db, action="platform.group_role_changed", entity_type="group_member", entity_id=member.id,
                 actor_user_id=admin.id, group_id=group.id, before=before, after={"role": role},
                 reason=(reason or "").strip() or None)
    if role == GroupRole.ADMIN:
        title, body = f"Você agora administra {group.name}", "A administração da plataforma tornou você administrador."
    else:
        title, body = f"Você não administra mais {group.name}", "A administração da plataforma alterou seu papel."
    notifications.notify(db, [member.user_id], NotificationType.MEMBERSHIP, title, body, group_id=group.id)
    db.commit()
    return member

