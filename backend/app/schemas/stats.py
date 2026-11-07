from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.enums import GameStatus

Side = Literal["home", "away"]
RankingSort = Literal["points", "goals", "assists", "win_rate", "matches", "wins"]


# ------------------------------------------------------------------ jogos e gols


class GoalIn(BaseModel):
    side: Side = Field(description="Lado beneficiado pelo gol")
    scorer_member_id: int | None = Field(default=None, description="Autor (nulo = não informado)")
    assist_member_id: int | None = None
    own_goal: bool = False
    minute: int | None = Field(default=None, ge=0, le=300)

    @model_validator(mode="after")
    def _check(self) -> "GoalIn":
        if self.own_goal and self.assist_member_id is not None:
            raise ValueError("Gol contra não tem assistência")
        if self.own_goal and self.scorer_member_id is None:
            raise ValueError("Informe o autor do gol contra")
        if self.scorer_member_id is not None and self.scorer_member_id == self.assist_member_id:
            raise ValueError("Autor e assistência devem ser jogadores diferentes")
        return self


class GameSidesIn(BaseModel):
    """Lados de um jogo. Cada lado vem do sorteio publicado (`*_team_index`) ou de uma escalação
    explícita (`*_member_ids`), que tem prioridade."""

    home_team_index: int | None = Field(default=None, ge=0)
    away_team_index: int | None = Field(default=None, ge=0)
    home_member_ids: list[int] | None = Field(default=None, max_length=30)
    away_member_ids: list[int] | None = Field(default=None, max_length=30)
    home_name: str | None = Field(default=None, min_length=1, max_length=60)
    away_name: str | None = Field(default=None, min_length=1, max_length=60)


class GameIn(GameSidesIn):
    """Jogo já disputado, lançado com o placar final."""

    home_score: int = Field(ge=0, le=99)
    away_score: int = Field(ge=0, le=99)
    goals: list[GoalIn] = Field(default_factory=list, max_length=200)


class LiveGameStart(GameSidesIn):
    """Inicia um jogo ao vivo: placar 0 x 0 e cronômetro regressivo."""

    duration_minutes: int = Field(default=10, ge=1, le=120)


class LiveGameUpdate(BaseModel):
    duration_minutes: int = Field(ge=1, le=120, description="Novo tempo total do jogo")


class GamePlayerOut(BaseModel):
    member_id: int
    name: str
    photo_url: str | None


class GoalOut(BaseModel):
    id: int
    side: Side
    scorer_member_id: int | None
    scorer_name: str | None
    assist_member_id: int | None
    assist_name: str | None
    own_goal: bool
    minute: int | None


class GameOut(BaseModel):
    id: int
    match_id: int
    sequence: int
    home_name: str
    away_name: str
    home_team_index: int | None
    away_team_index: int | None
    home_score: int
    away_score: int
    home_players: list[GamePlayerOut]
    away_players: list[GamePlayerOut]
    goals: list[GoalOut]
    status: GameStatus
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    elapsed_seconds: int | None = Field(description="Tempo decorrido no momento da resposta (jogos ao vivo)")
    created_at: datetime


# ------------------------------------------------------------------ estatísticas


class PlayerGameStats(BaseModel):
    matches: int = Field(description="Peladas com presença registrada")
    games: int
    wins: int
    draws: int
    losses: int
    points: int = Field(description="3 por vitória, 1 por empate")
    win_rate: float = Field(description="Aproveitamento (%) = pontos / (3 × jogos)")
    goals: int
    assists: int
    own_goals: int
    goals_for: int = Field(description="Gols do time com o jogador em campo")
    goals_against: int


class RankingRow(PlayerGameStats):
    position: int
    member_id: int
    name: str
    photo_url: str | None


class RankingOut(BaseModel):
    group_id: int
    sort: RankingSort
    since: date | None
    until: date | None
    rows: list[RankingRow]


class HeadToHeadSide(BaseModel):
    member_id: int
    name: str
    photo_url: str | None


class TogetherStats(BaseModel):
    games: int
    wins: int
    draws: int
    losses: int


class AgainstStats(BaseModel):
    games: int
    a_wins: int
    draws: int
    b_wins: int
    a_goals: int = Field(description="Gols de A nesses confrontos")
    b_goals: int


class HeadToHeadOut(BaseModel):
    a: HeadToHeadSide
    b: HeadToHeadSide
    together: TogetherStats
    against: AgainstStats


class AchievementOut(BaseModel):
    code: str
    name: str
    description: str
    icon: str
    achieved: bool
    achieved_at: datetime | None
    count: int = Field(description="Quantas vezes foi conquistada (conquistas repetíveis)")
    progress: float = Field(description="0 a 1")
    current: int
    target: int


class AwardWinner(BaseModel):
    member_id: int
    name: str
    photo_url: str | None
    value: float


class MonthAwards(BaseModel):
    month: str = Field(description="AAAA-MM")
    top_scorers: list[AwardWinner]
    top_assists: list[AwardWinner]
    best_win_rate: list[AwardWinner]
    most_present: list[AwardWinner]


class PublicRankingOut(BaseModel):
    group_name: str
    modality: str
    sort: RankingSort
    generated_at: datetime
    rows: list[RankingRow]
