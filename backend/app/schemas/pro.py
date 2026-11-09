"""Comércio de goleiros e juízes: anúncio, agenda, busca e contratação para peladas."""

import datetime as dt
from datetime import datetime, time
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.enums import HireRole, HireStatus, Modality

_PRICE = {"ge": 0, "le": 10000, "max_digits": 10, "decimal_places": 2}


class AvailabilitySlot(BaseModel):
    """Faixa de horário (Brasília). Semanal: `weekday` (0 = segunda ... 6 = domingo). Avulsa: `date`."""

    weekday: int | None = Field(default=None, ge=0, le=6)
    date: dt.date | None = None
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def _check(self) -> "AvailabilitySlot":
        if (self.weekday is None) == (self.date is None):
            raise ValueError("Informe o dia da semana ou a data (um dos dois)")
        if self.end_time <= self.start_time:
            raise ValueError("O horário final deve ser depois do inicial")
        return self


class AvailabilityOut(AvailabilitySlot):
    id: int


class ProProfileIn(BaseModel):
    is_goalkeeper: bool = False
    is_referee: bool = False
    goalkeeper_price: Decimal | None = Field(default=None, **_PRICE)
    referee_price: Decimal | None = Field(default=None, **_PRICE)
    max_duration_minutes: int = Field(default=90, ge=30, le=300)
    description: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=80)
    neighborhood: str | None = Field(default=None, max_length=80)
    modalities: list[Modality] = Field(default_factory=list)
    contact_phone: str | None = Field(default=None, max_length=30)
    is_active: bool = True
    # None mantém a agenda atual; lista substitui tudo
    availability: list[AvailabilitySlot] | None = Field(default=None, max_length=80)

    @field_validator("city", "neighborhood", "description", "contact_phone")
    @classmethod
    def _strip(cls, v: str | None):
        if v is None:
            return v
        v = v.strip()
        return v or None

    @model_validator(mode="after")
    def _roles(self) -> "ProProfileIn":
        if not (self.is_goalkeeper or self.is_referee):
            raise ValueError("Marque se você joga no gol, apita ou os dois")
        if self.is_goalkeeper and self.goalkeeper_price is None:
            raise ValueError("Informe o valor por pelada como goleiro")
        if self.is_referee and self.referee_price is None:
            raise ValueError("Informe o valor por pelada como juiz")
        return self


class ProCardOut(BaseModel):
    """Anúncio público (sem contato)."""

    id: int
    name: str
    photo_url: str | None
    is_goalkeeper: bool
    is_referee: bool
    goalkeeper_price: Decimal | None
    referee_price: Decimal | None
    max_duration_minutes: int
    description: str | None
    city: str | None
    neighborhood: str | None
    modalities: list[Modality]
    jobs_done: int  # peladas realizadas pelo app
    availability: list[AvailabilityOut]  # semanais e datas de hoje em diante
    available: bool | None = None  # disponível na data/horário buscados (nulo sem filtro de data)


class ProProfileOut(ProCardOut):
    """O próprio anúncio (com contato e situação)."""

    contact_phone: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class HireCreate(BaseModel):
    pro_id: int
    role: HireRole
    price: Decimal | None = Field(default=None, **_PRICE)  # padrão: valor do anúncio
    message: str | None = Field(default=None, max_length=300)


class HireDecision(BaseModel):
    note: str | None = Field(default=None, max_length=300)


class HirePerson(BaseModel):
    name: str
    photo_url: str | None = None
    phone: str | None = None  # só depois do pedido aceito


class HireOut(BaseModel):
    id: int
    match_id: int
    group_id: int
    group_name: str
    match_name: str
    starts_at: datetime
    ends_at: datetime
    location: str
    address: str | None
    modality: Modality
    role: HireRole
    price: Decimal
    status: HireStatus
    message: str | None
    response_note: str | None
    created_at: datetime
    responded_at: datetime | None
    pro_id: int
    pro: HirePerson
    organizer: HirePerson
    outside_availability: bool  # fora da agenda informada pelo profissional
    over_max_duration: bool  # pelada mais longa que o máximo do anúncio


class BookableMatchOut(BaseModel):
    """Pelada futura de um grupo que o usuário administra, para contratar o profissional."""

    match_id: int
    group_id: int
    group_name: str
    name: str
    starts_at: datetime
    ends_at: datetime
    location: str
    roles: list[HireRole]  # funções possíveis (goleiro só em pelada com goleiro)
    available: bool
    over_max_duration: bool
    already_requested: bool
