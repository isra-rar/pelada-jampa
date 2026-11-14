from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.enums import (
    AttendanceStatus,
    BookingStatus,
    EvaluationStatus,
    MatchStatus,
    Modality,
    Recurrence,
)
from app.schemas.common import ORMModel

EvaluationWindow = Literal[6, 24]


def check_expansion(waitlist_limit, expand: bool, num_teams, expanded_num_teams) -> None:
    """Aumento da pelada quando a espera lota: precisa do tamanho da espera e dos times depois de aumentar."""
    if not expand:
        return
    if not waitlist_limit:
        raise ValueError("Para aumentar a pelada quando a espera lotar, informe o tamanho da lista de espera")
    if not expanded_num_teams:
        raise ValueError("Informe quantos times a pelada terá depois de aumentar")
    if num_teams and expanded_num_teams <= num_teams:
        raise ValueError("Depois de aumentar, a pelada precisa ter mais times do que antes")


class _MatchBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    modality: Modality = Modality.SOCIETY
    # com `court_id` o local e o endereço vêm do espaço vinculado ao grupo
    location: str | None = Field(default=None, min_length=2, max_length=160)
    address: str | None = Field(default=None, max_length=300)
    court_id: int | None = None
    price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2,
                           description="Valor fixo por jogador (quando não é rateio)")
    split_cost: bool = Field(default=False, description="Divide o custo total (campo + contratados) entre quem jogar")
    field_cost: Decimal | None = Field(
        default=None, ge=0, max_digits=10, decimal_places=2,
        description="Custo do campo/quadra (total). Vazio com quadra do espaço: calculado pelo aluguel",
    )
    slots: int = Field(ge=2, le=200)
    field_players_per_team: int = Field(ge=1, le=20)
    goalkeepers_per_team: int = Field(default=1, ge=0, le=2)
    referee_slots: int = Field(default=0, ge=0, le=2, description="Vagas de juiz (fora das vagas de jogador)")
    num_teams: int | None = Field(default=None, ge=2, le=8, description="Times previstos (padrão do sorteio)")
    waitlist_limit: int | None = Field(default=None, ge=1, le=100, description="Tamanho máximo da espera")
    expand_when_waitlist_full: bool = Field(
        default=False, description="Quando a espera lota, a pelada aumenta uma vez: vagas + espera"
    )
    expanded_num_teams: int | None = Field(default=None, ge=2, le=16, description="Times depois de aumentar")
    evaluation_window_hours: EvaluationWindow = 24
    responsible_admin_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _place(self):
        if not self.location and self.court_id is None:
            raise ValueError("Informe o local ou a quadra")
        check_expansion(self.waitlist_limit, self.expand_when_waitlist_full, self.num_teams, self.expanded_num_teams)
        return self


class MatchCreate(_MatchBase):
    starts_at: datetime
    ends_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=600)
    confirmation_deadline: datetime | None = None  # padrão: 2h antes do início
    cancellation_deadline: datetime | None = None
    waitlist_auto_promote: bool | None = None  # padrão: configuração do grupo
    publish: bool = True

    @model_validator(mode="after")
    def _check(self) -> "MatchCreate":
        if self.ends_at is None and self.duration_minutes is None:
            raise ValueError("Informe ends_at ou duration_minutes")
        return self


class MatchUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    modality: Modality | None = None
    location: str | None = Field(default=None, min_length=2, max_length=160)
    address: str | None = Field(default=None, max_length=300)
    court_id: int | None = Field(default=None, description="null remove a reserva da quadra")
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    price: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    split_cost: bool | None = None
    field_cost: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    slots: int | None = Field(default=None, ge=2, le=200)
    field_players_per_team: int | None = Field(default=None, ge=1, le=20)
    goalkeepers_per_team: int | None = Field(default=None, ge=0, le=2)
    referee_slots: int | None = Field(default=None, ge=0, le=2)
    num_teams: int | None = Field(default=None, ge=2, le=8)
    waitlist_limit: int | None = Field(default=None, ge=1, le=100)
    expand_when_waitlist_full: bool | None = None
    expanded_num_teams: int | None = Field(default=None, ge=2, le=16)
    confirmation_deadline: datetime | None = None
    cancellation_deadline: datetime | None = None
    evaluation_window_hours: EvaluationWindow | None = None
    waitlist_auto_promote: bool | None = None
    responsible_admin_ids: list[int] | None = None


class MatchCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class AttendanceCounts(BaseModel):
    pending: int = 0
    confirmed: int = 0
    declined: int = 0
    waitlist: int = 0
    cancelled: int = 0
    present: int = 0
    absent: int = 0
    available_slots: int = 0
    referees: int = 0  # juízes confirmados (não ocupam vaga de jogador)
    hired: int = 0  # goleiros/juízes contratados confirmados


class MatchOut(ORMModel):
    id: int
    group_id: int
    series_id: int | None
    name: str
    description: str | None
    modality: Modality
    location: str
    address: str | None
    court_id: int | None = None
    # horário na agenda do espaço: pending = aguardando o dono confirmar; rejected = recusado (sem quadra)
    booking_status: BookingStatus | None = None
    starts_at: datetime
    ends_at: datetime
    duration_minutes: int
    price: Decimal
    split_cost: bool = False
    field_cost: Decimal | None = None
    slots: int
    field_players_per_team: int
    goalkeepers_per_team: int
    referee_slots: int = 0
    num_teams: int | None = None
    waitlist_limit: int | None = None
    expand_when_waitlist_full: bool = False
    expanded_num_teams: int | None = None
    confirmation_deadline: datetime
    cancellation_deadline: datetime | None
    evaluation_window_hours: int
    waitlist_auto_promote: bool
    expanded_at: datetime | None = None  # quando a espera lotou e a pelada aumentou
    responsible_admin_ids: list[int]
    status: MatchStatus
    ended_at: datetime | None
    evaluation_status: EvaluationStatus
    evaluation_opens_at: datetime | None
    evaluation_closes_at: datetime | None
    published_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    counts: AttendanceCounts | None = None
    my_status: AttendanceStatus | None = None
    # soma dos goleiros/juízes contratados (aceitos): rateada entre os jogadores presentes na cobrança
    hire_cost: Decimal = Decimal("0")
    cost: "MatchCost | None" = None


class MatchCost(BaseModel):
    """Composição do custo da pelada e quanto cada jogador paga (estimado até registrar a presença)."""

    split: bool  # rateio do custo total; falso = valor fixo por jogador (+ rateio dos contratados)
    field: Decimal  # campo/quadra
    goalkeepers: Decimal  # goleiros contratados
    referees: Decimal  # juízes contratados
    total: Decimal | None  # campo + contratados (só no rateio)
    players: int  # entre quantos se divide (confirmados; depois do jogo, presentes)
    per_player: Decimal | None  # quanto cada um paga (nulo sem jogadores)


class SeriesCreate(_MatchBase):
    recurrence: Recurrence
    first_starts_at: datetime
    duration_minutes: int = Field(ge=15, le=600)
    confirmation_deadline_hours: int = Field(default=2, ge=0, le=24 * 7)
    cancellation_deadline_hours: int | None = Field(default=None, ge=0, le=24 * 7)
    generate_count: int = Field(default=4, ge=0, le=26)
    auto_generate_ahead: int = Field(default=1, ge=0, le=8,
                                     description="Ocorrências futuras mantidas pelo agendador (0 = só manual)")


class SeriesUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = None
    location: str | None = None
    address: str | None = None
    court_id: int | None = Field(default=None, description="null remove a quadra das próximas ocorrências")
    duration_minutes: int | None = Field(default=None, ge=15, le=600)
    price: Decimal | None = Field(default=None, ge=0)
    split_cost: bool | None = None
    field_cost: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    slots: int | None = Field(default=None, ge=2, le=200)
    field_players_per_team: int | None = Field(default=None, ge=1, le=20)
    goalkeepers_per_team: int | None = Field(default=None, ge=0, le=2)
    referee_slots: int | None = Field(default=None, ge=0, le=2)
    num_teams: int | None = Field(default=None, ge=2, le=8)
    waitlist_limit: int | None = Field(default=None, ge=1, le=100)
    expand_when_waitlist_full: bool | None = None
    expanded_num_teams: int | None = Field(default=None, ge=2, le=16)
    confirmation_deadline_hours: int | None = Field(default=None, ge=0)
    cancellation_deadline_hours: int | None = Field(default=None, ge=0)
    evaluation_window_hours: EvaluationWindow | None = None
    responsible_admin_ids: list[int] | None = None
    is_active: bool | None = None
    auto_generate_ahead: int | None = Field(default=None, ge=0, le=8)


class SeriesOut(ORMModel):
    id: int
    group_id: int
    recurrence: Recurrence
    name: str
    description: str | None
    modality: Modality
    location: str
    address: str | None
    court_id: int | None = None
    first_starts_at: datetime
    duration_minutes: int
    price: Decimal
    split_cost: bool = False
    field_cost: Decimal | None = None
    slots: int
    field_players_per_team: int
    goalkeepers_per_team: int
    referee_slots: int = 0
    num_teams: int | None = None
    waitlist_limit: int | None = None
    expand_when_waitlist_full: bool = False
    expanded_num_teams: int | None = None
    confirmation_deadline_hours: int
    cancellation_deadline_hours: int | None
    evaluation_window_hours: int
    responsible_admin_ids: list[int]
    is_active: bool
    auto_generate_ahead: int = 1
    last_generated_starts_at: datetime | None


class MakeRecurringIn(BaseModel):
    """Transforma uma pelada avulsa em recorrente: a série copia os dados dela."""

    recurrence: Recurrence
    keep_ahead: int = Field(default=2, ge=1, le=8, description="Ocorrências futuras que o app mantém agendadas")


class DuplicateIn(BaseModel):
    """Cópia da pelada (sem jogadores) para outra data; já pode virar recorrente."""

    starts_at: datetime
    publish: bool = True
    recurrence: Recurrence | None = None
    keep_ahead: int = Field(default=2, ge=1, le=8)


class GenerateIn(BaseModel):
    count: int = Field(default=4, ge=1, le=26)


# ------------------------------------------------------------------ participação


class MatchPlayerOut(BaseModel):
    member_id: int
    user_id: int
    name: str
    photo_url: str | None
    status: AttendanceStatus
    is_goalkeeper: bool
    is_referee: bool = False
    hired: bool = False  # goleiro/juiz contratado pelo comércio
    # convidado de um membro (sem conta própria): quem trouxe, que responde pela cobrança
    invited_by_member_id: int | None = None
    invited_by_name: str | None = None
    confirmed_at: datetime | None
    waitlist_position: int | None
    promotion_offered_at: datetime | None
    late_cancellation: bool
    blocked: bool
    blocked_reason: str | None = None
    has_pending_debt: bool | None = None  # apenas para administradores


class DebtOut(BaseModel):
    payment_id: int
    match_id: int
    match_name: str
    match_date: datetime
    amount_due: Decimal
    amount_paid: Decimal
    outstanding: Decimal
    status: str
    guest_name: str | None = None  # cobrança de um convidado seu


class ConfirmIn(BaseModel):
    as_goalkeeper: bool = False
    as_referee: bool = False  # vai apitar (usa uma vaga de juiz, não de jogador)

    @model_validator(mode="after")
    def _one_role(self) -> "ConfirmIn":
        if self.as_goalkeeper and self.as_referee:
            raise ValueError("Escolha goleiro ou juiz, não os dois")
        return self


class ConfirmOut(BaseModel):
    participation: MatchPlayerOut
    warnings: list[str]
    pending_debts: list[DebtOut]


class AdminParticipationIn(BaseModel):
    status: AttendanceStatus | None = None
    is_goalkeeper: bool | None = None
    is_referee: bool | None = None
    blocked: bool | None = None
    blocked_reason: str | None = Field(default=None, max_length=300)
    reason: str | None = Field(default=None, max_length=300)


class PresenceIn(BaseModel):
    present: list[int] = Field(default_factory=list)
    absent: list[int] = Field(default_factory=list)
    goalkeepers: list[int] | None = None  # quem atuou no gol (opcional)
    ended_at: datetime | None = None  # término real; padrão: término previsto

    @model_validator(mode="after")
    def _disjoint(self) -> "PresenceIn":
        if set(self.present) & set(self.absent):
            raise ValueError("Um jogador não pode estar presente e ausente ao mesmo tempo")
        return self


class PresenceOut(BaseModel):
    match: MatchOut
    payments_created: int
    warnings: list[str]


MatchOut.model_rebuild()
