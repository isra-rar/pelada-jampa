"""Juiz na pelada e comércio de goleiros e juízes (anúncio, busca, contratação e rateio)."""

from datetime import timedelta

from sqlalchemy import select

from app.models import Notification, Payment
from app.services import pros as pros_service
from tests.conftest import T0, match_payload
from tests.test_api_flows import G, setup_group

# T0 = segunda 02/11/2026 12:00 UTC; match_payload começa 2 dias depois: quarta 04/11, 09:00 em Brasília (90 min)
MATCH_DAY = "2026-11-04"
WEDNESDAY = 2


def _pro(api, name="Paredao", **overrides):
    h = api.user(name)
    body = {
        "is_goalkeeper": True, "goalkeeper_price": "50.00", "is_referee": True, "referee_price": "40.00",
        "description": "Goleiro de society, 10 anos de várzea.", "city": "João Pessoa", "neighborhood": "Bessa",
        "modalities": ["society"], "contact_phone": "83 99999-0000",
        "availability": [{"weekday": WEDNESDAY, "start_time": "08:00", "end_time": "12:00"}],
    }
    body.update(overrides)
    profile = api.put("/pros/me", h, json=body)
    return h, profile


# ------------------------------------------------------------------ juiz voluntário (membro do grupo)


def test_member_referee_slot_outside_player_slots(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 4)
    gid = group["id"]
    mid = api.post(f"{G}/{gid}/matches", admin, 201,
                   json=match_payload(slots=3, referee_slots=1, price="10.00"))["id"]
    ref = api.post(f"/matches/{mid}/confirm", players[0], json={"as_referee": True})["participation"]
    assert ref["status"] == "confirmed" and ref["is_referee"] is True
    for p in players[1:]:
        api.post(f"/matches/{mid}/confirm", p, json={})
    m = api.get(f"/matches/{mid}", admin)
    assert m["referee_slots"] == 1
    assert m["counts"]["referees"] == 1 and m["counts"]["confirmed"] == 3 and m["counts"]["available_slots"] == 0
    r = api.post(f"/matches/{mid}/confirm", admin, 422, json={"as_referee": True})
    assert r["error"]["code"] == "referee_full"
    api.post(f"/matches/{mid}/confirm", admin, 422, json={"as_referee": True, "as_goalkeeper": True})

    draw = api.post(f"/matches/{mid}/draws", admin, 201, json={"num_teams": 2, "seed": 1})
    in_teams = {pl["member_id"] for t in draw["teams"] for pl in t["players"]}
    assert players[0]["_member_id"] not in in_teams

    clock.set(T0 + timedelta(days=2, hours=2))
    present = [p["_member_id"] for p in players]
    api.post(f"/matches/{mid}/presence", admin, json={"present": present})
    charged = {p["member_id"] for p in api.get(f"{G}/{gid}/payments", admin)["items"]}
    assert players[0]["_member_id"] not in charged and len(charged) == 3  # juiz não paga
    targets = api.get(f"/matches/{mid}/evaluations/targets", players[1])
    names = {t["name"] for t in targets["targets"]}
    assert "Jogador0" not in names  # juiz não é avaliado


# ------------------------------------------------------------------ anúncio e busca


def test_pro_profile_and_search(apiclient):
    api = apiclient
    me = api.user("Curioso")
    api.get("/pros/me", me, 404)
    r = api.put("/pros/me", me, 422, json={"is_goalkeeper": True})  # sem valor
    assert "valor" in str(r).lower()
    api.put("/pros/me", me, 422, json={"is_referee": True, "referee_price": "30",
                                        "availability": [{"weekday": 1, "start_time": "10:00", "end_time": "09:00"}]})

    _, gk = _pro(api)
    assert gk["is_goalkeeper"] and gk["contact_phone"] == "83 99999-0000" and len(gk["availability"]) == 1
    _pro(api, "Apito", is_goalkeeper=False, goalkeeper_price=None, referee_price="35.00",
         availability=[{"date": MATCH_DAY, "start_time": "08:00", "end_time": "11:00"}])

    found = api.get("/public/pros", {}, params={"role": "goalkeeper"})
    assert found["total"] == 1 and found["items"][0]["name"] == "Paredao"
    assert "contact_phone" not in found["items"][0]
    refs = api.get("/public/pros", {}, params={"role": "referee"})["items"]
    assert [p["name"] for p in refs] == ["Apito", "Paredao"]  # mais barato primeiro
    window = {"role": "referee", "date": MATCH_DAY, "start": "09:00", "end": "10:30"}
    assert api.get("/public/pros", {}, params=window)["total"] == 2
    assert api.get("/public/pros", {}, params={**window, "start": "13:00", "end": "14:00"})["total"] == 0
    assert api.get("/public/pros", {}, params={"role": "referee", "max_price": "36"})["total"] == 1
    assert api.get("/public/pros", {}, params={"role": "goalkeeper", "city": "recife"})["total"] == 0
    assert api.get("/public/pros", {}, params={"role": "goalkeeper", "modality": "futsal"})["total"] == 0

    api.put("/pros/me", me, json={"is_referee": True, "referee_price": "30", "is_active": False})
    assert all(p["name"] != "Curioso" for p in api.get("/public/pros", {}, params={"role": "referee"})["items"])


# ------------------------------------------------------------------ contratação


def test_hire_goalkeeper_enters_match_and_cost_is_split(apiclient, clock, db):
    api = apiclient
    admin, group, players = setup_group(api, 4)
    gid = group["id"]
    pro_h, pro = _pro(api)
    mid = api.post(f"{G}/{gid}/matches", admin, 201,
                   json=match_payload(slots=6, goalkeepers_per_team=1, price="20.00"))["id"]
    for p in players:
        api.post(f"/matches/{mid}/confirm", p, json={})

    api.post(f"/matches/{mid}/hires", players[0], 403, json={"pro_id": pro["id"], "role": "goalkeeper"})
    hire = api.post(f"/matches/{mid}/hires", admin, 201, json={"pro_id": pro["id"], "role": "goalkeeper"})
    assert hire["status"] == "pending" and hire["price"] == "50.00" and hire["pro"]["phone"] is None
    assert hire["outside_availability"] is False and hire["over_max_duration"] is False
    api.post(f"/matches/{mid}/hires", admin, 409, json={"pro_id": pro["id"], "role": "referee"})

    received = api.get("/pros/me/hires", pro_h)["items"]
    assert len(received) == 1 and received[0]["group_name"] == "Pelada dos Amigos"
    assert any(n["type"] == "hire" for n in api.get("/notifications", pro_h)["items"])

    accepted = api.post(f"/hires/{hire['id']}/accept", pro_h, json={"note": "Fechado!"})
    assert accepted["status"] == "accepted" and accepted["organizer"]["name"] == "Admin"
    admin_view = api.get(f"/matches/{mid}/hires", admin)[0]
    assert admin_view["pro"]["phone"] == "83 99999-0000"

    # entrou na pelada como goleiro contratado, sem virar membro do grupo
    parts = {p["name"]: p for p in api.get(f"/matches/{mid}/players", admin)}
    assert parts["Paredao"]["status"] == "confirmed" and parts["Paredao"]["is_goalkeeper"]
    assert parts["Paredao"]["hired"] is True
    m = api.get(f"/matches/{mid}", admin)
    assert m["counts"]["hired"] == 1 and m["hire_cost"] == "50.00"
    api.get(f"{G}/{gid}", pro_h, 404)
    assert "Paredao" not in {mm["name"] for mm in api.get(f"{G}/{gid}/members", admin)}

    draw = api.post(f"/matches/{mid}/draws", admin, 201, json={"num_teams": 2, "seed": 3})
    gks = [pl["name"] for t in draw["teams"] for pl in t["players"] if pl["is_goalkeeper"]]
    assert "Paredao" in gks
    api.post(f"/matches/{mid}/draws/{draw['id']}/publish", admin)

    # presença: o contratado não paga; os 4 jogadores pagam 20 + 50/4
    clock.set(T0 + timedelta(days=2, hours=2))
    present = [p["_member_id"] for p in players] + [parts["Paredao"]["member_id"]]
    api.post(f"/matches/{mid}/presence", admin, json={"present": present})
    pays = db.scalars(select(Payment).where(Payment.match_id == mid)).all()
    assert len(pays) == 4 and {str(p.amount_due) for p in pays} == {"32.50"}
    assert "rateio" in (pays[0].notes or "")
    api.post(f"/matches/{mid}/games", admin, 201, json={
        "home_team_index": 0, "away_team_index": 1, "home_score": 1, "away_score": 0})
    ranking = {r["name"] for r in api.get(f"{G}/{gid}/stats/ranking", admin)["rows"]}
    assert "Jogador0" in ranking and "Paredao" not in ranking
    targets = {t["name"] for t in api.get(f"/matches/{mid}/evaluations/targets", players[0])["targets"]}
    assert "Paredao" not in targets


def test_hire_referee_opens_slot_and_conflicts(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    pro_h, pro = _pro(api)
    other_h, other = _pro(api, "Apitador", is_goalkeeper=False, goalkeeper_price=None)
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload())["id"]
    assert api.get(f"/matches/{mid}", admin)["referee_slots"] == 0
    r = api.post(f"/matches/{mid}/hires", admin, 422, json={"pro_id": pro["id"], "role": "goalkeeper"})
    assert r["error"]["code"] == "no_goalkeepers"  # pelada sem goleiro

    h1 = api.post(f"/matches/{mid}/hires", admin, 201, json={"pro_id": pro["id"], "role": "referee",
                                                              "price": "45.00", "message": "Topa?"})
    assert api.get(f"/matches/{mid}", admin)["referee_slots"] == 1
    h2 = api.post(f"/matches/{mid}/hires", admin, 201, json={"pro_id": other["id"], "role": "referee"})
    api.post(f"/hires/{h1['id']}/accept", pro_h, json={})
    r = api.post(f"/hires/{h2['id']}/accept", other_h, 422, json={})
    assert r["error"]["code"] == "referee_full"
    api.post(f"/hires/{h2['id']}/decline", other_h, json={"note": "Ocupado"})
    api.post(f"/hires/{h2['id']}/accept", other_h, 422, json={})

    # mesmo horário em outro grupo: o profissional já está ocupado
    a2 = api.user("OutroAdmin")
    g2 = api.post(G, a2, 201, json={"name": "Outra pelada"})
    mid2 = api.post(f"{G}/{g2['id']}/matches", a2, 201, json=match_payload(referee_slots=1))["id"]
    r = api.post(f"/matches/{mid2}/hires", a2, 422, json={"pro_id": pro["id"], "role": "referee"})
    assert r["error"]["code"] == "pro_unavailable"
    booking = api.get(f"/pros/{pro['id']}/bookable-matches", a2)
    assert booking[0]["match_id"] == mid2 and booking[0]["available"] is False

    # organizador cancela: o juiz sai da pelada e é avisado
    api.post(f"/matches/{mid}/hires/{h1['id']}/cancel", admin, json={"note": "Mudou o plano"})
    parts = {p["name"]: p for p in api.get(f"/matches/{mid}/players", admin)}
    assert parts["Paredao"]["status"] == "cancelled" and parts["Paredao"]["hired"] is False
    assert api.get(f"/matches/{mid}", admin)["counts"]["referees"] == 0
    assert any("cancelado" in n["title"].lower() for n in api.get("/notifications", pro_h)["items"])


def test_match_cancel_and_expiry_close_hires(apiclient, clock, db):
    api = apiclient
    admin, group, _ = setup_group(api, 1)
    gid = group["id"]
    pro_h, pro = _pro(api)
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(goalkeepers_per_team=1))["id"]
    h = api.post(f"/matches/{mid}/hires", admin, 201, json={"pro_id": pro["id"], "role": "goalkeeper"})
    api.post(f"/matches/{mid}/cancel", admin, json={"reason": "Chuva"})
    assert api.get("/pros/me/hires", pro_h)["items"][0]["status"] == "cancelled"
    titles = [n.title for n in db.scalars(select(Notification).where(Notification.type == "hire")).all()]
    assert any("Pelada cancelada" in t for t in titles)

    mid2 = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(goalkeepers_per_team=1))["id"]
    h2 = api.post(f"/matches/{mid2}/hires", admin, 201, json={"pro_id": pro["id"], "role": "goalkeeper"})
    clock.set(T0 + timedelta(days=2, minutes=5))  # a pelada começou sem resposta
    assert pros_service.expire_due(db) == 1
    assert {x["id"]: x["status"] for x in api.get("/pros/me/hires", pro_h)["items"]}[h2["id"]] == "expired"
    assert h["id"] != h2["id"]


# ------------------------------------------------------------------ rateio do custo total


def test_split_cost_field_plus_hires_divided_among_players(apiclient, clock, db):
    """Exemplo do produto: campo R$ 150 + 2 goleiros de R$ 50 + juiz de R$ 50 = R$ 300 ÷ 10 jogadores."""
    api = apiclient
    admin, group, players = setup_group(api, 10)
    gid = group["id"]
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(
        slots=12, field_players_per_team=5, goalkeepers_per_team=1, referee_slots=1, price="0",
        split_cost=True, field_cost="150.00"))["id"]
    for p in players:
        api.post(f"/matches/{mid}/confirm", p, json={})
    hired = []
    for name, role in (("Gol1", "goalkeeper"), ("Gol2", "goalkeeper"), ("Apito", "referee")):
        h, pro = _pro(api, name, goalkeeper_price="50.00", referee_price="50.00")
        hire = api.post(f"/matches/{mid}/hires", admin, 201, json={"pro_id": pro["id"], "role": role})
        api.post(f"/hires/{hire['id']}/accept", h, json={})
        hired.append(name)

    cost = api.get(f"/matches/{mid}", admin)["cost"]
    assert cost["split"] is True and cost["field"] == "150.00" and cost["goalkeepers"] == "100.00"
    assert cost["referees"] == "50.00" and cost["total"] == "300.00"
    assert cost["players"] == 10 and cost["per_player"] == "30.00"

    clock.set(T0 + timedelta(days=2, hours=2))
    parts = {p["name"]: p["member_id"] for p in api.get(f"/matches/{mid}/players", admin)}
    everyone = [p["_member_id"] for p in players] + [parts[n] for n in hired]
    api.post(f"/matches/{mid}/presence", admin, json={"present": everyone})
    pays = db.scalars(select(Payment).where(Payment.match_id == mid)).all()
    assert len(pays) == 10 and {str(p.amount_due) for p in pays} == {"30.00"}
    assert pays[0].notes == (
        "Rateio: campo R$ 150,00 + goleiros R$ 100,00 + juiz R$ 50,00 = R$ 300,00 ÷ 10 jogadores"
    )

    # correção: um jogador não foi; os outros 9 dividem os mesmos R$ 300 (arredondado para cima)
    api.post(f"/matches/{mid}/presence", admin,
             json={"present": everyone[1:], "absent": [everyone[0]]})
    db.expire_all()
    by_member = {p.member_id: p for p in db.scalars(select(Payment).where(Payment.match_id == mid)).all()}
    assert by_member[everyone[0]].status.value == "refunded"
    assert {str(by_member[m].amount_due) for m in everyone[1:10]} == {"33.34"}


def test_split_cost_uses_court_rent_and_counts_no_shows(apiclient, clock, db):
    from tests.test_venues import _linked_group, _venue

    api = apiclient
    owner = api.user("Dono")
    venue, court = _venue(api, owner)
    api.patch(f"/venues/{venue['id']}/courts/{court['id']}", owner, json={"hourly_price": "100.00"})
    admin, group, players, _ = _linked_group(api, owner, venue)
    gid = group["id"]
    api.patch(f"{G}/{gid}", admin, json={"charge_no_shows": True})
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(
        court_id=court["id"], slots=4, price="0", split_cost=True))["id"]
    m = api.get(f"/matches/{mid}", admin)
    assert m["field_cost"] == "150.00" and m["cost"]["field"] == "150.00"  # R$ 100/h × 90 min
    for p in players:
        api.post(f"/matches/{mid}/confirm", p, json={})
    assert api.get(f"/matches/{mid}", admin)["cost"]["per_player"] == "50.00"

    clock.set(T0 + timedelta(days=2, hours=2))
    ids = [p["_member_id"] for p in players]
    api.post(f"/matches/{mid}/presence", admin, json={"present": ids[:2], "absent": ids[2:]})
    pays = {p.member_id: p for p in db.scalars(select(Payment).where(Payment.match_id == mid)).all()}
    # quem confirmou e faltou também paga (regra do grupo) e entra na divisão: 150 ÷ 3
    assert {str(p.amount_due) for p in pays.values()} == {"50.00"} and len(pays) == 3
    assert "Falta" in (pays[ids[2]].notes or "")

    # pelada com valor fixo continua cobrando o valor por jogador
    fixed = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(
        starts_at=(T0 + timedelta(days=5)).isoformat(), price="20.00"))
    assert fixed["split_cost"] is False and fixed["cost"]["per_player"] == "20.00"
