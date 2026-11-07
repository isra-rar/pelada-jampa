"""Placar e gols dos jogos disputados em uma pelada (base das estatísticas).

Um jogo pode ser lançado depois, com o placar final, ou acompanhado ao vivo: começa 0 x 0, o cronômetro
corre a partir de `started_at` e cada gol é lançado na hora (o minuto é calculado pelo servidor).
Só jogos encerrados entram nas estatísticas.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError, NotFound
from app.domain.enums import AttendanceStatus, GameStatus, MatchStatus, MemberStatus, NotificationType
from app.models import Draw, FootballGroup, GroupMember, Match, MatchGame, MatchGoal, MatchPlayer, User
from app.schemas.stats import GameIn, GameSidesIn, GoalIn, LiveGameStart, LiveGameUpdate
from app.services import audit, draws, notifications

_ELIGIBLE = (AttendanceStatus.CONFIRMED, AttendanceStatus.PRESENT)


def _published_draw(db: Session, match: Match) -> Draw | None:
    published = draws.list_draws(db, match, include_unpublished=False)
    return published[0] if published else None


def _require_playable(match: Match) -> None:
    if match.status not in (MatchStatus.PUBLISHED, MatchStatus.FINISHED):
        raise BusinessRuleError("Só é possível registrar placar em peladas publicadas ou encerradas",
                                code="invalid_status")
    if utcnow() < match.starts_at:
        raise BusinessRuleError("O placar só pode ser registrado depois do início da pelada", code="too_early")


def _eligible_ids(db: Session, match: Match, draw: Draw | None) -> set[int]:
    ids = set(db.scalars(
        select(MatchPlayer.member_id).where(MatchPlayer.match_id == match.id, MatchPlayer.status.in_(_ELIGIBLE))
    ).all())
    if draw is not None:
        ids |= {p.member_id for t in draw.teams for p in t.players}
    return ids


def _resolve_side(
    label: str, team_index: int | None, member_ids: list[int] | None, name: str | None, draw: Draw | None
) -> tuple[list[int], str, int | None]:
    if member_ids:
        ids = list(dict.fromkeys(member_ids))
        team_name = name
        if team_name is None and team_index is not None and draw is not None:
            team = next((t for t in draw.teams if t.index == team_index), None)
            team_name = team.name if team else None
        return ids, team_name or label, team_index
    if team_index is None:
        raise BusinessRuleError(f"Informe o time ({label.lower()}) do sorteio publicado ou a escalação",
                                code="side_required")
    if draw is None:
        raise BusinessRuleError("Não há times publicados: informe as escalações manualmente", code="no_published_draw")
    team = next((t for t in draw.teams if t.index == team_index), None)
    if team is None:
        raise BusinessRuleError(f"Time {team_index} não existe no sorteio publicado", code="invalid_team")
    return [p.member_id for p in team.players], name or team.name, team_index


@dataclass
class _Sides:
    home_ids: list[int]
    home_name: str
    home_idx: int | None
    away_ids: list[int]
    away_name: str
    away_idx: int | None


def _resolve_sides(db: Session, match: Match, data: GameSidesIn) -> _Sides:
    draw = _published_draw(db, match)
    home_ids, home_name, home_idx = _resolve_side("Casa", data.home_team_index, data.home_member_ids,
                                                  data.home_name, draw)
    away_ids, away_name, away_idx = _resolve_side("Visitante", data.away_team_index, data.away_member_ids,
                                                  data.away_name, draw)
    if home_idx is not None and home_idx == away_idx and not (data.home_member_ids and data.away_member_ids):
        raise BusinessRuleError("Um time não pode enfrentar ele mesmo", code="same_team")
    if not home_ids or not away_ids:
        raise BusinessRuleError("Cada lado precisa de pelo menos um jogador", code="empty_side")
    if set(home_ids) & set(away_ids):
        raise BusinessRuleError("Um jogador não pode estar nos dois lados", code="player_both_sides")
    eligible = _eligible_ids(db, match, draw)
    outsiders = (set(home_ids) | set(away_ids)) - eligible
    if outsiders:
        raise BusinessRuleError("Há jogadores que não participam desta pelada", code="not_participant",
                                details={"member_ids": sorted(outsiders)})
    return _Sides(home_ids, home_name, home_idx, away_ids, away_name, away_idx)


def _set_sides(game: MatchGame, sides: _Sides) -> None:
    game.home_member_ids, game.away_member_ids = sides.home_ids, sides.away_ids
    game.home_name, game.away_name = sides.home_name, sides.away_name
    game.home_team_index, game.away_team_index = sides.home_idx, sides.away_idx


def _check_goal(g: GoalIn, lineup: dict[str, set[int]]) -> None:
    other = "away" if g.side == "home" else "home"
    if g.scorer_member_id is not None:
        expected = other if g.own_goal else g.side
        if g.scorer_member_id not in lineup[expected]:
            raise BusinessRuleError("Autor do gol não está no time correspondente", code="invalid_scorer",
                                    details={"member_id": g.scorer_member_id})
    if g.assist_member_id is not None and g.assist_member_id not in lineup[g.side]:
        raise BusinessRuleError("Assistência de jogador fora do time que marcou", code="invalid_assist",
                                details={"member_id": g.assist_member_id})


def _new_goal(match: Match, g: GoalIn, minute: int | None) -> MatchGoal:
    return MatchGoal(match_id=match.id, side=g.side, scorer_member_id=g.scorer_member_id,
                     assist_member_id=g.assist_member_id, own_goal=g.own_goal, minute=minute)


def _apply(db: Session, match: Match, game: MatchGame, data: GameIn) -> None:
    sides = _resolve_sides(db, match, data)
    lineup = {"home": set(sides.home_ids), "away": set(sides.away_ids)}
    per_side = {"home": 0, "away": 0}
    for g in data.goals:
        per_side[g.side] += 1
        _check_goal(g, lineup)
    if per_side["home"] > data.home_score or per_side["away"] > data.away_score:
        raise BusinessRuleError("Há mais gols detalhados do que o placar informado", code="goals_exceed_score")

    _set_sides(game, sides)
    game.home_score, game.away_score = data.home_score, data.away_score
    game.goals.clear()
    db.flush()
    for g in data.goals:
        game.goals.append(_new_goal(match, g, g.minute))


def _snapshot(game: MatchGame) -> dict:
    return {
        "home": game.home_name, "away": game.away_name,
        "score": f"{game.home_score}x{game.away_score}",
        "home_member_ids": game.home_member_ids, "away_member_ids": game.away_member_ids,
        "goals": [
            {"side": g.side, "scorer": g.scorer_member_id, "assist": g.assist_member_id, "own_goal": g.own_goal}
            for g in game.goals
        ],
    }


def _notify_result(db: Session, group: FootballGroup, match: Match, game: MatchGame) -> None:
    """Avisa quem jogou que o resultado entrou (jogo lançado depois ou ao vivo encerrado)."""
    member_ids = list(game.home_member_ids or []) + list(game.away_member_ids or [])
    user_ids = db.scalars(
        select(GroupMember.user_id).where(GroupMember.id.in_(member_ids), GroupMember.status == MemberStatus.ACTIVE)
    ).all() if member_ids else []
    notifications.notify(
        db, user_ids, NotificationType.RESULT_REGISTERED,
        f"Resultado: {game.home_name} {game.home_score} x {game.away_score} {game.away_name}",
        f"Jogo {game.sequence} de {match.name}. Gols e ranking já foram atualizados.",
        group_id=group.id, match_id=match.id,
    )


def list_games(db: Session, match: Match) -> list[MatchGame]:
    return list(db.scalars(
        select(MatchGame).where(MatchGame.match_id == match.id).order_by(MatchGame.sequence)
    ).all())


def get_game(db: Session, match: Match, game_id: int) -> MatchGame:
    game = db.get(MatchGame, game_id)
    if game is None or game.match_id != match.id:
        raise NotFound("Jogo não encontrado")
    return game


def create_game(db: Session, group: FootballGroup, match: Match, data: GameIn, actor: User) -> MatchGame:
    _require_playable(match)
    game = MatchGame(match_id=match.id, sequence=_next_sequence(db, match), created_by_id=actor.id, home_name="", away_name="",
                     home_member_ids=[], away_member_ids=[])
    db.add(game)
    _apply(db, match, game, data)
    db.flush()
    audit.record(db, action="game.created", entity_type="match_game", entity_id=game.id, actor_user_id=actor.id,
                 group_id=group.id, after=_snapshot(game))
    _notify_result(db, group, match, game)
    db.commit()
    db.refresh(game)
    return game


def update_game(db: Session, group: FootballGroup, match: Match, game: MatchGame, data: GameIn,
                actor: User) -> MatchGame:
    _require_playable(match)
    if game.status == GameStatus.LIVE:
        raise BusinessRuleError("Encerre o jogo ao vivo antes de editá-lo", code="game_live")
    before = _snapshot(game)
    _apply(db, match, game, data)
    db.flush()
    audit.record(db, action="game.updated", entity_type="match_game", entity_id=game.id, actor_user_id=actor.id,
                 group_id=group.id, before=before, after=_snapshot(game))
    db.commit()
    db.refresh(game)
    return game


def delete_game(db: Session, group: FootballGroup, match: Match, game: MatchGame, actor: User) -> None:
    if match.status == MatchStatus.CANCELLED:
        raise BusinessRuleError("Pelada cancelada", code="invalid_status")
    audit.record(db, action="game.deleted", entity_type="match_game", entity_id=game.id, actor_user_id=actor.id,
                 group_id=group.id, before=_snapshot(game))
    db.delete(game)
    db.commit()


def game_out(db: Session, game: MatchGame, members: dict[int, GroupMember] | None = None) -> dict:
    ids = set(game.home_member_ids) | set(game.away_member_ids)
    ids |= {g.scorer_member_id for g in game.goals if g.scorer_member_id}
    ids |= {g.assist_member_id for g in game.goals if g.assist_member_id}
    if members is None or not ids <= members.keys():
        rows = db.scalars(select(GroupMember).where(GroupMember.id.in_(ids))).all() if ids else []
        members = {**(members or {}), **{m.id: m for m in rows}}

    def player(mid: int) -> dict:
        m = members.get(mid)
        return {"member_id": mid, "name": m.display_name if m else "Jogador removido",
                "photo_url": m.user.photo_url if m else None}

    def name(mid: int | None) -> str | None:
        if mid is None:
            return None
        m = members.get(mid)
        return m.display_name if m else "Jogador removido"

    return {
        "id": game.id,
        "match_id": game.match_id,
        "sequence": game.sequence,
        "home_name": game.home_name,
        "away_name": game.away_name,
        "home_team_index": game.home_team_index,
        "away_team_index": game.away_team_index,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "home_players": [player(m) for m in game.home_member_ids],
        "away_players": [player(m) for m in game.away_member_ids],
        "goals": [
            {"id": g.id, "side": g.side, "scorer_member_id": g.scorer_member_id, "scorer_name": name(g.scorer_member_id),
             "assist_member_id": g.assist_member_id, "assist_name": name(g.assist_member_id),
             "own_goal": g.own_goal, "minute": g.minute}
            for g in game.goals
        ],
        "status": game.status,
        "started_at": game.started_at,
        "ended_at": game.ended_at,
        "duration_seconds": game.duration_seconds,
        "elapsed_seconds": elapsed_seconds(game),
        "created_at": game.created_at,
    }


# ------------------------------------------------------------------ jogo ao vivo


def elapsed_seconds(game: MatchGame) -> int | None:
    if game.started_at is None:
        return None
    end = game.ended_at or utcnow()
    return max(0, int((end - game.started_at).total_seconds()))


def _require_live(game: MatchGame) -> None:
    if game.status != GameStatus.LIVE:
        raise BusinessRuleError("Este jogo não está ao vivo", code="game_not_live")


def _recount(game: MatchGame) -> None:
    game.home_score = sum(1 for g in game.goals if g.side == "home")
    game.away_score = sum(1 for g in game.goals if g.side == "away")


def _next_sequence(db: Session, match: Match) -> int:
    return (db.scalar(select(func.max(MatchGame.sequence)).where(MatchGame.match_id == match.id)) or 0) + 1


def live_game(db: Session, match: Match) -> MatchGame | None:
    return db.scalar(select(MatchGame).where(MatchGame.match_id == match.id, MatchGame.status == GameStatus.LIVE))


def start_live(db: Session, group: FootballGroup, match: Match, data: LiveGameStart, actor: User) -> MatchGame:
    _require_playable(match)
    if live_game(db, match) is not None:
        raise BusinessRuleError("Já há um jogo ao vivo nesta pelada; encerre-o antes de começar outro",
                                code="live_game_running")
    sides = _resolve_sides(db, match, data)
    game = MatchGame(match_id=match.id, sequence=_next_sequence(db, match), created_by_id=actor.id,
                     status=GameStatus.LIVE, started_at=utcnow(), duration_seconds=data.duration_minutes * 60,
                     home_score=0, away_score=0)
    _set_sides(game, sides)
    db.add(game)
    db.flush()
    audit.record(db, action="game.live_started", entity_type="match_game", entity_id=game.id,
                 actor_user_id=actor.id, group_id=group.id,
                 after={**_snapshot(game), "duration_seconds": game.duration_seconds})
    db.commit()
    db.refresh(game)
    return game


def add_live_goal(db: Session, group: FootballGroup, match: Match, game: MatchGame, data: GoalIn,
                  actor: User) -> MatchGame:
    _require_playable(match)
    _require_live(game)
    _check_goal(data, {"home": set(game.home_member_ids), "away": set(game.away_member_ids)})
    before = _snapshot(game)
    minute = data.minute if data.minute is not None else (elapsed_seconds(game) or 0) // 60 + 1
    game.goals.append(_new_goal(match, data, minute))
    _recount(game)
    db.flush()
    audit.record(db, action="game.goal_added", entity_type="match_game", entity_id=game.id, actor_user_id=actor.id,
                 group_id=group.id, before=before, after=_snapshot(game))
    db.commit()
    db.refresh(game)
    return game


def remove_live_goal(db: Session, group: FootballGroup, match: Match, game: MatchGame, goal_id: int,
                     actor: User) -> MatchGame:
    _require_live(game)
    goal = next((g for g in game.goals if g.id == goal_id), None)
    if goal is None:
        raise NotFound("Gol não encontrado")
    before = _snapshot(game)
    game.goals.remove(goal)
    _recount(game)
    db.flush()
    audit.record(db, action="game.goal_removed", entity_type="match_game", entity_id=game.id,
                 actor_user_id=actor.id, group_id=group.id, before=before, after=_snapshot(game))
    db.commit()
    db.refresh(game)
    return game


def update_live(db: Session, group: FootballGroup, match: Match, game: MatchGame, data: LiveGameUpdate,
                actor: User) -> MatchGame:
    _require_live(game)
    before = {"duration_seconds": game.duration_seconds}
    game.duration_seconds = data.duration_minutes * 60
    audit.record(db, action="game.duration_changed", entity_type="match_game", entity_id=game.id,
                 actor_user_id=actor.id, group_id=group.id, before=before,
                 after={"duration_seconds": game.duration_seconds})
    db.commit()
    db.refresh(game)
    return game


def finish_live(db: Session, group: FootballGroup, match: Match, game: MatchGame, actor: User) -> MatchGame:
    _require_live(game)
    game.status = GameStatus.FINISHED
    game.ended_at = utcnow()
    audit.record(db, action="game.finished", entity_type="match_game", entity_id=game.id, actor_user_id=actor.id,
                 group_id=group.id, after={**_snapshot(game), "elapsed_seconds": elapsed_seconds(game)})
    _notify_result(db, group, match, game)
    db.commit()
    db.refresh(game)
    return game
