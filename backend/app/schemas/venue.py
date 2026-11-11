"""Espaços esportivos: cadastro, agenda, vínculo com grupos, aluguel e painel do dono."""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.domain.enums import (
    BookingStatus,
    MatchStatus,
    Modality,
    RentStatus,
    VenueLinkOrigin,
    VenueLinkStatus,
    VenueRole,
)
from app.schemas.common import ORMModel

_HHMM = re.compile(r"^(([01]\d|2[0-3]):[0-5]\d|24:00)$")

OpeningHours = dict[str, list[str] | None]


def validate_opening_hours(v: OpeningHours | None) -> OpeningHours | None:
    """Chaves "0" (segunda) a "6" (domingo); valor [abre, fecha] em HH:MM local ou vazio para fechado."""
    if v is None:
        return v
    out: OpeningHours = {}
    for key, interval in v.items():
        if key not in {str(d) for d in range(7)}:
            raise ValueError("Use os dias 0 (segunda) a 6 (domingo)")
        if not interval:
            continue  # fechado
        if len(interval) != 2 or not all(_HHMM.match(t or "") for t in interval):
            raise ValueError("Horário no formato HH:MM, ex.: [\"08:00\", \"23:00\"]")
        if interval[0] >= interval[1]:
            raise ValueError("O horário de fechamento deve ser depois da abertura")
        out[key] = [interval[0], interval[1]]
    return out


class VenueCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=30)
    opening_hours: OpeningHours | None = None

    _hours = field_validator("opening_hours")(validate_opening_hours)


class AdminVenueCreate(VenueCreate):
    """Super-Admin cadastra o espaço já com o dono (DE)."""

    owner_name: str = Field(min_length=2, max_length=120)
    owner_email: EmailStr


class AdminVenueUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=30)
    is_active: bool | None = None


class OwnerIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr


class VenueOwnerOut(BaseModel):
    staff_id: int
    user_id: int
    name: str
    email: str
    claimed: bool  # já criou a conta (senão, o acesso vale quando criar com este e-mail)


class AdminVenueRow(BaseModel):
    id: int
    name: str
    address: str | None
    phone: str | None
    is_active: bool
    created_at: datetime
    owners: list[VenueOwnerOut]
    courts: int
    active_groups: int
    pending_bookings: int


class VenuePeladaCreate(BaseModel):
    """Pelada de horário fixo semanal criada pelo DE na agenda (já com o administrador)."""

    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    modality: Modality | None = None  # padrão: a da quadra
    court_id: int
    first_starts_at: datetime  # primeira data; repete toda semana no mesmo dia e hora
    duration_minutes: int = Field(default=60, ge=30, le=600)
    slots: int = Field(default=14, ge=2, le=200)
    num_teams: int | None = Field(default=None, ge=2, le=8)
    price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2,
                           description="Valor por jogador")
    admin_name: str = Field(min_length=2, max_length=120)
    admin_email: EmailStr
    rent_hourly_price: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)


class BookingOut(BaseModel):
    """Horário pedido por um grupo sem vínculo, aguardando o DE."""

    match_id: int
    match_name: str
    group_id: int
    group_name: str
    court_id: int | None
    court_name: str | None
    starts_at: datetime
    ends_at: datetime
    booking_status: BookingStatus | None
    confirmed: int
    slots: int
    created_at: datetime
    admins: list["GroupAdminContact"]  # só enquanto pendente


class BookingRejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class VenueUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=30)
    opening_hours: OpeningHours | None = None
    is_active: bool | None = None  # só o dono

    _hours = field_validator("opening_hours")(validate_opening_hours)


class CourtIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    modality: Modality = Modality.SOCIETY
    hourly_price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)


class CourtUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    modality: Modality | None = None
    hourly_price: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    is_active: bool | None = None


class CourtOut(ORMModel):
    id: int
    venue_id: int
    name: str
    modality: Modality
    hourly_price: Decimal
    is_active: bool


class VenueOut(BaseModel):
    """Visão da equipe do espaço."""

    id: int
    name: str
    description: str | None
    address: str | None
    phone: str | None
    opening_hours: OpeningHours
    is_active: bool
    link_code: str  # informado pelos administradores de grupos para pedir vínculo
    created_at: datetime
    courts: list[CourtOut]
    my_role: VenueRole


class VenuePublicOut(BaseModel):
    """O que os membros de um grupo vinculado veem do espaço."""

    id: int
    name: str
    address: str | None
    phone: str | None
    opening_hours: OpeningHours
    courts: list[CourtOut]


class StaffIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=120)
    role: VenueRole = VenueRole.MANAGER


class StaffUpdate(BaseModel):
    role: VenueRole | None = None
    is_active: bool | None = None


class StaffOut(BaseModel):
    id: int
    user_id: int
    name: str
    email: str
    role: VenueRole
    is_active: bool
    created_at: datetime


class BlockIn(BaseModel):
    court_id: int
    starts_at: datetime
    ends_at: datetime
    reason: str = Field(min_length=2, max_length=200)


class BlockOut(ORMModel):
    id: int
    court_id: int
    starts_at: datetime
    ends_at: datetime
    reason: str


# ------------------------------------------------------------------ agenda


class TimeRange(BaseModel):
    starts_at: datetime
    ends_at: datetime


class CalendarItem(BaseModel):
    kind: Literal["match", "block"]
    id: int
    starts_at: datetime
    ends_at: datetime
    title: str
    group_id: int | None = None
    group_name: str | None = None
    match_status: MatchStatus | None = None
    confirmed: int | None = None
    slots: int | None = None
    series_id: int | None = None
    mine: bool = False  # visão do grupo: pelada do próprio grupo
    booking_status: BookingStatus | None = None  # pending: aguardando o DE confirmar


class CalendarCourtDay(BaseModel):
    court_id: int
    items: list[CalendarItem]
    free: list[TimeRange]


class CalendarDay(BaseModel):
    date: date
    opens_at: datetime | None  # None = fechado
    closes_at: datetime | None
    courts: list[CalendarCourtDay]


class CalendarOut(BaseModel):
    start: date
    days: int
    courts: list[CourtOut]
    schedule: list[CalendarDay]


# ------------------------------------------------------------------ vínculo com grupos


class GroupAdminContact(BaseModel):
    name: str
    email: str
    phone: str | None


class VenueGroupOut(BaseModel):
    id: int  # id do vínculo
    venue_id: int
    venue_name: str
    group_id: int
    group_name: str
    status: VenueLinkStatus
    origin: VenueLinkOrigin
    rent_hourly_price: Decimal | None
    message: str | None
    created_at: datetime
    decided_at: datetime | None
    ended_at: datetime | None
    admins: list[GroupAdminContact]
    active_members: int
    next_match_at: datetime | None
    rent_pending: Decimal


class VenueGroupCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    modality: Modality = Modality.SOCIETY
    admin_email: EmailStr
    admin_name: str = Field(min_length=2, max_length=120)
    rent_hourly_price: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)


class VenueGroupUpdate(BaseModel):
    rent_hourly_price: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)


class VenueGroupAdminIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=120)
    replace: bool = Field(default=False, description="Tira o papel de administrador dos demais")


class LinkRequestIn(BaseModel):
    code: str = Field(min_length=4, max_length=16)
    message: str | None = Field(default=None, max_length=300)


class GroupVenueOut(BaseModel):
    """Espaço do grupo, visto pelos membros."""

    link_id: int | None = None
    status: VenueLinkStatus | None = None
    origin: VenueLinkOrigin | None = None
    venue: VenuePublicOut | None = None
    rent_hourly_price: Decimal | None = None  # apenas administradores


# ------------------------------------------------------------------ aluguel


class RentChargeOut(BaseModel):
    id: int
    venue_id: int
    venue_name: str
    court_id: int
    court_name: str
    group_id: int
    group_name: str
    match_id: int
    match_name: str
    match_starts_at: datetime
    amount: Decimal
    status: RentStatus
    reported_paid_at: datetime | None
    paid_at: datetime | None
    method: str | None
    notes: str | None


class RentChargeUpdate(BaseModel):
    status: RentStatus | None = None
    amount: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    method: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=300)


class RentSettleGroupIn(BaseModel):
    group_id: int
    month: str | None = Field(default=None, description="AAAA-MM; padrão: mês atual")
    method: str | None = Field(default=None, max_length=40)


class RentSettleIn(BaseModel):
    charge_ids: list[int] = Field(min_length=1, max_length=200)
    method: str | None = Field(default=None, max_length=40)


class RentGroupSummary(BaseModel):
    group_id: int
    group_name: str
    charges: int
    amount: Decimal
    paid: Decimal
    pending: Decimal
    reported: int  # pendentes que o grupo já informou ter pago


class RentListOut(BaseModel):
    month: str  # AAAA-MM
    total: Decimal
    paid: Decimal
    pending: Decimal
    exempt: Decimal
    items: list[RentChargeOut]  # paginado (filtros de grupo e situação valem só aqui)
    items_total: int = 0
    page: int = 1
    size: int = 50
    groups: list[RentGroupSummary]  # fechamento do mês inteiro


class GroupRentOut(BaseModel):
    pending_amount: Decimal
    pending_count: int
    items: list[RentChargeOut]  # todas as pendentes e as últimas quitadas


# ------------------------------------------------------------------ financeiro do dono


class FinanceMonth(BaseModel):
    month: str  # AAAA-MM
    matches: int
    expected: Decimal  # aluguel devido (sem isentos nem cancelados)
    received: Decimal
    pending: Decimal
    exempt: Decimal


class FinanceCourt(BaseModel):
    court_id: int
    name: str
    matches: int
    hours: float
    expected: Decimal
    received: Decimal


class FinanceMethod(BaseModel):
    method: str
    count: int
    amount: Decimal


class FinanceAging(BaseModel):
    upto_30: Decimal  # pendente de peladas com até 30 dias
    from_31_to_60: Decimal
    over_60: Decimal


class FinanceOut(BaseModel):
    """Financeiro consolidado do espaço (só o dono). Valores e nomes de grupos; nunca dados de atletas."""

    months: int
    series: list[FinanceMonth]  # do mês mais antigo ao atual
    expected: Decimal
    received: Decimal
    pending: Decimal
    exempt: Decimal
    collection_rate: float | None  # recebido / devido
    average_per_match: Decimal
    overdue_total: Decimal  # pendente de qualquer período
    aging: FinanceAging
    upcoming_matches: int  # peladas marcadas que ainda não geraram aluguel
    upcoming_expected: Decimal
    by_court: list[FinanceCourt]
    by_method: list[FinanceMethod]
    debtors: list["OverdueGroup"]  # definido mais abaixo


# ------------------------------------------------------------------ painel


class CourtOccupancy(BaseModel):
    court_id: int
    name: str
    open_hours: float
    booked_hours: float
    blocked_hours: float
    occupancy_rate: float | None


class AtRiskMatch(BaseModel):
    match_id: int
    match_name: str
    group_id: int
    group_name: str
    court_name: str
    starts_at: datetime
    confirmed: int
    slots: int


class OverdueGroup(BaseModel):
    group_id: int
    group_name: str
    pending_amount: Decimal
    charges: int
    oldest_match_at: datetime
    reported: int


class GroupActivity(BaseModel):
    group_id: int
    group_name: str
    matches_held: int
    matches_cancelled: int
    fill_rate: float | None  # confirmados (ou presentes) / vagas nas peladas realizadas
    rent_pending: Decimal


class VenueDashboardOut(BaseModel):
    since: date
    until: date
    open_hours: float
    booked_hours: float
    occupancy_rate: float | None
    matches_held: int
    matches_upcoming: int
    matches_cancelled: int
    cancelled_hours: float
    rent_expected: Decimal  # aluguel das peladas realizadas no período
    rent_received: Decimal
    rent_pending: Decimal
    rent_overdue_total: Decimal  # tudo que está pendente, de qualquer período
    active_groups: int
    pending_requests: int
    pending_bookings: int = 0  # horários pedidos por grupos sem vínculo, aguardando o dono
    courts: list[CourtOccupancy]
    at_risk: list[AtRiskMatch]  # próximos 7 dias com menos da metade das vagas confirmadas
    overdue_groups: list[OverdueGroup]
    groups: list[GroupActivity]


# ------------------------------------------------------------------ métricas por espaço (Super-Admin)


class VenueMetricsRow(BaseModel):
    venue_id: int
    name: str
    is_active: bool
    owners: list[str]
    courts: int
    occupancy_rate: float | None
    booked_hours: float
    matches_held: int
    matches_cancelled: int
    matches_upcoming: int
    players: int  # jogadores diferentes que confirmaram ou jogaram nas peladas realizadas
    active_groups: int
    pending_bookings: int
    rent_expected: Decimal
    rent_received: Decimal
    rent_pending: Decimal
    rent_overdue_total: Decimal  # tudo que está pendente, de qualquer período


class VenuesMetricsOut(BaseModel):
    since: date
    until: date
    venues: int
    active_venues: int
    matches_held: int
    matches_cancelled: int
    players: int  # soma por espaço (quem joga em dois espaços conta nos dois)
    occupancy_rate: float | None
    rent_expected: Decimal
    rent_received: Decimal
    rent_overdue_total: Decimal
    items: list[VenueMetricsRow]  # maior receita primeiro, paginado
    total: int = 0  # espaços (para a paginação)
    page: int = 1
    size: int = 20
