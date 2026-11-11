"""Direitos do titular dos dados (LGPD): exportação e exclusão da conta.

A exclusão anonimiza a conta em vez de apagar linhas: pagamentos, presenças, placares e avaliações continuam
no histórico do grupo (interesse legítimo e integridade das notas), mas sem nome, e-mail, telefone ou foto.
"""

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError
from app.core.security import verify_password
from app.domain.enums import AttendanceStatus as S
from app.domain.enums import GroupRole, MatchStatus, MemberStatus, VenueRole
from app.models import (
    Evaluation,
    FootballGroup,
    GroupMember,
    Match,
    MatchPlayer,
    Notification,
    PasswordResetToken,
    Payment,
    PlayerRating,
    PlayerRatingHistory,
    PushSubscription,
    User,
    Venue,
    VenueStaff,
)
from app.services import attendance, audit, media, ratings
from app.services.payments import payment_out

DELETED_NAME = "Jogador removido"
CONFIRM_WORD = "EXCLUIR"


# ------------------------------------------------------------------ exportação


def _group_export(db: Session, member: GroupMember) -> dict:
    group = db.get(FootballGroup, member.group_id)
    participations = db.execute(
        select(MatchPlayer, Match).join(Match, Match.id == MatchPlayer.match_id)
        .where(MatchPlayer.member_id == member.id).order_by(Match.starts_at)
    ).all()
    history = db.scalars(
        select(PlayerRatingHistory).where(PlayerRatingHistory.member_id == member.id)
        .order_by(PlayerRatingHistory.created_at, PlayerRatingHistory.id)
    ).all()
    given = db.scalars(
        select(Evaluation).where(Evaluation.evaluator_member_id == member.id).order_by(Evaluation.created_at)
    ).all()
    evaluated = {
        m.id: m.display_name
        for m in db.scalars(select(GroupMember).where(GroupMember.id.in_({e.evaluated_member_id for e in given})))
    } if given else {}
    payments = db.scalars(
        select(Payment).where(Payment.member_id == member.id).order_by(Payment.created_at)
    ).unique().all()
    return {
        "group_id": member.group_id,
        "group_name": group.name if group else None,
        "role": member.role,
        "status": member.status,
        "nickname": member.nickname,
        "preferred_position": member.preferred_position,
        "special_skill": member.special_skill.name if member.special_skill else None,
        "joined_at": member.joined_at,
        # quiz de nível: respostas e níveis (calculado, declarado e confirmado pelo administrador)
        "self_assessment": {
            "kind": member.self_assessment_kind,
            "answered_at": member.self_assessed_at,
            "answers": member.self_assessment,
            "quiz_stars": member.quiz_stars,
            "declared_stars": member.declared_stars,
            "confirmed_stars": member.confirmed_stars,
            "reviewed_at": member.level_reviewed_at,
        } if member.self_assessed_at is not None else None,
        "ratings": [ratings.to_out(r) for r in db.scalars(
            select(PlayerRating).where(PlayerRating.member_id == member.id)
        ).all()],
        # notas consolidadas de cada pelada; avaliações individuais recebidas não são exportadas para
        # preservar o anonimato de quem avaliou
        "rating_history": [
            {
                "date": h.created_at, "reason": h.reason, "match_id": h.match_id, "kind": h.kind,
                "match_scores": h.match_scores, "evaluations_count": h.evaluations_count,
                "average_before": h.average_before, "average_after": h.average_after,
                "stars_before": h.stars_before, "stars_after": h.stars_after, "attributes_after": h.attributes_after,
            }
            for h in history
        ],
        "participations": [
            {
                "match_id": m.id, "match_name": m.name, "starts_at": m.starts_at, "location": m.location,
                "status": mp.status, "is_goalkeeper": mp.is_goalkeeper, "confirmed_at": mp.confirmed_at,
                "cancelled_at": mp.cancelled_at, "late_cancellation": mp.late_cancellation,
            }
            for mp, m in participations
        ],
        "payments": [payment_out(p) for p in payments],
        "evaluations_given": [
            {"match_id": e.match_id, "evaluated": evaluated.get(e.evaluated_member_id), "kind": e.kind,
             "scores": e.scores, "date": e.created_at}
            for e in given
        ],
    }


def export_data(db: Session, user: User) -> dict:
    members = db.scalars(select(GroupMember).where(GroupMember.user_id == user.id).order_by(GroupMember.id)).all()
    notifications = db.scalars(
        select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at)
    ).all()
    devices = db.scalars(select(PushSubscription).where(PushSubscription.user_id == user.id)).all()
    venue_roles = db.execute(
        select(VenueStaff, Venue.name).join(Venue, Venue.id == VenueStaff.venue_id)
        .where(VenueStaff.user_id == user.id).order_by(VenueStaff.id)
    ).all()
    return {
        "generated_at": utcnow(),
        "account": {
            "id": user.id, "email": user.email, "name": user.name, "phone": user.phone,
            "photo_url": user.photo_url, "has_password": user.has_password, "google_linked": user.google_linked,
            "created_at": user.created_at, "updated_at": user.updated_at,
        },
        "groups": [_group_export(db, m) for m in members],
        "notifications": [
            {"type": n.type, "title": n.title, "body": n.body, "created_at": n.created_at, "read_at": n.read_at}
            for n in notifications
        ],
        "push_devices": [{"user_agent": d.user_agent, "created_at": d.created_at} for d in devices],
        "venues": [
            {"venue": name, "role": s.role, "is_active": s.is_active, "since": s.created_at}
            for s, name in venue_roles
        ],
        **_pro_export(db, user),
    }


def _pro_export(db: Session, user: User) -> dict:
    """Anúncio de goleiro/juiz e pedidos recebidos."""
    from app.services import pros

    profile = pros.my_profile(db, user)
    hires = pros.all_for_pro(db, user)  # exportação: o histórico inteiro, sem paginar
    return {
        "pro_profile": pros.profile_out(db, profile) if profile else None,
        "pro_hires": [
            {"match": h.match.name, "starts_at": h.match.starts_at, "role": h.role, "price": h.price,
             "status": h.status, "message": h.message, "response_note": h.response_note,
             "created_at": h.created_at}
            for h in hires
        ],
    }


# ------------------------------------------------------------------ exclusão


def _check_can_delete(db: Session, user: User, members: list[GroupMember]) -> None:
    for staff, venue in db.execute(
        select(VenueStaff, Venue).join(Venue, Venue.id == VenueStaff.venue_id).where(
            VenueStaff.user_id == user.id, VenueStaff.is_active.is_(True), VenueStaff.role == VenueRole.OWNER,
            Venue.is_active.is_(True))
    ).all():
        owners = db.scalar(select(func.count()).select_from(VenueStaff).where(
            VenueStaff.venue_id == venue.id, VenueStaff.role == VenueRole.OWNER, VenueStaff.is_active.is_(True),
            VenueStaff.id != staff.id)) or 0
        if not owners:
            raise BusinessRuleError(
                f"Você é o único dono de “{venue.name}”. Defina outro dono ou desative o espaço antes de excluir "
                "a conta.", code="sole_venue_owner", details={"venue_id": venue.id},
            )
    if user.is_platform_admin:
        others = db.scalar(select(func.count()).select_from(User).where(
            User.is_platform_admin.is_(True), User.is_active.is_(True), User.id != user.id)) or 0
        if not others:
            raise BusinessRuleError("Você é o único administrador da plataforma. Defina outro antes de excluir a conta.",
                                    code="sole_platform_admin")
    for m in members:
        if not (m.is_admin and m.status == MemberStatus.ACTIVE):
            continue
        group = db.get(FootballGroup, m.group_id)
        if group is None or not group.is_active:
            continue
        others = db.execute(
            select(GroupMember.role).where(GroupMember.group_id == m.group_id, GroupMember.id != m.id,
                                           GroupMember.status == MemberStatus.ACTIVE)
        ).scalars().all()
        if others and GroupRole.ADMIN not in others:
            raise BusinessRuleError(
                f"Você é o único administrador de “{group.name}”. Defina outro administrador antes de excluir a conta.",
                code="sole_admin", details={"group_id": group.id},
            )


def _leave_upcoming_matches(db: Session, member: GroupMember) -> None:
    """Libera as vagas nas próximas peladas (sem cobrança de desistência) e chama a lista de espera."""
    now = utcnow()
    rows = db.execute(
        select(MatchPlayer, Match).join(Match, Match.id == MatchPlayer.match_id)
        .where(MatchPlayer.member_id == member.id, Match.status == MatchStatus.PUBLISHED, Match.starts_at > now)
    ).all()
    freed: dict[int, Match] = {}
    for mp, match in rows:
        if mp.status == S.CONFIRMED:
            mp.status = S.CANCELLED
            mp.cancelled_at = now
            mp.late_cancellation = False
            freed[match.id] = match
        elif mp.status in (S.PENDING, S.WAITLIST):
            if mp.promotion_offered_at is not None:
                freed[match.id] = match
            mp.status = S.DECLINED
            mp.promotion_offered_at = None
            mp.responded_at = now
    for match in freed.values():
        attendance.promote_waitlist(db, match)


def delete_account(db: Session, user: User, *, password: str | None, confirm: str) -> None:
    if confirm.strip().upper() != CONFIRM_WORD:
        raise BusinessRuleError(f"Digite {CONFIRM_WORD} para confirmar", code="confirmation_required")
    if user.has_password and not verify_password(password or "", user.password_hash):
        raise BusinessRuleError("Senha incorreta", code="invalid_password")
    members = list(db.scalars(select(GroupMember).where(GroupMember.user_id == user.id)).all())
    _check_can_delete(db, user, members)

    now = utcnow()
    for m in members:
        _leave_upcoming_matches(db, m)
        before = audit.snapshot(m, ["role", "status", "nickname"])
        m.status = MemberStatus.REMOVED
        m.role = GroupRole.PLAYER
        m.nickname = None
        m.pending_skill_id = None
        m.self_assessment = None  # respostas do quiz são dados pessoais; o nível fica (histórico do grupo)
        audit.record(db, action="member.account_deleted", entity_type="group_member", entity_id=m.id,
                     actor_user_id=user.id, group_id=m.group_id, before=before,
                     after=audit.snapshot(m, ["role", "status", "nickname"]))
        group = db.get(FootballGroup, m.group_id)
        still_active = db.scalar(select(func.count()).select_from(GroupMember).where(
            GroupMember.group_id == m.group_id, GroupMember.status == MemberStatus.ACTIVE, GroupMember.id != m.id))
        if group is not None and group.is_active and not still_active:
            group.is_active = False  # ninguém mais no grupo

    for staff in db.scalars(select(VenueStaff).where(VenueStaff.user_id == user.id,
                                                     VenueStaff.is_active.is_(True))).all():
        staff.is_active = False  # sai da equipe dos espaços

    from app.services import pros

    pros.retire_profile(db, user)  # anúncio sai do ar e pedidos abertos são cancelados

    old_photo = user.photo_url
    user.name = DELETED_NAME
    user.email = f"removido-{user.id}@contas.invalid"
    user.phone = None
    user.photo_url = None
    user.password_hash = None
    user.google_sub = None
    user.is_active = False
    user.is_platform_admin = False
    user.password_changed_at = now  # invalida qualquer sessão aberta
    user.deleted_at = now
    for model in (PushSubscription, PasswordResetToken, Notification):
        db.execute(delete(model).where(model.user_id == user.id))
    audit.record(db, action="user.deleted", entity_type="user", entity_id=user.id, actor_user_id=user.id,
                 group_id=None, reason="Exclusão solicitada pelo titular")
    db.commit()
    media.delete_if_local(old_photo)
