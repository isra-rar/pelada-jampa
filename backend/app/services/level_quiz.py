"""Quiz de nível (autoavaliação), nível declarado, confirmação pelo administrador e lembretes.

- O quiz é pedido a todo membro ativo que ainda não respondeu, inclusive quem já estava no grupo antes dele.
  Quem já tem perfil consolidado pelas avaliações fica dispensado (`exempt`): o quiz não mudaria nada.
- Goleiros, em grupos com avaliação própria de goleiro, respondem as perguntas de goleiro; os demais, as de
  linha. No fim, o jogador diz quantas estrelas acha que tem (nível declarado).
- O nível calculado pelas respostas vale como nota inicial provisória até um administrador confirmar (ou
  ajustar) o nível declarado. Depois, as avaliações pós-pelada seguem ajustando.
- Um administrador não confirma o próprio nível quando há outro administrador ativo; se ele é o único, o nível
  que declarou é confirmado na hora.
- Depois de algumas peladas avaliadas, o nível declarado é comparado com o que as avaliações mostram
  (`self_estimate`: `over` = se superestimou, `under` = se subestimou, `accurate` = acertou).
"""

from collections.abc import Iterable
from datetime import timedelta

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError
from app.domain import rating as R
from app.domain import self_assessment as SA
from app.domain.enums import GoalkeeperRatingMode, GroupRole, MemberStatus, NotificationType, Position, RatingKind
from app.models import FootballGroup, GroupMember, PlayerRating, PlayerRatingHistory, User
from app.services import audit, notifications, platform, ratings

# históricos em que o nível foi definido por um administrador: o quiz não sobrescreve
ADMIN_REASONS = ("admin_provisional", "level_confirmed")
# comparação nível declarado × avaliações: só depois desta quantidade de peladas avaliadas
MIN_MATCHES_TO_COMPARE = 3
# lembretes
MANUAL_REMINDER_COOLDOWN = timedelta(hours=12)
AUTO_REMINDER_EVERY = timedelta(days=3)
AUTO_REMINDER_FIRST_AFTER = timedelta(days=1)
MAX_AUTO_REMINDERS = 3


# ------------------------------------------------------------------ situação


def stars_text(n: int) -> str:
    return f"{n} estrela" if n == 1 else f"{n} estrelas"


def _consolidated_ids(db: Session, member_ids: Iterable[int]) -> set[int]:
    ids = list(set(member_ids))
    if not ids:
        return set()
    return set(db.scalars(
        select(PlayerRating.member_id).where(PlayerRating.member_id.in_(ids), PlayerRating.is_provisional.is_(False))
    ).all())


def status_of(member: GroupMember, consolidated: bool) -> str:
    """not_answered | to_review (respondeu, aguarda o admin) | confirmed | exempt (perfil já consolidado)."""
    if member.self_assessed_at is None:
        return "exempt" if consolidated else "not_answered"
    return "confirmed" if member.level_reviewed_at is not None else "to_review"


def is_pending(db: Session, member: GroupMember) -> bool:
    """Membro ativo que ainda precisa responder o quiz do grupo."""
    if member.status != MemberStatus.ACTIVE or member.self_assessed_at is not None:
        return False
    return member.id not in _consolidated_ids(db, [member.id])


def _kind(member: GroupMember) -> RatingKind:
    return RatingKind(member.self_assessment_kind or RatingKind.FIELD)


def _self_estimate(member: GroupMember, rating: PlayerRating | None) -> str | None:
    if member.declared_stars is None or rating is None or rating.matches_evaluated_count < MIN_MATCHES_TO_COMPARE:
        return None
    diff = member.declared_stars - rating.stars
    return "over" if diff >= 1 else "under" if diff <= -1 else "accurate"


def info_bulk(db: Session, members: list[GroupMember]) -> dict[int, dict]:
    """Situação do quiz de cada membro (para administradores e o próprio jogador)."""
    if not members:
        return {}
    ids = [m.id for m in members]
    rows = db.scalars(select(PlayerRating).where(PlayerRating.member_id.in_(ids))).all()
    by_key = {(r.member_id, r.kind): r for r in rows}
    consolidated = {r.member_id for r in rows if not r.is_provisional}
    out = {}
    for m in members:
        rating = by_key.get((m.id, _kind(m)))
        compare = rating if m.self_assessed_at is not None else None
        out[m.id] = {
            "status": status_of(m, m.id in consolidated),
            "kind": m.self_assessment_kind,
            "answered_at": m.self_assessed_at,
            "quiz_stars": m.quiz_stars,
            "declared_stars": m.declared_stars,
            "confirmed_stars": m.confirmed_stars,
            "reviewed_at": m.level_reviewed_at,
            "reminded_at": m.quiz_reminded_at,
            "current_stars": rating.stars if rating else None,
            "matches_evaluated": rating.matches_evaluated_count if rating else 0,
            "self_estimate": _self_estimate(m, compare),
        }
    return out


def info(db: Session, member: GroupMember) -> dict:
    return info_bulk(db, [member])[member.id]


def counts(db: Session, group_id: int) -> dict[str, int]:
    """Para o painel do administrador: quem não respondeu (e não está dispensado) e quantos níveis aguardam."""
    active = (GroupMember.group_id == group_id, GroupMember.status == MemberStatus.ACTIVE)
    consolidated = exists().where(PlayerRating.member_id == GroupMember.id, PlayerRating.is_provisional.is_(False))
    not_answered = db.scalar(
        select(func.count()).select_from(GroupMember)
        .where(*active, GroupMember.self_assessed_at.is_(None), ~consolidated)
    ) or 0
    member_or_guest = or_(
        GroupMember.status == MemberStatus.ACTIVE,
        (GroupMember.status == MemberStatus.GUEST) & GroupMember.invited_by_member_id.is_not(None),
    )
    to_review = db.scalar(
        select(func.count()).select_from(GroupMember)
        .where(GroupMember.group_id == group_id, member_or_guest, GroupMember.self_assessed_at.is_not(None),
               GroupMember.level_reviewed_at.is_(None))
    ) or 0
    return {"quiz_not_answered": not_answered, "levels_to_review": to_review}


def unconfirmed_count(db: Session, members: list[GroupMember]) -> int:
    """Quantos destes jogadores ainda não têm o nível confirmado (sem quiz ou aguardando o admin), incluindo
    convidados. Contratados e perfis já consolidados pelas avaliações não contam."""
    members = [m for m in members if m.status == MemberStatus.ACTIVE or m.is_invited_guest]
    data = info_bulk(db, members)
    return sum(1 for m in members if data[m.id]["status"] in ("not_answered", "to_review"))


# ------------------------------------------------------------------ quiz


def _goalkeeper_quiz_enabled(group: FootballGroup) -> bool:
    return GoalkeeperRatingMode(group.goalkeeper_rating_mode) == GoalkeeperRatingMode.OWN


def _questions(kind: str) -> list[dict]:
    return [
        {
            "attribute": q["attribute"],
            "label": R.ATTRIBUTE_LABELS[q["attribute"]],
            "question": q["question"],
            "hint": q["hint"],
            "options": list(q["options"]),
        }
        for q in SA.questions_for(kind)
    ]


def quiz_out(db: Session, group: FootballGroup, member: GroupMember) -> dict:
    return {
        "pending": is_pending(db, member),
        "answers": member.self_assessment,
        **info(db, member),
        "is_goalkeeper": member.preferred_position == Position.GOALKEEPER,
        "star_labels": {str(n): label for n, label in R.STAR_LABELS.items()},
        "questions": _questions("field"),
        "goalkeeper_questions": _questions("goalkeeper") if _goalkeeper_quiz_enabled(group) else None,
    }


def _set_by_admin(db: Session, rating: PlayerRating) -> bool:
    return db.scalar(
        select(PlayerRatingHistory.id).where(
            PlayerRatingHistory.rating_id == rating.id, PlayerRatingHistory.reason.in_(ADMIN_REASONS)
        ).limit(1)
    ) is not None


def active_admin_ids(db: Session, group_id: int) -> list[int]:
    return list(db.scalars(
        select(GroupMember.user_id).where(
            GroupMember.group_id == group_id,
            GroupMember.role == GroupRole.ADMIN,
            GroupMember.status == MemberStatus.ACTIVE,
        )
    ).all())


def _apply_level(db: Session, group: FootballGroup, member: GroupMember, stars: int, actor_user_id: int) -> tuple:
    """Registra o nível confirmado e, se o perfil ainda é provisório, leva a nota até ele."""
    kind = _kind(member)
    keys = ratings.keys_for(group, kind)
    rating = ratings.ensure_rating(db, group, member, kind)
    member.confirmed_stars = stars
    member.level_reviewed_at = utcnow()
    applied = rating.is_provisional
    if applied:
        thresholds = platform.star_thresholds(db)
        attrs_before, avg_before, stars_before = dict(rating.attributes), rating.average, rating.stars
        rating.attributes = R.attributes_for_stars(rating.attributes, keys, stars, thresholds)
        rating.average = R.compute_average(rating.attributes, keys)
        rating.stars = R.compute_stars(rating.average, thresholds)
        rating.updated_at = utcnow()
        ratings.record_history(db, rating, reason="level_confirmed", before=attrs_before, avg_before=avg_before,
                               stars_before=stars_before, actor_user_id=actor_user_id)
    return rating, applied


def submit(
    db: Session,
    group: FootballGroup,
    member: GroupMember,
    answers: dict[str, int],
    declared_stars: int,
    kind: RatingKind = RatingKind.FIELD,
) -> tuple[PlayerRating, bool]:
    """Registra as respostas e o nível declarado. Retorna (nota do tipo respondido, respostas_aplicadas)."""
    if member.self_assessed_at is not None:
        raise BusinessRuleError("Você já respondeu o quiz deste grupo", code="self_assessment_done")
    if member.id in _consolidated_ids(db, [member.id]):
        raise BusinessRuleError("Seu nível já vem das avaliações das peladas; não precisa do quiz",
                                code="self_assessment_exempt")
    kind = RatingKind(kind)
    if kind == RatingKind.GOALKEEPER and not _goalkeeper_quiz_enabled(group):
        raise BusinessRuleError("Este grupo avalia goleiros com os atributos de linha; responda o quiz de linha",
                                code="invalid_answers")
    try:
        clean = SA.validate_answers(answers, kind.value)
    except ValueError as exc:
        raise BusinessRuleError(str(exc), code="invalid_answers") from None
    if not 0 <= declared_stars <= R.MAX_STARS:
        raise BusinessRuleError("Escolha um nível de 0 a 5 estrelas", code="invalid_answers")

    # quem diz que joga no gol passa a ser goleiro no grupo (e o contrário)
    if kind == RatingKind.GOALKEEPER:
        member.preferred_position = Position.GOALKEEPER
    elif member.preferred_position == Position.GOALKEEPER:
        member.preferred_position = Position.ANY
    ratings.ensure_member_ratings(db, group, member)

    keys = ratings.keys_for(group, kind)
    thresholds = platform.star_thresholds(db)
    scores = SA.scores_from_answers(clean, kind.value)
    rating = ratings.ensure_rating(db, group, member, kind)
    member.self_assessment = clean
    member.self_assessment_kind = kind.value
    member.self_assessed_at = utcnow()
    member.quiz_stars = R.compute_stars(R.compute_average(scores, keys), thresholds)
    member.declared_stars = declared_stars
    member.confirmed_stars = None
    member.level_reviewed_at = None
    applied = rating.is_provisional and rating.valid_evaluations_count == 0 and not _set_by_admin(db, rating)
    if applied:
        before, avg_before, stars_before = dict(rating.attributes), rating.average, rating.stars
        rating.attributes = scores
        rating.average = R.compute_average(scores, keys)
        rating.stars = R.compute_stars(rating.average, thresholds)
        rating.updated_at = utcnow()
        ratings.record_history(db, rating, reason="self_assessment", before=before, avg_before=avg_before,
                               stars_before=stars_before, actor_user_id=member.user_id)

    other_admins = [uid for uid in active_admin_ids(db, group.id) if uid != member.user_id]
    auto_confirmed = member.is_admin and not other_admins
    if auto_confirmed:
        # único administrador: não há quem confirme, então o nível que ele declarou vale
        rating, _ = _apply_level(db, group, member, declared_stars, member.user_id)

    audit.record(
        db,
        action="member.self_assessment",
        entity_type="group_member",
        entity_id=member.id,
        actor_user_id=member.user_id,
        group_id=group.id,
        after={"kind": kind.value, "answers": clean, "quiz_stars": member.quiz_stars,
               "declared_stars": declared_stars, "applied": applied, "auto_confirmed": auto_confirmed,
               "average": rating.average, "stars": rating.stars},
    )
    if other_admins:
        where = " no gol" if kind == RatingKind.GOALKEEPER else ""
        notifications.notify(
            db, other_admins, NotificationType.MEMBERSHIP, "Nível para confirmar",
            f"{member.display_name} respondeu o quiz{where}: {stars_text(member.quiz_stars)} pelas respostas, "
            f"se considera {stars_text(declared_stars)}. Confirme o nível em Gestão > Membros.",
            group_id=group.id,
        )
    return rating, applied


def review(
    db: Session, group: FootballGroup, member: GroupMember, stars: int, actor: User, reason: str | None
) -> tuple[PlayerRating, bool]:
    """Administrador confirma (ou ajusta) o nível depois do quiz. Perfil já consolidado pelas avaliações não
    muda: a confirmação fica só registrada. Retorna (nota, nível_aplicado)."""
    if member.self_assessed_at is None:
        raise BusinessRuleError("O jogador ainda não respondeu o quiz deste grupo", code="self_assessment_missing")
    if not 0 <= stars <= R.MAX_STARS:
        raise BusinessRuleError("Escolha um nível de 0 a 5 estrelas", code="invalid_stars")
    if member.user_id == actor.id and any(uid != actor.id for uid in active_admin_ids(db, group.id)):
        raise BusinessRuleError("Outro administrador do grupo precisa confirmar o seu nível",
                                code="self_review_not_allowed")
    before = {"confirmed_stars": member.confirmed_stars}
    rating, applied = _apply_level(db, group, member, stars, actor.id)
    audit.record(
        db,
        action="member.level_reviewed",
        entity_type="group_member",
        entity_id=member.id,
        actor_user_id=actor.id,
        group_id=group.id,
        before=before,
        after={"confirmed_stars": stars, "quiz_stars": member.quiz_stars, "declared_stars": member.declared_stars,
               "applied": applied, "stars": rating.stars, "average": rating.average},
        reason=reason,
    )
    if member.user_id != actor.id:
        if not applied:
            text = (f"Um administrador registrou {stars_text(stars)} para você em {group.name}. Seu nível segue "
                    "pelas avaliações das peladas.")
        elif stars == member.declared_stars:
            text = f"Seu nível em {group.name} foi confirmado: {stars_text(stars)}."
        else:
            text = (f"Seu nível em {group.name} foi ajustado para {stars_text(stars)} "
                    f"(você se considerou {stars_text(member.declared_stars)}).")
        notifications.notify(db, [member.user_id], NotificationType.MEMBERSHIP, "Nível confirmado", text,
                             group_id=group.id)
    return rating, applied


# ------------------------------------------------------------------ lembretes


def _pending_members(db: Session, group_ids: list[int] | None = None) -> list[tuple[GroupMember, FootballGroup]]:
    consolidated = exists().where(PlayerRating.member_id == GroupMember.id, PlayerRating.is_provisional.is_(False))
    stmt = (
        select(GroupMember, FootballGroup)
        .join(FootballGroup, FootballGroup.id == GroupMember.group_id)
        .join(User, User.id == GroupMember.user_id)
        .where(
            GroupMember.status == MemberStatus.ACTIVE,
            GroupMember.self_assessed_at.is_(None),
            ~consolidated,
            FootballGroup.is_active.is_(True),
            FootballGroup.suspended_at.is_(None),
            User.deleted_at.is_(None),
        )
    )
    if group_ids is not None:
        stmt = stmt.where(GroupMember.group_id.in_(group_ids))
    return [(m, g) for m, g in db.execute(stmt).all()]


def _remind(db: Session, member: GroupMember, group: FootballGroup) -> None:
    notifications.notify(
        db, [member.user_id], NotificationType.MEMBERSHIP, "Responda o quiz de nível",
        f"Falta você em {group.name}: são 5 perguntas rápidas para o sorteio montar times equilibrados.",
        group_id=group.id,
    )
    member.quiz_reminded_at = utcnow()


def remind_pending(db: Session, group: FootballGroup, actor: User) -> int:
    """Administrador lembra quem ainda não respondeu. Quem foi lembrado há menos de 12 horas fica de fora."""
    now = utcnow()
    reminded = 0
    for member, g in _pending_members(db, [group.id]):
        if member.user_id == actor.id:
            continue
        if member.quiz_reminded_at is not None and now - member.quiz_reminded_at < MANUAL_REMINDER_COOLDOWN:
            continue
        _remind(db, member, g)
        reminded += 1
    audit.record(db, action="member.self_assessment_reminded", entity_type="group", entity_id=group.id,
                 actor_user_id=actor.id, group_id=group.id, after={"reminded": reminded})
    db.commit()
    return reminded


def send_auto_reminders(db: Session) -> int:
    """Agendador: lembra quem não respondeu a cada 3 dias (o primeiro um dia depois de entrar), no máximo 3 vezes,
    das 9h às 21h de Brasília. Retorna quantos foram lembrados."""
    from app.services.payments import _BRASILIA, REMINDER_HOURS

    now = utcnow()
    if now.astimezone(_BRASILIA).hour not in REMINDER_HOURS:
        return 0
    reminded = 0
    for member, group in _pending_members(db):
        if (member.quiz_reminders_count or 0) >= MAX_AUTO_REMINDERS:
            continue
        if now - member.joined_at < AUTO_REMINDER_FIRST_AFTER:
            continue
        if member.quiz_reminded_at is not None and now - member.quiz_reminded_at < AUTO_REMINDER_EVERY:
            continue
        _remind(db, member, group)
        member.quiz_reminders_count = (member.quiz_reminders_count or 0) + 1
        reminded += 1
    if reminded:
        db.commit()
    return reminded
