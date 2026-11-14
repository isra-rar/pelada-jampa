"""Convidado que entra sozinho, sem login, pelo link de uma pelada.

- Um membro ativo gera o link da pelada (token assinado com a pelada e quem compartilhou).
- Quem abre o link informa nome (e nível/goleiro) e vira convidado de quem compartilhou: mesmo modelo dos
  convidados dos membros (`GroupMember` GUEST + `invited_by_member_id`). A cobrança vai para quem compartilhou
  e aparece nas pendências dele e no financeiro do grupo.
- O convidado recebe um passe (token) que só dá leitura da pelada e a opção de sair dela. Não avalia ninguém,
  não vê o grupo, o ranking nem o financeiro.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError, Forbidden, NotFound
from app.core.security import create_scoped_token, decode_scoped_token, short_code
from app.domain.enums import AttendanceStatus, MatchStatus, MemberStatus, NotificationType
from app.models import FootballGroup, GroupMember, Match, MatchGuestLink, Payment
from app.services import attendance, draws, games, guests, matches, notifications, payments
from app.services.access import get_group

LINK_KIND = "match_invite"
PASS_KIND = "guest_pass"
PASS_GRACE = timedelta(days=30)  # o passe segue valendo depois da pelada, para o convidado ver a cobrança


def invite_token(db: Session, match: Match, sharer: GroupMember) -> str:
    """Código curto (8 caracteres) do link de convidado: um por pelada e por quem compartilha."""
    link = db.scalar(select(MatchGuestLink).where(
        MatchGuestLink.match_id == match.id, MatchGuestLink.sharer_member_id == sharer.id))
    if link is None:
        while True:
            code = short_code(8)
            if db.scalar(select(MatchGuestLink.id).where(MatchGuestLink.code == code)) is None:
                break
        link = MatchGuestLink(code=code, match_id=match.id, sharer_member_id=sharer.id)
        db.add(link)
        db.commit()
    return link.code


def _link_ids(db: Session, token: str) -> tuple[int, int]:
    """Pelada e quem compartilhou, pelo código curto ou pelo token assinado dos links antigos."""
    link = db.scalar(select(MatchGuestLink).where(MatchGuestLink.code == token.upper()))
    if link is not None:
        return link.match_id, link.sharer_member_id
    claims = decode_scoped_token(LINK_KIND, token)
    if claims is None:
        raise _invalid()
    return claims["mid"], claims["sid"]


def _pass_token(match: Match, guest: GroupMember) -> str:
    return create_scoped_token(PASS_KIND, {"mid": match.id, "gid": guest.id}, match.ends_at + PASS_GRACE)


def _invalid(msg: str = "Link inválido ou expirado") -> NotFound:
    return NotFound(msg, code="invalid_guest_link")


def _link_context(db: Session, token: str) -> tuple[Match, FootballGroup, GroupMember]:
    match_id, sharer_id = _link_ids(db, token)
    match = db.get(Match, match_id)
    sharer = db.get(GroupMember, sharer_id)
    if match is None or sharer is None or sharer.group_id != match.group_id or sharer.status != MemberStatus.ACTIVE:
        raise _invalid()
    group = get_group(db, match.group_id)
    if not group.is_active or group.suspended_at is not None:
        raise _invalid()
    return match, group, sharer


def _pass_context(db: Session, token: str) -> tuple[Match, FootballGroup, GroupMember]:
    claims = decode_scoped_token(PASS_KIND, token)
    if claims is None:
        raise _invalid("Acesso expirado")
    match = db.get(Match, claims["mid"])
    guest = db.get(GroupMember, claims["gid"])
    if match is None or guest is None or guest.group_id != match.group_id or not guest.is_invited_guest \
            or guest.status != MemberStatus.GUEST:
        raise _invalid("Acesso expirado")
    return match, get_group(db, match.group_id), guest


def _open_state(match: Match) -> tuple[bool, str | None]:
    if match.status != MatchStatus.PUBLISHED:
        return False, "Esta pelada não está aberta para confirmações"
    now = utcnow()
    if now >= match.starts_at:
        return False, "A pelada já começou"
    if now > match.confirmation_deadline:
        return False, "O prazo de confirmação terminou"
    return True, None


def _match_card(db: Session, match: Match, group: FootballGroup) -> dict:
    counts = matches.attendance_counts(db, match)
    return {
        "id": match.id, "name": match.name, "group_name": group.name, "modality": match.modality.value,
        "location": match.location, "address": match.address, "starts_at": match.starts_at,
        "ends_at": match.ends_at, "status": match.status.value, "slots": match.slots,
        "confirmed": counts["confirmed"], "available_slots": counts["available_slots"],
        "waitlist": counts["waitlist"], "price": matches.per_player_if_full(db, match),
        "description": match.description,
    }


def link_preview(db: Session, token: str) -> dict:
    match, group, sharer = _link_context(db, token)
    open_, reason = _open_state(match)
    return {"match": _match_card(db, match, group), "invited_by_name": sharer.display_name,
            "can_join": open_, "closed_reason": reason}


def join(db: Session, token: str, name: str, stars: int, goalkeeper: bool) -> dict:
    match, group, sharer = _link_context(db, token)
    open_, reason = _open_state(match)
    if not open_:
        raise BusinessRuleError(reason, code="match_not_open")
    guest = guests.create_guest(db, group, sharer, name, stars, goalkeeper, self_joined=True)
    try:
        _, warnings = guests.add_to_match(db, group, match, guest, sharer, goalkeeper)
    except BusinessRuleError:
        # sem vaga nem espera: não deixa um convidado órfão
        guest.status = MemberStatus.REMOVED
        db.commit()
        raise
    notifications.notify(
        db, [sharer.user_id], NotificationType.CONFIRMATION_REGISTERED, "Convidado entrou pelo seu link",
        f"{guest.display_name} entrou em {match.name} pelo seu link. A cobrança dele fica com você.",
        group_id=group.id, match_id=match.id,
    )
    db.commit()
    access = _pass_token(match, guest)
    return {"access_token": access, "view": view(db, access), "warnings": warnings}


def _payment_of(db: Session, match: Match, guest: GroupMember) -> dict | None:
    p = db.scalar(select(Payment).where(Payment.match_id == match.id, Payment.member_id == guest.id))
    if p is None:
        return None
    payer = p.payer if p.payer_member_id else p.member
    return {
        "amount_due": p.amount_due, "amount_paid": p.amount_paid, "outstanding": payments.outstanding(p),
        "status": p.status.value, "payer_name": payer.display_name, "paid_at": p.paid_at,
    }


def view(db: Session, token: str) -> dict:
    match, group, guest = _pass_context(db, token)
    mp = attendance.get_participation(db, match, guest.id)
    published = draws.list_draws(db, match, include_unpublished=False)
    shown = (AttendanceStatus.CONFIRMED, AttendanceStatus.PRESENT, AttendanceStatus.WAITLIST)
    participants = [
        {"name": r["name"], "photo_url": r["photo_url"], "status": r["status"].value,
         "is_goalkeeper": r["is_goalkeeper"], "is_referee": r["is_referee"],
         "waitlist_position": r["waitlist_position"], "invited_by_name": r["invited_by_name"],
         "is_me": r["member_id"] == guest.id}
        for r in attendance.list_participants(db, match, admin=False) if r["status"] in shown
    ]
    position = next((p["waitlist_position"] for p in participants if p["is_me"]), None)
    open_, _ = _open_state(match)
    return {
        "match": _match_card(db, match, group),
        "me": {"name": guest.display_name, "invited_by_name": guest.invited_by.display_name,
               "status": mp.status.value if mp else None, "waitlist_position": position,
               "is_goalkeeper": bool(mp and mp.is_goalkeeper)},
        "can_leave": open_ and mp is not None and mp.status in (AttendanceStatus.CONFIRMED, AttendanceStatus.WAITLIST),
        "participants": participants,
        "teams": draws.draw_out(published[0]) if published else None,
        "games": [games.game_out(db, g) for g in games.list_games(db, match)],
        "payment": _payment_of(db, match, guest),
    }


def leave(db: Session, token: str) -> dict:
    match, group, guest = _pass_context(db, token)
    if group.suspended_at is not None:
        raise Forbidden("Grupo suspenso pela administração da plataforma", code="group_suspended")
    guests.remove_from_match(db, group, match, guest, guest.invited_by)
    return view(db, token)
