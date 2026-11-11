import random

import pytest

from app.domain import draw as D
from app.domain.rating import FIELD_ATTRIBUTES, compute_stars

SKILLS = ["muralha", "foguete", "matador", "maestro", None]


def player(i: int, avg: float, skill: str | None = None, rng: random.Random | None = None) -> D.DrawPlayer:
    rng = rng or random.Random(i)
    attrs = {k: max(0.0, min(5.0, avg + rng.uniform(-0.8, 0.8))) for k in FIELD_ATTRIBUTES}
    return D.DrawPlayer(id=i, average=avg, stars=compute_stars(avg), attributes=attrs, skill=skill)


def roster(n: int, seed: int = 7) -> list[D.DrawPlayer]:
    rng = random.Random(seed)
    return [player(i, round(rng.uniform(1.2, 4.9), 2), rng.choice(SKILLS), rng) for i in range(1, n + 1)]


def team_of(result, pid):
    return next(t.index for t in result.teams if pid in t.field_ids or pid in t.goalkeeper_ids)


def test_every_player_in_exactly_one_team():
    players = roster(10)
    res = D.run_draw(players, [], D.DrawConfig(num_teams=2, field_per_team=5, goalkeepers_per_team=0, seed=1,
                                               time_limit_seconds=0.3))
    ids = [pid for t in res.teams for pid in t.field_ids]
    assert sorted(ids) == sorted(p.id for p in players)
    assert all(len(t.field_ids) == 5 for t in res.teams)


def test_five_star_players_are_split_between_teams():
    players = [player(1, 4.9), player(2, 4.8), player(3, 4.0), player(4, 3.9)] + [
        player(i, 2.5) for i in range(5, 11)
    ]
    for seed in range(10):
        res = D.run_draw(players, [], D.DrawConfig(num_teams=2, field_per_team=5, goalkeepers_per_team=0,
                                                   seed=seed, time_limit_seconds=0.2))
        assert team_of(res, 1) != team_of(res, 2), f"seed {seed}: os dois 5 estrelas no mesmo time"
        assert team_of(res, 3) != team_of(res, 4), f"seed {seed}: os dois 4 estrelas no mesmo time"


def test_balance_is_good_on_random_rosters():
    for seed in range(5):
        res = D.run_draw(roster(12, seed), [], D.DrawConfig(num_teams=2, field_per_team=6, goalkeepers_per_team=0,
                                                            seed=seed, time_limit_seconds=0.3))
        assert res.metrics["average_difference"] < 0.35


def test_three_teams_and_uneven_count():
    players = roster(14)
    res = D.run_draw(players, [], D.DrawConfig(num_teams=3, field_per_team=5, goalkeepers_per_team=0, seed=3,
                                               time_limit_seconds=0.3))
    sizes = sorted(len(t.field_ids) for t in res.teams)
    assert sizes == [4, 5, 5]
    assert any("incompleta" in w for w in res.warnings)


def test_extra_players_become_reserves_in_priority_order():
    players = roster(12)
    res = D.run_draw(players, [], D.DrawConfig(num_teams=2, field_per_team=5, goalkeepers_per_team=0, seed=1,
                                               time_limit_seconds=0.2))
    assert res.reserves == [11, 12]


def test_goalkeepers_distributed_separately_best_to_weakest_team():
    players = roster(10)
    gks = [D.DrawGoalkeeper(id=100, average=4.5, stars=5), D.DrawGoalkeeper(id=101, average=2.0, stars=2)]
    res = D.run_draw(players, gks, D.DrawConfig(num_teams=2, field_per_team=5, goalkeepers_per_team=1, seed=2,
                                                time_limit_seconds=0.2))
    assert all(len(t.goalkeeper_ids) == 1 for t in res.teams)
    weakest = min(res.teams, key=lambda t: t.stats["strength"])
    assert weakest.goalkeeper_ids == [100]
    # goleiros não entram na média de linha
    assert all(t.stats["players"] == 5 for t in res.teams)


def test_missing_goalkeepers_produce_warning():
    res = D.run_draw(roster(10), [D.DrawGoalkeeper(id=100, average=3, stars=3)],
                     D.DrawConfig(num_teams=2, field_per_team=5, goalkeepers_per_team=1, seed=1,
                                  time_limit_seconds=0.2))
    assert any("Goleiros insuficientes" in w for w in res.warnings)


def test_extra_goalkeeper_plays_on_field_when_profile_available():
    gk_field = player(200, 3.0)
    gks = [D.DrawGoalkeeper(id=100, average=4, stars=4), D.DrawGoalkeeper(id=101, average=3.5, stars=3),
           D.DrawGoalkeeper(id=200, average=2, stars=2, field_profile=gk_field)]
    res = D.run_draw(roster(9), gks, D.DrawConfig(num_teams=2, field_per_team=5, goalkeepers_per_team=1, seed=1,
                                                  time_limit_seconds=0.2))
    field_ids = [pid for t in res.teams for pid in t.field_ids]
    assert 200 in field_ids


def test_insufficient_players_raise():
    with pytest.raises(D.DrawError):
        D.run_draw([player(1, 3.0)], [], D.DrawConfig(num_teams=2, field_per_team=5, goalkeepers_per_team=0))


def test_duplicate_player_raises():
    p = player(1, 3.0)
    with pytest.raises(D.DrawError):
        D.run_draw([p, p, player(2, 3)], [], D.DrawConfig(num_teams=2, field_per_team=2, goalkeepers_per_team=0))


def test_seed_is_reproducible():
    cfg = dict(num_teams=2, field_per_team=5, goalkeepers_per_team=0, max_candidates=20, time_limit_seconds=5)
    a = D.run_draw(roster(10), [], D.DrawConfig(seed=42, **cfg))
    b = D.run_draw(roster(10), [], D.DrawConfig(seed=42, **cfg))
    assert a.signature == b.signature


def test_randomness_between_equivalent_compositions():
    players = [player(i, 3.0, None) for i in range(1, 11)]  # todos iguais: muitas soluções ótimas
    sigs = {
        D.run_draw(players, [], D.DrawConfig(num_teams=2, field_per_team=5, goalkeepers_per_team=0, seed=s,
                                             time_limit_seconds=0.1, max_candidates=30)).signature
        for s in range(8)
    }
    assert len(sigs) > 1


def test_avoid_previous_signature():
    players = [player(i, 3.0) for i in range(1, 11)]
    cfg = dict(num_teams=2, field_per_team=5, goalkeepers_per_team=0, time_limit_seconds=0.2, max_candidates=40)
    first = D.run_draw(players, [], D.DrawConfig(seed=1, **cfg))
    second = D.run_draw(players, [], D.DrawConfig(seed=1, avoid_signatures={first.signature}, **cfg))
    assert second.signature != first.signature


def test_skills_are_spread():
    players = [player(i, 3.0, "muralha" if i <= 4 else None) for i in range(1, 11)]
    res = D.run_draw(players, [], D.DrawConfig(num_teams=2, field_per_team=5, goalkeepers_per_team=0, seed=5,
                                               time_limit_seconds=0.3))
    assert [t.stats["skills"].get("muralha", 0) for t in res.teams] == [2, 2]


def test_time_limit_is_respected():
    import time

    start = time.monotonic()
    D.run_draw(roster(30), [], D.DrawConfig(num_teams=3, field_per_team=10, goalkeepers_per_team=0, seed=1,
                                            time_limit_seconds=0.3, max_candidates=10_000))
    assert time.monotonic() - start < 1.5
