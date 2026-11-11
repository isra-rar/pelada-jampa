"""Execução, ajuste e publicação de sorteios (PRD §9). O algoritmo vive em app.domain.draw."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import BusinessRuleError, NotFound
from app.domain import draw as D
from app.domain import rating as R
from app.domain.enums import AttendanceStatus, DrawStatus, MatchStatus, MemberStatus, NotificationType, RatingKind
from app.models import Draw, FootballGroup, GroupMember, Match, MatchPlayer, Team, TeamPlayer, User
from app.schemas.draw import DrawCreate, PlayerMove
from app.services import audit, level_quiz, notifications, ratings

TEAM_NAMES = ["Time A", "Time B", "Time C", "Time D", "Time E", "Time F", "Time G", "Time H"]


def _participants(db: Session, match: Match) -> list[MatchPlayer]:
    return list(db.scalars(select(MatchPlayer).where(MatchPlayer.match_id == match.id)).all())


def _eligible(db: Session, match: Match, data: DrawCreate) -> list[MatchPlayer]:
    parts = _participants(db, match)
    by_member = {mp.member_id: mp for mp in parts}
    ok_status = (AttendanceStatus.CONFIRMED, AttendanceStatus.PRESENT)
    if data.eligible_member_ids:
        chosen = []
        for mid in dict.fromkeys(data.eligible_member_ids):
            mp = by_member.get(mid)
            if mp is None or mp.status not in ok_status:
                raise BusinessRuleError(f"Jogador {mid} não está confirmado nesta pelada", code="not_eligible")
            if mp.is_referee:
                raise BusinessRuleError(f"Jogador {mid} é o juiz da pelada", code="not_eligible")
            chosen.append(mp)
    else:
        players = [mp for mp in parts if not mp.is_referee]  # juiz fica fora dos times
        present = [mp for mp in players if mp.status == AttendanceStatus.PRESENT]
        chosen = present or [mp for mp in players if mp.status == AttendanceStatus.CONFIRMED]
    unavailable = set(data.unavailable_member_ids)
    chosen = [mp for mp in chosen if mp.member_id not in unavailable]
    # prioridade = ordem de confirmação (define reservas se houver excedente)
    chosen.sort(key=lambda mp: (mp.confirmed_at or mp.updated_at, mp.id))
    return chosen


def _snapshot_player(db: Session, group: FootballGroup, mp: MatchPlayer, as_goalkeeper: bool) -> dict:
    member = mp.member
    field = ratings.ensure_rating(db, group, member, RatingKind.FIELD)
    snap = {
        "member_id": member.id,
        "name": member.display_name,
        "is_goalkeeper": as_goalkeeper,
        "field": {
            "average": field.average,
            "stars": field.stars,
            "attributes": dict(field.attributes),
            "provisional": field.is_provisional,
        },
        "skill": member.special_skill.code if member.special_skill else None,
        "skill_name": member.special_skill.name if member.special_skill else None,
    }
    if as_goalkeeper:
        gk = ratings.ensure_rating(db, group, member, RatingKind.GOALKEEPER)
        snap["goalkeeper"] = {"average": gk.average, "stars": gk.stars, "provisional": gk.is_provisional}
    return snap


def _field_player(snap: dict) -> D.DrawPlayer:
    f = snap["field"]
    return D.DrawPlayer(
        id=snap["member_id"], average=f["average"], stars=R.stars_of(f.get("stars", f.get("tier"))),
        attributes=f["attributes"],
        skill=snap["skill"], provisional=f["provisional"],
    )


def _goalkeeper(snap: dict) -> D.DrawGoalkeeper:
    g = snap["goalkeeper"]
    return D.DrawGoalkeeper(
        id=snap["member_id"], average=g["average"], stars=R.stars_of(g.get("stars", g.get("tier"))),
        provisional=g["provisional"],
        field_profile=_field_player(snap),
    )


def create_draw(db: Session, group: FootballGroup, match: Match, data: DrawCreate, actor: User) -> Draw:
    if match.status not in (MatchStatus.PUBLISHED, MatchStatus.FINISHED):
        raise BusinessRuleError("O sorteio exige uma pelada publicada", code="invalid_status")
    eligible = _eligible(db, match, data)
    gk_per_team = data.goalkeepers_per_team if data.goalkeepers_per_team is not None else match.goalkeepers_per_team
    field_per_team = data.field_players_per_team or match.field_players_per_team
    num_teams = data.num_teams or match.num_teams or 2

    snapshots = {
        mp.member_id: _snapshot_player(db, group, mp, as_goalkeeper=mp.is_goalkeeper and gk_per_team > 0)
        for mp in eligible
    }
    field = [_field_player(s) for s in snapshots.values() if not s["is_goalkeeper"]]
    gks = [_goalkeeper(s) for s in snapshots.values() if s["is_goalkeeper"]]

    previous = db.scalars(select(Draw).where(Draw.match_id == match.id)).all()
    weights = {**group.draw_weights, **(data.weights or {})}
    config = D.DrawConfig(
        num_teams=num_teams,
        field_per_team=field_per_team,
        goalkeepers_per_team=gk_per_team,
        weights=weights,
        time_limit_seconds=settings.draw_time_limit_seconds,
        seed=data.seed,
        avoid_signatures={d.signature for d in previous} if data.avoid_previous else set(),
    )
    try:
        result = D.run_draw(field, gks, config)
    except D.DrawError as exc:
        raise BusinessRuleError(str(exc), code="draw_invalid") from None
    reserves = set(result.reserves)
    unconfirmed = level_quiz.unconfirmed_count(db, [mp.member for mp in eligible if mp.member_id not in reserves])
    if unconfirmed:
        result.warnings.append(
            f"{unconfirmed} jogador(es) com nível ainda não confirmado (quiz pendente ou aguardando o admin)"
        )

    sequence = (db.scalar(select(func.max(Draw.sequence)).where(Draw.match_id == match.id)) or 0) + 1
    draw = Draw(
        match_id=match.id,
        sequence=sequence,
        status=DrawStatus.DRAFT,
        params={
            "num_teams": num_teams,
            "field_players_per_team": field_per_team,
            "goalkeepers_per_team": gk_per_team,
            "weights": weights,
            "eligible_member_ids": [mp.member_id for mp in eligible],
            "unavailable_member_ids": data.unavailable_member_ids,
            "players": {str(k): v for k, v in snapshots.items()},
            "candidates_evaluated": result.candidates_evaluated,
            "time_limit_seconds": config.time_limit_seconds,
        },
        score=result.score,
        metrics=result.metrics,
        warnings=result.warnings,
        reserves=result.reserves,
        signature=result.signature,
        seed=result.seed,
        fallback_used=result.fallback_used,
        created_by_id=actor.id,
    )
    db.add(draw)
    db.flush()
    for team_result in result.teams:
        team = Team(draw_id=draw.id, index=team_result.index, name=TEAM_NAMES[team_result.index],
                    stats=team_result.stats)
        db.add(team)
        db.flush()
        for mid in team_result.goalkeeper_ids:
            db.add(TeamPlayer(team_id=team.id, draw_id=draw.id, member_id=mid, is_goalkeeper=True))
        for mid in team_result.field_ids:
            db.add(TeamPlayer(team_id=team.id, draw_id=draw.id, member_id=mid, is_goalkeeper=False))
    audit.record(db, action="draw.executed", entity_type="draw", entity_id=draw.id, actor_user_id=actor.id,
                 group_id=group.id, after={"sequence": sequence, "score": result.score, "seed": result.seed})
    if sequence == 1:
        notifications.notify(
            db, _participant_user_ids(db, match), NotificationType.DRAW_DONE, f"Sorteio realizado: {match.name}",
            "Os times foram sorteados e aguardam publicação pelo administrador.",
            group_id=group.id, match_id=match.id,
        )
    db.commit()
    db.refresh(draw)
    return draw


def _participant_user_ids(db: Session, match: Match) -> list[int]:
    return list(
        db.scalars(
            select(GroupMember.user_id)
            .join(MatchPlayer, MatchPlayer.member_id == GroupMember.id)
            .where(
                MatchPlayer.match_id == match.id,
                MatchPlayer.status.in_([AttendanceStatus.CONFIRMED, AttendanceStatus.PRESENT]),
                GroupMember.status == MemberStatus.ACTIVE,  # contratados não acessam o grupo
            )
        ).all()
    )


def draw_out(draw: Draw) -> dict:
    snaps = draw.params.get("players", {})
    teams = []
    for team in draw.teams:
        players = []
        for tp in sorted(team.players, key=lambda p: (not p.is_goalkeeper, p.member.display_name)):
            snap = snaps.get(str(tp.member_id), {})
            src = snap.get("goalkeeper") if tp.is_goalkeeper and snap.get("goalkeeper") else snap.get("field", {})
            players.append(
                {
                    "member_id": tp.member_id,
                    "name": tp.member.display_name,
                    "photo_url": tp.member.user.photo_url,
                    "is_goalkeeper": tp.is_goalkeeper,
                    "stars": R.stars_of(src.get("stars", src.get("tier"))),
                    "average_display": round(src.get("average", 0.0) + 1e-9, 1),
                    "special_skill": snap.get("skill_name"),
                    "is_provisional": src.get("provisional", False),
                }
            )
        teams.append({"index": team.index, "name": team.name, "stats": team.stats, "players": players})
    public_params = {k: v for k, v in draw.params.items() if k != "players"}
    return {
        "id": draw.id,
        "match_id": draw.match_id,
        "sequence": draw.sequence,
        "status": draw.status,
        "params": public_params,
        "score": draw.score,
        "metrics": draw.metrics,
        "warnings": draw.warnings,
        "reserves": draw.reserves,
        "seed": draw.seed,
        "fallback_used": draw.fallback_used,
        "manually_adjusted": draw.manually_adjusted,
        "created_at": draw.created_at,
        "published_at": draw.published_at,
        "teams": teams,
    }


def summary_out(draw: Draw) -> dict:
    return {
        "id": draw.id,
        "sequence": draw.sequence,
        "status": draw.status,
        "score": draw.score,
        "average_difference": (draw.metrics or {}).get("average_difference"),
        "manually_adjusted": draw.manually_adjusted,
        "created_at": draw.created_at,
        "published_at": draw.published_at,
    }


def list_draws(db: Session, match: Match, *, include_unpublished: bool) -> list[Draw]:
    stmt = select(Draw).where(Draw.match_id == match.id)
    if not include_unpublished:
        stmt = stmt.where(Draw.status == DrawStatus.PUBLISHED)
    return list(db.scalars(stmt.order_by(Draw.sequence.desc())).all())


def get_draw(db: Session, match: Match, draw_id: int) -> Draw:
    draw = db.get(Draw, draw_id)
    if draw is None or draw.match_id != match.id:
        raise NotFound("Sorteio não encontrado")
    return draw


def compare(base: Draw, other: Draw) -> dict:
    def team_of(d: Draw) -> dict[int, frozenset]:
        mapping = {}
        for team in d.teams:
            members = frozenset(tp.member_id for tp in team.players)
            for mid in members:
                mapping[mid] = members
        return mapping

    a, b = team_of(base), team_of(other)
    changed = sum(1 for mid in a if mid in b and (a[mid] - {mid}) != (b[mid] - {mid}))
    avg_a = (base.metrics or {}).get("average_difference")
    avg_b = (other.metrics or {}).get("average_difference")
    return {
        "base": summary_out(base),
        "other": summary_out(other),
        "score_delta": round(other.score - base.score, 6),
        "average_difference_delta": round(avg_b - avg_a, 4) if avg_a is not None and avg_b is not None else None,
        "players_changed_team": changed,
        "same_composition": base.signature == other.signature,
    }


def _recompute(draw: Draw) -> None:
    snaps = draw.params["players"]
    weights = draw.params["weights"]
    field_teams: list[list[D.DrawPlayer]] = []
    gk_teams: list[list[D.DrawGoalkeeper]] = []
    for team in draw.teams:
        field_teams.append([_field_player(snaps[str(tp.member_id)]) for tp in team.players if not tp.is_goalkeeper])
        gk_teams.append([_goalkeeper(snaps[str(tp.member_id)]) for tp in team.players if tp.is_goalkeeper])
    if any(not t for t in field_teams):
        raise BusinessRuleError("Todo time precisa de ao menos um jogador de linha", code="empty_team")
    score, metrics = D.evaluate(field_teams, weights)
    gk_avgs = [sum(g.average for g in t) / len(t) for t in gk_teams if t]
    metrics["goalkeeper_difference"] = round(max(gk_avgs) - min(gk_avgs), 4) if len(gk_avgs) > 1 else 0.0
    metrics["weights"] = weights
    for team, f, g in zip(draw.teams, field_teams, gk_teams, strict=True):
        team.stats = D.team_stats(f, g)
    draw.score = round(score, 6)
    draw.metrics = metrics
    draw.signature = D.signature_of([[p.id for p in t] for t in field_teams])


def adjust(db: Session, group: FootballGroup, match: Match, draw: Draw, moves: list[PlayerMove], reason, actor: User):
    if not group.allow_manual_team_adjust:
        raise BusinessRuleError("Ajuste manual desabilitado neste grupo", code="adjust_disabled")
    if draw.status != DrawStatus.DRAFT:
        raise BusinessRuleError("Apenas sorteios não publicados podem ser ajustados", code="invalid_status")
    teams_by_index = {t.index: t for t in draw.teams}
    before = {t.index: sorted(tp.member_id for tp in t.players) for t in draw.teams}
    for move in moves:
        if move.to_team_index not in teams_by_index:
            raise BusinessRuleError(f"Time {move.to_team_index} não existe", code="invalid_team")
        tp = db.scalar(select(TeamPlayer).where(TeamPlayer.draw_id == draw.id, TeamPlayer.member_id == move.member_id))
        if tp is None:
            raise BusinessRuleError(f"Jogador {move.member_id} não está neste sorteio", code="not_in_draw")
        source = next(t for t in draw.teams if t.id == tp.team_id)
        target = teams_by_index[move.to_team_index]
        if source.id != target.id:
            source.players.remove(tp)
            tp.team_id = target.id
            target.players.append(tp)
    db.flush()
    _recompute(draw)
    draw.manually_adjusted = True
    after = {t.index: sorted(tp.member_id for tp in t.players) for t in draw.teams}
    audit.record(db, action="draw.adjusted", entity_type="draw", entity_id=draw.id, actor_user_id=actor.id,
                 group_id=group.id, before={"teams": before}, after={"teams": after, "score": draw.score},
                 reason=reason)
    db.commit()
    db.refresh(draw)
    return draw


def publish(db: Session, group: FootballGroup, match: Match, draw: Draw, actor: User) -> Draw:
    """Publicação atômica: um único sorteio publicado por pelada."""
    db.scalar(select(Match).where(Match.id == match.id).with_for_update())
    if draw.status == DrawStatus.PUBLISHED:
        return draw
    if draw.status != DrawStatus.DRAFT:
        raise BusinessRuleError("Sorteio descartado não pode ser publicado", code="invalid_status")
    for other in db.scalars(
        select(Draw).where(Draw.match_id == match.id, Draw.status == DrawStatus.PUBLISHED, Draw.id != draw.id)
    ).all():
        other.status = DrawStatus.DISCARDED
    draw.status = DrawStatus.PUBLISHED
    draw.published_at = utcnow()
    audit.record(db, action="draw.published", entity_type="draw", entity_id=draw.id, actor_user_id=actor.id,
                 group_id=group.id, after={"sequence": draw.sequence, "score": draw.score})
    notifications.notify(
        db, _participant_user_ids(db, match), NotificationType.TEAMS_PUBLISHED, f"Times publicados: {match.name}",
        "Confira em qual time você vai jogar.", group_id=group.id, match_id=match.id,
    )
    db.commit()
    db.refresh(draw)
    return draw


def recent_draws(db: Session, group_id: int, limit: int = 5) -> list[Draw]:
    return list(
        db.scalars(
            select(Draw).join(Match, Match.id == Draw.match_id).where(Match.group_id == group_id)
            .order_by(Draw.created_at.desc()).limit(limit)
        ).all()
    )
