from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.domain.enums import DrawStatus


class DrawCreate(BaseModel):
    num_teams: int = Field(default=2, ge=2, le=8)
    field_players_per_team: int | None = Field(default=None, ge=1, le=20)
    goalkeepers_per_team: int | None = Field(default=None, ge=0, le=2)
    eligible_member_ids: list[int] | None = None  # padrão: confirmados (ou presentes)
    unavailable_member_ids: list[int] = Field(default_factory=list)
    weights: dict[str, float] | None = None
    seed: int | None = Field(default=None, ge=0, lt=2**31)
    avoid_previous: bool = True

    @field_validator("weights")
    @classmethod
    def _weights(cls, v):
        if v is None:
            return v
        allowed = {"average", "tier", "attributes", "skills"}
        if set(v) - allowed:
            raise ValueError(f"Pesos permitidos: {sorted(allowed)}")
        if any(w < 0 for w in v.values()):
            raise ValueError("Pesos não podem ser negativos")
        return v


class TeamPlayerOut(BaseModel):
    member_id: int
    name: str
    photo_url: str | None
    is_goalkeeper: bool
    tier: str
    average_display: float
    special_skill: str | None
    is_provisional: bool


class TeamOut(BaseModel):
    index: int
    name: str
    stats: dict
    players: list[TeamPlayerOut]


class DrawOut(BaseModel):
    id: int
    match_id: int
    sequence: int
    status: DrawStatus
    params: dict
    score: float
    metrics: dict
    warnings: list[str]
    reserves: list[int]
    seed: int
    fallback_used: bool
    manually_adjusted: bool
    created_at: datetime
    published_at: datetime | None
    teams: list[TeamOut]


class DrawSummaryOut(BaseModel):
    id: int
    sequence: int
    status: DrawStatus
    score: float
    average_difference: float | None
    manually_adjusted: bool
    created_at: datetime
    published_at: datetime | None


class PlayerMove(BaseModel):
    member_id: int
    to_team_index: int = Field(ge=0)


class DrawAdjustIn(BaseModel):
    moves: list[PlayerMove] = Field(min_length=1, max_length=40)
    reason: str | None = Field(default=None, max_length=300)


class DrawCompareOut(BaseModel):
    base: DrawSummaryOut
    other: DrawSummaryOut
    score_delta: float
    average_difference_delta: float | None
    players_changed_team: int
    same_composition: bool


class CalibrationGameOut(BaseModel):
    match_id: int
    match_date: date
    home_name: str
    away_name: str
    home_score: int
    away_score: int
    average_difference: float


class DrawCalibrationOut(BaseModel):
    """O sorteio gera jogos parelhos? Medição com os placares reais e sugestão de pesos (v2.0)."""

    since: date
    until: date
    games: int
    close_games: int
    close_game_rate: float | None  # jogos decididos por até 1 gol
    draw_rate: float | None
    blowout_rate: float | None  # 3 gols ou mais de diferença
    avg_goal_difference: float | None
    favorite_games: int
    favorite_win_rate: float | None  # o time de maior média venceu
    avg_components: dict[str, float | None]
    correlations: dict[str, float | None]
    current_weights: dict[str, float]
    suggested_weights: dict[str, float] | None
    blend: float
    min_games: int
    status: str  # sem_dados | poucos_dados | bom | regular | ruim
    message: str
    recent_games: list[CalibrationGameOut]
