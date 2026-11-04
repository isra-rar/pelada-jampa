"""Confirmação de presença, lista de espera e registro de presença efetiva (PRD §4)."""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError, Forbidden
from app.domain.enums import (
    AttendanceStatus as S,
    EvaluationStatus,
    MatchStatus,
    MemberStatus,
    NotificationType,
    PaymentStatus,
)
from app.models import FootballGroup, GroupMember, Match, MatchPlayer, Payment, User
from app.schemas.match import AdminParticipationIn, PresenceIn
from app.services import audit, notifications, payments
from app.services.access import get_member_in_group
from app.services.groups import group_admin_user_ids

_MP_FIELDS = ["status", "is_goalkeeper", "blocked", "blocked_reason", "confirmed_at", "waitlist_joined_at",
              "promotion_offered_at", "late_cancellation"]


def _now():
    return utcnow()


def get_participation(db: Session, match: Match, member_id: int) -> MatchPlayer | None:
    return db.scalar(select(MatchPlayer).where(MatchPlayer.match_id == match.id, MatchPlayer.member_id == member_id))


def _get_or_create(db: Session, match: Match, member: GroupMember) -> MatchPlayer:
    mp = get_participation(db, match, member.id)
    if mp is None:
        mp = MatchPlayer(match_id=match.id, member_id=member.id, status=S.PENDING)
        db.add(mp)
        db.flush()
    return mp


def waitlist(db: Session, match: Match) -> list[MatchPlayer]:
    return list(
        db.scalars(
            select(MatchPlayer)
            .where(MatchPlayer.match_id == match.id, MatchPlayer.status == S.WAITLIST)
            .order_by(MatchPlayer.waitlist_seq, MatchPlayer.waitlist_joined_at, MatchPlayer.id)
        ).all()
    )


def _enter_waitlist(db: Session, match: Match, mp: MatchPlayer) -> None:
    """Coloca o jogador no fim da fila."""
    last = db.scalar(select(func.max(MatchPlayer.waitlist_seq)).where(MatchPlayer.match_id == match.id)) or 0
    mp.status = S.WAITLIST
    mp.waitlist_joined_at = _now()
    mp.waitlist_seq = last + 1
    mp.promotion_offered_at = None


def _occupied(db: Session, match: Match) -> int:
    rows = db.scalars(
        select(MatchPlayer).where(MatchPlayer.match_id == match.id, MatchPlayer.status.in_([S.CONFIRMED, S.WAITLIST]))
    ).all()
    return sum(1 for r in rows if r.status == S.CONFIRMED or r.promotion_offered_at is not None)


def _set_confirmed(mp: MatchPlayer) -> None:
    mp.status = S.CONFIRMED
    mp.confirmed_at = _now()
    mp.was_confirmed = True
    mp.promotion_offered_at = None
    mp.responded_at = _now()


def participation_out(
    mp: MatchPlayer, *, waitlist_position: int | None = None, admin: bool = False, has_debt: bool | None = None
) -> dict:
    return {
        "member_id": mp.member_id,
        "user_id": mp.member.user_id,
        "name": mp.member.display_name,
        "photo_url": mp.member.user.photo_url,
        "status": mp.status,
        "is_goalkeeper": mp.is_goalkeeper,
        "confirmed_at": mp.confirmed_at,
        "waitlist_position": waitlist_position,
        "promotion_offered_at": mp.promotion_offered_at,
        "late_cancellation": mp.late_cancellation,
        "blocked": mp.blocked,
        "blocked_reason": mp.blocked_reason if admin else None,
        "has_pending_debt": has_debt if admin else None,
    }


_STATUS_ORDER = {S.CONFIRMED: 0, S.PRESENT: 0, S.WAITLIST: 1, S.PENDING: 2, S.ABSENT: 3, S.CANCELLED: 4, S.DECLINED: 5}


def list_participants(db: Session, match: Match, *, admin: bool, status: S | None = None) -> list[dict]:
    rows = db.scalars(select(MatchPlayer).where(MatchPlayer.match_id == match.id)).all()
    positions = {mp.id: i + 1 for i, mp in enumerate(waitlist(db, match))}
    debt = (
        payments.members_with_debt(db, match.group_id, [r.member_id for r in rows], exclude_match_id=match.id)
        if admin
        else set()
    )
    rows = [r for r in rows if status is None or r.status == status]
    rows.sort(key=lambda r: (_STATUS_ORDER[r.status], positions.get(r.id, 0), r.confirmed_at or r.updated_at, r.id))
    return [
        participation_out(r, waitlist_position=positions.get(r.id), admin=admin, has_debt=r.member_id in debt)
        for r in rows
    ]


def _require_open_for_players(match: Match) -> None:
    if match.status != MatchStatus.PUBLISHED:
        raise BusinessRuleError("Esta pelada não está aberta para confirmações", code="match_not_open")
    if _now() >= match.starts_at:
        raise BusinessRuleError("A pelada já começou", code="match_started")


def confirm(db: Session, group: FootballGroup, match: Match, member: GroupMember, as_goalkeeper: bool):
    _require_open_for_players(match)
    if _now() > match.confirmation_deadline:
        raise BusinessRuleError("O prazo de confirmação terminou", code="confirmation_closed")
    mp = _get_or_create(db, match, member)
    if mp.blocked:
        raise Forbidden(
            "Sua participação nesta pelada depende de liberação do administrador",
            code="participation_blocked",
            details={"reason": mp.blocked_reason},
        )
    debts = payments.pending_debts(db, group.id, member.id, exclude_match_id=match.id)
    warnings: list[str] = []
    if debts:
        total = sum(payments.outstanding(p) for p in debts)
        warnings.append(f"Você possui {len(debts)} pendência(s) financeira(s) no valor de R$ {total:.2f}")

    if mp.status in (S.CONFIRMED, S.WAITLIST):
        mp.is_goalkeeper = as_goalkeeper
        db.commit()
        return mp, warnings, debts  # idempotente

    mp.is_goalkeeper = as_goalkeeper
    mp.responded_at = _now()
    if _occupied(db, match) < match.slots:
        _set_confirmed(mp)
        title, body = "Presença confirmada", f"Você está confirmado em {match.name}."
    else:
        _enter_waitlist(db, match, mp)
        db.flush()
        position = len(waitlist(db, match))
        warnings.append(f"Vagas esgotadas: você entrou na lista de espera (posição {position})")
        title, body = "Lista de espera", f"Você está na posição {position} da lista de espera de {match.name}."
    notifications.notify(db, [member.user_id], NotificationType.CONFIRMATION_REGISTERED, title, body,
                         group_id=group.id, match_id=match.id)
    if debts:
        notifications.notify(
            db, group_admin_user_ids(db, group.id), NotificationType.PAYMENT_PENDING,
            "Jogador com pendência confirmou presença",
            f"{member.display_name} confirmou presença em {match.name} com {len(debts)} pendência(s).",
            group_id=group.id, match_id=match.id,
        )
    db.commit()
    return mp, warnings, debts


def decline(db: Session, group: FootballGroup, match: Match, member: GroupMember) -> MatchPlayer:
    _require_open_for_players(match)
    mp = _get_or_create(db, match, member)
    if mp.status == S.CONFIRMED:
        return cancel_confirmation(db, group, match, member)
    if mp.status in (S.PRESENT, S.ABSENT):
        raise BusinessRuleError("Presença já registrada", code="presence_registered")
    had_offer = mp.promotion_offered_at is not None
    mp.status = S.DECLINED
    mp.promotion_offered_at = None
    mp.responded_at = _now()
    if had_offer:
        promote_waitlist(db, match)
    db.commit()
    return mp


def cancel_confirmation(db: Session, group: FootballGroup, match: Match, member: GroupMember) -> MatchPlayer:
    _require_open_for_players(match)
    mp = get_participation(db, match, member.id)
    if mp is None or mp.status != S.CONFIRMED:
        raise BusinessRuleError("Você não está confirmado nesta pelada", code="not_confirmed")
    mp.status = S.CANCELLED
    mp.cancelled_at = _now()
    mp.late_cancellation = bool(match.cancellation_deadline and _now() > match.cancellation_deadline)
    promote_waitlist(db, match)
    db.commit()
    return mp


def accept_promotion(db: Session, group: FootballGroup, match: Match, member: GroupMember) -> MatchPlayer:
    _require_open_for_players(match)
    mp = get_participation(db, match, member.id)
    if mp is None or mp.status != S.WAITLIST or mp.promotion_offered_at is None:
        raise BusinessRuleError("Não há vaga oferecida para você", code="no_offer")
    _set_confirmed(mp)
    notifications.notify(db, [member.user_id], NotificationType.CONFIRMATION_REGISTERED, "Presença confirmada",
                         f"Você saiu da lista de espera e está confirmado em {match.name}.",
                         group_id=group.id, match_id=match.id)
    db.commit()
    return mp


def promote_waitlist(db: Session, match: Match) -> list[MatchPlayer]:
    """Preenche vagas livres a partir da fila. Não faz commit."""
    if match.status != MatchStatus.PUBLISHED or _now() >= match.starts_at:
        return []
    db.flush()
    promoted: list[MatchPlayer] = []
    while _occupied(db, match) < match.slots:
        candidates = [mp for mp in waitlist(db, match) if mp.promotion_offered_at is None and not mp.blocked]
        if not candidates:
            break
        mp = candidates[0]
        if match.waitlist_auto_promote:
            _set_confirmed(mp)
            title, body = "Vaga liberada: você está confirmado", f"Você foi promovido da lista de espera em {match.name}."
        else:
            mp.promotion_offered_at = _now()
            title, body = "Vaga liberada", f"Abriu uma vaga em {match.name}. Aceite para garantir sua participação."
        notifications.notify(db, [mp.member.user_id], NotificationType.SPOT_AVAILABLE, title, body,
                             group_id=match.group_id, match_id=match.id)
        db.flush()
        promoted.append(mp)
    return promoted


def expire_offers(db: Session, group: FootballGroup, match: Match) -> int:
    """Ofertas de vaga não aceitas no prazo voltam para o fim da fila."""
    limit = _now() - timedelta(minutes=group.waitlist_offer_expiry_minutes)
    expired = [mp for mp in waitlist(db, match) if mp.promotion_offered_at and mp.promotion_offered_at <= limit]
    for mp in expired:
        _enter_waitlist(db, match, mp)
    if expired:
        promote_waitlist(db, match)
    return len(expired)


def admin_update(
    db: Session, group: FootballGroup, match: Match, member_id: int, data: AdminParticipationIn, actor: User
) -> MatchPlayer:
    """Ajuste manual pelo administrador (sempre auditado)."""
    if match.status == MatchStatus.CANCELLED:
        raise BusinessRuleError("Pelada cancelada", code="invalid_status")
    member = get_member_in_group(db, group.id, member_id)
    if member.status != MemberStatus.ACTIVE:
        raise BusinessRuleError("Jogador não está ativo no grupo", code="member_inactive")
    mp = _get_or_create(db, match, member)
    before = audit.snapshot(mp, _MP_FIELDS)
    if data.is_goalkeeper is not None:
        mp.is_goalkeeper = data.is_goalkeeper
    if data.blocked is not None:
        mp.blocked = data.blocked
        mp.blocked_reason = data.blocked_reason if data.blocked else None
        if data.blocked and mp.status in (S.CONFIRMED, S.WAITLIST) and data.status is None:
            data.status = S.PENDING  # bloqueio retira o jogador da lista
    if data.status is not None and data.status != mp.status:
        new = data.status
        if new in (S.PRESENT, S.ABSENT):
            raise BusinessRuleError("Use o registro de presença para marcar presentes/ausentes", code="use_presence")
        if new == S.CONFIRMED:
            _set_confirmed(mp)  # admin pode exceder o limite de vagas conscientemente
        elif new == S.WAITLIST:
            _enter_waitlist(db, match, mp)
        else:
            if new == S.CANCELLED:
                mp.cancelled_at = _now()
            mp.status = new
            mp.promotion_offered_at = None
    db.flush()
    after = audit.snapshot(mp, _MP_FIELDS)
    if before != after:
        audit.record(db, action="participation.admin_update", entity_type="match_player", entity_id=mp.id,
                     actor_user_id=actor.id, group_id=group.id, before=before, after=after, reason=data.reason)
    promote_waitlist(db, match)
    db.commit()
    return mp


def register_presence(db: Session, group: FootballGroup, match: Match, data: PresenceIn, actor: User):
    if match.status not in (MatchStatus.PUBLISHED, MatchStatus.FINISHED):
        raise BusinessRuleError("Pelada não está publicada", code="invalid_status")
    if _now() < match.starts_at:
        raise BusinessRuleError("A presença só pode ser registrada após o início da pelada", code="too_early")
    if match.evaluation_status == EvaluationStatus.CLOSED:
        raise BusinessRuleError("Avaliações já encerradas; presença não pode mais ser alterada", code="closed")

    warnings: list[str] = []
    payments_created = 0
    new_payment_users: list[int] = []
    gk_ids = set(data.goalkeepers) if data.goalkeepers is not None else None
    changes: list[dict] = []

    for member_id, status in [(m, S.PRESENT) for m in data.present] + [(m, S.ABSENT) for m in data.absent]:
        member = get_member_in_group(db, group.id, member_id)
        mp = get_participation(db, match, member_id)
        if mp is None:
            mp = MatchPlayer(match_id=match.id, member_id=member_id, status=S.PENDING)
            db.add(mp)
            db.flush()
        if status == S.PRESENT and mp.status not in (S.CONFIRMED, S.PRESENT):
            warnings.append(f"{member.display_name} não estava confirmado e foi marcado como presente")
        old = mp.status
        mp.status = status
        mp.presence_registered_at = _now()
        if gk_ids is not None and status == S.PRESENT:
            mp.is_goalkeeper = member_id in gk_ids
        if old != status:
            changes.append({"member_id": member_id, "from": old, "to": status})

        if status == S.PRESENT and match.price > 0:
            p, created = payments.ensure_charge(db, match, member_id)
            if created:
                payments_created += 1
                new_payment_users.append(member.user_id)
            elif p.status == PaymentStatus.REFUNDED and p.notes == payments_auto_note():
                p.status = PaymentStatus.PENDING
                p.notes = None
        elif status == S.ABSENT:
            p = db.scalar(select(Payment).where(Payment.match_id == match.id, Payment.member_id == member_id))
            if p is not None and p.status == PaymentStatus.PENDING and p.amount_paid == 0:
                p.status = PaymentStatus.REFUNDED
                p.notes = payments_auto_note()

    unmarked = db.scalars(
        select(MatchPlayer).where(MatchPlayer.match_id == match.id, MatchPlayer.status == S.CONFIRMED)
    ).all()
    if unmarked:
        warnings.append(f"{len(unmarked)} jogador(es) confirmado(s) ainda sem presença registrada")

    match.status = MatchStatus.FINISHED
    if data.ended_at is not None:
        match.ended_at = data.ended_at
    elif match.ended_at is None:
        match.ended_at = match.ends_at

    present_users = [
        mp.member.user_id
        for mp in db.scalars(
            select(MatchPlayer).where(MatchPlayer.match_id == match.id, MatchPlayer.status == S.PRESENT)
        ).all()
    ]
    if match.evaluation_status == EvaluationStatus.NOT_OPEN:
        now = _now()
        match.evaluation_opens_at = now
        closes = match.ended_at + timedelta(hours=match.evaluation_window_hours)
        if closes <= now:
            closes = now + timedelta(hours=match.evaluation_window_hours)
            warnings.append("Presença registrada após o fim da janela prevista; janela calculada a partir de agora")
        match.evaluation_closes_at = closes
        match.evaluation_status = EvaluationStatus.OPEN
        notifications.notify(
            db, present_users, NotificationType.EVALUATION_OPEN, f"Avalie a pelada {match.name}",
            f"As avaliações ficam abertas até {closes.strftime('%d/%m %H:%M UTC')}.",
            group_id=group.id, match_id=match.id,
        )
    if new_payment_users:
        notifications.notify(
            db, new_payment_users, NotificationType.PAYMENT_PENDING, "Pagamento pendente",
            f"Valor de R$ {match.price:.2f} referente a {match.name}.", group_id=group.id, match_id=match.id,
        )
    audit.record(db, action="presence.registered", entity_type="match", entity_id=match.id, actor_user_id=actor.id,
                 group_id=group.id, after={"changes": changes, "ended_at": match.ended_at})
    db.commit()
    return match, payments_created, warnings


def payments_auto_note() -> str:
    return "Cancelado automaticamente: jogador marcado como ausente"


def attendance_stats(db: Session, member_id: int) -> dict:
    rows = db.scalars(
        select(MatchPlayer)
        .join(Match, Match.id == MatchPlayer.match_id)
        .where(MatchPlayer.member_id == member_id, Match.status != MatchStatus.CANCELLED)
    ).all()
    present = sum(1 for r in rows if r.status == S.PRESENT)
    absent = sum(1 for r in rows if r.status == S.ABSENT)
    return {
        "matches_played": present,
        "present": present,
        "absent": absent,
        "confirmations": sum(1 for r in rows if r.was_confirmed),
        "late_cancellations": sum(1 for r in rows if r.late_cancellation),
    }
