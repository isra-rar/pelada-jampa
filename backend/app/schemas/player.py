from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.enums import Position, RatingKind
from app.schemas.common import ORMModel
from app.schemas.group import MemberSelfAssessment, SkillOut
from app.schemas.stats import PlayerGameStats


class RatingOut(BaseModel):
    kind: RatingKind
    attributes: dict[str, float]  # precisão total
    attributes_display: dict[str, float]  # uma casa decimal
    labels: dict[str, str]
    average: float
    average_display: float
    stars: int  # nível de 0 a 5 estrelas
    stars_label: str
    is_provisional: bool
    initial_confirmed: bool = False
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
    stars_before: int | None
    stars_after: int
    evaluations_count: int
    weight_applied: float
    created_at: datetime


class SelfAssessmentQuestion(BaseModel):
    attribute: str
    label: str
    question: str
    hint: str
    options: list[str]  # da mais fraca para a mais forte; a resposta é o índice


class SelfAssessmentOut(MemberSelfAssessment):
    pending: bool
    answers: dict[str, int] | None
    is_goalkeeper: bool  # posição atual no grupo (pré-seleciona "no gol")
    star_labels: dict[str, str]  # "5" → "Craque"
    questions: list[SelfAssessmentQuestion]  # quiz de linha
    # quiz de goleiro; None quando o grupo avalia goleiros com os atributos de linha
    goalkeeper_questions: list[SelfAssessmentQuestion] | None = None


class SelfAssessmentIn(BaseModel):
    kind: RatingKind = RatingKind.FIELD  # goalkeeper = respondeu as perguntas de goleiro
    answers: dict[str, int]  # atributo → índice da opção escolhida (0 a 4)
    declared_stars: int = Field(ge=0, le=5)  # nível que o jogador diz ter (contraponto ao quiz)


class SelfAssessmentReminderOut(BaseModel):
    reminded: int  # quantos jogadores foram avisados


class SelfAssessmentResult(BaseModel):
    applied: bool  # False quando o perfil já tinha avaliações ou nota definida pelo administrador
    rating: RatingOut
    self_assessment: MemberSelfAssessment


class LevelReviewResult(BaseModel):
    applied: bool  # False quando o perfil já foi consolidado pelas avaliações (nível não muda)
    rating: RatingOut
    self_assessment: MemberSelfAssessment


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
    stars: int
    average: float
    average_display: float
    is_provisional: bool
    goalkeeper_stars: int | None = None
    goalkeeper_average_display: float | None = None


class AttendanceStats(BaseModel):
    matches_played: int
    present: int
    absent: int
    confirmations: int
    late_cancellations: int
    no_shows: int = 0  # confirmou e faltou


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
    stars: dict[str, str]


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
    game_stats: PlayerGameStats | None = None
    pending_skill: SkillOut | None = None
