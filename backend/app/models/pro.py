"""Comércio de goleiros e juízes: anúncio do profissional, agenda e contratações para peladas."""

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Index, Integer, Numeric, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow
from app.domain.enums import HireRole, HireStatus
from app.models._types import str_enum


class ProProfile(Base):
    """Anúncio de quem joga no gol e/ou apita peladas por um valor. Um por usuário."""

    __tablename__ = "pro_profiles"
    __table_args__ = (UniqueConstraint("user_id", name="uq_pro_profiles_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    is_goalkeeper: Mapped[bool] = mapped_column(Boolean, default=False)
    is_referee: Mapped[bool] = mapped_column(Boolean, default=False)
    # valor por pelada, até a duração máxima informada
    goalkeeper_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    referee_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    max_duration_minutes: Mapped[int] = mapped_column(Integer, default=90)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    neighborhood: Mapped[str | None] = mapped_column(String(80), nullable=True)
    modalities: Mapped[list] = mapped_column(JSON, default=list)  # vazio = todas
    # contato mostrado só a quem contratou (pedido aceito)
    contact_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # aparece na busca
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", lazy="joined")
    availability = relationship(
        "ProAvailability", cascade="all, delete-orphan", lazy="selectin",
        order_by="(ProAvailability.date, ProAvailability.weekday, ProAvailability.start_time)",
    )


class ProAvailability(Base):
    """Faixa de horário disponível (horário de Brasília): semanal (`weekday`, 0 = segunda) ou numa data."""

    __tablename__ = "pro_availability"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("pro_profiles.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date: Mapped[date | None] = mapped_column(Date, nullable=True)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


class MatchHire(Base):
    """Pedido de contratação de um goleiro/juiz para uma pelada. Aceito, o profissional entra na pelada."""

    __tablename__ = "match_hires"
    __table_args__ = (Index("ix_match_hires_pro_status", "pro_profile_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("football_groups.id"), index=True)
    pro_profile_id: Mapped[int] = mapped_column(ForeignKey("pro_profiles.id"))
    pro_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[HireRole] = mapped_column(str_enum(HireRole, 12))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))  # valor combinado por pelada
    status: Mapped[HireStatus] = mapped_column(str_enum(HireStatus, 12), default=HireStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(300), nullable=True)  # do organizador
    response_note: Mapped[str | None] = mapped_column(String(300), nullable=True)  # do profissional
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    match = relationship("Match", lazy="joined")
    profile = relationship("ProProfile", lazy="joined")
