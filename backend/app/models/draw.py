from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow
from app.domain.enums import DrawStatus
from app.models._types import str_enum


class Draw(Base):
    """Execução do sorteio: parâmetros, pontuação e resultado (RN16)."""

    __tablename__ = "draws"
    __table_args__ = (UniqueConstraint("match_id", "sequence", name="uq_draw_match_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[DrawStatus] = mapped_column(str_enum(DrawStatus), default=DrawStatus.DRAFT)
    params: Mapped[dict] = mapped_column(JSON)
    score: Mapped[float] = mapped_column(Float)
    metrics: Mapped[dict] = mapped_column(JSON)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    reserves: Mapped[list] = mapped_column(JSON, default=list)
    signature: Mapped[str] = mapped_column(Text)
    seed: Mapped[int] = mapped_column(Integer)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    manually_adjusted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    teams = relationship("Team", order_by="Team.index", cascade="all, delete-orphan", lazy="selectin")


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("draw_id", "index", name="uq_team_draw_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draw_id: Mapped[int] = mapped_column(ForeignKey("draws.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(60))
    stats: Mapped[dict] = mapped_column(JSON)

    players = relationship("TeamPlayer", cascade="all, delete-orphan", lazy="selectin")


class TeamPlayer(Base):
    __tablename__ = "team_players"
    # um jogador não pode ocupar duas equipes na mesma execução
    __table_args__ = (UniqueConstraint("draw_id", "member_id", name="uq_team_player_draw_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    draw_id: Mapped[int] = mapped_column(ForeignKey("draws.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("group_members.id"))
    is_goalkeeper: Mapped[bool] = mapped_column(Boolean, default=False)

    member = relationship("GroupMember", lazy="joined")
