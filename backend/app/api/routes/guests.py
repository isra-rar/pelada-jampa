from fastapi import APIRouter, status

from app.api.deps import DB, CurrentUser
from app.schemas.guest import GuestCreate, GuestMatchIn, GuestMatchOut, GuestOut, GuestUpdate
from app.services import attendance, guests, level_quiz
from app.services.access import get_group, match_for_member, require_member

router = APIRouter(tags=["Convidados"])


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
