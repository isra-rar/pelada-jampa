"""Três níveis: Super-Admin (cadastra espaços e define os DEs), DE (dono do espaço: cria as peladas na agenda e
define os administradores) e Admin (do grupo). Pelada marcada por grupo sem vínculo fica com horário pendente."""

from datetime import timedelta

from app.core.db import SessionLocal
from app.services.users import ensure_super_admin
from tests.conftest import T0, match_payload
from tests.test_api_flows import G, setup_group
from tests.test_venues import V, _venue, root


def _login(api, email: str, password: str) -> dict:
    r = api.c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}", "_email": email}


def test_super_admin_creates_venues_and_manages_owners(apiclient):
    api = apiclient
    su = root(api)
    dono = api.user("Dono")
    api.call("POST", V, dono, 405, json={"name": "Arena Própria"})  # DE não cadastra espaço sozinho
    api.get("/admin/venues", dono, 403)

    row = api.post("/admin/venues", su, 201, json={"name": "Arena Gestão", "owner_name": "Dono",
                                                   "owner_email": dono["_email"]})
    assert [o["email"] for o in row["owners"]] == [dono["_email"]] and row["owners"][0]["claimed"]
    assert api.get(V, dono)[0]["my_role"] == "owner"

    socio = api.post(f"/admin/venues/{row['id']}/owners", su, 201, json={"name": "Sócio",
                                                                          "email": "socio@example.com"})
    assert len(socio["owners"]) == 2 and not socio["owners"][1]["claimed"]
    first = socio["owners"][0]["staff_id"]
    api.call("DELETE", f"/admin/venues/{row['id']}/owners/{first}", su)
    err = api.call("DELETE", f"/admin/venues/{row['id']}/owners/{socio['owners'][1]['staff_id']}", su, 422)
    assert err["error"]["code"] == "last_owner"
    api.get(f"{V}/{row['id']}", dono, 404)  # deixou de ser dono

    found = api.get("/admin/venues", su, params={"search": "sócio"})["items"]
    assert [v["id"] for v in found] == [row["id"]]
    off = api.patch(f"/admin/venues/{row['id']}", su, json={"is_active": False})
    assert off["is_active"] is False


def test_de_creates_weekly_pelada_with_admin(apiclient, clock):
    api = apiclient
    dono = api.user("Dono")
    venue, court = _venue(api, dono)
    link = api.post(f"{V}/{venue['id']}/peladas", dono, 201, json={
        "name": "Terça 20h", "court_id": court["id"], "first_starts_at": (T0 + timedelta(days=1)).isoformat(),
        "duration_minutes": 60, "slots": 14, "price": "15.00", "admin_name": "Chefe",
        "admin_email": "chefe@example.com"})
    assert link["origin"] == "venue" and link["status"] == "active"
    admin = api.user("Chefe")  # cria a conta com o e-mail indicado pelo DE
    assert api.get(f"{G}/{link['group_id']}", admin)["my_role"] == "admin"
    matches = api.get(f"{G}/{link['group_id']}/matches", admin)["items"]
    assert len(matches) == 4 and {m["booking_status"] for m in matches} == {"confirmed"}
    api.get(f"{G}/{link['group_id']}", dono, 404)  # o DE não entra no grupo


def test_unlinked_group_booking_is_pending_until_de_confirms(apiclient, clock):
    api = apiclient
    dono = api.user("Dono")
    venue, court = _venue(api, dono)
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    assert venue["id"] in [v["id"] for v in api.get(f"{V}/directory", admin)]
    free = api.get(f"{G}/{gid}/venues/{venue['id']}/calendar", admin, params={"start": "2026-11-04", "days": 1})
    assert free["schedule"][0]["courts"][0]["free"]

    match = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(location=None, court_id=court["id"]))
    assert match["booking_status"] == "pending"
    api.post(f"/matches/{match['id']}/confirm", players[0], json={})  # jogadores confirmam normalmente
    pending = api.get(f"{V}/{venue['id']}/bookings", dono)
    assert pending[0]["match_id"] == match["id"] and pending[0]["admins"][0]["email"] == admin["_email"]

    # horário pendente ocupa a agenda e não gera aluguel
    clock.advance(days=2, hours=1)
    assert api.get(f"{V}/{venue['id']}/rent", dono, params={"month": "2026-11"})["items"] == []
    api.post(f"{V}/{venue['id']}/bookings/{match['id']}/confirm", dono)
    assert api.get(f"/matches/{match['id']}", admin)["booking_status"] == "confirmed"
    assert api.get(f"{G}/{gid}/venue", admin)["status"] == "active"  # confirmar vinculou o grupo
    assert api.get(f"{V}/{venue['id']}/rent", dono, params={"month": "2026-11"})["items"][0]["amount"] == "180.00"


def test_de_rejects_booking_and_match_keeps_going(apiclient):
    api = apiclient
    dono = api.user("Dono")
    venue, court = _venue(api, dono)
    admin, group, _ = setup_group(api, 1)
    match = api.post(f"{G}/{group['id']}/matches", admin, 201,
                     json=match_payload(location=None, court_id=court["id"]))
    api.post(f"{V}/{venue['id']}/bookings/{match['id']}/reject", dono, json={"reason": "Torneio"})
    after = api.get(f"/matches/{match['id']}", admin)
    assert after["booking_status"] == "rejected" and after["court_id"] is None and after["status"] == "published"
    assert api.get(f"{V}/{venue['id']}/bookings", dono) == []


def test_ensure_super_admin_creates_account_and_rotates_password(apiclient):
    api = apiclient
    with SessionLocal() as s:
        user, password, created = ensure_super_admin(s, "Chefe@Example.com", "Chefe")
    assert created and len(password) >= 16 and user.email == "chefe@example.com"
    first = _login(api, "chefe@example.com", password)
    assert api.get("/me", first)["is_platform_admin"] is True
    with SessionLocal() as s:
        _, new_password, created = ensure_super_admin(s, "chefe@example.com", "Chefe")
    assert not created and new_password != password
    r = api.c.post("/api/v1/auth/login", json={"email": "chefe@example.com", "password": password})
    assert r.status_code == 401
    _login(api, "chefe@example.com", new_password)
