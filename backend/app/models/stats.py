from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow
from app.domain.enums import GameStatus
from app.models._types import str_enum


class MatchGame(Base):
    """Jogo (rodada) disputado dentro de uma pelada, com placar e escalações congeladas."""

    __tablename__ = "match_games"
    __table_args__ = (UniqueConstraint("match_id", "sequence", name="uq_game_match_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    home_name: Mapped[str] = mapped_column(String(60))
    away_name: Mapped[str] = mapped_column(String(60))
    home_team_index: Mapped[int | None] = mapped_column(Integer, nullable=True)  # índice no sorteio publicado
    away_team_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_member_ids: Mapped[list] = mapped_column(JSON)
    away_member_ids: Mapped[list] = mapped_column(JSON)
    home_score: Mapped[int] = mapped_column(Integer, default=0)
    away_score: Mapped[int] = mapped_column(Integer, default=0)
    # jogo ao vivo: cronômetro regressivo a partir de started_at; jogos lançados depois já nascem encerrados
    status: Mapped[GameStatus] = mapped_column(str_enum(GameStatus, length=10), default=GameStatus.FINISHED)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    goals = relationship("MatchGoal", cascade="all, delete-orphan", lazy="selectin", order_by="MatchGoal.id")


class MatchGoal(Base):
    """Gol de um jogo. `side` é o lado beneficiado; em gol contra o autor é do lado adversário."""

    __tablename__ = "match_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("match_games.id", ondelete="CASCADE"), index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    side: Mapped[str] = mapped_column(String(4))  # home | away
    scorer_member_id: Mapped[int | None] = mapped_column(ForeignKey("group_members.id"), nullable=True, index=True)
    assist_member_id: Mapped[int | None] = mapped_column(ForeignKey("group_members.id"), nullable=True, index=True)
    own_goal: Mapped[bool] = mapped_column(Boolean, default=False)
    minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
