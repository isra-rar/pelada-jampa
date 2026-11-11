from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow
from app.domain.enums import (
    AttendanceStatus,
    BookingStatus,
    EvaluationStatus,
    MatchStatus,
    Modality,
    Recurrence,
)
from app.models._types import str_enum


class MatchSeries(Base):
    """Configuração padrão de uma pelada recorrente. Cada ocorrência copia os valores,
    portanto alterações aqui não afetam partidas já geradas."""

    __tablename__ = "match_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("football_groups.id"), index=True)
    recurrence: Mapped[Recurrence] = mapped_column(str_enum(Recurrence))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    modality: Mapped[Modality] = mapped_column(str_enum(Modality))
    location: Mapped[str] = mapped_column(String(160))
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # quadra de um espaço vinculado ao grupo (opcional): reserva o horário na agenda do espaço
    court_id: Mapped[int | None] = mapped_column(
        ForeignKey("courts.id", name="fk_match_series_court_id"), nullable=True
    )
    first_starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    # cobrança por rateio: o custo total (campo + goleiros/juízes contratados) é dividido entre quem jogou
    split_cost: Mapped[bool] = mapped_column(Boolean, default=False)
    field_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)  # campo/quadra (total)
    slots: Mapped[int] = mapped_column(Integer)
    field_players_per_team: Mapped[int] = mapped_column(Integer)
    goalkeepers_per_team: Mapped[int] = mapped_column(Integer, default=1)
    referee_slots: Mapped[int] = mapped_column(Integer, default=0)  # vagas de juiz (fora das vagas de jogador)
    # times previstos (padrão do sorteio) e lista de espera limitada que pode aumentar a pelada uma vez:
    # quando a espera lota, as vagas crescem do tamanho da espera e os times passam a `expanded_num_teams`
    num_teams: Mapped[int | None] = mapped_column(Integer, nullable=True)
    waitlist_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)  # nulo = espera sem limite
    expand_when_waitlist_full: Mapped[bool] = mapped_column(Boolean, default=False)
    expanded_num_teams: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmation_deadline_hours: Mapped[int] = mapped_column(Integer, default=2)  # antes do início
    cancellation_deadline_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluation_window_hours: Mapped[int] = mapped_column(Integer, default=24)
    responsible_admin_ids: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # quantas ocorrências futuras o agendador mantém criadas (0 = só geração manual)
    auto_generate_ahead: Mapped[int] = mapped_column(Integer, default=1)
    last_generated_starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Match(Base):
    """Ocorrência de uma pelada."""

    __tablename__ = "matches"
    __table_args__ = (Index("ix_matches_group_start", "group_id", "starts_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("football_groups.id"), index=True)
    series_id: Mapped[int | None] = mapped_column(ForeignKey("match_series.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    modality: Mapped[Modality] = mapped_column(str_enum(Modality))
    location: Mapped[str] = mapped_column(String(160))
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    court_id: Mapped[int | None] = mapped_column(
        ForeignKey("courts.id", name="fk_matches_court_id"), nullable=True, index=True
    )
    # horário na agenda do espaço: grupo sem vínculo marca como pendente e o DE confirma ou recusa.
    # Nulo = pelada sem quadra de espaço
    booking_status: Mapped[BookingStatus | None] = mapped_column(
        str_enum(BookingStatus, length=12), nullable=True
    )
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    # cobrança por rateio: o custo total (campo + goleiros/juízes contratados) é dividido entre quem jogou
    split_cost: Mapped[bool] = mapped_column(Boolean, default=False)
    field_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)  # campo/quadra (total)
    slots: Mapped[int] = mapped_column(Integer)
    field_players_per_team: Mapped[int] = mapped_column(Integer)
    goalkeepers_per_team: Mapped[int] = mapped_column(Integer, default=1)
    referee_slots: Mapped[int] = mapped_column(Integer, default=0)  # vagas de juiz (fora das vagas de jogador)
    # times previstos (padrão do sorteio) e lista de espera limitada que pode aumentar a pelada uma vez:
    # quando a espera lota, as vagas crescem do tamanho da espera e os times passam a `expanded_num_teams`
    num_teams: Mapped[int | None] = mapped_column(Integer, nullable=True)
    waitlist_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)  # nulo = espera sem limite
    expand_when_waitlist_full: Mapped[bool] = mapped_column(Boolean, default=False)
    expanded_num_teams: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmation_deadline: Mapped[datetime] = mapped_column(UTCDateTime)
    cancellation_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    evaluation_window_hours: Mapped[int] = mapped_column(Integer, default=24)
    waitlist_auto_promote: Mapped[bool] = mapped_column(Boolean, default=True)
    expanded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # quando a pelada aumentou
    responsible_admin_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[MatchStatus] = mapped_column(str_enum(MatchStatus), default=MatchStatus.DRAFT)

    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # término registrado
    evaluation_status: Mapped[EvaluationStatus] = mapped_column(
        str_enum(EvaluationStatus), default=EvaluationStatus.NOT_OPEN
    )
    evaluation_opens_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    evaluation_closes_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    evaluation_closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    evaluation_reminder_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    confirmation_reminder_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    @property
    def duration_minutes(self) -> int:
        return int((self.ends_at - self.starts_at).total_seconds() // 60)


class MatchPlayer(Base):
    """Participação e status do jogador em uma ocorrência."""

    __tablename__ = "match_players"
    __table_args__ = (UniqueConstraint("match_id", "member_id", name="uq_match_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("group_members.id"), index=True)
    status: Mapped[AttendanceStatus] = mapped_column(str_enum(AttendanceStatus), default=AttendanceStatus.PENDING)
    is_goalkeeper: Mapped[bool] = mapped_column(Boolean, default=False)
    # juiz: confirmado na pelada, mas fora das vagas de jogador, do sorteio, da cobrança e das avaliações
    is_referee: Mapped[bool] = mapped_column(Boolean, default=False)
    # goleiro/juiz contratado pelo comércio (não paga a pelada; o valor dele é rateado entre os jogadores)
    hire_id: Mapped[int | None] = mapped_column(
        ForeignKey("match_hires.id", name="fk_match_players_hire_id"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    waitlist_joined_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # ordem estrita da fila (desempata entradas no mesmo instante)
    waitlist_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    promotion_offered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    late_cancellation: Mapped[bool] = mapped_column(Boolean, default=False)
    was_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)  # distingue confirmação de presença
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)  # decisão do admin (ex.: inadimplência)
    blocked_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    presence_registered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    member = relationship("GroupMember", lazy="joined")
