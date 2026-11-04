from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.enums import AttendanceStatus, EvaluationStatus, MatchStatus, Modality, Recurrence
from app.schemas.common import ORMModel

EvaluationWindow = Literal[6, 24]


class _MatchBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    modality: Modality = Modality.SOCIETY
    location: str = Field(min_length=2, max_length=160)
    address: str | None = Field(default=None, max_length=300)
    price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    slots: int = Field(ge=2, le=200)
    field_players_per_team: int = Field(ge=1, le=20)
    goalkeepers_per_team: int = Field(default=1, ge=0, le=2)
    evaluation_window_hours: EvaluationWindow = 24
    responsible_admin_ids: list[int] = Field(default_factory=list)


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
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    price: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    slots: int | None = Field(default=None, ge=2, le=200)
    field_players_per_team: int | None = Field(default=None, ge=1, le=20)
    goalkeepers_per_team: int | None = Field(default=None, ge=0, le=2)
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


class MatchOut(ORMModel):
    id: int
    group_id: int
    series_id: int | None
    name: str
    description: str | None
    modality: Modality
    location: str
    address: str | None
    starts_at: datetime
    ends_at: datetime
    duration_minutes: int
    price: Decimal
    slots: int
    field_players_per_team: int
    goalkeepers_per_team: int
    confirmation_deadline: datetime
    cancellation_deadline: datetime | None
    evaluation_window_hours: int
    waitlist_auto_promote: bool
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


class SeriesCreate(_MatchBase):
    recurrence: Recurrence
    first_starts_at: datetime
    duration_minutes: int = Field(ge=15, le=600)
    confirmation_deadline_hours: int = Field(default=2, ge=0, le=24 * 7)
    cancellation_deadline_hours: int | None = Field(default=None, ge=0, le=24 * 7)
    generate_count: int = Field(default=4, ge=0, le=26)


class SeriesUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = None
    location: str | None = None
    address: str | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=600)
    price: Decimal | None = Field(default=None, ge=0)
    slots: int | None = Field(default=None, ge=2, le=200)
    field_players_per_team: int | None = Field(default=None, ge=1, le=20)
    goalkeepers_per_team: int | None = Field(default=None, ge=0, le=2)
    confirmation_deadline_hours: int | None = Field(default=None, ge=0)
    cancellation_deadline_hours: int | None = Field(default=None, ge=0)
    evaluation_window_hours: EvaluationWindow | None = None
    responsible_admin_ids: list[int] | None = None
    is_active: bool | None = None


class SeriesOut(ORMModel):
    id: int
    group_id: int
    recurrence: Recurrence
    name: str
    description: str | None
    modality: Modality
    location: str
    address: str | None
    first_starts_at: datetime
    duration_minutes: int
    price: Decimal
    slots: int
    field_players_per_team: int
    goalkeepers_per_team: int
    confirmation_deadline_hours: int
    cancellation_deadline_hours: int | None
    evaluation_window_hours: int
    responsible_admin_ids: list[int]
    is_active: bool
    last_generated_starts_at: datetime | None


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


class ConfirmIn(BaseModel):
    as_goalkeeper: bool = False


class ConfirmOut(BaseModel):
    participation: MatchPlayerOut
    warnings: list[str]
    pending_debts: list[DebtOut]


class AdminParticipationIn(BaseModel):
    status: AttendanceStatus | None = None
    is_goalkeeper: bool | None = None
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
