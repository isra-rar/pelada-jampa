from fastapi import APIRouter, Query, status

from app.api.deps import DB, CurrentUser, Paging, page_of
from app.domain.enums import AttendanceStatus, MatchStatus
from app.schemas.common import Page
from app.schemas.match import (
    AdminParticipationIn,
    ConfirmIn,
    ConfirmOut,
    DuplicateIn,
    GenerateIn,
    MakeRecurringIn,
    MatchCancel,
    MatchCreate,
    MatchOut,
    MatchPlayerOut,
    MatchUpdate,
    PresenceIn,
    PresenceOut,
    SeriesCreate,
    SeriesOut,
    SeriesUpdate,
)
from app.services import attendance, matches, payments
from app.services.access import get_group, match_for_admin, match_for_member, require_admin, require_member

router = APIRouter(tags=["Peladas e presença"])


# ------------------------------------------------------------------ ocorrências


@router.post("/groups/{group_id}/matches", response_model=MatchOut, status_code=status.HTTP_201_CREATED)
def create_match(group_id: int, data: MatchCreate, user: CurrentUser, db: DB):
    """Cria uma pelada avulsa. Com `publish=true` (padrão) os jogadores são notificados."""
    member = require_admin(db, group_id, user)
    match = matches.create_match(db, get_group(db, group_id), data, user)
    return matches.match_out(db, match, member)


@router.get("/groups/{group_id}/matches", response_model=Page[MatchOut])
def list_matches(
    group_id: int, user: CurrentUser, db: DB, paging: Paging,
    status_: list[MatchStatus] | None = Query(default=None, alias="status", description="Repita para vários"),
    upcoming: bool | None = Query(default=None, description="true: futuras; false: passadas"),
):
    member = require_member(db, group_id, user)
    items, total = matches.list_matches(db, group_id, status=status_, upcoming=upcoming,
                                        include_drafts=member.is_admin, page=paging.page, size=paging.size)
    return page_of([matches.match_out(db, m, member) for m in items], total, paging)


@router.get("/matches/{match_id}", response_model=MatchOut)
def get_match(match_id: int, user: CurrentUser, db: DB):
    match, member = match_for_member(db, match_id, user)
    if match.status == MatchStatus.DRAFT and not member.is_admin:
        from app.core.errors import NotFound

        raise NotFound("Pelada não encontrada")
    return matches.match_out(db, match, member)


@router.patch("/matches/{match_id}", response_model=MatchOut)
def update_match(match_id: int, data: MatchUpdate, user: CurrentUser, db: DB):
    match, member = match_for_admin(db, match_id, user)
    return matches.match_out(db, matches.update_match(db, get_group(db, match.group_id), match, data, user), member)


@router.post("/matches/{match_id}/publish", response_model=MatchOut)
def publish_match(match_id: int, user: CurrentUser, db: DB):
    match, member = match_for_admin(db, match_id, user)
    return matches.match_out(db, matches.publish_match(db, get_group(db, match.group_id), match, user), member)


@router.post("/matches/{match_id}/duplicate", response_model=MatchOut, status_code=status.HTTP_201_CREATED)
def duplicate_match(match_id: int, data: DuplicateIn, user: CurrentUser, db: DB):
    """Cópia da pelada, sem jogadores, em outra data (e já recorrente, se pedido)."""
    match, member = match_for_admin(db, match_id, user)
    group = get_group(db, match.group_id)
    return matches.match_out(db, matches.duplicate_match(db, group, match, data, user), member)


@router.post("/matches/{match_id}/recurrence", response_model=MatchOut)
def make_recurring(match_id: int, data: MakeRecurringIn, user: CurrentUser, db: DB):
    """Ativa a recorrência de uma pelada avulsa."""
    match, member = match_for_admin(db, match_id, user)
    group = get_group(db, match.group_id)
    return matches.match_out(db, matches.make_recurring(db, group, match, data, user), member)


@router.post("/matches/{match_id}/cancel", response_model=MatchOut)
def cancel_match(match_id: int, data: MatchCancel, user: CurrentUser, db: DB):
    match, member = match_for_admin(db, match_id, user)
    return matches.match_out(db, matches.cancel_match(db, get_group(db, match.group_id), match, data.reason, user),
                             member)


# ------------------------------------------------------------------ recorrência


@router.post("/groups/{group_id}/series", response_model=SeriesOut, status_code=status.HTTP_201_CREATED)
def create_series(group_id: int, data: SeriesCreate, user: CurrentUser, db: DB):
    """Cria uma pelada recorrente (semanal, quinzenal ou mensal) e gera as próximas ocorrências."""
    require_admin(db, group_id, user)
    series, _ = matches.create_series(db, get_group(db, group_id), data, user)
    return series


@router.get("/groups/{group_id}/series", response_model=list[SeriesOut])
def list_series(group_id: int, user: CurrentUser, db: DB):
    require_member(db, group_id, user)
    return matches.list_series(db, group_id)


@router.patch("/groups/{group_id}/series/{series_id}", response_model=SeriesOut)
def update_series(group_id: int, series_id: int, data: SeriesUpdate, user: CurrentUser, db: DB):
    """Altera o padrão das próximas ocorrências. Partidas já geradas não são modificadas."""
    require_admin(db, group_id, user)
    group = get_group(db, group_id)
    return matches.update_series(db, group, matches.get_series(db, group_id, series_id), data, user)


@router.post("/groups/{group_id}/series/{series_id}/generate", response_model=list[MatchOut])
def generate(group_id: int, series_id: int, data: GenerateIn, user: CurrentUser, db: DB):
    member = require_admin(db, group_id, user)
    group = get_group(db, group_id)
    created = matches.generate_occurrences(db, group, matches.get_series(db, group_id, series_id), data.count, user)
    return [matches.match_out(db, m, member) for m in created]


# ------------------------------------------------------------------ confirmação


@router.get("/matches/{match_id}/players", response_model=list[MatchPlayerOut])
def participants(match_id: int, user: CurrentUser, db: DB,
                 status_: AttendanceStatus | None = Query(default=None, alias="status")):
    match, member = match_for_member(db, match_id, user)
    return attendance.list_participants(db, match, admin=member.is_admin, status=status_)


@router.get("/matches/{match_id}/my-debts")
def my_debts(match_id: int, user: CurrentUser, db: DB):
    """Pendências anteriores exibidas antes de confirmar (PRD §8.3)."""
    match, member = match_for_member(db, match_id, user)
    return [payments.debt_out(p) for p in payments.pending_debts(db, match.group_id, member.id, match.id)]


@router.post("/matches/{match_id}/confirm", response_model=ConfirmOut)
def confirm(match_id: int, data: ConfirmIn, user: CurrentUser, db: DB):
    match, member = match_for_member(db, match_id, user)
    group = get_group(db, match.group_id)
    mp, warnings, debts = attendance.confirm(db, group, match, member, data.as_goalkeeper, data.as_referee)
    positions = {p.id: i + 1 for i, p in enumerate(attendance.waitlist(db, match))}
    return {
        "participation": attendance.participation_out(mp, waitlist_position=positions.get(mp.id)),
        "warnings": warnings,
        "pending_debts": [payments.debt_out(p) for p in debts],
    }


@router.post("/matches/{match_id}/decline", response_model=MatchPlayerOut)
def decline(match_id: int, user: CurrentUser, db: DB):
    """Recusa a pelada (ou desiste, se já confirmado)."""
    match, member = match_for_member(db, match_id, user)
    return attendance.participation_out(attendance.decline(db, get_group(db, match.group_id), match, member))


@router.post("/matches/{match_id}/cancel-confirmation", response_model=MatchPlayerOut)
def cancel_confirmation(match_id: int, user: CurrentUser, db: DB):
    match, member = match_for_member(db, match_id, user)
    mp = attendance.cancel_confirmation(db, get_group(db, match.group_id), match, member)
    return attendance.participation_out(mp)


@router.post("/matches/{match_id}/accept-spot", response_model=MatchPlayerOut)
def accept_spot(match_id: int, user: CurrentUser, db: DB):
    """Aceita a vaga oferecida da lista de espera (quando a promoção não é automática)."""
    match, member = match_for_member(db, match_id, user)
    return attendance.participation_out(attendance.accept_promotion(db, get_group(db, match.group_id), match, member))


@router.patch("/matches/{match_id}/players/{member_id}", response_model=MatchPlayerOut)
def admin_participation(match_id: int, member_id: int, data: AdminParticipationIn, user: CurrentUser, db: DB):
    """Ajuste manual auditado: status, goleiro, bloqueio por inadimplência (RN11)."""
    match, _ = match_for_admin(db, match_id, user)
    mp = attendance.admin_update(db, get_group(db, match.group_id), match, member_id, data, user)
    return attendance.participation_out(mp, admin=True)


@router.post("/matches/{match_id}/presence", response_model=PresenceOut)
def register_presence(match_id: int, data: PresenceIn, user: CurrentUser, db: DB):
    """Registra presença efetiva, gera cobranças e abre a janela de avaliação."""
    match, member = match_for_admin(db, match_id, user)
    match, created, warnings = attendance.register_presence(db, get_group(db, match.group_id), match, data, user)
    return {"match": matches.match_out(db, match, member), "payments_created": created, "warnings": warnings}
