from datetime import timedelta

from sqlalchemy import select

from app.models import AuditLog, PlayerRatingHistory
from tests.conftest import T0, match_payload

G = "/groups"


def setup_group(api, n_players=4, **group_kw):
    admin = api.user("Admin")
    group = api.post(G, admin, 201, json={"name": "Pelada dos Amigos", **group_kw})
    players = []
    for i in range(n_players):
        h = api.user(f"Jogador{i}")
        joined = api.post(f"/invites/{group['invite_token']}/join", h)
        h["_member_id"] = joined["member_id"]
        players.append(h)
    admin["_member_id"] = group["my_member_id"]
    return admin, group, players


# ------------------------------------------------------------------ grupos / permissões


def test_group_creation_invite_and_isolation(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    assert group["my_role"] == "admin" and group["invite_token"]

    other = api.user("Intruso")
    api.get(f"{G}/{group['id']}", other, 404)
    api.get(f"{G}/{group['id']}/players", other, 404)

    player_view = api.get(f"{G}/{group['id']}", players[0])
    assert player_view["invite_token"] is None  # convite só para admins
    api.patch(f"{G}/{group['id']}", players[0], 403, json={"name": "Hack"})

    members = api.get(f"{G}/{group['id']}/members", players[0])
    assert {m["name"] for m in members} == {"Admin", "Jogador0"}
    assert all(m["email"] is None for m in members if m["name"] == "Admin")


def test_join_requires_approval_when_enabled(apiclient):
    api = apiclient
    admin = api.user("Admin")
    group = api.post(G, admin, 201, json={"name": "Fechado", "require_join_approval": True})
    p = api.user("Novato")
    joined = api.post(f"/invites/{group['invite_token']}/join", p)
    assert joined["status"] == "pending"
    api.get(f"{G}/{group['id']}", p, 404)
    api.patch(f"{G}/{group['id']}/members/{joined['member_id']}", admin, json={"status": "active"})
    assert api.get(f"{G}/{group['id']}", p)["my_role"] == "player"


def test_cannot_remove_last_admin(apiclient):
    api = apiclient
    admin, group, _ = setup_group(api, 0)
    r = api.patch(f"{G}/{group['id']}/members/{admin['_member_id']}", admin, 422, json={"role": "player"})
    assert r["error"]["code"] == "last_admin"


def test_manual_member_add_and_claim_account(apiclient):
    api = apiclient
    admin, group, _ = setup_group(api, 0)
    m = api.post(f"{G}/{group['id']}/members", admin, 201, json={"email": "ze@example.com", "name": "Zé"})
    assert m["status"] == "active"
    claimed = api.user("Ze")  # mesmo e-mail -> reivindica a conta
    assert api.get(f"{G}/{group['id']}", claimed)["my_member_id"] == m["id"]


def test_skill_choice_with_approval(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 1, skill_change_requires_approval=True)
    gid = group["id"]
    skills = api.get(f"{G}/{gid}/skills", players[0])
    assert len(skills) == 12
    muralha = next(s for s in skills if s["code"] == "muralha")
    m = api.put(f"{G}/{gid}/members/me/skill", players[0], json={"skill_id": muralha["id"]})
    assert m["special_skill"] is None and m["pending_skill"]["code"] == "muralha"
    m = api.post(f"{G}/{gid}/members/{m['id']}/skill-decision", admin, json={"approve": True})
    assert m["special_skill"]["code"] == "muralha"


def test_profile_starts_provisional_and_admin_can_set_initial(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    gid, mid = group["id"], players[0]["_member_id"]
    prof = api.get(f"{G}/{gid}/players/me", players[0])
    field = prof["ratings"][0]
    assert field["is_provisional"] and field["tier"] == "C" and field["average"] == 2.5
    api.put(f"{G}/{gid}/players/{mid}/provisional-rating", players[0], 403,
            json={"attributes": {k: 5 for k in field["attributes"]}})
    r = api.put(f"{G}/{gid}/players/{mid}/provisional-rating", admin,
                json={"attributes": {"finishing": 5, "passing": 4.5, "dribbling": 4.5, "defense": 4.5,
                                     "physical": 4.0}})
    assert r["average"] == 4.5 and r["tier"] == "S" and r["is_provisional"]


# ------------------------------------------------------------------ confirmação / lista de espera


def test_confirmation_slots_waitlist_and_auto_promotion(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 5)
    match = api.post(f"{G}/{group['id']}/matches", admin, 201, json=match_payload(slots=4))
    mid = match["id"]
    assert match["counts"]["pending"] == 6

    for p in players[:4]:
        assert api.post(f"/matches/{mid}/confirm", p, json={})["participation"]["status"] == "confirmed"
    r = api.post(f"/matches/{mid}/confirm", players[4], json={})
    assert r["participation"]["status"] == "waitlist" and r["participation"]["waitlist_position"] == 1
    r = api.post(f"/matches/{mid}/confirm", admin, json={})
    assert r["participation"]["waitlist_position"] == 2

    # idempotente
    assert api.post(f"/matches/{mid}/confirm", players[0], json={})["participation"]["status"] == "confirmed"

    api.post(f"/matches/{mid}/cancel-confirmation", players[0])
    parts = {p["name"]: p for p in api.get(f"/matches/{mid}/players", admin)}
    assert parts["Jogador4"]["status"] == "confirmed"  # promoção automática
    assert parts["Admin"]["waitlist_position"] == 1
    assert parts["Jogador0"]["status"] == "cancelled"

    notes = api.get("/notifications", players[4])["items"]
    assert any(n["type"] == "spot_available" for n in notes)


def test_waitlist_offer_requires_acceptance(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 3, waitlist_auto_promote=False)
    mid = api.post(f"{G}/{group['id']}/matches", admin, 201, json=match_payload(slots=2))["id"]
    api.post(f"/matches/{mid}/confirm", players[0], json={})
    api.post(f"/matches/{mid}/confirm", players[1], json={})
    api.post(f"/matches/{mid}/confirm", players[2], json={})
    api.post(f"/matches/{mid}/decline", players[0])  # desistência de confirmado = cancelado
    parts = {p["name"]: p for p in api.get(f"/matches/{mid}/players", admin)}
    assert parts["Jogador2"]["status"] == "waitlist" and parts["Jogador2"]["promotion_offered_at"]
    # a vaga está reservada para quem recebeu a oferta
    assert api.get(f"/matches/{mid}", admin)["counts"]["available_slots"] == 0
    r = api.post(f"/matches/{mid}/accept-spot", players[2])
    assert r["status"] == "confirmed"


def test_confirmation_deadline_and_late_cancellation(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    starts = T0 + timedelta(days=1)
    payload = match_payload(starts_at=starts.isoformat(),
                            cancellation_deadline=(starts - timedelta(hours=24)).isoformat())
    mid = api.post(f"{G}/{group['id']}/matches", admin, 201, json=payload)["id"]
    api.post(f"/matches/{mid}/confirm", players[0], json={})
    clock.set(starts - timedelta(hours=3))
    r = api.post(f"/matches/{mid}/cancel-confirmation", players[0])
    assert r["status"] == "cancelled" and r["late_cancellation"] is True
    clock.set(starts - timedelta(hours=1))
    r = api.post(f"/matches/{mid}/confirm", players[1], 422, json={})
    assert r["error"]["code"] == "confirmation_closed"


def test_admin_block_for_debt_is_audited(apiclient, db):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    mid = api.post(f"{G}/{group['id']}/matches", admin, 201, json=match_payload())["id"]
    pid = players[0]["_member_id"]
    api.patch(f"/matches/{mid}/players/{pid}", admin,
              json={"blocked": True, "blocked_reason": "Pendência", "reason": "inadimplente"})
    r = api.post(f"/matches/{mid}/confirm", players[0], 403, json={})
    assert r["error"]["code"] == "participation_blocked"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "participation.admin_update"))


# ------------------------------------------------------------------ fluxo completo


def play_match(api, clock, admin, group, players, *, price="20.00", slots=4):
    payload = match_payload(slots=slots, price=price)
    mid = api.post(f"{G}/{group['id']}/matches", admin, 201, json=payload)["id"]
    for p in players:
        api.post(f"/matches/{mid}/confirm", p, json={})
    match = api.get(f"/matches/{mid}", admin)
    clock.set(T0 + timedelta(days=2, hours=1, minutes=30))
    return mid, match


def test_full_flow_presence_evaluation_rating_payments(apiclient, clock, db):
    api = apiclient
    admin, group, players = setup_group(api, 4, min_evaluations_to_consolidate=3)
    gid = group["id"]
    mid, _ = play_match(api, clock, admin, group, players)
    ids = [p["_member_id"] for p in players]

    # avaliação ainda não aberta
    api.get(f"/matches/{mid}/evaluations/targets", players[0], 403)

    res = api.post(f"/matches/{mid}/presence", admin, json={"present": ids[:3], "absent": [ids[3]]})
    assert res["payments_created"] == 3
    m = res["match"]
    assert m["status"] == "finished" and m["evaluation_status"] == "open"
    # janela de 6h a partir do término previsto
    assert m["evaluation_closes_at"].startswith((T0 + timedelta(days=2, hours=7, minutes=30)).strftime("%Y-%m-%dT%H:%M"))

    # ausente não avalia
    api.get(f"/matches/{mid}/evaluations/targets", players[3], 403)

    t = api.get(f"/matches/{mid}/evaluations/targets", players[0])
    assert t["total"] == 2 and {x["member_id"] for x in t["targets"]} == set(ids[1:3])
    good = {"finishing": 5, "passing": 5, "dribbling": 4, "defense": 4, "physical": 5}

    # auto-avaliação proibida
    r = api.post(f"/matches/{mid}/evaluations", players[0], 422,
                 json={"items": [{"evaluated_member_id": ids[0], "scores": good}]})
    assert r["error"]["code"] == "self_evaluation"
    # nota fora da faixa
    api.post(f"/matches/{mid}/evaluations", players[0], 422,
             json={"items": [{"evaluated_member_id": ids[1], "scores": {**good, "finishing": 7}}]})
    # avaliar quem não esteve presente
    api.post(f"/matches/{mid}/evaluations", players[0], 422,
             json={"items": [{"evaluated_member_id": ids[3], "scores": good}]})

    for i, p in enumerate(players[:3]):
        items = [{"evaluated_member_id": ids[j], "scores": good} for j in range(3) if j != i]
        out = api.post(f"/matches/{mid}/evaluations", p, 201, json={"items": items})
        assert out["done"] == 2
    # duplicada
    api.post(f"/matches/{mid}/evaluations", players[0], 409,
             json={"items": [{"evaluated_member_id": ids[1], "scores": good}]})

    prog = api.get(f"/matches/{mid}/evaluations/progress", admin)
    assert prog["submitted"] == 6 and prog["completion_rate"] == 1.0
    api.get(f"/matches/{mid}/evaluations/progress", players[0], 403)

    # janela expira -> encerramento automático (verificação preguiçosa) e idempotente
    clock.advance(hours=7)
    t = api.get(f"/matches/{mid}/evaluations/targets", players[0])
    assert t["evaluation_status"] == "closed"
    assert api.post(f"/matches/{mid}/evaluations/close", admin)["message"].startswith("Avaliações já")

    prof = api.get(f"{G}/{gid}/players/me", players[0])
    field = prof["ratings"][0]
    # provisório: peso 0.5 * (2 avaliadores / 3) = 1/3
    expected_fin = 2.5 * (2 / 3) + 5 * (1 / 3)
    assert abs(field["attributes"]["finishing"] - expected_fin) < 1e-9
    assert field["valid_evaluations_count"] == 2 and field["matches_evaluated_count"] == 1
    assert field["is_provisional"] is True  # mínimo de 3 avaliações
    absent_prof = api.get(f"{G}/{gid}/players/{ids[3]}", players[0])
    assert absent_prof["ratings"][0]["matches_evaluated_count"] == 0  # sem nota fictícia
    assert absent_prof["finance"] is None  # dados financeiros de outro jogador ocultos

    hist = api.get(f"{G}/{gid}/players/{ids[0]}/rating-history", players[0])
    assert [h["reason"] for h in hist["items"]] == ["match", "initial"]
    api.get(f"{G}/{gid}/players/{ids[0]}/rating-history", players[1], 403)

    received = api.get(f"/matches/{mid}/evaluations/received/{ids[0]}", players[0])
    assert received["count"] == 2 and "evaluator" not in str(received)

    # presença não pode mudar depois da consolidação
    api.post(f"/matches/{mid}/presence", admin, 422, json={"present": ids})

    # ---- financeiro
    summary = api.get(f"{G}/{gid}/finance/summary", admin)
    assert summary["expected"] == "60.00" and summary["pending"] == "60.00" and summary["debtors_count"] == 3
    pays = api.get(f"{G}/{gid}/payments?match_id={mid}", admin)["items"]
    p0 = next(p for p in pays if p["member_id"] == ids[0])

    # pendência aparece na próxima confirmação
    clock.set(T0 + timedelta(days=3))
    mid2 = api.post(f"{G}/{gid}/matches", admin, 201,
                    json=match_payload(starts_at=(T0 + timedelta(days=9)).isoformat()))["id"]
    r = api.post(f"/matches/{mid2}/confirm", players[0], json={})
    assert r["participation"]["status"] == "confirmed"  # não bloqueia (RN10)
    assert r["pending_debts"][0]["outstanding"] == "20.00" and r["warnings"]
    admin_view = {p["member_id"]: p for p in api.get(f"/matches/{mid2}/players", admin)}
    assert admin_view[ids[0]]["has_pending_debt"] is True

    # pagamento parcial + total; reenvio idempotente
    part = api.patch(f"{G}/{gid}/payments/{p0['id']}", admin, json={"amount_paid": "10.00", "method": "pix"})
    assert part["status"] == "pending" and part["outstanding"] == "10.00"
    full = api.patch(f"{G}/{gid}/payments/{p0['id']}", admin, json={"amount_paid": "20.00"})
    again = api.patch(f"{G}/{gid}/payments/{p0['id']}", admin, json={"amount_paid": "20.00"})
    assert full["status"] == again["status"] == "paid" and again["amount_paid"] == "20.00"
    api.patch(f"{G}/{gid}/payments/{p0['id']}", admin, 422, json={"amount_paid": "25.00"})
    api.patch(f"{G}/{gid}/payments/{p0['id']}", players[0], 403, json={"status": "paid"})

    others = [p["id"] for p in pays if p["member_id"] != ids[0]]
    settled = api.post(f"{G}/{gid}/payments/settle", admin, json={"payment_ids": others, "method": "dinheiro"})
    assert all(p["status"] == "paid" for p in settled)
    assert api.get(f"{G}/{gid}/finance/debtors", admin) == []
    assert api.get(f"{G}/{gid}/finance/summary?match_id={mid}", admin)["received"] == "60.00"

    # jogador vê apenas seus pagamentos
    mine = api.get(f"{G}/{gid}/payments", players[1])["items"]
    assert {p["member_id"] for p in mine} == {ids[1]}

    # auditoria
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.group_id == gid)).all())
    assert {"presence.registered", "evaluation.closed", "payment.updated", "payment.settled"} <= actions
    assert db.scalar(select(PlayerRatingHistory).where(PlayerRatingHistory.reason == "match"))


def test_outlier_evaluation_is_flagged_and_excluded(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 4)
    mid, _ = play_match(api, clock, admin, group, players, slots=6)
    ids = [p["_member_id"] for p in players]
    target = ids[0]
    api.post(f"/matches/{mid}/presence", admin, json={"present": ids})
    normal = {"finishing": 4, "passing": 4, "dribbling": 4, "defense": 4, "physical": 4}
    sabotage = {k: 0 for k in normal}
    for p in players[1:3]:
        api.post(f"/matches/{mid}/evaluations", p, 201,
                 json={"items": [{"evaluated_member_id": target, "scores": normal}]})
    api.post(f"/matches/{mid}/evaluations", players[3], 201,
             json={"items": [{"evaluated_member_id": target, "scores": sabotage}]})
    flagged = api.get(f"/matches/{mid}/evaluations/flagged", admin)
    assert len(flagged) == 1 and flagged[0]["scores"]["finishing"] == 0
    assert "evaluator" not in str(flagged)
    api.post(f"/matches/{mid}/evaluations/close", admin)
    field = api.get(f"{G}/{group['id']}/players/{target}", admin)["ratings"][0]
    assert field["valid_evaluations_count"] == 2  # avaliação sabotada não contou
    assert field["attributes"]["finishing"] > 2.5


def test_draw_flow_adjust_compare_publish(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 9)
    gid = group["id"]
    payload = match_payload(slots=10, field_players_per_team=4, goalkeepers_per_team=1)
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=payload)["id"]
    for i, p in enumerate(players):
        api.post(f"/matches/{mid}/confirm", p, json={"as_goalkeeper": i < 2})
    api.post(f"/matches/{mid}/confirm", admin, json={})

    api.post(f"/matches/{mid}/draws", players[0], 403, json={})
    d1 = api.post(f"/matches/{mid}/draws", admin, 201, json={"num_teams": 2, "seed": 1})
    assert len(d1["teams"]) == 2
    all_ids = [pl["member_id"] for t in d1["teams"] for pl in t["players"]]
    assert len(all_ids) == len(set(all_ids)) == 10
    assert all(sum(pl["is_goalkeeper"] for pl in t["players"]) == 1 for t in d1["teams"])
    assert "players" not in d1["params"]

    # jogador não vê sorteio não publicado
    api.get(f"/matches/{mid}/teams", players[0], 404)
    api.get(f"/matches/{mid}/draws/{d1['id']}", players[0], 404)

    d2 = api.post(f"/matches/{mid}/draws", admin, 201, json={"num_teams": 2})
    assert d2["sequence"] == 2
    cmp = api.get(f"/matches/{mid}/draws/{d1['id']}/compare/{d2['id']}", admin)
    assert "score_delta" in cmp

    field_player = next(pl for pl in d2["teams"][0]["players"] if not pl["is_goalkeeper"])
    adj = api.patch(f"/matches/{mid}/draws/{d2['id']}", admin,
                    json={"moves": [{"member_id": field_player["member_id"], "to_team_index": 1}]})
    assert adj["manually_adjusted"] and adj["metrics"]["size_penalty"] > 0

    pub = api.post(f"/matches/{mid}/draws/{d1['id']}/publish", admin)
    assert pub["status"] == "published"
    api.post(f"/matches/{mid}/draws/{d2['id']}/publish", admin)
    summaries = {d["id"]: d["status"] for d in api.get(f"/matches/{mid}/draws", admin)}
    assert summaries[d1["id"]] == "discarded" and summaries[d2["id"]] == "published"
    assert api.get(f"/matches/{mid}/teams", players[0])["id"] == d2["id"]
    api.patch(f"/matches/{mid}/draws/{d2['id']}", admin, 422,
              json={"moves": [{"member_id": field_player["member_id"], "to_team_index": 0}]})
    assert any(n["type"] == "teams_published" for n in api.get("/notifications", players[0])["items"])


def test_series_generates_independent_occurrences(apiclient):
    api = apiclient
    admin, group, _ = setup_group(api, 1)
    gid = group["id"]
    base = match_payload()
    base.pop("starts_at")
    base.pop("duration_minutes")
    series = api.post(f"{G}/{gid}/series", admin, 201, json={
        **base, "recurrence": "weekly", "first_starts_at": (T0 + timedelta(days=1)).isoformat(),
        "duration_minutes": 60, "generate_count": 3,
    })
    matches = api.get(f"{G}/{gid}/matches?upcoming=true", admin)["items"]
    assert len(matches) == 3
    deltas = {(b_["starts_at"], a_["starts_at"]) for a_, b_ in zip(matches, matches[1:])}
    assert len(deltas) == 2
    api.patch(f"{G}/{gid}/series/{series['id']}", admin, json={"price": "35.00"})
    assert all(m["price"] == "20.00" for m in api.get(f"{G}/{gid}/matches", admin)["items"])
    new = api.post(f"{G}/{gid}/series/{series['id']}/generate", admin, json={"count": 1})
    assert new[0]["price"] == "35.00"


def test_monthly_recurrence_clamps_day():
    from datetime import datetime, timezone

    from app.services.matches import add_months

    dt = datetime(2027, 1, 31, 20, tzinfo=timezone.utc)
    assert add_months(dt, 1, 31).day == 28
    assert add_months(add_months(dt, 1, 31), 1, 31).day == 31


def test_dashboard_and_home(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    api.post(f"{G}/{group['id']}/matches", admin, 201, json=match_payload())
    dash = api.get(f"{G}/{group['id']}/dashboard", admin)
    assert dash["total_players"] == 3 and dash["next_match"] and dash["pending_confirmation"] == 3
    api.get(f"{G}/{group['id']}/dashboard", players[0], 403)
    home = api.get("/me/home", players[0])
    assert home["groups"][0]["next_match"]["name"] == "Pelada de quinta"
    assert home["unread_notifications"] >= 1
    api.post("/notifications/read-all", players[0])
    assert api.get("/me/home", players[0])["unread_notifications"] == 0


def test_scheduler_closes_windows_and_sends_reminders(apiclient, clock):
    from app.services.scheduler import run_jobs_once

    api = apiclient
    admin, group, players = setup_group(api, 2)
    mid, _ = play_match(api, clock, admin, group, players)
    ids = [p["_member_id"] for p in players]
    api.post(f"/matches/{mid}/presence", admin, json={"present": ids})
    clock.advance(hours=5, minutes=30)
    assert run_jobs_once()["evaluation_reminders"] == 2
    clock.advance(hours=1)
    assert run_jobs_once()["evaluations_closed"] == 1
    assert run_jobs_once()["evaluations_closed"] == 0  # idempotente
    assert api.get(f"/matches/{mid}", admin)["evaluation_status"] == "closed"


def test_auth_errors(apiclient):
    api = apiclient
    api.get("/me", {}, 401)
    api.get("/me", {"Authorization": "Bearer lixo"}, 401)
    api.user("Repetido")
    r = api.c.post("/api/v1/auth/register",
                   json={"email": "repetido@example.com", "name": "Xavier", "password": "outra-senha"})
    assert r.status_code == 409
    r = api.c.post("/api/v1/auth/login", json={"email": "repetido@example.com", "password": "errada!!"})
    assert r.status_code == 401
