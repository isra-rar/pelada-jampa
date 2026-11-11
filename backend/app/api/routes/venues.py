from datetime import date

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DB, CurrentUser
from app.core.db import utcnow
from app.core.errors import NotFound
from app.domain.enums import RentStatus, VenueLinkStatus
from app.schemas.venue import (
    BlockIn,
    BlockOut,
    BookingOut,
    BookingRejectIn,
    CalendarOut,
    CourtIn,
    CourtOut,
    CourtUpdate,
    FinanceOut,
    GroupRentOut,
    GroupVenueOut,
    LinkRequestIn,
    RentChargeOut,
    RentChargeUpdate,
    RentListOut,
    RentSettleIn,
    StaffIn,
    StaffOut,
    StaffUpdate,
    VenueDashboardOut,
    VenueGroupAdminIn,
    VenueGroupCreate,
    VenueGroupOut,
    VenueGroupUpdate,
    VenueOut,
    VenuePeladaCreate,
    VenuePublicOut,
    VenueUpdate,
)
from app.services import venue_finance, venues
from app.services.access import get_group, require_admin, require_member
from app.services.stats import BR_TZ

router = APIRouter(tags=["Espaços (quadras)"])

_Start = Query(default=None, description="Primeiro dia (data local); padrão: hoje")
_Days = Query(default=7, ge=1, le=31)


def _start(start: date | None) -> date:
    return start or utcnow().astimezone(BR_TZ).date()


# ------------------------------------------------------------------ espaço (equipe)


@router.get("/venues", response_model=list[VenueOut])
def my_venues(user: CurrentUser, db: DB):
    """Espaços de que a pessoa é dona (DE) ou funcionária. Espaços são cadastrados pelo Super-Admin."""
    return [venues.venue_out(db, v, s) for v, s in venues.list_my_venues(db, user)]


@router.get("/venues/directory", response_model=list[VenuePublicOut])
def venue_directory(user: CurrentUser, db: DB, search: str | None = None):
    """Espaços ativos para o administrador de um grupo marcar a pelada numa quadra."""
    return venues.directory(db, search)


@router.get("/venues/{venue_id}", response_model=VenueOut)
def get_venue(venue_id: int, user: CurrentUser, db: DB):
    venue, staff = venues.require_staff(db, venue_id, user)
    return venues.venue_out(db, venue, staff)


@router.patch("/venues/{venue_id}", response_model=VenueOut)
def update_venue(venue_id: int, data: VenueUpdate, user: CurrentUser, db: DB):
    venue, staff = venues.require_staff(db, venue_id, user)
    return venues.venue_out(db, venues.update_venue(db, venue, staff, data, user), staff)


@router.post("/venues/{venue_id}/code/rotate", response_model=VenueOut)
def rotate_code(venue_id: int, user: CurrentUser, db: DB):
    """Gera um novo código de vínculo (o anterior deixa de funcionar)."""
    venue, staff = venues.require_staff(db, venue_id, user)
    return venues.venue_out(db, venues.rotate_code(db, venue, user), staff)


@router.post("/venues/{venue_id}/courts", response_model=CourtOut, status_code=status.HTTP_201_CREATED)
def add_court(venue_id: int, data: CourtIn, user: CurrentUser, db: DB):
    venue, _ = venues.require_staff(db, venue_id, user)
    return venues.add_court(db, venue, data, user)


@router.patch("/venues/{venue_id}/courts/{court_id}", response_model=CourtOut)
def update_court(venue_id: int, court_id: int, data: CourtUpdate, user: CurrentUser, db: DB):
    venue, _ = venues.require_staff(db, venue_id, user)
    return venues.update_court(db, venue, venues.get_court(db, venue, court_id), data, user)


@router.get("/venues/{venue_id}/staff", response_model=list[StaffOut])
def list_staff(venue_id: int, user: CurrentUser, db: DB):
    venue, _ = venues.require_staff(db, venue_id, user)
    return [venues.staff_out(s) for s in venues.list_staff(db, venue)]


@router.post("/venues/{venue_id}/staff", response_model=StaffOut, status_code=status.HTTP_201_CREATED)
def add_staff(venue_id: int, data: StaffIn, user: CurrentUser, db: DB):
    """Só o dono. Quem ainda não tem conta passa a ter acesso ao criar a conta com o mesmo e-mail."""
    venue, _ = venues.require_staff(db, venue_id, user, owner=True)
    return venues.staff_out(venues.add_staff(db, venue, data, user))


@router.patch("/venues/{venue_id}/staff/{staff_id}", response_model=StaffOut)
def update_staff(venue_id: int, staff_id: int, data: StaffUpdate, user: CurrentUser, db: DB):
    venue, _ = venues.require_staff(db, venue_id, user, owner=True)
    return venues.staff_out(venues.update_staff(db, venue, venues.get_staff(db, venue, staff_id), data, user))


# ------------------------------------------------------------------ agenda


@router.get("/venues/{venue_id}/calendar", response_model=CalendarOut)
def venue_calendar(venue_id: int, user: CurrentUser, db: DB, start: date | None = _Start, days: int = _Days):
    """Agenda por dia e quadra: peladas (com confirmados), bloqueios e horários livres."""
    venue, _ = venues.require_staff(db, venue_id, user)
    return venues.calendar(db, venue, _start(start), days)


@router.post("/venues/{venue_id}/blocks", response_model=BlockOut, status_code=status.HTTP_201_CREATED)
def add_block(venue_id: int, data: BlockIn, user: CurrentUser, db: DB):
    """Bloqueia um horário (manutenção, evento, reserva avulsa). Não pode cobrir pelada marcada."""
    venue, _ = venues.require_staff(db, venue_id, user)
    return venues.add_block(db, venue, data, user)


@router.delete("/venues/{venue_id}/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_block(venue_id: int, block_id: int, user: CurrentUser, db: DB):
    venue, _ = venues.require_staff(db, venue_id, user)
    venues.delete_block(db, venue, block_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ grupos do espaço


@router.get("/venues/{venue_id}/groups", response_model=list[VenueGroupOut])
def venue_groups(venue_id: int, user: CurrentUser, db: DB,
                 status_: VenueLinkStatus | None = Query(default=None, alias="status",
                                                         description="Padrão: pendentes e ativos")):
    venue, _ = venues.require_staff(db, venue_id, user)
    return [venues.link_out(db, link) for link in venues.list_links(db, venue, status_)]


@router.post("/venues/{venue_id}/groups", response_model=VenueGroupOut, status_code=status.HTTP_201_CREATED)
def create_venue_group(venue_id: int, data: VenueGroupCreate, user: CurrentUser, db: DB):
    """Cria uma pelada no espaço e define quem a administra (o espaço não entra no grupo)."""
    venue, _ = venues.require_staff(db, venue_id, user)
    return venues.link_out(db, venues.create_group(db, venue, data, user))


@router.patch("/venues/{venue_id}/groups/{link_id}", response_model=VenueGroupOut)
def update_venue_group(venue_id: int, link_id: int, data: VenueGroupUpdate, user: CurrentUser, db: DB):
    """Preço por hora combinado com o grupo (vazio = preço da quadra). Vale para os próximos aluguéis."""
    venue, _ = venues.require_staff(db, venue_id, user)
    link = venues.get_link(db, venue, link_id)
    return venues.link_out(db, venues.update_link(db, link, data.rent_hourly_price, user))


@router.post("/venues/{venue_id}/groups/{link_id}/approve", response_model=VenueGroupOut)
def approve_link(venue_id: int, link_id: int, user: CurrentUser, db: DB):
    venue, _ = venues.require_staff(db, venue_id, user)
    return venues.link_out(db, venues.decide_link(db, venue, venues.get_link(db, venue, link_id), True, user))


@router.post("/venues/{venue_id}/groups/{link_id}/reject", response_model=VenueGroupOut)
def reject_link(venue_id: int, link_id: int, user: CurrentUser, db: DB):
    venue, _ = venues.require_staff(db, venue_id, user)
    return venues.link_out(db, venues.decide_link(db, venue, venues.get_link(db, venue, link_id), False, user))


@router.post("/venues/{venue_id}/groups/{link_id}/end", response_model=VenueGroupOut)
def end_link(venue_id: int, link_id: int, user: CurrentUser, db: DB):
    """Encerra o vínculo: as próximas peladas do grupo saem da agenda; histórico e aluguel lançado ficam."""
    venue, _ = venues.require_staff(db, venue_id, user)
    return venues.link_out(db, venues.end_link(db, venues.get_link(db, venue, link_id), user, by_venue=True))


@router.post("/venues/{venue_id}/groups/{link_id}/admin", response_model=VenueGroupOut)
def set_group_admin(venue_id: int, link_id: int, data: VenueGroupAdminIn, user: CurrentUser, db: DB):
    """Pelada criada pelo espaço: define (ou troca, com `replace`) quem a administra."""
    venue, _ = venues.require_staff(db, venue_id, user)
    link = venues.get_link(db, venue, link_id)
    return venues.link_out(db, venues.set_group_admin(db, venue, link, data, user))


# ------------------------------------------------------------------ horários pedidos e peladas do DE


@router.get("/venues/{venue_id}/bookings", response_model=list[BookingOut])
def venue_bookings(venue_id: int, user: CurrentUser, db: DB):
    """Horários pendentes: peladas marcadas por grupos sem vínculo, aguardando confirmação de data e horário."""
    venue, _ = venues.require_staff(db, venue_id, user)
    return [venues.booking_out(db, m) for m in venues.list_bookings(db, venue)]


@router.post("/venues/{venue_id}/bookings/{match_id}/confirm", response_model=BookingOut)
def confirm_booking(venue_id: int, match_id: int, user: CurrentUser, db: DB):
    """Confirma o horário; o grupo passa a ser vinculado ao espaço (se não tiver outro)."""
    venue, _ = venues.require_staff(db, venue_id, user)
    return venues.booking_out(db, venues.decide_booking(db, venue, match_id, True, user))


@router.post("/venues/{venue_id}/bookings/{match_id}/reject", response_model=BookingOut)
def reject_booking(venue_id: int, match_id: int, data: BookingRejectIn, user: CurrentUser, db: DB):
    """Recusa o horário: a pelada continua para os jogadores, mas sem a quadra."""
    venue, _ = venues.require_staff(db, venue_id, user)
    return venues.booking_out(db, venues.decide_booking(db, venue, match_id, False, user, data.reason))


@router.post("/venues/{venue_id}/peladas", response_model=VenueGroupOut, status_code=status.HTTP_201_CREATED)
def create_weekly_pelada(venue_id: int, data: VenuePeladaCreate, user: CurrentUser, db: DB):
    """Cria uma pelada de horário fixo semanal numa quadra, com o administrador. As próximas semanas já entram
    confirmadas na agenda."""
    venue, _ = venues.require_staff(db, venue_id, user)
    return venues.link_out(db, venues.create_weekly_pelada(db, venue, data, user))


# ------------------------------------------------------------------ aluguel e painel


@router.get("/venues/{venue_id}/rent", response_model=RentListOut)
def venue_rent(venue_id: int, user: CurrentUser, db: DB,
               month: str | None = Query(default=None, description="AAAA-MM; padrão: mês atual"),
               status_: RentStatus | None = Query(default=None, alias="status"), group_id: int | None = None):
    """Aluguel do mês (pelas datas das peladas), com totais e resumo por grupo."""
    venue, _ = venues.require_staff(db, venue_id, user)
    return venue_finance.list_rent(db, venue, month=month, status=status_, group_id=group_id)


@router.patch("/venues/{venue_id}/rent/{charge_id}", response_model=RentChargeOut)
def update_rent(venue_id: int, charge_id: int, data: RentChargeUpdate, user: CurrentUser, db: DB):
    venue, _ = venues.require_staff(db, venue_id, user)
    charge = venue_finance.update_charge(db, venue, venue_finance.get_charge(db, charge_id), data, user)
    return venue_finance.charge_out(db, charge)


@router.post("/venues/{venue_id}/rent/settle", response_model=list[RentChargeOut])
def settle_rent(venue_id: int, data: RentSettleIn, user: CurrentUser, db: DB):
    """Quita vários aluguéis de uma vez (ex.: fechamento do mês de um grupo)."""
    venue, _ = venues.require_staff(db, venue_id, user)
    charges = venue_finance.settle(db, venue, data.charge_ids, data.method, user)
    return [venue_finance.charge_out(db, c) for c in charges]


@router.get("/venues/{venue_id}/finance", response_model=FinanceOut)
def venue_finance_overview(venue_id: int, user: CurrentUser, db: DB,
                           months: int = Query(default=6, ge=1, le=24, description="Meses até o atual")):
    """Financeiro consolidado do espaço (receita de aluguel por mês e quadra, inadimplência, previsão). Só o dono;
    traz valores e nomes de grupos, nunca dados de atletas ou de administradores de outras peladas."""
    venue, _ = venues.require_staff(db, venue_id, user, owner=True)
    return venue_finance.finance_overview(db, venue, months)


@router.get(
    "/venues/{venue_id}/finance/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Planilha CSV (separador ;)"}},
)
def venue_finance_export(venue_id: int, user: CurrentUser, db: DB,
                         month: str | None = Query(default=None, description="AAAA-MM; padrão: mês atual")):
    """Aluguel do mês em CSV para planilha. Só o dono."""
    venue, _ = venues.require_staff(db, venue_id, user, owner=True)
    content, filename, _rows = venue_finance.export_rent_csv(db, venue, month, user)
    return Response(
        content=content.encode("utf-8"), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/venues/{venue_id}/dashboard", response_model=VenueDashboardOut)
def venue_dashboard(venue_id: int, user: CurrentUser, db: DB,
                    since: date | None = Query(default=None, description="Padrão: início do mês"),
                    until: date | None = Query(default=None, description="Padrão: fim do mês")):
    """Ocupação das quadras, peladas realizadas e canceladas, aluguel e grupos em atraso ou em risco."""
    venue, _ = venues.require_staff(db, venue_id, user)
    return venue_finance.dashboard(db, venue, since, until)


# ------------------------------------------------------------------ visão do grupo


@router.get("/groups/{group_id}/venue", response_model=GroupVenueOut)
def group_venue(group_id: int, user: CurrentUser, db: DB):
    """Espaço onde o grupo joga (vazio se não houver vínculo)."""
    member = require_member(db, group_id, user)
    return venues.group_venue(db, get_group(db, group_id), member)


@router.post("/groups/{group_id}/venue/request", response_model=GroupVenueOut, status_code=status.HTTP_201_CREATED)
def request_venue_link(group_id: int, data: LinkRequestIn, user: CurrentUser, db: DB):
    """Pede vínculo com um espaço pelo código que o espaço informa. O espaço verá nome e contato dos
    administradores do grupo."""
    member = require_admin(db, group_id, user)
    group = get_group(db, group_id)
    venues.request_link(db, group, user, data.code, data.message)
    return venues.group_venue(db, group, member)


@router.delete("/groups/{group_id}/venue", response_model=GroupVenueOut)
def leave_venue(group_id: int, user: CurrentUser, db: DB):
    """Cancela o pedido ou sai do espaço (peladas criadas pelo espaço só o espaço desvincula)."""
    member = require_admin(db, group_id, user)
    group = get_group(db, group_id)
    venues.end_link(db, venues.group_link_for_admin(db, group), user, by_venue=False)
    return venues.group_venue(db, group, member)


@router.get("/groups/{group_id}/venue/calendar", response_model=CalendarOut)
def group_venue_calendar(group_id: int, user: CurrentUser, db: DB, start: date | None = _Start,
                         days: int = _Days):
    """Agenda do espaço vinculado para marcar peladas: horários de outros grupos aparecem só como ocupados."""
    require_admin(db, group_id, user)
    link = venues.active_link(db, group_id)
    if link is None:
        raise NotFound("O grupo não tem espaço vinculado", code="no_venue")
    return venues.calendar(db, venues.get_venue(db, link.venue_id), _start(start), days, viewer_group_id=group_id)


@router.get("/groups/{group_id}/venues/{venue_id}/calendar", response_model=CalendarOut)
def group_any_venue_calendar(group_id: int, venue_id: int, user: CurrentUser, db: DB,
                             start: date | None = _Start, days: int = _Days):
    """Horários livres de qualquer espaço ativo, para o administrador pedir um horário (peladas dos outros grupos
    aparecem só como "Ocupado")."""
    require_admin(db, group_id, user)
    venue = venues.get_venue(db, venue_id)
    if not venue.is_active:
        raise NotFound("Espaço não encontrado")
    return venues.calendar(db, venue, _start(start), days, viewer_group_id=group_id)


@router.get("/groups/{group_id}/rent", response_model=GroupRentOut)
def group_rent(group_id: int, user: CurrentUser, db: DB):
    """Aluguel devido aos espaços: pendentes e últimos quitados."""
    require_admin(db, group_id, user)
    return venue_finance.group_rent(db, get_group(db, group_id))


@router.post("/groups/{group_id}/rent/{charge_id}/report", response_model=RentChargeOut)
def report_rent(group_id: int, charge_id: int, user: CurrentUser, db: DB):
    """Avisa o espaço que o aluguel foi pago ("já paguei"); o espaço confirma o recebimento."""
    require_admin(db, group_id, user)
    charge = venue_finance.report_paid(db, get_group(db, group_id), venue_finance.get_charge(db, charge_id), user)
    return venue_finance.charge_out(db, charge)
