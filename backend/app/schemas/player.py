from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.domain.enums import Position, RatingKind
from app.schemas.common import ORMModel
from app.schemas.group import SkillOut


class RatingOut(BaseModel):
    kind: RatingKind
    attributes: dict[str, float]  # precisão total
    attributes_display: dict[str, float]  # uma casa decimal
    labels: dict[str, str]
    average: float
    average_display: float
    tier: str
    tier_label: str
    is_provisional: bool
    valid_evaluations_count: int
    matches_evaluated_count: int
    updated_at: datetime


class RatingHistoryOut(ORMModel):
    id: int
    kind: RatingKind
    match_id: int | None
    reason: str
    attributes_before: dict | None
    attributes_after: dict
    match_scores: dict | None
    average_before: float | None
    average_after: float
    tier_before: str | None
    tier_after: str
    evaluations_count: int
    weight_applied: float
    created_at: datetime


class ProvisionalRatingIn(BaseModel):
    kind: RatingKind = RatingKind.FIELD
    attributes: dict[str, float]
    reason: str | None = None


class PlayerSummary(BaseModel):
    member_id: int
    user_id: int
    name: str
    photo_url: str | None
    preferred_position: Position
    special_skill: SkillOut | None
    tier: str
    average: float
    average_display: float
    is_provisional: bool
    goalkeeper_tier: str | None = None
    goalkeeper_average_display: float | None = None


class AttendanceStats(BaseModel):
    matches_played: int
    present: int
    absent: int
    confirmations: int
    late_cancellations: int


class FinanceSituation(BaseModel):
    pending_count: int
    pending_amount: Decimal
    paid_amount: Decimal


class RatingExplanation(BaseModel):
    formula: str
    match_weight: float
    provisional_match_weight: float
    min_evaluators_full_weight: int
    min_evaluations_to_consolidate: int
    tiers: dict[str, str]


class PlayerProfile(BaseModel):
    member_id: int
    user_id: int
    name: str
    nickname: str | None
    photo_url: str | None
    preferred_position: Position
    special_skill: SkillOut | None
    ratings: list[RatingOut]
    attendance: AttendanceStats
    finance: FinanceSituation | None  # apenas para o próprio jogador e administradores
    rating_explanation: RatingExplanation
