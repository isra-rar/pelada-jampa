"""Funcionalidades que completam o PRD: filtros paginados, recorrência automática, aviso de resultado,
métricas de sucesso (§21), configurações globais da plataforma (§2) e direitos do titular (LGPD)."""

from datetime import timedelta

from sqlalchemy import select, update

from app.domain.enums import GroupRole
from app.models import GroupMember, Match, User
from app.services.scheduler import run_jobs_once
from tests.conftest import T0, match_payload
from tests.test_api_flows import G, play_match, setup_group
from tests.test_features_v2 import _match_with_teams


def _make_platform_admin(db, headers) -> None:
    db.execute(update(User).where(User.email == headers["_email"]).values(is_platform_admin=True))
    db.commit()


def _finish_with_evaluations(api, clock, admin, group, players):
    """Pelada com 4 confirmados: 3 presentes (que se avaliam) e 1 falta."""
    mid, _ = play_match(api, clock, admin, group, players)
    ids = [p["_member_id"] for p in players]
    api.post(f"/matches/{mid}/presence", admin, json={"present": ids[:3], "absent": [ids[3]]})
    good = {"finishing": 4, "passing": 4, "dribbling": 4, "defense": 4, "physical": 4}
    for i, p in enumerate(players[:3]):
        items = [{"evaluated_member_id": ids[j], "scores": good} for j in range(3) if j != i]
        api.post(f"/matches/{mid}/evaluations", p, 201, json={"items": items})
    return mid


# ------------------------------------------------------------------ filtros das listas


def test_payment_and_match_filters(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 4)
    gid = group["id"]
    mid, _ = play_match(api, clock, admin, group, players)
    ids = [p["_member_id"] for p in players]
    api.post(f"/matches/{mid}/presence", admin, json={"present": ids[:3], "absent": [ids[3]]})
    pay = api.get(f"{G}/{gid}/payments", players[0])["items"][0]
    api.post(f"{G}/{gid}/payments/{pay['id']}/report", players[0])

    reported = api.get(f"{G}/{gid}/payments", admin, params={"reported": "true"})
    assert reported["total"] == 1 and reported["items"][0]["id"] == pay["id"]
    others = api.get(f"{G}/{gid}/payments", admin, params={"status": "pending", "reported": "false"})
    assert others["total"] == 2
    by_member = api.get(f"{G}/{gid}/payments", admin, params={"member_id": ids[1]})
    assert by_member["total"] == 1 and by_member["items"][0]["member_id"] == ids[1]
    paged = api.get(f"{G}/{gid}/payments", admin, params={"size": 2, "page": 2})
    assert paged["total"] == 3 and len(paged["items"]) == 1

    other = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(
        starts_at=(T0 + timedelta(days=9)).isoformat()))["id"]
    api.post(f"/matches/{other}/cancel", admin, json={"reason": "Chuva"})
    past = api.get(f"{G}/{gid}/matches", admin, params=[("status", "finished"), ("status", "cancelled")])
    assert {m["id"] for m in past["items"]} == {mid, other}
    assert api.get(f"{G}/{gid}/matches", admin, params={"status": "published"})["total"] == 0


# ------------------------------------------------------------------ recorrência automática


def _series(api, admin, gid, **kw):
    base = match_payload()
    base.pop("starts_at")
    base.pop("duration_minutes")
    return api.post(f"{G}/{gid}/series", admin, 201, json={
        **base, "recurrence": "weekly", "first_starts_at": (T0 + timedelta(days=1)).isoformat(),
        "duration_minutes": 60, **kw,
    })


def test_scheduler_keeps_next_occurrences_of_series(apiclient, clock, db):
    api = apiclient
    admin, group, _ = setup_group(api, 1)
    gid = group["id"]
    series = _series(api, admin, gid, generate_count=1, auto_generate_ahead=2)
    assert series["auto_generate_ahead"] == 2
    _series(api, admin, gid, name="Só manual", generate_count=1, auto_generate_ahead=0)

    def upcoming():
        items = api.get(f"{G}/{gid}/matches", admin, params={"upcoming": "true", "size": 50})["items"]
        return [m for m in items if m["series_id"] == series["id"] and m["status"] != "cancelled"]

    assert len(upcoming()) == 1
    assert run_jobs_once()["series_generated"] == 1
    assert len(upcoming()) == 2
    assert run_jobs_once()["series_generated"] == 0  # idempotente

    # ocorrência cancelada não conta: a semana seguinte é gerada no lugar
    api.post(f"/matches/{upcoming()[0]['id']}/cancel", admin, json={"reason": "Feriado"})
    assert run_jobs_once()["series_generated"] == 1
    dates = [m["starts_at"] for m in upcoming()]
    assert len(dates) == 2 and len(set(dates)) == 2

    # a semana passa (a pelada de hoje já terminou): a recorrência repõe a próxima
    clock.advance(days=8, hours=2)
    assert run_jobs_once()["series_generated"] == 1
    assert len(upcoming()) == 2

    # recorrência pausada não gera
    api.patch(f"{G}/{gid}/series/{series['id']}", admin, json={"auto_generate_ahead": 0})
    clock.advance(days=8)
    assert run_jobs_once()["series_generated"] == 0


def test_auto_generation_survives_creator_losing_admin(apiclient, db):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    gid = group["id"]
    series = _series(api, admin, gid, generate_count=0, auto_generate_ahead=1)
    # outro administrador assume; o criador vira jogador
    api.patch(f"{G}/{gid}/members/{players[0]['_member_id']}", admin, json={"role": "admin"})
    db.execute(update(GroupMember).where(GroupMember.id == admin["_member_id"]).values(role=GroupRole.PLAYER))
    db.commit()
    assert run_jobs_once()["series_generated"] == 1
    db.expire_all()
    match = db.scalar(select(Match).where(Match.series_id == series["id"]))
    new_admin_user = db.get(GroupMember, players[0]["_member_id"]).user_id
    assert match.created_by_id == new_admin_user
    assert match.responsible_admin_ids == [new_admin_user]


# ------------------------------------------------------------------ aviso de resultado


def test_result_notification_for_players_of_the_game(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 4)
    mid, teams = _match_with_teams(api, clock, admin, group, players)
    api.post(f"/matches/{mid}/games", admin, 201,
             json={"home_team_index": 0, "away_team_index": 1, "home_score": 2, "away_score": 1})
    for p in players:
        avisos = [n for n in api.get("/notifications", p)["items"] if n["type"] == "result_registered"]
        assert len(avisos) == 1 and "2 x 1" in avisos[0]["title"]
    assert not [n for n in api.get("/notifications", admin)["items"] if n["type"] == "result_registered"]

    # jogo ao vivo: avisa ao encerrar, não ao começar
    live = api.post(f"/matches/{mid}/games/live", admin, 201, json={"home_team_index": 0, "away_team_index": 1})
    count = lambda: len([n for n in api.get("/notifications", players[0])["items"]  # noqa: E731
                         if n["type"] == "result_registered"])
    assert count() == 1
    api.post(f"/matches/{mid}/games/{live['id']}/finish", admin)
    assert count() == 2


# ------------------------------------------------------------------ métricas (PRD §21)


def test_group_and_platform_metrics(apiclient, clock, db):
    api = apiclient
    admin, group, players = setup_group(api, 4)
    gid = group["id"]
    _finish_with_evaluations(api, clock, admin, group, players)

    api.get(f"{G}/{gid}/metrics", players[0], 403)
    m = api.get(f"{G}/{gid}/metrics", admin)
    assert m["matches"] == 1 and m["cancelled_matches"] == 0
    assert m["invited"] == 5 and m["responded"] == 4 and m["response_rate"] == 0.8
    assert m["occupancy_rate"] == 1.0
    assert m["confirmations"] == 4 and m["no_shows"] == 1 and m["cancellations_after_confirmation"] == 0
    assert m["evaluations_expected"] == 6 and m["evaluations_submitted"] == 6
    assert m["evaluation_completion_rate"] == 1.0 and m["avg_evaluation_hours"] >= 0
    assert m["charges"] == 3 and m["charges_paid"] == 0 and m["payment_rate"] == 0.0
    assert m["open_debts"] == 3 and m["open_debts_amount"] == "60.00"
    assert m["matches_with_published_draw"] == 0 and m["draws_per_match"] is None
    assert m["active_players"] == 3 and m["previous_active_players"] == 0 and m["retention_rate"] is None
    r = api.get(f"{G}/{gid}/metrics", admin, 422, params={"since": "2026-12-01", "until": "2026-11-01"})
    assert r["error"]["code"] == "invalid_period"

    api.get("/admin/metrics", admin, 403)
    _make_platform_admin(db, admin)
    p = api.get("/admin/metrics", admin)
    assert p["users_total"] == 5 and p["groups_total"] == 1 and p["groups_active"] == 1
    assert p["matches"] == 1 and p["players_active"] == 3


# ------------------------------------------------------------------ configurações globais


def test_platform_settings_thresholds_and_group_defaults(apiclient, db):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    api.get("/admin/settings", admin, 403)
    _make_platform_admin(db, admin)

    s = api.get("/admin/settings", admin)
    assert s["star_thresholds"] == {"5": 4.5, "4": 3.5, "3": 2.5, "2": 1.5, "1": 0.5}
    assert s["star_labels"]["5"] == "Craque"
    assert s["group_defaults"]["initial_rating"] == 2.5
    member = players[0]["_member_id"]
    assert api.get(f"{G}/{gid}/players/{member}", admin)["ratings"][0]["stars"] == 3  # média inicial 2,5

    api.patch("/admin/settings", admin, 422, json={"star_thresholds": {"5": 4.0, "4": 4.2, "3": 3.0, "2": 2.0, "1": 1.0}})
    api.patch("/admin/settings", admin, 422, json={"star_thresholds": {"5": 4.5}})
    out = api.patch("/admin/settings", admin, json={
        "star_thresholds": {"5": 4.5, "4": 3.5, "3": 3.0, "2": 2.0, "1": 1.0}, "reason": "Calibração",
    })
    assert out["recalculated_ratings"] == 3  # admin + 2 jogadores
    profile = api.get(f"{G}/{gid}/players/{member}", admin)
    assert profile["ratings"][0]["stars"] == 2 and profile["ratings"][0]["average"] == 2.5
    assert profile["rating_explanation"]["stars"]["2"].endswith("2.00)")
    hist = api.get(f"{G}/{gid}/players/{member}/rating-history", admin)["items"]
    assert hist[0]["reason"] == "star_thresholds" and hist[0]["stars_before"] == 3
    # repetir os mesmos limites não reclassifica nada
    assert api.patch("/admin/settings", admin, json={
        "star_thresholds": {"5": 4.5, "4": 3.5, "3": 3.0, "2": 2.0, "1": 1.0}})["recalculated_ratings"] is None

    api.patch("/admin/settings", admin, json={"group_defaults": {"initial_rating": 3.0,
                                                                  "draw_weights": {"average": 0.6}}})
    new_group = api.post(G, admin, 201, json={"name": "Grupo novo"})
    assert new_group["initial_rating"] == 3.0
    assert new_group["draw_weights"] == {"average": 0.6, "stars": 0.25, "attributes": 0.20, "skills": 0.15}
    custom = api.post(G, admin, 201, json={"name": "Grupo próprio", "initial_rating": 1.0})
    assert custom["initial_rating"] == 1.0
    assert api.get(f"{G}/{gid}", admin)["initial_rating"] == 2.5  # grupos existentes não mudam
    assert api.get(f"{G}/{new_group['id']}/players/me", admin)["ratings"][0]["stars"] == 3


# ------------------------------------------------------------------ LGPD


def test_export_my_data(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 4)
    _finish_with_evaluations(api, clock, admin, group, players)
    r = api.c.get("/api/v1/me/export", headers={"Authorization": players[0]["Authorization"]})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["account"]["email"] == players[0]["_email"]
    g = data["groups"][0]
    assert g["group_name"] == "Pelada dos Amigos"
    assert g["participations"][0]["status"] == "present"
    assert len(g["payments"]) == 1 and len(g["evaluations_given"]) == 2
    assert all("evaluator" not in h for h in g["rating_history"])
    assert data["notifications"]


def test_delete_account_anonymizes_and_frees_spot(apiclient, clock, db):
    api = apiclient
    admin, group, players = setup_group(api, 3)
    gid = group["id"]
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(slots=2))["id"]
    for p in players:
        api.post(f"/matches/{mid}/confirm", p, json={})
    status = lambda: {x["member_id"]: x["status"] for x in api.get(f"/matches/{mid}/players", admin)}  # noqa: E731
    assert status()[players[2]["_member_id"]] == "waitlist"

    victim = players[0]
    r = api.post("/me/delete", victim, 422, json={"confirm": "sim", "password": "senha-segura"})
    assert r["error"]["code"] == "confirmation_required"
    r = api.post("/me/delete", victim, 422, json={"confirm": "EXCLUIR", "password": "errada"})
    assert r["error"]["code"] == "invalid_password"
    r = api.post("/me/delete", admin, 422, json={"confirm": "EXCLUIR", "password": "senha-segura"})
    assert r["error"]["code"] == "sole_admin"

    api.post("/me/delete", victim, json={"confirm": "excluir", "password": "senha-segura"})
    after = status()
    assert after[victim["_member_id"]] == "cancelled"
    assert after[players[2]["_member_id"]] == "confirmed"  # a vaga foi para a lista de espera

    api.get("/me", victim, 401)
    api.post("/auth/login", {}, 401, json={"email": victim["_email"], "password": "senha-segura"})
    user = db.scalar(select(User).join(GroupMember, GroupMember.user_id == User.id)
                     .where(GroupMember.id == victim["_member_id"]))
    assert user.deleted_at is not None and user.name == "Jogador removido"
    assert user.email.endswith("@contas.invalid") and user.phone is None and not user.is_active
    members = {m["id"]: m for m in api.get(f"{G}/{gid}/members", admin)}
    assert victim["_member_id"] not in members or members[victim["_member_id"]]["status"] == "removed"
    # o e-mail fica livre para uma conta nova
    api.post("/auth/register", {}, 201, json={"email": victim["_email"], "name": "De volta",
                                               "password": "senha-segura"})

    # com outro administrador, o antigo pode excluir a conta
    api.patch(f"{G}/{gid}/members/{players[1]['_member_id']}", admin, json={"role": "admin"})
    api.post("/me/delete", admin, json={"confirm": "EXCLUIR", "password": "senha-segura"})
