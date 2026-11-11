"""Convidados dos membros e lista de espera que aumenta a pelada."""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import GroupMember, User
from tests.conftest import T0, match_payload
from tests.test_api_flows import G, setup_group


def _guest(api, h, gid, name="Primo do Beto", stars=4, goalkeeper=False, expected=201):
    return api.post(f"{G}/{gid}/guests", h, expected, json={"name": name, "stars": stars, "goalkeeper": goalkeeper})


# ------------------------------------------------------------------ convidados


def test_member_invites_guest_and_admin_confirms_level(apiclient, db):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    beto, other = players

    guest = _guest(api, beto, gid, stars=4)
    assert guest["stars"] == 4 and guest["invited_by_member_id"] == beto["_member_id"] and guest["can_manage"]
    assert guest["level"]["status"] == "to_review" and guest["level"]["declared_stars"] == 4
    # não é conta: não aparece entre os membros nem nas contas da plataforma
    assert guest["member_id"] not in {m["id"] for m in api.get(f"{G}/{gid}/members", admin)}
    user = db.scalar(select(User).join(GroupMember, GroupMember.user_id == User.id)
                     .where(GroupMember.id == guest["member_id"]))
    assert user.email.endswith("@convidado.invalid") and user.password_hash is None

    # todos veem o convidado; o detalhe do nível só quem convidou e os admins
    seen = api.get(f"{G}/{gid}/guests", other)[0]
    assert seen["name"] == "Primo do Beto" and seen["stars"] == 4 and seen["level"] is None
    assert not seen["can_manage"]
    notes = api.get("/notifications", admin)["items"]
    assert any(n["title"] == "Nível de convidado para confirmar" for n in notes)
    assert api.get(f"{G}/{gid}/dashboard", admin)["levels_to_review"] == 1

    review = api.post(f"{G}/{gid}/members/{guest['member_id']}/level-review", admin, json={"stars": 3})
    assert review["rating"]["stars"] == 3 and review["self_assessment"]["status"] == "confirmed"
    api.post(f"{G}/{gid}/members/{guest['member_id']}/level-review", beto, 403, json={"stars": 5})

    # admin que convida já confirma
    by_admin = _guest(api, admin, gid, name="Goleiro Visitante", stars=5, goalkeeper=True)
    assert by_admin["level"]["status"] == "confirmed" and by_admin["is_goalkeeper"] and by_admin["stars"] == 5
    assert by_admin["level"]["kind"] == "goalkeeper"

    # validações e permissões
    _guest(api, beto, gid, stars=6, expected=422)
    api.patch(f"{G}/{gid}/guests/{guest['member_id']}", other, 403, json={"name": "Hack"})
    renamed = api.patch(f"{G}/{gid}/guests/{guest['member_id']}", beto, json={"name": "Primo Zé"})
    assert renamed["name"] == "Primo Zé"


def test_guest_in_match_and_charge_goes_to_inviter(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    beto, other = players
    guest = _guest(api, beto, gid)
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(slots=4, price="20.00"))["id"]

    api.post(f"/matches/{mid}/guests", other, 403, json={"member_id": guest["member_id"]})
    out = api.post(f"/matches/{mid}/guests", beto, json={"member_id": guest["member_id"]})
    assert out["participation"]["status"] == "confirmed"
    assert out["participation"]["invited_by_name"] == "Jogador0"
    listed = {p["member_id"]: p for p in api.get(f"/matches/{mid}/players", admin)}
    assert listed[guest["member_id"]]["invited_by_member_id"] == beto["_member_id"]

    api.post(f"/matches/{mid}/confirm", beto, json={})
    clock.set(T0 + timedelta(days=2, hours=2))
    api.post(f"/matches/{mid}/presence", admin,
             json={"present": [guest["member_id"], beto["_member_id"]], "absent": []})

    # a cobrança do convidado fica no nome dele, mas quem paga é quem convidou
    pays = api.get(f"{G}/{gid}/payments?match_id={mid}", admin)["items"]
    gp = next(p for p in pays if p["member_id"] == guest["member_id"])
    assert gp["payer_member_id"] == beto["_member_id"] and gp["payer_name"] == "Jogador0"
    mine = api.get(f"{G}/{gid}/payments", beto)["items"]
    assert {p["member_id"] for p in mine} == {beto["_member_id"], guest["member_id"]}
    debtors = api.get(f"{G}/{gid}/finance/debtors", admin)
    assert debtors == [d for d in debtors if d["member_id"] == beto["_member_id"]]
    assert debtors[0]["pending_count"] == 2 and debtors[0]["outstanding"] == "40.00"
    profile = api.get(f"{G}/{gid}/players/me", beto)
    assert profile["finance"]["pending_count"] == 2
    api.post(f"{G}/{gid}/payments/{gp['id']}/report", other, 403)
    assert api.post(f"{G}/{gid}/payments/{gp['id']}/report", beto)["reported_paid_at"]

    # convidado não entra no ranking
    ranking = api.get(f"{G}/{gid}/stats/ranking", admin)
    assert guest["member_id"] not in {r["member_id"] for r in ranking["rows"]}


def test_guest_waitlist_promotion_and_removal(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    beto = players[0]
    guest = _guest(api, beto, gid)
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(slots=2))["id"]
    api.post(f"/matches/{mid}/confirm", admin, json={})
    api.post(f"/matches/{mid}/confirm", players[1], json={})
    out = api.post(f"/matches/{mid}/guests", beto, json={"member_id": guest["member_id"]})
    assert out["participation"]["status"] == "waitlist" and out["participation"]["waitlist_position"] == 1
    # abriu vaga: o convidado é promovido direto e quem convidou é avisado
    api.post(f"/matches/{mid}/cancel-confirmation", players[1])
    listed = {p["member_id"]: p for p in api.get(f"/matches/{mid}/players", admin)}
    assert listed[guest["member_id"]]["status"] == "confirmed"
    assert any("convidado" in n["title"].lower() or "Primo" in n["body"] for n in api.get("/notifications", beto)["items"])

    removed = api.call("DELETE", f"/matches/{mid}/guests/{guest['member_id']}", beto)
    assert removed["participation"]["status"] == "cancelled"
    api.call("DELETE", f"{G}/{gid}/guests/{guest['member_id']}", beto, 204)
    assert api.get(f"{G}/{gid}/guests", beto) == []


# ------------------------------------------------------------------ lista que aumenta


def _expanding(**kw):
    # 4 vagas (2 times de 2), espera de 2; quando a espera lota vira 6 vagas e 3 times
    return match_payload(slots=4, field_players_per_team=2, goalkeepers_per_team=0, num_teams=2,
                         waitlist_limit=2, expand_when_waitlist_full=True, expanded_num_teams=3, **kw)


def test_full_waitlist_expands_match_once(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 8)
    gid = group["id"]
    m = api.post(f"{G}/{gid}/matches", admin, 201, json=_expanding())
    mid = m["id"]
    assert m["num_teams"] == 2 and m["waitlist_limit"] == 2 and m["expanded_at"] is None

    for p in players[:4]:
        assert api.post(f"/matches/{mid}/confirm", p, json={})["participation"]["status"] == "confirmed"
    assert api.post(f"/matches/{mid}/confirm", players[4], json={})["participation"]["status"] == "waitlist"
    r = api.post(f"/matches/{mid}/confirm", players[5], json={})
    # a espera lotou: a pelada aumentou e quem esperava foi confirmado
    assert r["participation"]["status"] == "confirmed"
    assert any("aumentou" in w for w in r["warnings"])
    m = api.get(f"/matches/{mid}", admin)
    assert m["slots"] == 6 and m["num_teams"] == 3 and m["expanded_at"]
    statuses = {p["member_id"]: p["status"] for p in api.get(f"/matches/{mid}/players", admin)}
    assert statuses[players[4]["_member_id"]] == "confirmed"
    assert any(n["title"].startswith("A pelada aumentou") for n in api.get("/notifications", players[0])["items"])

    # depois de aumentar: nova espera com o mesmo limite, sem aumentar de novo
    assert api.post(f"/matches/{mid}/confirm", players[6], json={})["participation"]["status"] == "waitlist"
    assert api.post(f"/matches/{mid}/confirm", players[7], json={})["participation"]["status"] == "waitlist"
    r = api.post(f"/matches/{mid}/confirm", admin, 422, json={})
    assert r["error"]["code"] == "waitlist_full"
    assert api.get(f"/matches/{mid}", admin)["slots"] == 6

    # o sorteio usa os times previstos
    draw = api.post(f"/matches/{mid}/draws", admin, 201, json={"seed": 1})
    assert len(draw["teams"]) == 3


def test_waitlist_limit_without_expansion_and_validation(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 4)
    gid = group["id"]
    mid = api.post(f"{G}/{gid}/matches", admin, 201,
                   json=match_payload(slots=2, waitlist_limit=1))["id"]
    api.post(f"/matches/{mid}/confirm", players[0], json={})
    api.post(f"/matches/{mid}/confirm", players[1], json={})
    assert api.post(f"/matches/{mid}/confirm", players[2], json={})["participation"]["status"] == "waitlist"
    api.post(f"/matches/{mid}/confirm", players[3], 422, json={})

    # validações: aumentar exige espera e mais times
    api.post(f"{G}/{gid}/matches", admin, 422, json=match_payload(expand_when_waitlist_full=True,
                                                                  expanded_num_teams=3))
    api.post(f"{G}/{gid}/matches", admin, 422, json=match_payload(num_teams=3, waitlist_limit=2,
                                                                  expand_when_waitlist_full=True,
                                                                  expanded_num_teams=3))
    # editar a pelada: tirar o limite da espera
    api.patch(f"/matches/{mid}", admin, json={"waitlist_limit": None})
    assert api.get(f"/matches/{mid}", admin)["waitlist_limit"] is None
    assert api.post(f"/matches/{mid}/confirm", players[3], json={})["participation"]["status"] == "waitlist"


def test_series_carries_team_rules(apiclient):
    api = apiclient
    admin, group, _ = setup_group(api, 0)
    gid = group["id"]
    payload = _expanding()
    series = api.post(f"{G}/{gid}/series", admin, 201, json={
        **{k: v for k, v in payload.items() if k not in ("starts_at", "evaluation_window_hours")},
        "recurrence": "weekly", "first_starts_at": payload["starts_at"], "generate_count": 1,
    })
    assert series["waitlist_limit"] == 2 and series["expand_when_waitlist_full"] is True
    matches = api.get(f"{G}/{gid}/matches", admin)
    items = matches["items"] if isinstance(matches, dict) else matches
    assert items[0]["expanded_num_teams"] == 3 and items[0]["num_teams"] == 2


def test_edit_match_deadlines(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    gid = group["id"]
    payload = match_payload()
    m = api.post(f"{G}/{gid}/matches", admin, 201, json=payload)
    starts = datetime.fromisoformat(m["starts_at"])
    # prazos novos: confirmar até 30 min antes e desistir até 5 h antes
    out = api.patch(f"/matches/{m['id']}", admin, json={
        "confirmation_deadline": (starts - timedelta(minutes=30)).isoformat(),
        "cancellation_deadline": (starts - timedelta(hours=5)).isoformat(),
    })
    assert datetime.fromisoformat(out["confirmation_deadline"]) == starts - timedelta(minutes=30)
    assert datetime.fromisoformat(out["cancellation_deadline"]) == starts - timedelta(hours=5)
    # desistência livre de novo
    assert api.patch(f"/matches/{m['id']}", admin, json={"cancellation_deadline": None})["cancellation_deadline"] is None
    # prazo depois do início não vale; jogador não edita
    api.patch(f"/matches/{m['id']}", admin, 422, json={
        "confirmation_deadline": (starts + timedelta(hours=1)).isoformat()})
    api.patch(f"/matches/{m['id']}", players[0], 403, json={"cancellation_deadline": None})
    # com o prazo estendido, quem chegou atrasado ainda confirma
    clock.set(starts - timedelta(hours=1))
    assert api.post(f"/matches/{m['id']}/confirm", players[0], json={})["participation"]["status"] == "confirmed"
