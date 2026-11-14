"""Confirmação de presença, lista de espera e registro de presença efetiva (PRD §4)."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError, Forbidden
from app.domain.enums import (
    AttendanceStatus as S,
    EvaluationStatus,
    HireRole,
    HireStatus,
    MatchStatus,
    MemberStatus,
    NotificationType,
    PaymentStatus,
)
from app.domain import money
from app.models import FootballGroup, GroupMember, Match, MatchPlayer, Payment, User
from app.schemas.match import AdminParticipationIn, PresenceIn
from app.services import audit, match_lock, notifications, payments
from app.services.access import get_member_in_group
from app.services.groups import group_admin_user_ids

_MP_FIELDS = ["status", "is_goalkeeper", "is_referee", "blocked", "blocked_reason", "confirmed_at", "waitlist_joined_at",
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


def notify_user_id(member: GroupMember) -> int:
    """Quem recebe os avisos do jogador: o próprio ou, para convidado (sem conta), quem trouxe."""
    if member.is_invited_guest and member.invited_by is not None:
        return member.invited_by.user_id
    return member.user_id


def waitlist_full(db: Session, match: Match) -> bool:
    return match.waitlist_limit is not None and len(waitlist(db, match)) >= match.waitlist_limit


def maybe_expand(db: Session, group: FootballGroup, match: Match) -> bool:
    """Aumenta a pelada uma única vez quando a lista de espera lota: as vagas crescem do tamanho da espera, os
    times previstos passam a `expanded_num_teams` e quem esperava é promovido. Não faz commit."""
    if not match.expand_when_waitlist_full or match.expanded_at is not None or not match.waitlist_limit:
        return False
    if match.status != MatchStatus.PUBLISHED or _now() >= match.starts_at:
        return False
    db.flush()
    if len(waitlist(db, match)) < match.waitlist_limit:
        return False
    before = {"slots": match.slots, "num_teams": match.num_teams}
    match.slots += match.waitlist_limit
    match.num_teams = match.expanded_num_teams
    match.expanded_at = _now()
    audit.record(db, action="match.expanded", entity_type="match", entity_id=match.id, actor_user_id=None,
                 group_id=group.id, before=before, after={"slots": match.slots, "num_teams": match.num_teams})
    promoted = {mp.member_id for mp in promote_waitlist(db, match)}
    confirmed = db.scalars(
        select(MatchPlayer).where(MatchPlayer.match_id == match.id, MatchPlayer.status == S.CONFIRMED)
    ).all()
    targets = {notify_user_id(mp.member) for mp in confirmed if mp.member_id not in promoted}
    targets |= set(group_admin_user_ids(db, group.id))
    teams = f" e {match.num_teams} times" if match.num_teams else ""
    notifications.notify(
        db, sorted(targets), NotificationType.CONFIRMATION_REGISTERED, f"A pelada aumentou: {match.name}",
        f"A lista de espera lotou e a pelada passou para {match.slots} jogadores{teams}.",
        group_id=group.id, match_id=match.id,
    )
    return True


def _occupied(db: Session, match: Match) -> int:
    """Vagas de jogador ocupadas (juízes têm vaga própria e não contam)."""
    rows = db.scalars(
        select(MatchPlayer).where(
            MatchPlayer.match_id == match.id, MatchPlayer.status.in_([S.CONFIRMED, S.WAITLIST]),
            MatchPlayer.is_referee.is_(False),
        )
    ).all()
    return sum(1 for r in rows if r.status == S.CONFIRMED or r.promotion_offered_at is not None)


def referees_taken(db: Session, match: Match, exclude_member_id: int | None = None) -> int:
    """Vagas de juiz ocupadas (confirmados ou já presentes)."""
    stmt = select(func.count()).where(
        MatchPlayer.match_id == match.id, MatchPlayer.is_referee.is_(True),
        MatchPlayer.status.in_([S.CONFIRMED, S.PRESENT]),
    )
    if exclude_member_id is not None:
        stmt = stmt.where(MatchPlayer.member_id != exclude_member_id)
    return db.scalar(stmt) or 0


def _require_referee_slot(db: Session, match: Match, member_id: int) -> None:
    if referees_taken(db, match, exclude_member_id=member_id) >= match.referee_slots:
        raise BusinessRuleError(
            "Não há vaga de juiz nesta pelada" if match.referee_slots == 0 else "A vaga de juiz já está preenchida",
            code="referee_full",
        )


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
        "is_referee": mp.is_referee,
        "hired": mp.hire_id is not None,
        "invited_by_member_id": mp.member.invited_by_member_id,
        "invited_by_name": mp.member.invited_by.display_name if mp.member.invited_by is not None else None,
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


def confirm(
    db: Session, group: FootballGroup, match: Match, member: GroupMember, as_goalkeeper: bool,
    as_referee: bool = False,
):
    _require_open_for_players(match)
    match_lock.require_unlocked(db, match)
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

    if mp.hire_id is not None and mp.status == S.CONFIRMED:
        return mp, warnings, debts  # contratado: a função vem do pedido aceito

    if as_referee:
        # juiz não ocupa vaga de jogador, não entra na fila e não paga a pelada
        _require_referee_slot(db, match, member.id)
        was_player = mp.status in (S.CONFIRMED, S.WAITLIST) and not mp.is_referee
        mp.is_goalkeeper = False
        if mp.status == S.CONFIRMED and mp.is_referee:
            db.commit()
            return mp, warnings, debts  # idempotente
        mp.is_referee = True
        _set_confirmed(mp)
        mp.waitlist_seq = mp.waitlist_joined_at = None
        if was_player:
            promote_waitlist(db, match)
        notifications.notify(db, [member.user_id], NotificationType.CONFIRMATION_REGISTERED, "Você vai apitar",
                             f"Você está confirmado como juiz em {match.name}.", group_id=group.id, match_id=match.id)
        db.commit()
        return mp, warnings, debts

    if mp.status in (S.CONFIRMED, S.WAITLIST) and not mp.is_referee:
        mp.is_goalkeeper = as_goalkeeper
        db.commit()
        return mp, warnings, debts  # idempotente

    mp.is_goalkeeper = as_goalkeeper
    mp.is_referee = False  # deixa de apitar para jogar: precisa de vaga de jogador
    mp.responded_at = _now()
    if _occupied(db, match) < match.slots:
        _set_confirmed(mp)
        title, body = "Presença confirmada", f"Você está confirmado em {match.name}."
    else:
        if waitlist_full(db, match):
            raise BusinessRuleError("Vagas e lista de espera esgotadas", code="waitlist_full")
        _enter_waitlist(db, match, mp)
        db.flush()
        if maybe_expand(db, group, match) and mp.status == S.CONFIRMED:
            # quem lotou a espera já foi avisado pela promoção
            warnings.append(f"A lista de espera lotou e a pelada aumentou para {match.slots} jogadores")
            title = body = None
        else:
            position = len(waitlist(db, match))
            warnings.append(f"Vagas esgotadas: você entrou na lista de espera (posição {position})")
            title, body = "Lista de espera", f"Você está na posição {position} da lista de espera de {match.name}."
    if title:
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
        if match.waitlist_auto_promote or mp.member.is_invited_guest:  # convidado não tem como aceitar
            _set_confirmed(mp)
            title, body = "Vaga liberada: você está confirmado", f"Você foi promovido da lista de espera em {match.name}."
        else:
            mp.promotion_offered_at = _now()
            title, body = "Vaga liberada", f"Abriu uma vaga em {match.name}. Aceite para garantir sua participação."
        if mp.member.is_invited_guest:  # convidado sem conta: o aviso vai para quem trouxe
            title = "Vaga liberada: seu convidado está confirmado"
            body = f"{mp.member.display_name} saiu da lista de espera e está confirmado em {match.name}."
        notifications.notify(db, [notify_user_id(mp.member)], NotificationType.SPOT_AVAILABLE, title, body,
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
    if member.status != MemberStatus.ACTIVE and not (member.status == MemberStatus.GUEST and member.is_invited_guest):
        raise BusinessRuleError("Jogador não está ativo no grupo", code="member_inactive")
    mp = _get_or_create(db, match, member)
    before = audit.snapshot(mp, _MP_FIELDS)
    if mp.hire_id is not None and (data.is_goalkeeper is not None or data.is_referee is not None):
        raise BusinessRuleError("A função do contratado vem do pedido; cancele o pedido para mudar",
                                code="hired_player")
    if data.is_referee is not None and data.is_referee != mp.is_referee:
        if data.is_referee:
            _require_referee_slot(db, match, member.id)
            mp.is_goalkeeper = False
            if mp.status == S.WAITLIST:
                _set_confirmed(mp)
        mp.is_referee = data.is_referee
    if data.is_goalkeeper is not None:
        mp.is_goalkeeper = data.is_goalkeeper and not mp.is_referee
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
            maybe_expand(db, group, match)
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

    # Juízes e contratados não pagam a pelada. Rateio (split_cost): campo + contratados presentes divididos entre
    # todos que pagam (presentes e, pelas regras do grupo, quem faltou sem avisar ou desistiu fora do prazo).
    # Valor fixo: valor por jogador + parte dos contratados para os presentes; penalidades pagam o valor fixo.
    participants = {
        mp.member_id: mp for mp in db.scalars(select(MatchPlayer).where(MatchPlayer.match_id == match.id)).all()
    }
    staff = {mid for mid, mp in participants.items() if mp.is_referee or mp.hire_id is not None}
    present_ids, absent_ids = set(data.present), set(data.absent)
    gk_cost, ref_cost = hires_cost_present(db, match, participants, present_ids)
    payers = present_ids - staff
    no_show_payers = {
        mid for mid in absent_ids - staff if participants.get(mid) is not None and participants[mid].was_confirmed
    } if group.charge_no_shows else set()
    late_payers = {
        mid for mid, mp in participants.items()
        if mp.status == S.CANCELLED and mp.late_cancellation and mid not in staff | present_ids | absent_ids
    } if group.charge_late_cancellations else set()
    if match.split_cost:
        charged = payers | no_show_payers | late_payers
        field = Decimal(match.field_cost or 0)
        share = money.split(field + gk_cost + ref_cost, len(charged))
        amount = penalty = share
        charge_note = split_note(field, gk_cost, ref_cost, len(charged))
    else:
        charged = set()
        hired_share = money.split(gk_cost + ref_cost, len(payers))
        amount, penalty = Decimal(match.price) + hired_share, Decimal(match.price)
        charge_note = f"Inclui R$ {hired_share:.2f} do rateio de goleiro/juiz contratado" if hired_share > 0 else None

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
        if gk_ids is not None and status == S.PRESENT and member_id not in staff:
            mp.is_goalkeeper = member_id in gk_ids
        if old != status:
            changes.append({"member_id": member_id, "from": old, "to": status})
        if member_id in staff:
            continue  # juiz/contratado: sem cobrança

        if status == S.PRESENT and amount > 0:
            p, created = payments.ensure_charge(db, match, member_id, amount)
            if created:
                p.notes = charge_note
                payments_created += 1
                new_payment_users.append(notify_user_id(member))
            elif p.status == PaymentStatus.REFUNDED and p.notes == payments_auto_note():
                p.status = PaymentStatus.PENDING
                p.notes = None
        elif status == S.ABSENT:
            p = db.scalar(select(Payment).where(Payment.match_id == match.id, Payment.member_id == member_id))
            if group.charge_no_shows and mp.was_confirmed and penalty > 0:
                # regra do grupo: quem confirmou e faltou paga a pelada mesmo assim
                if p is None:
                    p, _ = payments.ensure_charge(db, match, member_id, penalty)
                    p.notes = NO_SHOW_NOTE
                    payments_created += 1
                    new_payment_users.append(notify_user_id(member))
                elif p.status == PaymentStatus.REFUNDED and p.notes == payments_auto_note():
                    p.status = PaymentStatus.PENDING
                    p.notes = NO_SHOW_NOTE
            elif p is not None and p.status == PaymentStatus.PENDING and p.amount_paid == 0:
                p.status = PaymentStatus.REFUNDED
                p.notes = payments_auto_note()

    db.flush()  # autoflush desligado: as consultas abaixo precisam ver as marcações acima
    if group.charge_late_cancellations and penalty > 0:
        late = db.scalars(
            select(MatchPlayer).where(
                MatchPlayer.match_id == match.id, MatchPlayer.status == S.CANCELLED,
                MatchPlayer.late_cancellation.is_(True), MatchPlayer.is_referee.is_(False),
                MatchPlayer.hire_id.is_(None),
            )
        ).all()
        for mp in late:
            p, created = payments.ensure_charge(db, match, mp.member_id, penalty)
            if created:
                p.notes = LATE_CANCEL_NOTE
                payments_created += 1
                new_payment_users.append(notify_user_id(mp.member))

    if match.split_cost and charged:
        # correção de presença no rateio: a parte de cada um muda com o número de pagantes
        db.flush()
        for p in db.scalars(select(Payment).where(
            Payment.match_id == match.id, Payment.member_id.in_(charged),
            Payment.status == PaymentStatus.PENDING, Payment.amount_paid == 0,
        )).all():
            p.amount_due = share
            if p.member_id in payers:
                p.notes = charge_note

    unmarked = db.scalars(
        select(MatchPlayer).where(MatchPlayer.match_id == match.id, MatchPlayer.status == S.CONFIRMED)
    ).all()
    if unmarked:
        warnings.append(f"{len(unmarked)} jogador(es) confirmado(s) ainda sem presença registrada")

    was_open = match.status == MatchStatus.PUBLISHED
    match.status = MatchStatus.FINISHED
    if was_open:
        db.flush()
        match_lock.notify_next_opened(db, group, match)
    if data.ended_at is not None:
        match.ended_at = data.ended_at
    elif match.ended_at is None:
        match.ended_at = match.ends_at

    present_users = [  # quem avalia: jogadores presentes (juízes e contratados ficam fora)
        mp.member.user_id
        for mp in db.scalars(
            select(MatchPlayer).where(
                MatchPlayer.match_id == match.id, MatchPlayer.status == S.PRESENT,
                MatchPlayer.is_referee.is_(False), MatchPlayer.hire_id.is_(None),
            )
        ).all()
        if not mp.member.is_invited_guest  # convidado não tem conta: é avaliado, mas não avalia
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
            f"As avaliações ficam abertas até {notifications.when(closes)}.",
            group_id=group.id, match_id=match.id,
        )
    if new_payment_users:
        notifications.notify(
            db, new_payment_users, NotificationType.PAYMENT_PENDING, "Pagamento pendente",
            f"Valor de R$ {amount:.2f} referente a {match.name}.", group_id=group.id, match_id=match.id,
        )
    audit.record(db, action="presence.registered", entity_type="match", entity_id=match.id, actor_user_id=actor.id,
                 group_id=group.id, after={"changes": changes, "ended_at": match.ended_at})
    db.commit()
    return match, payments_created, warnings


NO_SHOW_NOTE = "Falta sem aviso (regra do grupo)"


def _brl(v: Decimal) -> str:
    return f"R$ {v:.2f}".replace(".", ",")


def split_note(field: Decimal, goalkeepers: Decimal, referees: Decimal, people: int) -> str:
    """Ex.: "Rateio: campo R$ 150,00 + goleiros R$ 100,00 + juiz R$ 50,00 = R$ 300,00 ÷ 10 jogadores"."""
    parts = [f"{label} {_brl(v)}" for label, v in (("campo", field), ("goleiros", goalkeepers), ("juiz", referees))
             if v > 0]
    total = field + goalkeepers + referees
    plural = "jogadores" if people != 1 else "jogador"
    return f"Rateio: {' + '.join(parts) or 'sem custo'} = {_brl(total)} ÷ {people} {plural}"


def hires_cost_present(
    db: Session, match: Match, participants: dict[int, MatchPlayer], present_ids: set[int]
) -> tuple[Decimal, Decimal]:
    """Soma dos goleiros e dos juízes contratados (pedido aceito) marcados como presentes."""
    from app.models import MatchHire

    hire_member = {mp.hire_id: mid for mid, mp in participants.items() if mp.hire_id is not None}
    hires = db.scalars(
        select(MatchHire).where(MatchHire.match_id == match.id, MatchHire.status == HireStatus.ACCEPTED)
    ).all()
    present = [h for h in hires if hire_member.get(h.id) in present_ids]
    gk = sum((Decimal(h.price) for h in present if h.role == HireRole.GOALKEEPER), Decimal("0"))
    ref = sum((Decimal(h.price) for h in present if h.role == HireRole.REFEREE), Decimal("0"))
    return gk, ref
LATE_CANCEL_NOTE = "Desistência fora do prazo (regra do grupo)"


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
    no_shows = sum(1 for r in rows if r.status == S.ABSENT and r.was_confirmed)
    return {
        "no_shows": no_shows,
        "matches_played": present,
        "present": present,
        "absent": absent,
        "confirmations": sum(1 for r in rows if r.was_confirmed),
        "late_cancellations": sum(1 for r in rows if r.late_cancellation),
    }
