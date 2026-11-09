"""Grupos, membros, convites e habilidades especiais (PRD §2, §3.3, §6)."""

import secrets

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError, Conflict, NotFound
from app.domain.enums import AttendanceStatus, GroupRole, MatchStatus, MemberStatus, NotificationType, Position
from app.models import FootballGroup, GroupMember, Match, MatchPlayer, SpecialSkill, User
from app.schemas.group import GroupCreate, GroupUpdate, MemberAdd, MemberAdminUpdate, MyMemberUpdate
from app.services import audit, notifications, platform, ratings, users

DEFAULT_SKILLS = [
    ("muralha", "Muralha", "Sou muito bom na defesa"),
    ("foguete", "Foguete", "Sou muito rápido"),
    ("matador", "Matador", "Tenho ótima finalização"),
    ("driblador", "Driblador", "Gosto de partir para cima"),
    ("maestro", "Maestro", "Tenho visão de jogo e bons passes"),
    ("motorzinho", "Motorzinho", "Tenho muita resistência e corro o jogo inteiro"),
    ("jogo_aereo", "Jogo aéreo", "Sou bom em bolas aéreas"),
    ("forte_fisicamente", "Forte fisicamente", "Tenho força e proteção de bola"),
    ("posicionamento", "Posicionamento", "Sei ocupar espaços e me posicionar"),
    ("bola_parada", "Bola parada", "Sou especialista em faltas e escanteios"),
    ("versatil", "Versátil", "Jogo bem em diferentes posições"),
    ("lider", "Líder", "Organizo e oriento o time"),
]

_GROUP_AUDIT_FIELDS = [
    "name", "description", "modality", "is_active", "require_join_approval", "waitlist_auto_promote",
    "waitlist_offer_expiry_minutes", "skill_change_requires_approval", "allow_manual_team_adjust",
    "goalkeeper_rating_mode", "initial_rating", "min_evaluations_to_consolidate", "rating_match_weight",
    "provisional_match_weight", "min_evaluators_full_weight", "outlier_threshold", "exclude_outliers",
    "draw_weights", "charge_no_shows", "charge_late_cancellations", "public_ranking_enabled",
    "debt_reminder_days", "discoverable", "discover_matches", "public_join_requires_approval", "city",
    "neighborhood",
]
_NULLABLE_SETTINGS = {"description", "city", "neighborhood"}


def seed_default_skills(db: Session) -> None:
    existing = set(db.scalars(select(SpecialSkill.code).where(SpecialSkill.group_id.is_(None))).all())
    for code, name, description in DEFAULT_SKILLS:
        if code not in existing:
            db.add(SpecialSkill(group_id=None, code=code, name=name, description=description))
    db.commit()


def _new_token() -> str:
    return secrets.token_urlsafe(24)


# ------------------------------------------------------------------ grupos


def create_group(
    db: Session, user: User, data: GroupCreate, *, actor: User | None = None, commit: bool = True
) -> tuple[FootballGroup, GroupMember]:
    """Cria o grupo com `user` como administrador. `actor` é quem cria, quando não é o próprio administrador
    (ex.: o espaço que cria a pelada e define quem a administra)."""
    actor = actor or user
    # regras de nota e sorteio partem dos padrões da plataforma; o que vier no pedido prevalece
    defaults = platform.group_defaults(db)
    values = data.model_dump(exclude_none=True)
    if "draw_weights" in values:
        values["draw_weights"] = {**defaults["draw_weights"], **values["draw_weights"]}
    group = FootballGroup(**{**defaults, **values}, invite_token=_new_token(), created_by_id=actor.id)
    if group.public_ranking_enabled:
        group.public_token = _new_token()
    db.add(group)
    db.flush()
    member = GroupMember(group_id=group.id, user_id=user.id, role=GroupRole.ADMIN, status=MemberStatus.ACTIVE)
    db.add(member)
    db.flush()
    ratings.ensure_member_ratings(db, group, member)
    audit.record(db, action="group.created", entity_type="group", entity_id=group.id, actor_user_id=actor.id,
                 group_id=group.id, after=audit.snapshot(group, _GROUP_AUDIT_FIELDS))
    if commit:
        db.commit()
    return group, member


def list_user_groups(db: Session, user: User) -> list[tuple[FootballGroup, GroupMember]]:
    rows = db.execute(
        select(FootballGroup, GroupMember)
        .join(GroupMember, GroupMember.group_id == FootballGroup.id)
        .where(GroupMember.user_id == user.id, GroupMember.status == MemberStatus.ACTIVE)
        .order_by(FootballGroup.name)
    ).all()
    return [(g, m) for g, m in rows]


def update_group(db: Session, group: FootballGroup, data: GroupUpdate, actor: User) -> FootballGroup:
    before = audit.snapshot(group, _GROUP_AUDIT_FIELDS)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None and key not in _NULLABLE_SETTINGS:
            continue
        if key == "draw_weights":
            value = {**group.draw_weights, **value}
        setattr(group, key, value)
    if group.public_ranking_enabled and not group.public_token:
        group.public_token = _new_token()
    after = audit.snapshot(group, _GROUP_AUDIT_FIELDS)
    if before != after:
        audit.record(db, action="group.updated", entity_type="group", entity_id=group.id, actor_user_id=actor.id,
                     group_id=group.id, before=before, after=after)
    db.commit()
    return group


def rotate_invite(db: Session, group: FootballGroup, actor: User) -> FootballGroup:
    group.invite_token = _new_token()
    audit.record(db, action="group.invite_rotated", entity_type="group", entity_id=group.id,
                 actor_user_id=actor.id, group_id=group.id)
    db.commit()
    return group


def rotate_public_token(db: Session, group: FootballGroup, actor: User) -> FootballGroup:
    group.public_token = _new_token()
    audit.record(db, action="group.public_link_rotated", entity_type="group", entity_id=group.id,
                 actor_user_id=actor.id, group_id=group.id)
    db.commit()
    return group


def group_admin_user_ids(db: Session, group_id: int) -> list[int]:
    return list(
        db.scalars(
            select(GroupMember.user_id).where(
                GroupMember.group_id == group_id,
                GroupMember.role == GroupRole.ADMIN,
                GroupMember.status == MemberStatus.ACTIVE,
            )
        ).all()
    )


# ------------------------------------------------------------------ membros


def on_member_activated(db: Session, group: FootballGroup, member: GroupMember) -> None:
    """Cria o perfil esportivo provisório e inclui o jogador nas próximas peladas publicadas."""
    ratings.ensure_member_ratings(db, group, member)
    upcoming = db.scalars(
        select(Match).where(
            Match.group_id == group.id,
            Match.status == MatchStatus.PUBLISHED,
            Match.starts_at > utcnow(),
        )
    ).all()
    for match in upcoming:
        exists = db.scalar(
            select(MatchPlayer.id).where(MatchPlayer.match_id == match.id, MatchPlayer.member_id == member.id)
        )
        if not exists:
            db.add(MatchPlayer(match_id=match.id, member_id=member.id, status=AttendanceStatus.PENDING))


def on_member_deactivated(db: Session, member: GroupMember) -> None:
    """Remove participações ainda não respondidas em peladas futuras."""
    rows = db.scalars(
        select(MatchPlayer)
        .join(Match, Match.id == MatchPlayer.match_id)
        .where(
            MatchPlayer.member_id == member.id,
            MatchPlayer.status == AttendanceStatus.PENDING,
            Match.starts_at > utcnow(),
        )
    ).all()
    for row in rows:
        db.delete(row)


def _join(
    db: Session, user: User, group: FootballGroup, *, needs_approval: bool, action: str, request_text: str | None
) -> GroupMember:
    """Entrada no grupo (convite ou descoberta). Não faz commit; quem chama decide."""
    member = db.scalar(select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.user_id == user.id))
    target_status = MemberStatus.PENDING if needs_approval else MemberStatus.ACTIVE
    if member is not None:
        if member.status in (MemberStatus.ACTIVE, MemberStatus.PENDING):
            return member
        if member.status == MemberStatus.INACTIVE:
            raise BusinessRuleError("Sua participação neste grupo está inativa. Fale com o administrador.",
                                    code="member_inactive")
        member.status = target_status  # removido anteriormente: nova solicitação
    else:
        member = GroupMember(group_id=group.id, user_id=user.id, role=GroupRole.PLAYER, status=target_status)
        db.add(member)
    db.flush()
    if member.status == MemberStatus.ACTIVE:
        on_member_activated(db, group, member)
    else:
        notifications.notify(db, group_admin_user_ids(db, group.id), NotificationType.MEMBERSHIP,
                             "Nova solicitação de entrada",
                             request_text or f"{user.name} pediu para entrar em {group.name}.",
                             group_id=group.id)
    audit.record(db, action=action, entity_type="group_member", entity_id=member.id,
                 actor_user_id=user.id, group_id=group.id, after={"status": member.status})
    return member


def join_by_token(db: Session, user: User, token: str) -> GroupMember:
    group = db.scalar(select(FootballGroup).where(FootballGroup.invite_token == token))
    if group is None or not group.is_active:
        raise NotFound("Convite inválido ou expirado", code="invalid_invite")
    member = _join(db, user, group, needs_approval=group.require_join_approval, action="member.joined_by_invite",
                   request_text=None)
    db.commit()
    return member


def join_public(db: Session, user: User, group: FootballGroup, request_text: str | None = None) -> GroupMember:
    """Entrada pela página pública da comunidade (sem convite). Por padrão passa pela aprovação de um admin
    (`public_join_requires_approval`). Não faz commit."""
    text = request_text or f"{user.name} encontrou {group.name} em Explorar e pediu para entrar."
    return _join(db, user, group, needs_approval=group.public_join_requires_approval,
                 action="member.joined_by_discovery", request_text=text)


def list_members(db: Session, group_id: int, *, status: MemberStatus | None, search: str | None) -> list[GroupMember]:
    stmt = select(GroupMember).join(User, User.id == GroupMember.user_id).where(GroupMember.group_id == group_id)
    if status:
        stmt = stmt.where(GroupMember.status == status)
    else:
        stmt = stmt.where(GroupMember.status != MemberStatus.GUEST)  # contratados não são membros
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(or_(func.lower(User.name).like(like), func.lower(GroupMember.nickname).like(like)))
    return list(db.scalars(stmt.order_by(User.name)).unique().all())


def add_member(db: Session, group: FootballGroup, data: MemberAdd, actor: User) -> GroupMember:
    user = users.get_or_create_placeholder(db, data.email, data.name)
    member = db.scalar(select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.user_id == user.id))
    if member is not None and member.status == MemberStatus.ACTIVE:
        raise Conflict("Jogador já faz parte do grupo", code="already_member")
    if member is None:
        member = GroupMember(group_id=group.id, user_id=user.id)
        db.add(member)
    member.role = data.role
    member.status = MemberStatus.ACTIVE
    member.preferred_position = data.preferred_position
    db.flush()
    on_member_activated(db, group, member)
    audit.record(db, action="member.added", entity_type="group_member", entity_id=member.id,
                 actor_user_id=actor.id, group_id=group.id,
                 after={"user_id": user.id, "role": member.role, "status": member.status})
    db.commit()
    return member


def _active_admins(db: Session, group_id: int) -> int:
    return db.scalar(
        select(func.count()).where(
            GroupMember.group_id == group_id,
            GroupMember.role == GroupRole.ADMIN,
            GroupMember.status == MemberStatus.ACTIVE,
        )
    ) or 0


def admin_update_member(
    db: Session, group: FootballGroup, member: GroupMember, data: MemberAdminUpdate, actor: User
) -> GroupMember:
    fields = ["role", "status", "nickname", "preferred_position"]
    before = audit.snapshot(member, fields)
    was_active = member.status == MemberStatus.ACTIVE
    changes = data.model_dump(exclude_unset=True, exclude={"reason"})
    losing_admin = member.is_admin and was_active and (
        changes.get("role") == GroupRole.PLAYER
        or changes.get("status") in (MemberStatus.INACTIVE, MemberStatus.REMOVED, MemberStatus.PENDING)
    )
    if losing_admin and _active_admins(db, group.id) <= 1:
        raise BusinessRuleError("O grupo precisa de ao menos um administrador ativo", code="last_admin")
    for key, value in changes.items():
        if value is not None or key == "nickname":
            setattr(member, key, value)
    db.flush()
    now_active = member.status == MemberStatus.ACTIVE
    if now_active and not was_active:
        on_member_activated(db, group, member)
        notifications.notify(db, [member.user_id], NotificationType.MEMBERSHIP, "Entrada aprovada",
                             f"Você agora faz parte de {group.name}.", group_id=group.id)
    elif was_active and not now_active:
        on_member_deactivated(db, member)
    elif now_active:
        ratings.ensure_member_ratings(db, group, member)
    after = audit.snapshot(member, fields)
    if before != after:
        audit.record(db, action="member.updated", entity_type="group_member", entity_id=member.id,
                     actor_user_id=actor.id, group_id=group.id, before=before, after=after, reason=data.reason)
    db.commit()
    return member


def update_my_member(db: Session, group: FootballGroup, member: GroupMember, data: MyMemberUpdate) -> GroupMember:
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is not None or key == "nickname":
            setattr(member, key, value)
    if member.preferred_position == Position.GOALKEEPER:
        ratings.ensure_member_ratings(db, group, member)
    db.commit()
    return member


# ------------------------------------------------------------------ habilidades


def list_skills(db: Session, group_id: int, include_inactive: bool = False) -> list[SpecialSkill]:
    stmt = select(SpecialSkill).where(or_(SpecialSkill.group_id.is_(None), SpecialSkill.group_id == group_id))
    if not include_inactive:
        stmt = stmt.where(SpecialSkill.is_active.is_(True))
    return list(db.scalars(stmt.order_by(SpecialSkill.group_id.is_(None).desc(), SpecialSkill.name)).all())


def create_skill(db: Session, group: FootballGroup, code: str, name: str, description: str, actor: User):
    clash = db.scalar(
        select(SpecialSkill.id).where(
            SpecialSkill.code == code, or_(SpecialSkill.group_id.is_(None), SpecialSkill.group_id == group.id)
        )
    )
    if clash:
        raise Conflict("Já existe uma habilidade com esse código", code="skill_exists")
    skill = SpecialSkill(group_id=group.id, code=code, name=name, description=description)
    db.add(skill)
    db.flush()
    audit.record(db, action="skill.created", entity_type="special_skill", entity_id=skill.id,
                 actor_user_id=actor.id, group_id=group.id, after={"code": code, "name": name})
    db.commit()
    return skill


def update_skill(db: Session, group: FootballGroup, skill_id: int, changes: dict, actor: User) -> SpecialSkill:
    skill = db.get(SpecialSkill, skill_id)
    if skill is None or skill.group_id != group.id:
        raise NotFound("Habilidade não encontrada (habilidades padrão não podem ser alteradas)")
    before = audit.snapshot(skill, ["name", "description", "is_active"])
    for key, value in changes.items():
        if value is not None:
            setattr(skill, key, value)
    audit.record(db, action="skill.updated", entity_type="special_skill", entity_id=skill.id,
                 actor_user_id=actor.id, group_id=group.id, before=before,
                 after=audit.snapshot(skill, ["name", "description", "is_active"]))
    db.commit()
    return skill


def choose_skill(db: Session, group: FootballGroup, member: GroupMember, skill_id: int) -> GroupMember:
    skill = db.get(SpecialSkill, skill_id)
    if skill is None or not skill.is_active or skill.group_id not in (None, group.id):
        raise NotFound("Habilidade não encontrada")
    if group.skill_change_requires_approval and not member.is_admin:
        member.pending_skill_id = skill.id
        notifications.notify(db, group_admin_user_ids(db, group.id), NotificationType.MEMBERSHIP,
                             "Alteração de habilidade aguardando aprovação",
                             f"{member.display_name} escolheu a habilidade {skill.name}.", group_id=group.id)
    else:
        member.special_skill_id = skill.id
        member.pending_skill_id = None
    db.commit()
    db.refresh(member)
    return member


def decide_skill(db: Session, group: FootballGroup, member: GroupMember, approve: bool, actor: User) -> GroupMember:
    if member.pending_skill_id is None:
        raise BusinessRuleError("Não há alteração de habilidade pendente", code="no_pending_skill")
    before = {"special_skill_id": member.special_skill_id, "pending_skill_id": member.pending_skill_id}
    if approve:
        member.special_skill_id = member.pending_skill_id
    member.pending_skill_id = None
    audit.record(db, action="skill.change_approved" if approve else "skill.change_rejected",
                 entity_type="group_member", entity_id=member.id, actor_user_id=actor.id, group_id=group.id,
                 before=before, after={"special_skill_id": member.special_skill_id})
    db.commit()
    db.refresh(member)
    return member
