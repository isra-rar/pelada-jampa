"""Jogo ao vivo: placar 0 x 0, cronômetro regressivo, gols lançados na hora e estatísticas só após o fim."""

from tests.conftest import match_payload
from tests.test_api_flows import G, setup_group
from tests.test_features_v2 import _match_with_teams


def _row(api, gid, headers, member_id):
    rows = api.get(f"{G}/{gid}/stats/ranking", headers)["rows"]
    return next((r for r in rows if r["member_id"] == member_id), None)


def test_live_game_flow(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 4)
    gid = group["id"]
    mid, teams = _match_with_teams(api, clock, admin, group, players)
    a1, a2 = teams[0]
    b1, _b2 = teams[1]
    sides = {"home_team_index": 0, "away_team_index": 1}

    api.post(f"/matches/{mid}/games/live", players[0], 403, json=sides)
    r = api.post(f"/matches/{mid}/games/live", admin, 422, json={"home_team_index": 0, "away_team_index": 0})
    assert r["error"]["code"] == "same_team"

    game = api.post(f"/matches/{mid}/games/live", admin, 201, json=sides)
    gid_game = game["id"]
    assert game["status"] == "live" and game["sequence"] == 1
    assert (game["home_score"], game["away_score"]) == (0, 0)
    assert game["duration_seconds"] == 600 and game["elapsed_seconds"] == 0 and game["started_at"]
    assert game["home_name"] == "Time A" and len(game["home_players"]) == 2

    r = api.post(f"/matches/{mid}/games/live", admin, 422, json=sides)
    assert r["error"]["code"] == "live_game_running"

    # gol aos 3:20 -> minuto 4, calculado pelo servidor
    clock.advance(minutes=3, seconds=20)
    api.post(f"/matches/{mid}/games/{gid_game}/goals", players[0], 403, json={"side": "home", "scorer_member_id": a1})
    r = api.post(f"/matches/{mid}/games/{gid_game}/goals", admin, 422, json={"side": "away", "scorer_member_id": a1})
    assert r["error"]["code"] == "invalid_scorer"
    game = api.post(f"/matches/{mid}/games/{gid_game}/goals", admin, 201,
                    json={"side": "home", "scorer_member_id": a1, "assist_member_id": a2})
    assert (game["home_score"], game["away_score"]) == (1, 0)
    assert game["goals"][0]["minute"] == 4 and game["goals"][0]["scorer_name"]

    clock.advance(minutes=2)
    game = api.post(f"/matches/{mid}/games/{gid_game}/goals", admin, 201, json={"side": "away", "scorer_member_id": b1})
    assert (game["home_score"], game["away_score"]) == (1, 1) and game["goals"][1]["minute"] == 6

    # gol lançado por engano é desfeito
    game = api.call("DELETE", f"/matches/{mid}/games/{gid_game}/goals/{game['goals'][1]['id']}", admin)
    assert (game["home_score"], game["away_score"]) == (1, 0) and len(game["goals"]) == 1
    api.call("DELETE", f"/matches/{mid}/games/{gid_game}/goals/999999", admin, 404)

    # jogo ao vivo não é editado pelo formulário completo e ainda não conta nas estatísticas
    r = api.put(f"/matches/{mid}/games/{gid_game}", admin, 422, json={**sides, "home_score": 5, "away_score": 0})
    assert r["error"]["code"] == "game_live"
    row = _row(api, gid, admin, a1)
    assert row is None or row["games"] == 0

    # tempo alterado durante o jogo
    game = api.patch(f"/matches/{mid}/games/{gid_game}/live", admin, json={"duration_minutes": 15})
    assert game["duration_seconds"] == 900
    api.patch(f"/matches/{mid}/games/{gid_game}/live", admin, 422, json={"duration_minutes": 0})

    clock.advance(minutes=12)
    listed = api.get(f"/matches/{mid}/games", players[1])
    assert listed[0]["status"] == "live" and listed[0]["elapsed_seconds"] == 3 * 60 + 20 + 2 * 60 + 12 * 60

    game = api.post(f"/matches/{mid}/games/{gid_game}/finish", admin)
    assert game["status"] == "finished" and game["ended_at"] and game["elapsed_seconds"] == 1040
    clock.advance(minutes=5)
    assert api.get(f"/matches/{mid}/games", admin)[0]["elapsed_seconds"] == 1040  # congelado no fim

    r = api.post(f"/matches/{mid}/games/{gid_game}/goals", admin, 422, json={"side": "home"})
    assert r["error"]["code"] == "game_not_live"
    api.post(f"/matches/{mid}/games/{gid_game}/finish", admin, 422)
    api.patch(f"/matches/{mid}/games/{gid_game}/live", admin, 422, json={"duration_minutes": 5})

    row = _row(api, gid, admin, a1)
    assert row["games"] == 1 and row["wins"] == 1 and row["goals"] == 1
    assert _row(api, gid, admin, a2)["assists"] == 1

    # encerrado, o jogo volta a ser editável e outro jogo ao vivo pode começar
    api.put(f"/matches/{mid}/games/{gid_game}", admin, json={**sides, "home_score": 2, "away_score": 0})
    nxt = api.post(f"/matches/{mid}/games/live", admin, 201, json={**sides, "duration_minutes": 7})
    assert nxt["sequence"] == 2 and nxt["duration_seconds"] == 420


def test_live_game_requires_started_match(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    mid = api.post(f"{G}/{group['id']}/matches", admin, 201, json=match_payload())["id"]
    for p in players:
        api.post(f"/matches/{mid}/confirm", p, json={})
    ids = [p["_member_id"] for p in players]
    r = api.post(f"/matches/{mid}/games/live", admin, 422,
                 json={"home_member_ids": ids[:1], "away_member_ids": ids[1:]})
    assert r["error"]["code"] == "too_early"
