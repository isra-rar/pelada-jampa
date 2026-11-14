"""Convidado que entra pelo link da pelada, sem login."""

from tests.conftest import match_payload
from tests.test_api_flows import G, setup_group

PUB = "/public/guest"


def test_guest_joins_by_link_without_login(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    beto, other = players
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(slots=4, price="20.00"))["id"]

    token = api.get(f"/matches/{mid}/guest-link", beto)["token"]
    assert len(token) == 8 and api.get(f"/matches/{mid}/guest-link", beto)["token"] == token
    shared = api.get(f"/matches/{mid}/share-links", beto)
    assert shared["guest_token"] == token and shared["invite_token"] == token  # mesmo código nas duas portas
    assert api.get(f"/public/invites/{token}", {})["group_id"] == group["id"]
    prev = api.get(f"{PUB}/link/{token}", {})
    assert prev["can_join"] and prev["invited_by_name"] == "Jogador0" and prev["match"]["available_slots"] == 4

    out = api.post(f"{PUB}/link/{token}/join", {}, 201, json={"name": "Zé Visitante", "stars": 3})
    view = out["view"]
    assert view["me"]["status"] == "confirmed" and view["me"]["invited_by_name"] == "Jogador0"
    assert any(p["is_me"] for p in view["participants"])
    assert view["payment"] is None or view["payment"]["payer_name"] == "Jogador0"

    # passe só lê; sem login nenhuma rota do grupo abre
    again = api.get(f"{PUB}/pass/{out['access_token']}", {})
    assert again["match"]["confirmed"] == 1
    api.get(f"/matches/{mid}", {}, 401)
    api.get(f"/matches/{mid}", {"Authorization": f"Bearer {out['access_token']}"}, 401)
    api.get(f"{PUB}/pass/{token}", {}, 404)  # link de convite não vale como passe

    # nível vai para o admin; quem compartilhou é avisado
    assert any(n["title"] == "Nível de convidado para confirmar" for n in api.get("/notifications", admin)["items"])
    assert any("pelo seu link" in n["body"] for n in api.get("/notifications", beto)["items"])

    # a cobrança entra nas pendências de quem compartilhou
    left = api.post(f"{PUB}/pass/{out['access_token']}/leave", {})
    assert left["me"]["status"] == "cancelled" and not left["can_leave"]


def test_link_rejects_garbage_and_non_published(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    api.get(f"{PUB}/link/lixo", {}, 404)
    mid = api.post(f"{G}/{group['id']}/matches", admin, 201, json=match_payload(publish=False))["id"]
    api.get(f"/matches/{mid}/guest-link", admin, 422)
