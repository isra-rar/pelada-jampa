from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow
from app.domain.enums import PaymentStatus
from app.models._types import str_enum


class Payment(Base):
    """Lançamento financeiro de uma participação. Um por jogador por ocorrência (RN08)."""

    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("match_id", "member_id", name="uq_payment_match_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("football_groups.id"), index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("group_members.id"), index=True)
    amount_due: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    status: Mapped[PaymentStatus] = mapped_column(str_enum(PaymentStatus), default=PaymentStatus.PENDING)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confirmed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reported_paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # jogador informou
    last_reminder_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # lembrete periódico
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    match = relationship("Match", lazy="joined")
    member = relationship("GroupMember", lazy="joined")
