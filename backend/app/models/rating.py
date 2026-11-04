from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UTCDateTime, utcnow
from app.domain.enums import RatingKind
from app.models._types import str_enum


class PlayerRating(Base):
    """Notas atuais (atributos, média, Tier) de um membro em um grupo."""

    __tablename__ = "player_ratings"
    __table_args__ = (UniqueConstraint("member_id", "kind", name="uq_rating_member_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("group_members.id"), index=True)
    kind: Mapped[RatingKind] = mapped_column(str_enum(RatingKind))
    attributes: Mapped[dict] = mapped_column(JSON)  # precisão total; arredondamento só na exibição
    average: Mapped[float] = mapped_column(Float)
    tier: Mapped[str] = mapped_column(String(1))
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=True)
    valid_evaluations_count: Mapped[int] = mapped_column(Integer, default=0)
    matches_evaluated_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PlayerRatingHistory(Base):
    __tablename__ = "player_rating_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rating_id: Mapped[int] = mapped_column(ForeignKey("player_ratings.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("group_members.id"), index=True)
    kind: Mapped[RatingKind] = mapped_column(str_enum(RatingKind))
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id"), nullable=True)
    reason: Mapped[str] = mapped_column(String(30))  # initial | match | admin_provisional
    attributes_before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attributes_after: Mapped[dict] = mapped_column(JSON)
    match_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    average_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_after: Mapped[float] = mapped_column(Float)
    tier_before: Mapped[str | None] = mapped_column(String(1), nullable=True)
    tier_after: Mapped[str] = mapped_column(String(1))
    evaluations_count: Mapped[int] = mapped_column(Integer, default=0)
    weight_applied: Mapped[float] = mapped_column(Float, default=0.0)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
