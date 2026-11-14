"""Goleiros têm 2 vagas próprias, fora das vagas de linha (ex.: 20 de linha + 2 goleiros)."""

from tests.conftest import match_payload
from tests.test_api_flows import G, setup_group


def test_goalkeepers_do_not_take_field_slots(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 6)
    a, b, c, gk1, gk2, gk3 = players
    mid = api.post(f"{G}/{group['id']}/matches", admin, 201,
                   json=match_payload(slots=2, goalkeepers_per_team=1))["id"]

    for p in (a, b):
        api.post(f"/matches/{mid}/confirm", p, json={})
    api.post(f"/matches/{mid}/confirm", gk1, json={"as_goalkeeper": True})
    api.post(f"/matches/{mid}/confirm", gk2, json={"as_goalkeeper": True})
    counts = api.get(f"/matches/{mid}", admin)["counts"]
    assert counts["confirmed"] == 2 and counts["goalkeepers"] == 2 and counts["available_slots"] == 0

    # linha cheia: o próximo vai para a espera; o 3º goleiro é recusado (sem fila de goleiro)
    out = api.post(f"/matches/{mid}/confirm", c, json={})
    assert out["participation"]["status"] == "waitlist"
    err = api.post(f"/matches/{mid}/confirm", gk3, 422, json={"as_goalkeeper": True})
    assert err["error"]["code"] == "goalkeeper_full"
    # goleiro não volta para a linha cheia
    err = api.post(f"/matches/{mid}/confirm", gk1, 422, json={"as_goalkeeper": False})
    assert err["error"]["code"] == "slots_full"

    # goleiro desiste: a vaga de gol abre e quem está na espera de linha continua esperando
    api.post(f"/matches/{mid}/decline", gk2)
    assert api.get(f"/matches/{mid}", admin)["counts"]["goalkeepers"] == 1
    # quem estava na espera pode ir para o gol: sai da fila e confirma
    moved = api.post(f"/matches/{mid}/confirm", c, json={"as_goalkeeper": True})
    assert moved["participation"]["status"] == "confirmed" and moved["participation"]["is_goalkeeper"]


def test_match_without_goalkeeper_keeps_single_pool(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 3)
    mid = api.post(f"{G}/{group['id']}/matches", admin, 201,
                   json=match_payload(slots=2, goalkeepers_per_team=0))["id"]
    api.post(f"/matches/{mid}/confirm", players[0], json={"as_goalkeeper": True})
    api.post(f"/matches/{mid}/confirm", players[1], json={})
    out = api.post(f"/matches/{mid}/confirm", players[2], json={})
    assert out["participation"]["status"] == "waitlist"
    assert api.get(f"/matches/{mid}", admin)["counts"]["confirmed"] == 2
