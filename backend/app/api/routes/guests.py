from fastapi import APIRouter, Request, status

from app.api.deps import DB, CurrentUser
from app.core import rate_limit as rl
from app.core.db import utcnow
from app.core.errors import BusinessRuleError
from app.domain.enums import MatchStatus
from app.schemas.guest import (
    GuestCreate,
    GuestJoinIn,
    GuestJoinOut,
    GuestLinkOut,
    GuestLinkPreviewOut,
    GuestMatchIn,
    GuestMatchOut,
    GuestOut,
    GuestPassOut,
    GuestUpdate,
    ShareLinksOut,
)
from app.services import attendance, guest_pass, guests, level_quiz
from app.services.access import get_group, match_for_member, require_member

router = APIRouter(tags=["Convidados"])
public_router = APIRouter(prefix="/public/guest", tags=["Convidado sem login"])


@router.get("/groups/{group_id}/guests", response_model=list[GuestOut])
def list_guests(group_id: int, user: CurrentUser, db: DB):
    """Convidados do grupo (jogadores sem conta trazidos pelos membros)."""
    me = require_member(db, group_id, user)
    items = guests.list_guests(db, group_id)
    levels = level_quiz.info_bulk(db, items)
    return [guests.guest_out(db, g, me, levels[g.id]) for g in items]


@router.post("/groups/{group_id}/guests", response_model=GuestOut, status_code=status.HTTP_201_CREATED)
def create_guest(group_id: int, data: GuestCreate, user: CurrentUser, db: DB):
    """Qualquer membro cadastra um convidado e diz quantas estrelas acha que ele tem. O nível vale desde já e,
    se quem convidou não é administrador, aguarda a confirmação de um admin."""
    me = require_member(db, group_id, user)
    guest = guests.create_guest(db, get_group(db, group_id), me, data.name, data.stars, data.goalkeeper)
    return guests.guest_out(db, guest, me)


@router.patch("/groups/{group_id}/guests/{member_id}", response_model=GuestOut)
def update_guest(group_id: int, member_id: int, data: GuestUpdate, user: CurrentUser, db: DB):
    me = require_member(db, group_id, user)
    guest = guests.get_guest(db, group_id, member_id)
    guest = guests.update_guest(db, get_group(db, group_id), guest, me, name=data.name, goalkeeper=data.goalkeeper)
    return guests.guest_out(db, guest, me)


@router.delete("/groups/{group_id}/guests/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_guest(group_id: int, member_id: int, user: CurrentUser, db: DB):
    """Tira o convidado do grupo (e das próximas peladas). Histórico e cobranças continuam."""
    me = require_member(db, group_id, user)
    guests.remove_guest(db, get_group(db, group_id), guests.get_guest(db, group_id, member_id), me)


@router.post("/matches/{match_id}/guests", response_model=GuestMatchOut)
def add_guest_to_match(match_id: int, data: GuestMatchIn, user: CurrentUser, db: DB):
    """Coloca um convidado na pelada (quem convidou ou admin). Ocupa vaga ou entra na lista de espera; a cobrança
    vai para quem convidou."""
    match, me = match_for_member(db, match_id, user)
    group = get_group(db, match.group_id)
    guest = guests.get_guest(db, group.id, data.member_id)
    mp, warnings = guests.add_to_match(db, group, match, guest, me, data.as_goalkeeper)
    position = None
    if mp.status.value == "waitlist":
        position = next((i + 1 for i, w in enumerate(attendance.waitlist(db, match)) if w.id == mp.id), None)
    return {"participation": attendance.participation_out(mp, waitlist_position=position), "warnings": warnings}


@router.delete("/matches/{match_id}/guests/{member_id}", response_model=GuestMatchOut)
def remove_guest_from_match(match_id: int, member_id: int, user: CurrentUser, db: DB):
    match, me = match_for_member(db, match_id, user)
    group = get_group(db, match.group_id)
    guest = guests.get_guest(db, group.id, member_id)
    mp = guests.remove_from_match(db, group, match, guest, me)
    return {"participation": attendance.participation_out(mp), "warnings": []}


@router.get("/matches/{match_id}/guest-link", response_model=GuestLinkOut)
def guest_link(match_id: int, user: CurrentUser, db: DB):
    """Link da pelada para mandar a quem não tem conta. Quem entrar por ele vira convidado de quem compartilhou
    (e a cobrança vai para quem compartilhou)."""
    match, me = match_for_member(db, match_id, user)
    if match.status != MatchStatus.PUBLISHED or utcnow() >= match.starts_at:
        raise BusinessRuleError("Só peladas publicadas e ainda não iniciadas têm link de convidado",
                                code="match_not_open")
    return {"token": guest_pass.invite_token(db, match, me), "expires_at": match.starts_at}


@router.get("/matches/{match_id}/share-links", response_model=ShareLinksOut)
def share_links(match_id: int, user: CurrentUser, db: DB):
    """Os dois links para compartilhar a pelada: convidado sem conta e convite normal (entra no grupo)."""
    match, me = match_for_member(db, match_id, user)
    group = get_group(db, match.group_id)
    can_guest = match.status == MatchStatus.PUBLISHED and utcnow() < match.starts_at
    return {
        "guest_token": guest_pass.invite_token(db, match, me) if can_guest else None,
        "invite_token": group.invite_token if group.is_active else None,
        "expires_at": match.starts_at,
    }


@public_router.get("/link/{token}", response_model=GuestLinkPreviewOut)
def link_preview(token: str, db: DB):
    """Prévia da pelada para quem abriu o link (sem login)."""
    return guest_pass.link_preview(db, token)


@public_router.post("/link/{token}/join", response_model=GuestJoinOut, status_code=status.HTTP_201_CREATED)
def join_by_link(token: str, data: GuestJoinIn, request: Request, db: DB):
    """Entra na pelada como convidado, sem login. Devolve o passe que dá acesso de leitura à pelada."""
    rl.limiter.consume(rl.GUEST_JOIN_PER_IP, rl.client_ip(request))
    rl.limiter.consume(rl.GUEST_JOIN_PER_LINK, token[-40:])
    return guest_pass.join(db, token, data.name, data.stars, data.goalkeeper)


@public_router.get("/pass/{token}", response_model=GuestPassOut)
def pass_view(token: str, db: DB):
    return guest_pass.view(db, token)


@public_router.post("/pass/{token}/leave", response_model=GuestPassOut)
def pass_leave(token: str, db: DB):
    """O convidado desiste da pelada."""
    return guest_pass.leave(db, token)
