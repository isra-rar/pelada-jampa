from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UTCDateTime, utcnow
from app.domain.enums import EvaluationDecision, RatingKind
from app.models._types import str_enum


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        UniqueConstraint("match_id", "evaluator_member_id", "evaluated_member_id", name="uq_evaluation_once"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    evaluator_member_id: Mapped[int] = mapped_column(ForeignKey("group_members.id"), index=True)
    evaluated_member_id: Mapped[int] = mapped_column(ForeignKey("group_members.id"), index=True)
    kind: Mapped[RatingKind] = mapped_column(str_enum(RatingKind))
    scores: Mapped[dict] = mapped_column(JSON)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    admin_decision: Mapped[EvaluationDecision | None] = mapped_column(str_enum(EvaluationDecision), nullable=True)
    counted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # definido no encerramento
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
