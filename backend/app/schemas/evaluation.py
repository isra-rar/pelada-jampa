from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import EvaluationDecision, EvaluationStatus, RatingKind


class EvaluationTarget(BaseModel):
    member_id: int
    name: str
    photo_url: str | None
    kind: RatingKind
    attributes: list[str]
    labels: dict[str, str]
    already_evaluated: bool


class EvaluationTargetsOut(BaseModel):
    match_id: int
    evaluation_status: EvaluationStatus
    closes_at: datetime | None
    seconds_remaining: int | None
    targets: list[EvaluationTarget]
    done: int
    total: int


class EvaluationItem(BaseModel):
    evaluated_member_id: int
    scores: dict[str, int] = Field(description="Notas inteiras de 0 a 5 por atributo")


class EvaluationSubmit(BaseModel):
    items: list[EvaluationItem] = Field(min_length=1, max_length=60)


class EvaluationSubmitOut(BaseModel):
    created: int
    done: int
    total: int


class EvaluatorProgress(BaseModel):
    member_id: int
    name: str
    done: int
    total: int
    completed: bool


class EvaluationProgressOut(BaseModel):
    match_id: int
    evaluation_status: EvaluationStatus
    opens_at: datetime | None
    closes_at: datetime | None
    present_players: int
    expected: int
    submitted: int
    completion_rate: float
    flagged: int
    evaluators: list[EvaluatorProgress]


class FlaggedEvaluationOut(BaseModel):
    """Avaliação sinalizada. A identidade do avaliador não é exposta."""

    id: int
    evaluated_member_id: int
    evaluated_name: str
    kind: RatingKind
    scores: dict[str, float]
    peer_median: dict[str, float]
    flag_reason: str | None
    admin_decision: EvaluationDecision | None
    counted: bool | None
    created_at: datetime


class EvaluationDecisionIn(BaseModel):
    decision: EvaluationDecision
    reason: str = Field(min_length=3, max_length=300)


class ReceivedEvaluationsOut(BaseModel):
    """Resumo anônimo das avaliações recebidas por um jogador em uma partida."""

    match_id: int
    kind: RatingKind
    count: int
    counted: int
    average_scores: dict[str, float] | None
