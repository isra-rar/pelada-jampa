from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.enums import PaymentStatus


class PaymentOut(BaseModel):
    id: int
    group_id: int
    match_id: int
    match_name: str
    match_date: datetime
    member_id: int
    player_name: str
    amount_due: Decimal
    amount_paid: Decimal
    outstanding: Decimal
    status: PaymentStatus
    due_date: date | None
    paid_at: datetime | None
    method: str | None
    confirmed_by_id: int | None
    notes: str | None
    reported_paid_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PaymentUpdate(BaseModel):
    """Os valores são absolutos (não incrementais): reenviar a mesma requisição não duplica pagamento."""

    status: PaymentStatus | None = None
    amount_paid: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    amount_due: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    method: str | None = Field(default=None, max_length=40)
    paid_at: datetime | None = None
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=300)


class ChargeCreate(BaseModel):
    match_id: int
    member_id: int
    amount_due: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=500)


class SettleIn(BaseModel):
    payment_ids: list[int] = Field(min_length=1, max_length=200)
    method: str | None = Field(default=None, max_length=40)
    paid_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=500)


class FinanceSummary(BaseModel):
    group_id: int
    match_id: int | None
    expected: Decimal
    received: Decimal
    pending: Decimal
    exempt_count: int
    refunded_count: int
    pending_count: int
    paid_count: int
    debtors_count: int


class DebtorOut(BaseModel):
    member_id: int
    name: str
    pending_count: int
    outstanding: Decimal
    oldest_match_date: datetime
