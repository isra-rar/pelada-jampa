"""Espaços esportivos (quadras/campos) que hospedam peladas: agenda, vínculo com os grupos e aluguel."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow
from app.domain.enums import Modality, RentStatus, VenueLinkOrigin, VenueLinkStatus, VenueRole
from app.models._types import str_enum


def default_opening_hours() -> dict:
    """Segunda (0) a domingo (6), horário local: [abre, fecha]. Dia ausente = fechado."""
    return {str(d): ["08:00", "23:00"] for d in range(7)}


class Venue(Base):
    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # código que o administrador de um grupo informa para pedir vínculo com o espaço
    link_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    opening_hours: Mapped[dict] = mapped_column(JSON, default=default_opening_hours)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class VenueStaff(Base):
    __tablename__ = "venue_staff"
    __table_args__ = (UniqueConstraint("venue_id", "user_id", name="uq_venue_staff_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[VenueRole] = mapped_column(str_enum(VenueRole), default=VenueRole.MANAGER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    user = relationship("User", lazy="joined")


class Court(Base):
    """Quadra ou campo do espaço. O preço por hora é a base do aluguel."""

    __tablename__ = "courts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    modality: Mapped[Modality] = mapped_column(str_enum(Modality), default=Modality.SOCIETY)
    hourly_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CourtBlock(Base):
    """Horário bloqueado na agenda (manutenção, evento, reserva avulsa)."""

    __tablename__ = "court_blocks"
    __table_args__ = (Index("ix_court_blocks_court_start", "court_id", "starts_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    court_id: Mapped[int] = mapped_column(ForeignKey("courts.id"))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    reason: Mapped[str] = mapped_column(String(200))
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class VenueGroup(Base):
    """Vínculo entre um espaço e um grupo de pelada. Um grupo tem no máximo um vínculo pendente ou ativo."""

    __tablename__ = "venue_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("football_groups.id"), index=True)
    status: Mapped[VenueLinkStatus] = mapped_column(str_enum(VenueLinkStatus), default=VenueLinkStatus.PENDING)
    origin: Mapped[VenueLinkOrigin] = mapped_column(str_enum(VenueLinkOrigin), default=VenueLinkOrigin.REQUEST)
    # preço por hora combinado com o grupo (ex.: mensalista); vazio = preço da quadra
    rent_hourly_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class RentCharge(Base):
    """Aluguel devido pelo grupo ao espaço por uma pelada realizada (controle manual, sem processar pagamento)."""

    __tablename__ = "rent_charges"
    __table_args__ = (UniqueConstraint("match_id", name="uq_rent_charge_match"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    court_id: Mapped[int] = mapped_column(ForeignKey("courts.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("football_groups.id"), index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[RentStatus] = mapped_column(str_enum(RentStatus), default=RentStatus.PENDING)
    reported_paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # "já paguei" do grupo
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(300), nullable=True)
    confirmed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
