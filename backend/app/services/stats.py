"""Estatísticas de jogo (PRD §11.1): ranking, confrontos, conquistas e prêmios mensais.

Tudo é calculado a partir dos jogos registrados (placar/gols) e da presença efetiva; nada disso
interfere nas notas usadas pelo sorteio.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError
from app.domain import rating as R
from app.domain.enums import AttendanceStatus, GameStatus, MatchStatus, MemberStatus, RatingKind
from app.models import GroupMember, Match, MatchGame, MatchPlayer, PlayerRating, PlayerRatingHistory

# horário de Brasília (sem horário de verão desde 2019): usado para recortes por dia/mês
BR_TZ = timezone(timedelta(hours=-3))
MIN_GAMES_WIN_RATE_AWARD = 3


@dataclass
class _Acc:
    matches: int = 0
    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals: int = 0
    assists: int = 0
    own_goals: int = 0
    goals_for: int = 0
    goals_against: int = 0

    def out(self) -> dict:
        points = 3 * self.wins + self.draws
        win_rate = round(points / (3 * self.games) * 100, 1) if self.games else 0.0
        return {
            "matches": self.matches, "games": self.games, "wins": self.wins, "draws": self.draws,
            "losses": self.losses, "points": points, "win_rate": win_rate, "goals": self.goals,
            "assists": self.assists, "own_goals": self.own_goals, "goals_for": self.goals_for,
            "goals_against": self.goals_against,
        }


def local_bounds(since: date | None, until: date | None) -> tuple[datetime | None, datetime | None]:
    """Converte datas locais (inclusivas) em instantes UTC [início, fim)."""
    start = datetime.combine(since, time.min, BR_TZ).astimezone(timezone.utc) if since else None
    end = datetime.combine(until + timedelta(days=1), time.min, BR_TZ).astimezone(timezone.utc) if until else None
    return start, end


def _games(db: Session, group_id: int, start: datetime | None, end: datetime | None) -> list[tuple[MatchGame, Match]]:
    stmt = (
        select(MatchGame, Match)
        .join(Match, Match.id == MatchGame.match_id)
        .where(Match.group_id == group_id, Match.status != MatchStatus.CANCELLED,
               MatchGame.status == GameStatus.FINISHED)  # jogo ao vivo só conta depois de encerrado
    )
    if start is not None:
        stmt = stmt.where(Match.starts_at >= start)
    if end is not None:
        stmt = stmt.where(Match.starts_at < end)
    return [(g, m) for g, m in db.execute(stmt.order_by(Match.starts_at, MatchGame.sequence)).all()]


def _presences(db: Session, group_id: int, start: datetime | None, end: datetime | None) -> dict[int, int]:
    stmt = (
        select(MatchPlayer.member_id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .where(Match.group_id == group_id, Match.status == MatchStatus.FINISHED,
               MatchPlayer.status == AttendanceStatus.PRESENT, MatchPlayer.is_referee.is_(False))
    )
    if start is not None:
        stmt = stmt.where(Match.starts_at >= start)
    if end is not None:
        stmt = stmt.where(Match.starts_at < end)
    counts: dict[int, int] = defaultdict(int)
    for mid in db.scalars(stmt).all():
        counts[mid] += 1
    return counts


def _result(game: MatchGame, side: str) -> int:
    """1 vitória, 0 empate, -1 derrota (do ponto de vista do lado)."""
    mine, theirs = (game.home_score, game.away_score) if side == "home" else (game.away_score, game.home_score)
    return (mine > theirs) - (mine < theirs)


def _accumulate(games: list[tuple[MatchGame, Match]], presences: dict[int, int]) -> dict[int, _Acc]:
    acc: dict[int, _Acc] = defaultdict(_Acc)
    for mid, n in presences.items():
        acc[mid].matches = n
    for game, _ in games:
        for side, ids in (("home", game.home_member_ids), ("away", game.away_member_ids)):
            res = _result(game, side)
            gf, ga = (game.home_score, game.away_score) if side == "home" else (game.away_score, game.home_score)
            for mid in ids:
                a = acc[mid]
                a.games += 1
                a.goals_for += gf
                a.goals_against += ga
                if res > 0:
                    a.wins += 1
                elif res == 0:
                    a.draws += 1
                else:
                    a.losses += 1
        for goal in game.goals:
            if goal.scorer_member_id is not None:
                if goal.own_goal:
                    acc[goal.scorer_member_id].own_goals += 1
                else:
                    acc[goal.scorer_member_id].goals += 1
            if goal.assist_member_id is not None:
                acc[goal.assist_member_id].assists += 1
    return acc


def aggregate(db: Session, group_id: int, start: datetime | None = None, end: datetime | None = None) -> dict[int, dict]:
    acc = _accumulate(_games(db, group_id, start, end), _presences(db, group_id, start, end))
    return {mid: a.out() for mid, a in acc.items()}


def member_stats(db: Session, group_id: int, member_id: int) -> dict:
    return aggregate(db, group_id).get(member_id) or _Acc().out()


_SORTS = {
    "points": lambda r: (-r["points"], -r["win_rate"], -r["goals"], r["name"].lower()),
    "goals": lambda r: (-r["goals"], -r["assists"], r["games"], r["name"].lower()),
    "assists": lambda r: (-r["assists"], -r["goals"], r["games"], r["name"].lower()),
    "win_rate": lambda r: (-r["win_rate"], -r["games"], r["name"].lower()),
    "matches": lambda r: (-r["matches"], -r["games"], r["name"].lower()),
    "wins": lambda r: (-r["wins"], -r["win_rate"], r["name"].lower()),
}


def ranking(
    db: Session, group_id: int, *, sort: str = "points", since: date | None = None, until: date | None = None,
    min_games: int = 0, only_active_players: bool = False,
) -> list[dict]:
    if since and until and since > until:
        raise BusinessRuleError("Período inválido: início depois do fim", code="invalid_period")
    start, end = local_bounds(since, until)
    stats = aggregate(db, group_id, start, end)
    members = db.scalars(select(GroupMember).where(GroupMember.group_id == group_id)).all()
    rows = []
    for m in members:
        s = stats.get(m.id)
        if m.status == MemberStatus.GUEST or (m.status != MemberStatus.ACTIVE and s is None):
            continue  # goleiro/juiz contratado não entra no ranking do grupo
        s = s or _Acc().out()
        if only_active_players and s["games"] == 0 and s["matches"] == 0:
            continue
        rows.append({"member_id": m.id, "name": m.display_name, "photo_url": m.user.photo_url, **s})
    key = _SORTS[sort]
    if sort == "win_rate" and min_games > 0:
        rows.sort(key=lambda r: (r["games"] < min_games, *key(r)))
    else:
        rows.sort(key=key)
    for i, r in enumerate(rows):
        r["position"] = i + 1
    return rows


def head_to_head(db: Session, group_id: int, a: GroupMember, b: GroupMember) -> dict:
    if a.id == b.id:
        raise BusinessRuleError("Escolha dois jogadores diferentes", code="same_player")
    together = {"games": 0, "wins": 0, "draws": 0, "losses": 0}
    against = {"games": 0, "a_wins": 0, "draws": 0, "b_wins": 0, "a_goals": 0, "b_goals": 0}
    for game, _ in _games(db, group_id, None, None):
        sides = {mid: "home" for mid in game.home_member_ids} | {mid: "away" for mid in game.away_member_ids}
        sa, sb = sides.get(a.id), sides.get(b.id)
        if sa is None or sb is None:
            continue
        res = _result(game, sa)
        if sa == sb:
            together["games"] += 1
            together["wins" if res > 0 else "draws" if res == 0 else "losses"] += 1
        else:
            against["games"] += 1
            against["a_wins" if res > 0 else "draws" if res == 0 else "b_wins"] += 1
            for goal in game.goals:
                if goal.own_goal:
                    continue
                if goal.scorer_member_id == a.id:
                    against["a_goals"] += 1
                elif goal.scorer_member_id == b.id:
                    against["b_goals"] += 1

    def side(m: GroupMember) -> dict:
        return {"member_id": m.id, "name": m.display_name, "photo_url": m.user.photo_url}

    return {"a": side(a), "b": side(b), "together": together, "against": against}


# ------------------------------------------------------------------ prêmios mensais


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _winner_rows(rows: list[dict], key: str, *, min_games: int = 0) -> list[dict]:
    candidates = [r for r in rows if r[key] > 0 and r["games"] >= min_games]
    if not candidates:
        return []
    best = max(r[key] for r in candidates)
    return [r for r in candidates if r[key] == best]


def _winners(rows: list[dict], key: str, members: dict[int, GroupMember], *, min_games: int = 0) -> list[dict]:
    out = []
    for r in _winner_rows(rows, key, min_games=min_games):
        m = members.get(r["member_id"])
        if m is not None:
            out.append({"member_id": m.id, "name": m.display_name, "photo_url": m.user.photo_url, "value": r[key]})
    return sorted(out, key=lambda w: w["name"].lower())


def monthly_awards(db: Session, group_id: int, months: int = 6) -> list[dict]:
    members = {m.id: m for m in db.scalars(select(GroupMember).where(GroupMember.group_id == group_id)).all()}
    current = _month_start(utcnow().astimezone(BR_TZ).date())
    out = []
    for i in range(months):
        first = _add_months(current, -i)
        last = _add_months(first, 1) - timedelta(days=1)
        start, end = local_bounds(first, last)
        stats = aggregate(db, group_id, start, end)
        rows = [{"member_id": mid, **s} for mid, s in stats.items()]
        out.append({
            "month": first.strftime("%Y-%m"),
            "top_scorers": _winners(rows, "goals", members),
            "top_assists": _winners(rows, "assists", members),
            "best_win_rate": _winners(rows, "win_rate", members, min_games=MIN_GAMES_WIN_RATE_AWARD),
            "most_present": _winners(rows, "matches", members),
        })
    return out


# ------------------------------------------------------------------ conquistas individuais


@dataclass
class _Badge:
    code: str
    name: str
    description: str
    icon: str
    target: int = 1
    current: int = 0
    count: int = 0
    achieved_at: datetime | None = None
    dates: list[datetime] = field(default_factory=list)

    def out(self) -> dict:
        achieved = self.current >= self.target or self.count > 0
        return {
            "code": self.code, "name": self.name, "description": self.description, "icon": self.icon,
            "achieved": achieved, "achieved_at": self.achieved_at if achieved else None,
            "count": self.count if self.count else (1 if achieved else 0),
            "progress": 1.0 if achieved else round(min(self.current, self.target) / self.target, 2),
            "current": self.current, "target": self.target,
        }


_MILESTONES = [(10, "peladas_10", "Figurinha carimbada"), (25, "peladas_25", "Veterano"),
               (50, "peladas_50", "Lenda do grupo"), (100, "peladas_100", "Centenário")]
STREAK_TARGET = 5


def member_achievements(db: Session, group_id: int, member: GroupMember) -> list[dict]:
    finished = db.scalars(
        select(Match).where(Match.group_id == group_id, Match.status == MatchStatus.FINISHED).order_by(Match.starts_at)
    ).all()
    statuses = {
        mp.match_id: mp.status
        for mp in db.scalars(select(MatchPlayer).where(MatchPlayer.member_id == member.id)).all()
    }
    present_dates = [m.starts_at for m in finished if statuses.get(m.id) == AttendanceStatus.PRESENT]
    n = len(present_dates)

    badges: list[_Badge] = []
    debut = _Badge("estreia", "Estreia", "Jogou a primeira pelada do grupo", "sparkles", current=min(n, 1))
    debut.achieved_at = present_dates[0] if n else None
    badges.append(debut)
    for target, code, name in _MILESTONES:
        b = _Badge(code, name, f"Jogou {target} peladas", "medal", target=target, current=n)
        b.achieved_at = present_dates[target - 1] if n >= target else None
        badges.append(b)

    # sequência de presenças consecutivas nas peladas encerradas
    streak = best = 0
    streak_at: datetime | None = None
    for m in finished:
        if statuses.get(m.id) == AttendanceStatus.PRESENT:
            streak += 1
            if streak == STREAK_TARGET and streak_at is None:
                streak_at = m.starts_at
            best = max(best, streak)
        else:
            streak = 0
    fiel = _Badge("fiel", "Presença garantida", f"{STREAK_TARGET} peladas seguidas sem faltar", "calendar-check",
                  target=STREAK_TARGET, current=best)
    fiel.achieved_at = streak_at
    badges.append(fiel)

    # gols, assistências e invencibilidade por pelada
    goals_by_match: dict[int, int] = defaultdict(int)
    assists_by_match: dict[int, int] = defaultdict(int)
    games_by_match: dict[int, list[int]] = defaultdict(list)
    match_date: dict[int, datetime] = {}
    for game, match in _games(db, group_id, None, None):
        match_date[match.id] = match.starts_at
        side = "home" if member.id in game.home_member_ids else "away" if member.id in game.away_member_ids else None
        if side is not None:
            games_by_match[match.id].append(_result(game, side))
        for goal in game.goals:
            if goal.scorer_member_id == member.id and not goal.own_goal:
                goals_by_match[match.id] += 1
            if goal.assist_member_id == member.id:
                assists_by_match[match.id] += 1

    def per_match(code: str, name: str, desc: str, icon: str, values: dict[int, int], threshold: int) -> _Badge:
        hits = sorted((match_date[mid] for mid, v in values.items() if v >= threshold))
        best_value = max(values.values(), default=0)
        b = _Badge(code, name, desc, icon, target=threshold, current=min(best_value, threshold), count=len(hits))
        b.achieved_at = hits[0] if hits else None
        return b

    badges.append(per_match("primeiro_gol", "Primeiro gol", "Marcou o primeiro gol", "goal", goals_by_match, 1))
    badges.append(per_match("hat_trick", "Hat-trick", "3 gols ou mais na mesma pelada", "flame", goals_by_match, 3))
    badges.append(per_match("garcom", "Garçom", "3 assistências ou mais na mesma pelada", "hand-helping",
                            assists_by_match, 3))

    unbeaten = sorted(match_date[mid] for mid, res in games_by_match.items() if len(res) >= 3 and min(res) >= 0)
    best_games = max((len(r) for r in games_by_match.values() if min(r) >= 0), default=0)
    inv = _Badge("invicto", "Invicto", "Jogou 3 jogos ou mais numa pelada sem perder", "shield",
                 target=3, current=min(best_games, 3), count=len(unbeaten))
    inv.achieved_at = unbeaten[0] if unbeaten else None
    badges.append(inv)

    # artilheiro de algum mês (desde a primeira pelada com jogos registrados)
    top_months = []
    if match_date:
        first = _month_start(min(match_date.values()).astimezone(BR_TZ).date())
        current = _month_start(utcnow().astimezone(BR_TZ).date())
        span = (current.year - first.year) * 12 + current.month - first.month + 1
        for i in range(span):
            month_first = _add_months(first, i)
            start, end = local_bounds(month_first, _add_months(month_first, 1) - timedelta(days=1))
            stats = aggregate(db, group_id, start, end)
            rows = [{"member_id": mid, **s} for mid, s in stats.items()]
            if any(r["member_id"] == member.id for r in _winner_rows(rows, "goals")):
                top_months.append(end - timedelta(seconds=1))
    art = _Badge("artilheiro_mes", "Artilheiro do mês", "Terminou um mês como artilheiro do grupo", "trophy",
                 count=len(top_months), current=min(len(top_months), 1))
    art.achieved_at = top_months[0] if top_months else None
    badges.append(art)

    # 5 estrelas em algum momento
    reached = db.scalar(
        select(PlayerRatingHistory.created_at)
        .where(PlayerRatingHistory.member_id == member.id, PlayerRatingHistory.stars_after == R.MAX_STARS)
        .order_by(PlayerRatingHistory.created_at)
        .limit(1)
    )
    if reached is None:
        current_s = db.scalar(select(PlayerRating).where(
            PlayerRating.member_id == member.id, PlayerRating.kind == RatingKind.FIELD,
            PlayerRating.stars == R.MAX_STARS))
        reached = current_s.updated_at if current_s else None
    craque = _Badge("cinco_estrelas", "Craque", "Alcançou 5 estrelas", "star", current=1 if reached else 0)
    craque.achieved_at = reached
    badges.append(craque)

    return [b.out() for b in badges]
