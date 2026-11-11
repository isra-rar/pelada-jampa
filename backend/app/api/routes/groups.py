from datetime import date

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, Paging, page_of
from app.domain import rating as R
from app.domain.enums import MemberStatus, Position, RatingKind
from app.models import GroupMember, PlayerRating
from app.schemas.common import Page
from app.schemas.group import (
    GroupCreate,
    GroupOut,
    GroupUpdate,
    InviteOut,
    JoinOut,
    LevelReviewIn,
    MemberAdd,
    MemberAdminUpdate,
    MemberOut,
    MyMemberUpdate,
    SkillChoice,
    SkillCreate,
    SkillDecision,
    SkillOut,
    SkillUpdate,
)
from app.schemas.metrics import GroupMetricsOut
from app.schemas.misc import AdminDashboard, AuditOut
from app.schemas.player import (
    LevelReviewResult,
    PlayerProfile,
    PlayerSummary,
    ProvisionalRatingIn,
    RatingHistoryOut,
    RatingOut,
    SelfAssessmentIn,
    SelfAssessmentOut,
    SelfAssessmentReminderOut,
    SelfAssessmentResult,
)
from app.services import attendance, audit, dashboard, groups, level_quiz, metrics, payments, ratings, stats
from app.services.access import get_group, get_member_in_group, require_admin, require_member

router = APIRouter(tags=["Grupos, jogadores e perfis"])


def group_out(db, group, member: GroupMember) -> dict:
    data = GroupOut.model_validate(group).model_dump()
    data["my_role"] = member.role
    data["my_member_id"] = member.id
    data["my_self_assessment_pending"] = level_quiz.is_pending(db, member)
    data["invite_token"] = group.invite_token if member.is_admin else None
    data["public_token"] = group.public_token if (group.public_ranking_enabled or member.is_admin) else None
    return data


def member_out(db, m: GroupMember, *, show_email: bool, level: dict | None = None) -> dict:
    """`level`: situação do quiz já calculada (listas); só vai para administradores e o próprio jogador."""
    if show_email and level is None and m.status != MemberStatus.GUEST:
        level = level_quiz.info(db, m)
    return {
        "id": m.id,
        "user_id": m.user_id,
        "name": m.user.name,
        "nickname": m.nickname,
        "photo_url": m.user.photo_url,
        "email": m.user.email if show_email else None,
        "role": m.role,
        "status": m.status,
        "preferred_position": m.preferred_position,
        "special_skill": m.special_skill,
        "pending_skill": m.pending_skill,
        "joined_at": m.joined_at,
        # quiz de nível: só para administradores e o próprio jogador (mesma regra do e-mail)
        "self_assessment": level if show_email and m.status != MemberStatus.GUEST else None,
    }


# ------------------------------------------------------------------ grupos


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(data: GroupCreate, user: CurrentUser, db: DB):
    group, member = groups.create_group(db, user, data)
    return group_out(db, group, member)


@router.get("/groups", response_model=list[GroupOut])
def my_groups(user: CurrentUser, db: DB):
    return [group_out(db, g, m) for g, m in groups.list_user_groups(db, user)]


@router.get("/groups/{group_id}", response_model=GroupOut)
def get_group_detail(group_id: int, user: CurrentUser, db: DB):
    member = require_member(db, group_id, user)
    return group_out(db, get_group(db, group_id), member)


@router.patch("/groups/{group_id}", response_model=GroupOut)
def update_group(group_id: int, data: GroupUpdate, user: CurrentUser, db: DB):
    member = require_admin(db, group_id, user)
    return group_out(db, groups.update_group(db, get_group(db, group_id), data, user), member)


@router.post("/groups/{group_id}/invite/rotate", response_model=InviteOut)
def rotate_invite(group_id: int, user: CurrentUser, db: DB):
    """Gera um novo link de convite, invalidando o anterior."""
    require_admin(db, group_id, user)
    group = groups.rotate_invite(db, get_group(db, group_id), user)
    return {"invite_token": group.invite_token, "group_id": group.id}


@router.post("/groups/{group_id}/public-ranking/rotate", response_model=GroupOut)
def rotate_public_link(group_id: int, user: CurrentUser, db: DB):
    """Gera um novo link público do ranking, invalidando o anterior."""
    member = require_admin(db, group_id, user)
    return group_out(db, groups.rotate_public_token(db, get_group(db, group_id), user), member)


@router.post("/invites/{token}/join", response_model=JoinOut)
def join(token: str, user: CurrentUser, db: DB):
    """Entrar em um grupo pelo link de convite."""
    member = groups.join_by_token(db, user, token)
    return {"group_id": member.group_id, "member_id": member.id, "status": member.status}


@router.get("/groups/{group_id}/dashboard", response_model=AdminDashboard)
def admin_dashboard(group_id: int, user: CurrentUser, db: DB):
    member = require_admin(db, group_id, user)
    return dashboard.admin_dashboard(db, get_group(db, group_id), member)


@router.get("/groups/{group_id}/metrics", response_model=GroupMetricsOut)
def group_metrics(
    group_id: int, user: CurrentUser, db: DB,
    since: date | None = Query(default=None, description="Data inicial (inclusiva, horário de Brasília); padrão: 90 dias"),
    until: date | None = Query(default=None, description="Data final (inclusiva); padrão: hoje"),
):
    """Indicadores de sucesso do grupo (PRD §21): respostas, ocupação, desistências, avaliações,
    pagamentos, equilíbrio dos sorteios e retenção de jogadores."""
    require_admin(db, group_id, user)
    return metrics.group_metrics(db, group_id, since, until)


@router.get("/groups/{group_id}/audit", response_model=Page[AuditOut])
def group_audit(
    group_id: int, user: CurrentUser, db: DB, paging: Paging,
    entity_type: str | None = None, entity_id: int | None = None,
):
    require_admin(db, group_id, user)
    items, total = audit.list_group_audit(db, group_id, entity_type=entity_type, entity_id=entity_id,
                                          page=paging.page, size=paging.size)
    return page_of(items, total, paging)


# ------------------------------------------------------------------ membros


@router.get("/groups/{group_id}/members", response_model=list[MemberOut])
def list_members(
    group_id: int, user: CurrentUser, db: DB,
    status_: MemberStatus | None = Query(default=None, alias="status"), search: str | None = None,
):
    me = require_member(db, group_id, user)
    if not me.is_admin:
        status_ = MemberStatus.ACTIVE
    members = groups.list_members(db, group_id, status=status_, search=search)
    visible = [m for m in members if me.is_admin or m.id == me.id]
    levels = level_quiz.info_bulk(db, visible)
    return [member_out(db, m, show_email=me.is_admin or m.id == me.id, level=levels.get(m.id))
            for m in members]


@router.post("/groups/{group_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(group_id: int, data: MemberAdd, user: CurrentUser, db: DB):
    """Adiciona manualmente um jogador (cria a conta se o e-mail ainda não existir)."""
    require_admin(db, group_id, user)
    return member_out(db, groups.add_member(db, get_group(db, group_id), data, user), show_email=True)


@router.patch("/groups/{group_id}/members/me", response_model=MemberOut)
def update_my_membership(group_id: int, data: MyMemberUpdate, user: CurrentUser, db: DB):
    me = require_member(db, group_id, user)
    return member_out(db, groups.update_my_member(db, get_group(db, group_id), me, data), show_email=True)


@router.patch("/groups/{group_id}/members/{member_id}", response_model=MemberOut)
def admin_update_member(group_id: int, member_id: int, data: MemberAdminUpdate, user: CurrentUser, db: DB):
    """Aprovar solicitação, inativar, remover, promover a administrador etc."""
    require_admin(db, group_id, user)
    member = get_member_in_group(db, group_id, member_id)
    updated = groups.admin_update_member(db, get_group(db, group_id), member, data, user)
    return member_out(db, updated, show_email=True)


# ------------------------------------------------------------------ habilidades


@router.get("/groups/{group_id}/skills", response_model=list[SkillOut])
def list_skills(group_id: int, user: CurrentUser, db: DB, include_inactive: bool = False):
    me = require_member(db, group_id, user)
    return groups.list_skills(db, group_id, include_inactive=include_inactive and me.is_admin)


@router.post("/groups/{group_id}/skills", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
def create_skill(group_id: int, data: SkillCreate, user: CurrentUser, db: DB):
    require_admin(db, group_id, user)
    return groups.create_skill(db, get_group(db, group_id), data.code, data.name, data.description, user)


@router.patch("/groups/{group_id}/skills/{skill_id}", response_model=SkillOut)
def update_skill(group_id: int, skill_id: int, data: SkillUpdate, user: CurrentUser, db: DB):
    require_admin(db, group_id, user)
    return groups.update_skill(db, get_group(db, group_id), skill_id, data.model_dump(exclude_unset=True), user)


@router.put("/groups/{group_id}/members/me/skill", response_model=MemberOut)
def choose_skill(group_id: int, data: SkillChoice, user: CurrentUser, db: DB):
    """Escolhe a habilidade principal. Pode exigir aprovação conforme o grupo."""
    me = require_member(db, group_id, user)
    return member_out(db, groups.choose_skill(db, get_group(db, group_id), me, data.skill_id), show_email=True)


@router.post("/groups/{group_id}/members/{member_id}/skill-decision", response_model=MemberOut)
def decide_skill(group_id: int, member_id: int, data: SkillDecision, user: CurrentUser, db: DB):
    require_admin(db, group_id, user)
    member = get_member_in_group(db, group_id, member_id)
    decided = groups.decide_skill(db, get_group(db, group_id), member, data.approve, user)
    return member_out(db, decided, show_email=True)


# ------------------------------------------------------------------ jogadores / perfil esportivo


@router.get("/groups/{group_id}/players", response_model=list[PlayerSummary])
def list_players(
    group_id: int, user: CurrentUser, db: DB,
    search: str | None = None, stars: int | None = Query(default=None, ge=0, le=5),
    skill: str | None = None, position: Position | None = None,
):
    require_member(db, group_id, user)
    members = groups.list_members(db, group_id, status=MemberStatus.ACTIVE, search=search)
    rows = db.scalars(select(PlayerRating).where(PlayerRating.member_id.in_([m.id for m in members]))).all()
    by_member: dict[tuple[int, RatingKind], PlayerRating] = {(r.member_id, r.kind): r for r in rows}
    out = []
    for m in members:
        field = by_member.get((m.id, RatingKind.FIELD))
        if field is None:
            continue
        gk = by_member.get((m.id, RatingKind.GOALKEEPER))
        if stars is not None and field.stars != stars and not (gk and gk.stars == stars):
            continue
        if skill and (m.special_skill is None or m.special_skill.code != skill):
            continue
        if position and m.preferred_position != position:
            continue
        out.append({
            "member_id": m.id, "user_id": m.user_id, "name": m.display_name, "photo_url": m.user.photo_url,
            "preferred_position": m.preferred_position, "special_skill": m.special_skill,
            "stars": field.stars, "average": field.average, "average_display": R.display_average(field.average),
            "is_provisional": field.is_provisional,
            "goalkeeper_stars": gk.stars if gk else None,
            "goalkeeper_average_display": R.display_average(gk.average) if gk else None,
        })
    out.sort(key=lambda p: (-p["average"], p["name"]))
    return out


def _profile(db, group, member: GroupMember, viewer: GroupMember) -> dict:
    ratings.ensure_member_ratings(db, group, member)
    db.commit()
    rows = db.scalars(select(PlayerRating).where(PlayerRating.member_id == member.id).order_by(PlayerRating.kind)).all()
    can_see_finance = viewer.is_admin or viewer.id == member.id
    return {
        "member_id": member.id,
        "user_id": member.user_id,
        "name": member.user.name,
        "nickname": member.nickname,
        "photo_url": member.user.photo_url,
        "preferred_position": member.preferred_position,
        "special_skill": member.special_skill,
        "ratings": [ratings.to_out(r) for r in rows],
        "attendance": attendance.attendance_stats(db, member.id),
        "finance": payments.member_finance(db, group.id, member.id) if can_see_finance else None,
        "rating_explanation": ratings.explanation(db, group),
        "game_stats": stats.member_stats(db, group.id, member.id),
        "pending_skill": member.pending_skill if can_see_finance else None,
    }


@router.get("/groups/{group_id}/players/me", response_model=PlayerProfile)
def my_profile(group_id: int, user: CurrentUser, db: DB):
    me = require_member(db, group_id, user)
    return _profile(db, get_group(db, group_id), me, me)


@router.get("/groups/{group_id}/self-assessment", response_model=SelfAssessmentOut)
def get_self_assessment(group_id: int, user: CurrentUser, db: DB):
    """Quiz de nível: cinco perguntas sobre o próprio futebol (de linha ou de goleiro) e o nível que o jogador diz
    ter. Fica pendente para todo membro ativo que ainda não respondeu, inclusive quem já estava no grupo; quem
    já tem perfil consolidado pelas avaliações fica dispensado."""
    me = require_member(db, group_id, user)
    return level_quiz.quiz_out(db, get_group(db, group_id), me)


@router.post("/groups/{group_id}/self-assessment", response_model=SelfAssessmentResult)
def submit_self_assessment(group_id: int, data: SelfAssessmentIn, user: CurrentUser, db: DB):
    """Envia as respostas e o nível declarado. Se o perfil ainda não tem avaliações nem nota definida pelo
    administrador, as respostas viram a nota inicial provisória; o nível declarado aguarda a confirmação de outro
    administrador (`POST /groups/{id}/members/{member_id}/level-review`). Único administrador: confirmado na
    hora."""
    me = require_member(db, group_id, user)
    rating, applied = level_quiz.submit(db, get_group(db, group_id), me, data.answers, data.declared_stars, data.kind)
    db.commit()
    return {"applied": applied, "rating": ratings.to_out(rating), "self_assessment": level_quiz.info(db, me)}


@router.post("/groups/{group_id}/self-assessment/remind", response_model=SelfAssessmentReminderOut)
def remind_self_assessment(group_id: int, user: CurrentUser, db: DB):
    """Avisa quem ainda não respondeu o quiz (quem foi lembrado nas últimas 12 horas fica de fora)."""
    require_admin(db, group_id, user)
    return {"reminded": level_quiz.remind_pending(db, get_group(db, group_id), user)}


@router.post("/groups/{group_id}/members/{member_id}/level-review", response_model=LevelReviewResult)
def review_level(group_id: int, member_id: int, data: LevelReviewIn, user: CurrentUser, db: DB):
    """Administrador confirma (ou ajusta) o nível em estrelas que o jogador declarou no quiz. Enquanto o perfil é
    provisório, a nota passa a ter esse nível; depois as avaliações pós-pelada seguem ajustando. O próprio nível
    só pode ser confirmado por outro administrador, quando houver."""
    require_admin(db, group_id, user)
    member = get_member_in_group(db, group_id, member_id)
    rating, applied = level_quiz.review(db, get_group(db, group_id), member, data.stars, user, data.reason)
    db.commit()
    return {"applied": applied, "rating": ratings.to_out(rating), "self_assessment": level_quiz.info(db, member)}


@router.get("/groups/{group_id}/players/{member_id}", response_model=PlayerProfile)
def player_profile(group_id: int, member_id: int, user: CurrentUser, db: DB):
    me = require_member(db, group_id, user)
    member = get_member_in_group(db, group_id, member_id)
    return _profile(db, get_group(db, group_id), member, me)


@router.get("/groups/{group_id}/players/{member_id}/rating-history", response_model=Page[RatingHistoryOut])
def rating_history(group_id: int, member_id: int, user: CurrentUser, db: DB, paging: Paging,
                   kind: RatingKind | None = None):
    """Evolução dos atributos. Disponível ao próprio jogador e aos administradores (auditoria)."""
    me = require_member(db, group_id, user)
    member = get_member_in_group(db, group_id, member_id)
    if not (me.is_admin or me.id == member.id):
        from app.core.errors import Forbidden

        raise Forbidden("Histórico detalhado disponível apenas para o próprio jogador e administradores")
    items, total = ratings.history(db, member.id, kind, paging.page, paging.size)
    return page_of(items, total, paging)


@router.put("/groups/{group_id}/players/{member_id}/provisional-rating", response_model=RatingOut)
def set_provisional_rating(group_id: int, member_id: int, data: ProvisionalRatingIn, user: CurrentUser, db: DB):
    """Administrador define a nota inicial provisória (somente perfis ainda provisórios)."""
    require_admin(db, group_id, user)
    group = get_group(db, group_id)
    member = get_member_in_group(db, group_id, member_id)
    rating = ratings.set_provisional(db, group, member, data.kind, data.attributes, user.id, data.reason)
    db.commit()
    return ratings.to_out(rating)
