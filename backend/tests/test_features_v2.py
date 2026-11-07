"""Funcionalidades pós-MVP: placar e estatísticas, conquistas, ranking público, aviso de pagamento,
redefinição de senha, foto de perfil, penalidades por falta e Web Push."""

from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.models import Payment
from app.services import mailer
from tests.conftest import T0, match_payload
from tests.test_api_flows import G, setup_group

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _match_with_teams(api, clock, admin, group, players, **payload_kw):
    """Pelada com todos confirmados e times publicados (2 times de 2 jogadores de linha)."""
    payload = match_payload(slots=len(players), **payload_kw)
    mid = api.post(f"{G}/{group['id']}/matches", admin, 201, json=payload)["id"]
    for p in players:
        api.post(f"/matches/{mid}/confirm", p, json={})
    draw = api.post(f"/matches/{mid}/draws", admin, 201, json={"num_teams": 2, "seed": 7})
    api.post(f"/matches/{mid}/draws/{draw['id']}/publish", admin)
    clock.set(T0 + timedelta(days=2, hours=1))
    teams = {t["index"]: [pl["member_id"] for pl in t["players"]] for t in draw["teams"]}
    return mid, teams


# ------------------------------------------------------------------ aviso de pagamento (pago por fora do app)


def test_player_reports_payment_and_admin_confirms(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(price="25.00"))["id"]
    for p in players:
        api.post(f"/matches/{mid}/confirm", p, json={})
    clock.set(T0 + timedelta(days=2, hours=2))
    api.post(f"/matches/{mid}/presence", admin, json={"present": [p["_member_id"] for p in players]})
    pay = api.get(f"{G}/{gid}/payments", players[0])["items"][0]

    reported = api.post(f"{G}/{gid}/payments/{pay['id']}/report", players[0])
    assert reported["reported_paid_at"] is not None and reported["status"] == "pending"
    again = api.post(f"{G}/{gid}/payments/{pay['id']}/report", players[0])  # repetir não gera novo aviso
    assert again["reported_paid_at"] == reported["reported_paid_at"]
    api.post(f"{G}/{gid}/payments/{pay['id']}/report", players[1], 403)  # lançamento de outro jogador
    avisos = [n for n in api.get("/notifications", admin)["items"] if n["type"] == "payment_reported"]
    assert len(avisos) == 1

    # o admin confere e confirma; depois disso não há mais o que informar
    api.patch(f"{G}/{gid}/payments/{pay['id']}", admin, json={"status": "paid", "method": "pix"})
    r = api.post(f"{G}/{gid}/payments/{pay['id']}/report", players[0], 422)
    assert r["error"]["code"] == "not_pending"
    # rotas do Pix copia e cola foram removidas: pagamento é feito por fora do app
    api.get(f"{G}/{gid}/members/me/pix", players[0], 404)
    assert "pix_key" not in api.get(f"{G}/{gid}", admin)


# ------------------------------------------------------------------ redefinição de senha


def test_password_reset_flow(apiclient, clock):
    api = apiclient
    user = api.user("Esquecido")
    mailer.OUTBOX.clear()
    api.post("/auth/password-reset/request", {}, 202, json={"email": "naoexiste@example.com"})
    assert not mailer.OUTBOX  # resposta idêntica, sem e-mail
    api.post("/auth/password-reset/request", {}, 202, json={"email": "ESQUECIDO@example.com"})
    assert len(mailer.OUTBOX) == 1
    api.post("/auth/password-reset/request", {}, 202, json={"email": "esquecido@example.com"})
    assert len(mailer.OUTBOX) == 1  # pedidos repetidos em sequência são ignorados
    body = mailer.OUTBOX[-1]["body"]
    token = body.split("token=")[1].split()[0]

    clock.advance(seconds=5)
    r = api.post("/auth/password-reset/confirm", {}, 422, json={"token": "x" * 20, "new_password": "nova-senha-1"})
    assert r["error"]["code"] == "invalid_reset_token"
    out = api.post("/auth/password-reset/confirm", {}, json={"token": token, "new_password": "nova-senha-1"})
    assert out["access_token"]

    api.get("/me", user, 401)  # sessão antiga encerrada
    api.get("/me", {"Authorization": f"Bearer {out['access_token']}"})
    api.post("/auth/login", {}, 401, json={"email": "esquecido@example.com", "password": "senha-segura"})
    api.post("/auth/login", {}, json={"email": "esquecido@example.com", "password": "nova-senha-1"})
    # link de uso único
    api.post("/auth/password-reset/confirm", {}, 422, json={"token": token, "new_password": "outra-senha-2"})


def test_password_reset_link_expires(apiclient, clock):
    api = apiclient
    api.user("Lento")
    mailer.OUTBOX.clear()
    api.post("/auth/password-reset/request", {}, 202, json={"email": "lento@example.com"})
    token = mailer.OUTBOX[-1]["body"].split("token=")[1].split()[0]
    clock.advance(minutes=settings.password_reset_expire_minutes + 1)
    api.post("/auth/password-reset/confirm", {}, 422, json={"token": token, "new_password": "nova-senha-1"})


# ------------------------------------------------------------------ foto de perfil


def test_photo_upload_validates_content_and_replaces_file(apiclient):
    api = apiclient
    user = api.user("Fotogenico")
    h = {"Authorization": user["Authorization"]}
    r = api.c.post("/api/v1/me/photo", headers=h, files={"file": ("x.png", b"<svg></svg>", "image/png")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_file_type"
    big = b"\x89PNG\r\n\x1a\n" + b"0" * settings.max_upload_bytes
    r = api.c.post("/api/v1/me/photo", headers=h, files={"file": ("big.png", big, "image/png")})
    assert r.json()["error"]["code"] == "file_too_large"

    r = api.c.post("/api/v1/me/photo", headers=h, files={"file": ("foto.exe", PNG_1PX, "application/octet-stream")})
    assert r.status_code == 200, r.text
    url = r.json()["photo_url"]
    assert url.endswith(".png") and "/media/avatars/" in url
    first = Path(settings.media_dir) / url.split("/media/")[1]
    assert first.is_file()
    served = api.c.get("/media/" + url.split("/media/")[1])
    assert served.status_code == 200 and served.content == PNG_1PX

    r = api.c.post("/api/v1/me/photo", headers=h, files={"file": ("f.png", PNG_1PX, "image/png")})
    assert not first.exists()  # foto anterior removida
    second = Path(settings.media_dir) / r.json()["photo_url"].split("/media/")[1]
    assert api.c.delete("/api/v1/me/photo", headers=h).json()["photo_url"] is None
    assert not second.exists()


# ------------------------------------------------------------------ placar e estatísticas


def test_games_validation_and_stats(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 4)
    gid = group["id"]
    mid, teams = _match_with_teams(api, clock, admin, group, players)
    a1, a2 = teams[0]
    b1, b2 = teams[1]

    api.post(f"/matches/{mid}/games", players[0], 403, json={"home_team_index": 0, "away_team_index": 1,
                                                              "home_score": 1, "away_score": 0})
    r = api.post(f"/matches/{mid}/games", admin, 422, json={
        "home_team_index": 0, "away_team_index": 1, "home_score": 1, "away_score": 0,
        "goals": [{"side": "home", "scorer_member_id": b1}]})
    assert r["error"]["code"] == "invalid_scorer"
    r = api.post(f"/matches/{mid}/games", admin, 422, json={
        "home_team_index": 0, "away_team_index": 1, "home_score": 0, "away_score": 0,
        "goals": [{"side": "home", "scorer_member_id": a1}]})
    assert r["error"]["code"] == "goals_exceed_score"
    r = api.post(f"/matches/{mid}/games", admin, 422, json={
        "home_team_index": 0, "away_team_index": 0, "home_score": 0, "away_score": 0})
    assert r["error"]["code"] == "same_team"

    # jogo 1: A 3 x 1 B (hat-trick de a1, assistências de a2, gol contra de a2 a favor de B)
    g1 = api.post(f"/matches/{mid}/games", admin, 201, json={
        "home_team_index": 0, "away_team_index": 1, "home_score": 3, "away_score": 1,
        "goals": [
            {"side": "home", "scorer_member_id": a1, "assist_member_id": a2},
            {"side": "home", "scorer_member_id": a1, "assist_member_id": a2},
            {"side": "home", "scorer_member_id": a1, "assist_member_id": a2},
            {"side": "away", "scorer_member_id": a2, "own_goal": True},
        ]})
    assert g1["sequence"] == 1 and len(g1["home_players"]) == 2 and g1["goals"][3]["own_goal"]
    # jogo 2: empate 0 x 0 com escalação manual (troca de um jogador entre os lados)
    g2 = api.post(f"/matches/{mid}/games", admin, 201, json={
        "home_member_ids": [a1, b1], "away_member_ids": [a2, b2], "home_name": "Mistos",
        "home_score": 0, "away_score": 0})
    assert g2["home_name"] == "Mistos" and g2["away_name"] == "Visitante"
    assert len(api.get(f"/matches/{mid}/games", players[0])) == 2

    ranking = api.get(f"{G}/{gid}/stats/ranking", players[1], params={"sort": "goals"})["rows"]
    top = ranking[0]
    assert top["member_id"] == a1 and top["goals"] == 3 and top["games"] == 2
    assert top["wins"] == 1 and top["draws"] == 1 and top["points"] == 4 and top["win_rate"] == 66.7
    row_a2 = next(r for r in ranking if r["member_id"] == a2)
    assert row_a2["assists"] == 3 and row_a2["own_goals"] == 1
    row_b1 = next(r for r in ranking if r["member_id"] == b1)
    assert row_b1["losses"] == 1 and row_b1["goals_against"] == 3

    h2h = api.get(f"{G}/{gid}/stats/head-to-head", players[0], params={"a": a1, "b": b1})
    assert h2h["against"] == {"games": 1, "a_wins": 1, "draws": 0, "b_wins": 0, "a_goals": 3, "b_goals": 0}
    assert h2h["together"] == {"games": 1, "wins": 0, "draws": 1, "losses": 0}
    api.get(f"{G}/{gid}/stats/head-to-head", players[0], 422, params={"a": a1, "b": a1})

    # edição recalcula; exclusão remove
    api.put(f"/matches/{mid}/games/{g2['id']}", admin, json={
        "home_member_ids": [a1, b1], "away_member_ids": [a2, b2], "home_score": 1, "away_score": 0,
        "goals": [{"side": "home", "scorer_member_id": b1, "assist_member_id": a1}]})
    ranking = api.get(f"{G}/{gid}/stats/ranking", admin)["rows"]
    assert ranking[0]["member_id"] == a1 and ranking[0]["points"] == 6
    api.call("DELETE", f"/matches/{mid}/games/{g2['id']}", admin, 204)
    assert len(api.get(f"/matches/{mid}/games", admin)) == 1

    profile = api.get(f"{G}/{gid}/players/{a1}", players[2])
    assert profile["game_stats"]["goals"] == 3


def test_games_require_started_match(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    mid = api.post(f"{G}/{group['id']}/matches", admin, 201, json=match_payload())["id"]
    for p in players:
        api.post(f"/matches/{mid}/confirm", p, json={})
    ids = [p["_member_id"] for p in players]
    r = api.post(f"/matches/{mid}/games", admin, 422, json={
        "home_member_ids": ids[:1], "away_member_ids": ids[1:], "home_score": 0, "away_score": 0})
    assert r["error"]["code"] == "too_early"


def test_achievements_awards_and_public_ranking(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 4)
    gid = group["id"]
    mid, teams = _match_with_teams(api, clock, admin, group, players)
    a1, a2 = teams[0]
    b1, _ = teams[1]
    for n in range(3):  # três jogos, time A invicto
        api.post(f"/matches/{mid}/games", admin, 201, json={
            "home_team_index": 0, "away_team_index": 1, "home_score": 1 if n else 3, "away_score": 0,
            "goals": [{"side": "home", "scorer_member_id": a1, "assist_member_id": a2}] * (1 if n else 3)})
    api.post(f"/matches/{mid}/presence", admin, json={"present": [p["_member_id"] for p in players]})

    badges = {b["code"]: b for b in api.get(f"{G}/{gid}/players/{a1}/achievements", players[0])}
    assert badges["estreia"]["achieved"] and badges["primeiro_gol"]["achieved"]
    assert badges["hat_trick"]["achieved"] and badges["invicto"]["achieved"]
    assert badges["artilheiro_mes"]["achieved"]
    assert not badges["peladas_10"]["achieved"] and badges["peladas_10"]["progress"] == 0.1
    assert badges["garcom"]["achieved"] is False
    assert {b["code"]: b for b in api.get(f"{G}/{gid}/players/{a2}/achievements", admin)}["garcom"]["achieved"]
    assert not {b["code"]: b for b in api.get(f"{G}/{gid}/players/{b1}/achievements", admin)}["invicto"]["achieved"]

    awards = api.get(f"{G}/{gid}/awards", players[1], params={"months": 2})
    assert awards[0]["month"] == "2026-11"
    assert [w["member_id"] for w in awards[0]["top_scorers"]] == [a1]
    assert awards[0]["top_scorers"][0]["value"] == 5
    assert {w["member_id"] for w in awards[0]["best_win_rate"]} == {a1, a2}
    assert awards[1]["top_scorers"] == []

    # ranking público: desligado por padrão, link só aparece para admins
    assert api.get(f"{G}/{gid}", players[0])["public_token"] is None
    g = api.patch(f"{G}/{gid}", admin, json={"public_ranking_enabled": True})
    token = g["public_token"]
    assert token and api.get(f"{G}/{gid}", players[0])["public_token"] == token
    pub = api.get(f"/public/ranking/{token}", {})
    assert pub["group_name"] == "Pelada dos Amigos" and pub["rows"][0]["member_id"] == a1
    assert all("email" not in r for r in pub["rows"])
    assert len(pub["rows"]) == 4  # admin não jogou: fora do ranking público
    rotated = api.post(f"{G}/{gid}/public-ranking/rotate", admin)["public_token"]
    api.get(f"/public/ranking/{token}", {}, 404)
    api.patch(f"{G}/{gid}", admin, json={"public_ranking_enabled": False})
    api.get(f"/public/ranking/{rotated}", {}, 404)


# ------------------------------------------------------------------ penalidades por falta


def test_no_show_and_late_cancellation_charges(apiclient, clock, db):
    api = apiclient
    admin, group, players = setup_group(api, 3, charge_no_shows=True, charge_late_cancellations=True)
    gid = group["id"]
    starts = T0 + timedelta(days=2)
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(
        cancellation_deadline=(starts - timedelta(hours=24)).isoformat()))["id"]
    for p in players:
        api.post(f"/matches/{mid}/confirm", p, json={})
    clock.set(starts - timedelta(hours=3))
    api.post(f"/matches/{mid}/cancel-confirmation", players[2])  # fora do prazo
    clock.set(starts + timedelta(hours=2))
    ids = [p["_member_id"] for p in players]
    res = api.post(f"/matches/{mid}/presence", admin, json={"present": [ids[0]], "absent": [ids[1]]})
    assert res["payments_created"] == 3
    notes = {p.member_id: p.notes for p in db.scalars(select(Payment).where(Payment.match_id == mid)).all()}
    assert notes[ids[0]] is None
    assert "Falta" in notes[ids[1]] and "Desistência" in notes[ids[2]]
    profile = api.get(f"{G}/{gid}/players/me", players[1])
    assert profile["attendance"]["no_shows"] == 1


def test_absent_is_refunded_without_rule(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    mid = api.post(f"{G}/{group['id']}/matches", admin, 201, json=match_payload())["id"]
    for p in players:
        api.post(f"/matches/{mid}/confirm", p, json={})
    clock.set(T0 + timedelta(days=2, hours=2))
    ids = [p["_member_id"] for p in players]
    res = api.post(f"/matches/{mid}/presence", admin, json={"present": [ids[0]], "absent": [ids[1]]})
    assert res["payments_created"] == 1
    # todos os confirmados foram marcados: nenhum aviso de "sem presença registrada"
    assert not any("sem presença" in w for w in res["warnings"]), res["warnings"]


# ------------------------------------------------------------------ push


def test_push_disabled_without_vapid_keys(apiclient):
    api = apiclient
    user = api.user("Push")
    assert api.get("/push/config", user) == {"enabled": False, "public_key": None}
    r = api.post("/push/subscriptions", user, 422, json={
        "endpoint": "https://push.example.com/abc123", "keys": {"p256dh": "x" * 20, "auth": "y" * 10}})
    assert r["error"]["code"] == "push_disabled"


def test_push_subscription_and_queue(apiclient, monkeypatch):
    from app.services import push

    sent: list = []
    monkeypatch.setattr(settings, "vapid_public_key", "pub")
    monkeypatch.setattr(settings, "vapid_private_key", "priv")
    monkeypatch.setattr(push, "_send_batch", lambda items: sent.extend(items))

    class Inline:
        def submit(self, fn, *args):
            fn(*args)

    monkeypatch.setattr(push, "_executor", Inline())
    api = apiclient
    admin, group, players = setup_group(api, 1)
    sub = {"endpoint": "https://push.example.com/abc123", "keys": {"p256dh": "x" * 20, "auth": "y" * 10}}
    api.post("/push/subscriptions", players[0], 201, json=sub)
    api.post("/push/subscriptions", players[0], 201, json=sub)  # idempotente
    player_user_id = api.get("/me", players[0])["id"]
    api.post(f"{G}/{group['id']}/matches", admin, 201, json=match_payload())
    created = [item for item in sent if player_user_id in item["user_ids"]]
    assert created and created[0]["url"].startswith("/partidas/")
    assert api.post("/push/subscriptions/remove", players[0], json={"endpoint": sub["endpoint"]})["message"]
