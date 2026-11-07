from datetime import date

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DB, CurrentUser
from app.core.db import utcnow
from app.core.errors import NotFound
from app.models import FootballGroup
from app.schemas.stats import (
    AchievementOut,
    GameIn,
    GameOut,
    GoalIn,
    HeadToHeadOut,
    LiveGameStart,
    LiveGameUpdate,
    MonthAwards,
    PublicRankingOut,
    RankingOut,
    RankingSort,
)
from app.services import games, stats
from app.services.access import get_group, get_member_in_group, match_for_admin, match_for_member, require_member

router = APIRouter(tags=["Placar e estatísticas"])
public_router = APIRouter(prefix="/public", tags=["Público (sem login)"])


# ------------------------------------------------------------------ jogos da pelada


@router.get("/matches/{match_id}/games", response_model=list[GameOut])
def list_games(match_id: int, user: CurrentUser, db: DB):
    match, _ = match_for_member(db, match_id, user)
    return [games.game_out(db, g) for g in games.list_games(db, match)]


@router.post("/matches/{match_id}/games", response_model=GameOut, status_code=status.HTTP_201_CREATED)
def create_game(match_id: int, data: GameIn, user: CurrentUser, db: DB):
    """Registra um jogo (placar e, opcionalmente, autores dos gols e assistências)."""
    match, _ = match_for_admin(db, match_id, user)
    return games.game_out(db, games.create_game(db, get_group(db, match.group_id), match, data, user))


@router.put("/matches/{match_id}/games/{game_id}", response_model=GameOut)
def update_game(match_id: int, game_id: int, data: GameIn, user: CurrentUser, db: DB):
    match, _ = match_for_admin(db, match_id, user)
    game = games.get_game(db, match, game_id)
    return games.game_out(db, games.update_game(db, get_group(db, match.group_id), match, game, data, user))


@router.delete("/matches/{match_id}/games/{game_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_game(match_id: int, game_id: int, user: CurrentUser, db: DB):
    match, _ = match_for_admin(db, match_id, user)
    games.delete_game(db, get_group(db, match.group_id), match, games.get_game(db, match, game_id), user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ jogo ao vivo


@router.post("/matches/{match_id}/games/live", response_model=GameOut, status_code=status.HTTP_201_CREATED)
def start_live_game(match_id: int, data: LiveGameStart, user: CurrentUser, db: DB):
    """Começa um jogo ao vivo (0 x 0, cronômetro regressivo, padrão 10 min). Um por vez em cada pelada."""
    match, _ = match_for_admin(db, match_id, user)
    return games.game_out(db, games.start_live(db, get_group(db, match.group_id), match, data, user))


@router.post("/matches/{match_id}/games/{game_id}/goals", response_model=GameOut,
             status_code=status.HTTP_201_CREATED)
def add_live_goal(match_id: int, game_id: int, data: GoalIn, user: CurrentUser, db: DB):
    """Lança um gol no jogo ao vivo. Sem `minute`, o minuto vem do cronômetro."""
    match, _ = match_for_admin(db, match_id, user)
    game = games.get_game(db, match, game_id)
    return games.game_out(db, games.add_live_goal(db, get_group(db, match.group_id), match, game, data, user))


@router.delete("/matches/{match_id}/games/{game_id}/goals/{goal_id}", response_model=GameOut)
def remove_live_goal(match_id: int, game_id: int, goal_id: int, user: CurrentUser, db: DB):
    """Desfaz um gol lançado por engano (somente com o jogo ao vivo)."""
    match, _ = match_for_admin(db, match_id, user)
    game = games.get_game(db, match, game_id)
    return games.game_out(db, games.remove_live_goal(db, get_group(db, match.group_id), match, game, goal_id, user))


@router.patch("/matches/{match_id}/games/{game_id}/live", response_model=GameOut)
def update_live_game(match_id: int, game_id: int, data: LiveGameUpdate, user: CurrentUser, db: DB):
    """Altera o tempo total do jogo ao vivo."""
    match, _ = match_for_admin(db, match_id, user)
    game = games.get_game(db, match, game_id)
    return games.game_out(db, games.update_live(db, get_group(db, match.group_id), match, game, data, user))


@router.post("/matches/{match_id}/games/{game_id}/finish", response_model=GameOut)
def finish_live_game(match_id: int, game_id: int, user: CurrentUser, db: DB):
    """Encerra o jogo ao vivo; a partir daí ele conta nas estatísticas."""
    match, _ = match_for_admin(db, match_id, user)
    game = games.get_game(db, match, game_id)
    return games.game_out(db, games.finish_live(db, get_group(db, match.group_id), match, game, user))


# ------------------------------------------------------------------ estatísticas do grupo


@router.get("/groups/{group_id}/stats/ranking", response_model=RankingOut)
def group_ranking(
    group_id: int, user: CurrentUser, db: DB,
    sort: RankingSort = "points",
    since: date | None = Query(default=None, description="Data inicial (inclusiva, horário de Brasília)"),
    until: date | None = Query(default=None, description="Data final (inclusiva)"),
    min_games: int = Query(default=0, ge=0, le=100, description="Mínimo de jogos para o ranking de aproveitamento"),
):
    """Ranking de gols, assistências, vitórias, aproveitamento e presenças."""
    require_member(db, group_id, user)
    rows = stats.ranking(db, group_id, sort=sort, since=since, until=until, min_games=min_games)
    return {"group_id": group_id, "sort": sort, "since": since, "until": until, "rows": rows}


@router.get("/groups/{group_id}/stats/head-to-head", response_model=HeadToHeadOut)
def head_to_head(group_id: int, user: CurrentUser, db: DB, a: int = Query(...), b: int = Query(...)):
    """Histórico de confrontos entre dois jogadores: jogando juntos e um contra o outro."""
    require_member(db, group_id, user)
    return stats.head_to_head(db, group_id, get_member_in_group(db, group_id, a), get_member_in_group(db, group_id, b))


@router.get("/groups/{group_id}/awards", response_model=list[MonthAwards])
def awards(group_id: int, user: CurrentUser, db: DB, months: int = Query(default=6, ge=1, le=24)):
    """Premiações mensais: artilheiro, garçom, melhor aproveitamento e mais presente."""
    require_member(db, group_id, user)
    return stats.monthly_awards(db, group_id, months)


@router.get("/groups/{group_id}/players/{member_id}/achievements", response_model=list[AchievementOut])
def achievements(group_id: int, member_id: int, user: CurrentUser, db: DB):
    """Conquistas do jogador (desbloqueadas e em progresso)."""
    require_member(db, group_id, user)
    return stats.member_achievements(db, group_id, get_member_in_group(db, group_id, member_id))


# ------------------------------------------------------------------ público


@public_router.get("/ranking/{token}", response_model=PublicRankingOut)
def public_ranking(
    token: str, db: DB, sort: RankingSort = "points",
    since: date | None = None, until: date | None = None,
):
    """Ranking do grupo por link público (somente estatísticas de jogo; nenhuma nota ou dado pessoal)."""
    group = db.query(FootballGroup).filter(FootballGroup.public_token == token).one_or_none()
    if group is None or not group.public_ranking_enabled or not group.is_active:
        raise NotFound("Ranking não encontrado")
    rows = stats.ranking(db, group.id, sort=sort, since=since, until=until,
                         min_games=3 if sort == "win_rate" else 0, only_active_players=True)
    return {"group_name": group.name, "modality": group.modality.value, "sort": sort, "generated_at": utcnow(),
            "rows": rows}
