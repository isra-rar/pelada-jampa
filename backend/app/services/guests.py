"""Convidados: jogadores sem conta trazidos por um membro do grupo.

- Qualquer membro ativo cadastra um convidado no grupo (sem precisar de pelada) e diz quantas estrelas acha que
  ele tem. O convidado fica com esse nível; se quem convidou não é administrador, o nível entra na fila de
  confirmação do admin (a mesma do quiz de nível).
- O convidado é um membro com status `guest` e `invited_by_member_id`: não tem acesso ao grupo, não aparece no
  ranking, não recebe avisos (quem trouxe recebe) e não avalia, mas é avaliado depois da pelada.
- Quem trouxe (ou um admin) coloca o convidado nas peladas; ele ocupa vaga como qualquer jogador (ou entra na
  espera). A cobrança da pelada vai para quem trouxe (`Payment.payer_member_id`).
"""

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError, Forbidden, NotFound
from app.domain import rating as R
from app.domain.enums import (
    AttendanceStatus,
    GroupRole,
    MatchStatus,
    MemberStatus,
    NotificationType,
    Position,
    RatingKind,
)
from app.models import FootballGroup, GroupMember, Match, MatchPlayer, User
from app.services import attendance, audit, level_quiz, notifications, platform, ratings
from app.services.users import GUEST_EMAIL_DOMAIN

def _guest_email() -> str:
    return f"convidado-{secrets.token_hex(10)}@{GUEST_EMAIL_DOMAIN}"  # domínio reservado: nunca recebe e-mail


def list_guests(db: Session, group_id: int) -> list[GroupMember]:
    return list(db.scalars(
        select(GroupMember).join(User, User.id == GroupMember.user_id).where(
            GroupMember.group_id == group_id,
            GroupMember.status == MemberStatus.GUEST,
            GroupMember.invited_by_member_id.is_not(None),
        ).order_by(User.name)
    ).unique().all())


def get_guest(db: Session, group_id: int, member_id: int) -> GroupMember:
    guest = db.get(GroupMember, member_id)
    if guest is None or guest.group_id != group_id or not guest.is_invited_guest \
            or guest.status != MemberStatus.GUEST:
        raise NotFound("Convidado não encontrado")
    return guest


def _require_manager(guest: GroupMember, actor: GroupMember) -> None:
    if not (actor.is_admin or guest.invited_by_member_id == actor.id):
        raise Forbidden("Só quem convidou ou um administrador pode mexer neste convidado")


def guest_out(db: Session, guest: GroupMember, viewer: GroupMember, level: dict | None = None) -> dict:
    kind = RatingKind(guest.self_assessment_kind or RatingKind.FIELD)
    rating = ratings.get_rating(db, guest.id, kind)
    can_manage = viewer.is_admin or guest.invited_by_member_id == viewer.id
    games = db.scalar(
        select(MatchPlayer.id).where(MatchPlayer.member_id == guest.id).limit(1)
    )
    return {
        "member_id": guest.id,
        "name": guest.display_name,
        "invited_by_member_id": guest.invited_by_member_id,
        "invited_by_name": guest.invited_by.display_name if guest.invited_by else None,
        "is_goalkeeper": guest.preferred_position == Position.GOALKEEPER,
        "stars": rating.stars if rating else None,
        "average_display": R.display_average(rating.average) if rating else None,
        "level": (level or level_quiz.info(db, guest)) if can_manage else None,
        "can_manage": can_manage,
        "has_played": games is not None,
        "created_at": guest.joined_at,
    }


def create_guest(
    db: Session, group: FootballGroup, inviter: GroupMember, name: str, stars: int, goalkeeper: bool
) -> GroupMember:
    if not 0 <= stars <= R.MAX_STARS:
        raise BusinessRuleError("Escolha um nível de 0 a 5 estrelas", code="invalid_stars")
    user = User(email=_guest_email(), name=name.strip(), password_hash=None)
    db.add(user)
    db.flush()
    guest = GroupMember(
        group_id=group.id, user_id=user.id, role=GroupRole.PLAYER, status=MemberStatus.GUEST,
        preferred_position=Position.GOALKEEPER if goalkeeper else Position.ANY,
        invited_by_member_id=inviter.id,
    )
    db.add(guest)
    db.flush()
    ratings.ensure_member_ratings(db, group, guest)

    # o nível sugerido por quem convidou vale desde já; o admin confirma (ou ajusta) depois
    kind = RatingKind.GOALKEEPER if goalkeeper else RatingKind.FIELD
    keys = ratings.keys_for(group, kind)
    thresholds = platform.star_thresholds(db)
    rating = ratings.ensure_rating(db, group, guest, kind)
    before, avg_before, stars_before = dict(rating.attributes), rating.average, rating.stars
    rating.attributes = R.initial_attributes(keys, R.target_average(stars, thresholds))
    rating.average = R.compute_average(rating.attributes, keys)
    rating.stars = R.compute_stars(rating.average, thresholds)
    rating.updated_at = utcnow()
    reason = "level_confirmed" if inviter.is_admin else "guest_level"
    ratings.record_history(db, rating, reason=reason, before=before, avg_before=avg_before,
                           stars_before=stars_before, actor_user_id=inviter.user_id)

    guest.self_assessment_kind = kind.value
    guest.self_assessed_at = utcnow()
    guest.declared_stars = stars
    if inviter.is_admin:
        guest.confirmed_stars = stars
        guest.level_reviewed_at = utcnow()
    audit.record(db, action="guest.created", entity_type="group_member", entity_id=guest.id,
                 actor_user_id=inviter.user_id, group_id=group.id,
                 after={"name": user.name, "stars": stars, "goalkeeper": goalkeeper, "invited_by": inviter.id})
    if not inviter.is_admin:
        admins = level_quiz.active_admin_ids(db, group.id)
        where = " no gol" if goalkeeper else ""
        notifications.notify(
            db, admins, NotificationType.MEMBERSHIP, "Nível de convidado para confirmar",
            f"{inviter.display_name} convidou {user.name} e acha que ele tem "
            f"{level_quiz.stars_text(stars)}{where}. Confirme o nível em Gestão > Membros.",
            group_id=group.id,
        )
    db.commit()
    db.refresh(guest)
    return guest


def update_guest(
    db: Session, group: FootballGroup, guest: GroupMember, actor: GroupMember, *, name: str | None,
    goalkeeper: bool | None,
) -> GroupMember:
    _require_manager(guest, actor)
    before = {"name": guest.user.name, "position": guest.preferred_position}
    if name is not None:
        guest.user.name = name.strip()
    if goalkeeper is not None:
        guest.preferred_position = Position.GOALKEEPER if goalkeeper else Position.ANY
        ratings.ensure_member_ratings(db, group, guest)
    audit.record(db, action="guest.updated", entity_type="group_member", entity_id=guest.id,
                 actor_user_id=actor.user_id, group_id=group.id, before=before,
                 after={"name": guest.user.name, "position": guest.preferred_position})
    db.commit()
    db.refresh(guest)
    return guest


def remove_guest(db: Session, group: FootballGroup, guest: GroupMember, actor: GroupMember) -> None:
    """Tira o convidado do grupo e das peladas futuras em aberto. Histórico e cobranças continuam."""
    _require_manager(guest, actor)
    now = utcnow()
    rows = db.scalars(
        select(MatchPlayer).join(Match, Match.id == MatchPlayer.match_id).where(
            MatchPlayer.member_id == guest.id,
            Match.starts_at > now,
            MatchPlayer.status.in_((AttendanceStatus.CONFIRMED, AttendanceStatus.WAITLIST)),
        )
    ).all()
    for mp in rows:
        mp.status = AttendanceStatus.CANCELLED
        mp.cancelled_at = now
        attendance.promote_waitlist(db, db.get(Match, mp.match_id))
    guest.status = MemberStatus.REMOVED
    audit.record(db, action="guest.removed", entity_type="group_member", entity_id=guest.id,
                 actor_user_id=actor.user_id, group_id=group.id)
    db.commit()


def _require_open(match: Match) -> None:
    if match.status != MatchStatus.PUBLISHED or utcnow() >= match.starts_at:
        raise BusinessRuleError("A pelada não está aberta para confirmações", code="match_closed")


def add_to_match(
    db: Session, group: FootballGroup, match: Match, guest: GroupMember, actor: GroupMember, as_goalkeeper: bool
) -> tuple[MatchPlayer, list[str]]:
    """Coloca o convidado na pelada: ocupa vaga como qualquer jogador ou entra na lista de espera."""
    _require_manager(guest, actor)
    _require_open(match)
    warnings: list[str] = []
    mp = attendance.get_participation(db, match, guest.id)
    if mp is not None and mp.status in (AttendanceStatus.CONFIRMED, AttendanceStatus.WAITLIST):
        mp.is_goalkeeper = as_goalkeeper
        db.commit()
        return mp, warnings
    if mp is None:
        mp = MatchPlayer(match_id=match.id, member_id=guest.id, status=AttendanceStatus.PENDING)
        db.add(mp)
        db.flush()
    mp.is_goalkeeper = as_goalkeeper
    mp.blocked = False
    mp.responded_at = utcnow()
    if attendance._occupied(db, match) < match.slots:
        attendance._set_confirmed(mp)
    else:
        if attendance.waitlist_full(db, match):
            raise BusinessRuleError("Vagas e lista de espera esgotadas", code="waitlist_full")
        attendance._enter_waitlist(db, match, mp)
        db.flush()
        if attendance.maybe_expand(db, group, match) and mp.status == AttendanceStatus.CONFIRMED:
            warnings.append(f"A lista de espera lotou e a pelada aumentou para {match.slots} jogadores")
        else:
            position = len(attendance.waitlist(db, match))
            warnings.append(f"Vagas esgotadas: {guest.display_name} entrou na lista de espera (posição {position})")
    audit.record(db, action="guest.added_to_match", entity_type="match_player", entity_id=mp.id,
                 actor_user_id=actor.user_id, group_id=group.id, after={"status": mp.status})
    db.commit()
    db.refresh(mp)
    return mp, warnings


def remove_from_match(db: Session, group: FootballGroup, match: Match, guest: GroupMember,
                      actor: GroupMember) -> MatchPlayer:
    _require_manager(guest, actor)
    _require_open(match)
    mp = attendance.get_participation(db, match, guest.id)
    if mp is None or mp.status not in (AttendanceStatus.CONFIRMED, AttendanceStatus.WAITLIST):
        raise BusinessRuleError("O convidado não está nesta pelada", code="not_in_match")
    mp.status = AttendanceStatus.CANCELLED
    mp.cancelled_at = utcnow()
    mp.promotion_offered_at = None
    attendance.promote_waitlist(db, match)
    audit.record(db, action="guest.removed_from_match", entity_type="match_player", entity_id=mp.id,
                 actor_user_id=actor.user_id, group_id=group.id)
    db.commit()
    db.refresh(mp)
    return mp
