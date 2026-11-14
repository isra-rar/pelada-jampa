"""Uma pelada por vez: a próxima só abre confirmações quando a anterior encerra."""

from datetime import timedelta

from tests.conftest import T0, match_payload
from tests.test_api_flows import G, setup_group

PUB = "/public/guest"


def test_next_match_locked_until_current_finishes(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    first = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload())
    later = (T0 + timedelta(days=9)).isoformat()
    second = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(starts_at=later))

    assert first["opens_after_match_id"] is None
    assert second["opens_after_match_id"] == first["id"]
    # aviso de "nova pelada" só da primeira; a segunda avisa quando abrir
    titles = [n["title"] for n in api.get("/notifications", players[0])["items"]]
    assert titles.count("Nova pelada: Pelada de quinta") == 1

    api.post(f"/matches/{first['id']}/confirm", players[0], json={})
    err = api.post(f"/matches/{second['id']}/confirm", players[0], 422, json={})
    assert err["error"]["code"] == "match_locked" and "encerrar" in err["error"]["message"]
    token = api.get(f"/matches/{second['id']}/guest-link", players[0])["token"]
    prev = api.get(f"{PUB}/link/{token}", {})
    assert prev["can_join"] is False and "encerrar" in prev["closed_reason"]

    # encerra a primeira: a segunda abre e quem não respondeu é avisado
    clock.set(T0 + timedelta(days=2, hours=2))
    api.post(f"/matches/{first['id']}/presence", admin, json={"present": [players[0]["_member_id"]]})
    assert api.get(f"/matches/{second['id']}", players[1])["opens_after_match_id"] is None
    assert any(n["title"].startswith("Confirmações abertas")
               for n in api.get("/notifications", players[1])["items"])
    api.post(f"/matches/{second['id']}/confirm", players[1], json={})


def test_cancelling_current_opens_next(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    gid = group["id"]
    first = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload())
    second = api.post(f"{G}/{gid}/matches", admin, 201,
                      json=match_payload(starts_at=(T0 + timedelta(days=9)).isoformat()))
    api.post(f"/matches/{first['id']}/cancel", admin, json={"reason": "chuva"})
    api.post(f"/matches/{second['id']}/confirm", players[0], json={})


def test_pending_evaluation_warns_but_does_not_block(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    first = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload())
    second = api.post(f"{G}/{gid}/matches", admin, 201,
                      json=match_payload(starts_at=(T0 + timedelta(days=9)).isoformat()))
    for p in players:
        api.post(f"/matches/{first['id']}/confirm", p, json={})
    clock.set(T0 + timedelta(days=2, hours=2))
    api.post(f"/matches/{first['id']}/presence", admin,
             json={"present": [p["_member_id"] for p in players]})

    pending = api.get(f"/matches/{second['id']}/my-pending-evaluations", players[0])
    assert len(pending) == 1 and pending[0]["match_id"] == first["id"] and pending[0]["remaining"] == 1
    # só avisa: a confirmação na próxima segue liberada
    api.post(f"/matches/{second['id']}/confirm", players[0], json={})
