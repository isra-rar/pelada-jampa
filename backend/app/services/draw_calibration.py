"""Coleta das amostras para a calibração do sorteio (regras em app.domain.calibration).

Amostra = jogo encerrado entre dois times do sorteio publicado da pelada (o placar e a escalação foram
lançados a partir do sorteio). Jogos montados à mão, sem os índices dos times, ficam de fora.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import calibration as C
from app.domain.enums import DrawStatus, GameStatus, MatchStatus
from app.models import Draw, Match, MatchGame
from app.services import metrics
from app.services.stats import BR_TZ, local_bounds

DEFAULT_DAYS = 365
RECENT_LIMIT = 10


def collect(db: Session, group_id: int | None, since: date, until: date) -> tuple[list[C.GameSample], list[dict]]:
    start, end = local_bounds(since, until)
    stmt = (
        select(MatchGame, Match)
        .join(Match, Match.id == MatchGame.match_id)
        .where(
            Match.status.in_((MatchStatus.PUBLISHED, MatchStatus.FINISHED)),
            Match.starts_at >= start, Match.starts_at < end,
            MatchGame.status == GameStatus.FINISHED,
            MatchGame.home_team_index.is_not(None),
            MatchGame.away_team_index.is_not(None),
        )
        .order_by(Match.starts_at.desc(), MatchGame.sequence.desc())
    )
    if group_id is not None:
        stmt = stmt.where(Match.group_id == group_id)
    rows = db.execute(stmt).all()
    match_ids = {m.id for _, m in rows}
    published = {
        d.match_id: {t.index: t for t in d.teams}
        for d in db.scalars(
            select(Draw).where(Draw.match_id.in_(match_ids), Draw.status == DrawStatus.PUBLISHED)
        ).all()
    } if match_ids else {}

    samples: list[C.GameSample] = []
    recent: list[dict] = []
    for game, match in rows:
        teams = published.get(match.id)
        if not teams or game.home_team_index not in teams or game.away_team_index not in teams:
            continue
        if game.home_team_index == game.away_team_index:
            continue
        home, away = teams[game.home_team_index], teams[game.away_team_index]
        samples.append(C.GameSample(home.stats or {}, away.stats or {}, game.home_score, game.away_score))
        if len(recent) < RECENT_LIMIT:
            recent.append({
                "match_id": match.id,
                "match_date": match.starts_at.astimezone(BR_TZ).date(),
                "home_name": game.home_name,
                "away_name": game.away_name,
                "home_score": game.home_score,
                "away_score": game.away_score,
                "average_difference": round(abs(
                    float((home.stats or {}).get("average", 0.0)) - float((away.stats or {}).get("average", 0.0))
                ), 2),
            })
    return samples, recent


def calibration(
    db: Session, group_id: int | None, current_weights: dict[str, float], since: date | None, until: date | None
) -> dict:
    since, until = metrics.period(since, until, DEFAULT_DAYS)
    samples, recent = collect(db, group_id, since, until)
    return {"since": since, "until": until, **C.analyze(samples, current_weights), "recent_games": recent}
