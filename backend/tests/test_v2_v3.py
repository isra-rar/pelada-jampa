"""Lembrete de pendências, exportação CSV, calibração do sorteio e descoberta (v2.0/v3.0)."""

from datetime import timedelta

from sqlalchemy import select

from app.domain import calibration as C
from app.models import Notification, Payment
from app.services import payments
from tests.conftest import T0, match_payload
from tests.test_api_flows import G, setup_group
from tests.test_features_v2 import _match_with_teams


def _played_match(api, clock, admin, gid, players, price="20.00", days=2):
    """Pelada encerrada com todos presentes: gera uma cobrança pendente por jogador."""
    starts = T0 + timedelta(days=days)
    mid = api.post(f"{G}/{gid}/matches", admin, 201,
                   json=match_payload(price=price, slots=max(2, len(players)), starts_at=starts.isoformat()))["id"]
    for p in players:
        api.post(f"/matches/{mid}/confirm", p, json={})
    clock.set(starts + timedelta(hours=2))
    api.post(f"/matches/{mid}/presence", admin, json={"present": [p["_member_id"] for p in players]})
    return mid


def _reminders(db, user_id=None):
    stmt = select(Notification).where(Notification.title == "Lembrete: pagamento pendente")
    if user_id is not None:
        stmt = stmt.where(Notification.user_id == user_id)
    return db.scalars(stmt).all()


# ------------------------------------------------------------------ lembrete periódico de pendências


def test_debt_reminders_every_n_days(apiclient, clock, db):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    assert group["debt_reminder_days"] == 3
    _played_match(api, clock, admin, gid, players)
    me0 = api.get("/me", players[0])["id"]

    # cobrança acabou de nascer (já gerou aviso): nada de lembrete antes de 3 dias
    assert payments.send_debt_reminders(db) == 0
    clock.advance(days=3, hours=1)  # 12h em Brasília
    assert payments.send_debt_reminders(db) == 2
    assert payments.send_debt_reminders(db) == 0  # idempotente dentro do intervalo
    body = _reminders(db, me0)[0].body
    assert "1 pendência" in body and "R$ 20.00" in body and "Já paguei" in body

    # quem avisou que pagou sai da lista; o outro continua sendo lembrado
    pay = api.get(f"{G}/{gid}/payments", players[0])["items"][0]
    api.post(f"{G}/{gid}/payments/{pay['id']}/report", players[0])
    clock.advance(days=3)
    assert payments.send_debt_reminders(db) == 1
    assert len(_reminders(db, me0)) == 1

    # de madrugada não manda; desligado (0) não manda
    clock.advance(days=4)
    clock.set(clock.now.replace(hour=5))  # 2h em Brasília
    assert payments.send_debt_reminders(db) == 0
    api.patch(f"{G}/{gid}", admin, json={"debt_reminder_days": 0})
    clock.set(clock.now.replace(hour=15))
    assert payments.send_debt_reminders(db) == 0
    api.patch(f"{G}/{gid}", admin, json={"debt_reminder_days": 61}, expected=422)


def test_debt_reminder_sums_all_debts(apiclient, clock, db):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    gid = group["id"]
    _played_match(api, clock, admin, gid, players, price="15.00", days=2)
    clock.set(T0)
    _played_match(api, clock, admin, gid, players, price="10.00", days=4)
    clock.advance(days=3)  # 11h em Brasília
    assert payments.send_debt_reminders(db) == 1
    body = _reminders(db)[0].body
    assert "2 pendências" in body and "R$ 25.00" in body
    rows = db.scalars(select(Payment)).all()
    assert all(p.last_reminder_at is not None for p in rows)


# ------------------------------------------------------------------ exportação CSV


def test_finance_csv_export(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    # apelido que viraria fórmula no Excel
    api.patch(f"{G}/{gid}/members/{players[1]['_member_id']}", admin, json={"nickname": "=HYPERLINK(\"x\")"})
    _played_match(api, clock, admin, gid, players, price="12.50")
    pays = api.get(f"{G}/{gid}/payments", admin)["items"]
    api.patch(f"{G}/{gid}/payments/{pays[0]['id']}", admin, json={"status": "paid", "method": "pix"})

    r = api.c.get(f"/api/v1{G}/{gid}/finance/export", headers={"Authorization": admin["Authorization"]})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and ".csv" in r.headers["content-disposition"]
    text = r.content.decode("utf-8")
    assert text.startswith("﻿")
    lines = text.lstrip("﻿").strip().split("\r\n")
    assert lines[0].startswith("Data da pelada;Pelada;Jogador;Valor devido")
    assert len(lines) == 3
    assert any(";12,50;12,50;0,00;Pago;" in ln and ";Pix;" in ln for ln in lines[1:])
    assert any(";Pendente;" in ln for ln in lines[1:])
    assert "'=HYPERLINK" in text  # escapado

    # filtros iguais aos da lista
    r = api.c.get(f"/api/v1{G}/{gid}/finance/export", params={"status": "pending"},
                  headers={"Authorization": admin["Authorization"]})
    assert len(r.content.decode("utf-8").strip().split("\r\n")) == 2

    r = api.c.get(f"/api/v1{G}/{gid}/finance/export", params={"view": "debtors"},
                  headers={"Authorization": admin["Authorization"]})
    debt_lines = r.content.decode("utf-8").lstrip("﻿").strip().split("\r\n")
    assert debt_lines[0] == "Jogador;Peladas em aberto;Valor em aberto;Pelada mais antiga"
    assert len(debt_lines) == 2 and ";1;12,50;" in debt_lines[1]

    r = api.c.get(f"/api/v1{G}/{gid}/finance/export", headers={"Authorization": players[0]["Authorization"]})
    assert r.status_code == 403
    audit = api.get(f"{G}/{gid}/audit", admin)["items"]
    assert any(a["action"] == "finance.exported" for a in audit)


# ------------------------------------------------------------------ calibração do sorteio


def test_calibration_domain_suggests_normalized_weights():
    def stats(avg, defense):
        return {"average": avg, "stars": {"3": 5}, "attributes": {"finishing": 3, "passing": 3, "dribbling": 3,
                                                                    "defense": defense, "physical": 3},
                "skills": {}}

    samples = []
    for i in range(12):  # quanto maior a diferença de média, maior o placar
        gap = i * 0.05
        samples.append(C.GameSample(stats(3.0 + gap, 3.0), stats(3.0, 3.0), i // 2, 0))
    out = C.analyze(samples, {"average": 0.4, "stars": 0.25, "attributes": 0.2, "skills": 0.15})
    assert out["games"] == 12 and out["status"] in ("bom", "regular", "ruim")
    assert out["correlations"]["average"] > 0.9
    sug = out["suggested_weights"]
    assert abs(sum(sug.values()) - 1.0) < 1e-9
    assert sug["average"] > out["current_weights"]["average"]
    assert min(sug.values()) >= C.MIN_WEIGHT - 0.01
    few = C.analyze(samples[:3], {"average": 1})
    assert few["suggested_weights"] is None and few["status"] == "poucos_dados"
    assert C.analyze([], {})["status"] == "sem_dados"


def test_calibration_endpoint_uses_registered_games(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 4)
    gid = group["id"]
    mid, _ = _match_with_teams(api, clock, admin, group, players)
    api.post(f"/matches/{mid}/games", admin, 201, json={
        "home_team_index": 0, "away_team_index": 1, "home_score": 2, "away_score": 1})
    api.post(f"/matches/{mid}/games", admin, 201, json={
        "home_team_index": 1, "away_team_index": 0, "home_score": 4, "away_score": 0})
    out = api.get(f"{G}/{gid}/draws/calibration", admin)
    assert out["games"] == 2 and out["close_games"] == 1 and out["blowout_rate"] == 0.5
    assert out["suggested_weights"] is None and out["status"] == "poucos_dados"
    assert len(out["recent_games"]) == 2
    api.get(f"{G}/{gid}/draws/calibration", players[0], 403)
    api.get("/admin/draws/calibration", admin, 403)


# ------------------------------------------------------------------ descoberta e comunidades


def test_discovery_lists_only_public_groups_and_hides_players(apiclient, clock):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(slots=4))["id"]
    api.post(f"/matches/{mid}/confirm", players[0], json={})

    assert api.get("/public/discover/matches", {})["total"] == 0
    assert api.get("/public/discover/groups", {})["total"] == 0
    api.get(f"/public/discover/groups/{gid}", {}, 404)

    api.patch(f"{G}/{gid}", admin, json={"discoverable": True, "city": " João   Pessoa ", "neighborhood": "Bessa"})
    assert api.get(f"{G}/{gid}", admin)["city"] == "João Pessoa"
    found = api.get("/public/discover/matches", {}, params={"city": "joão"})
    assert found["total"] == 1
    card = found["items"][0]
    assert card["id"] == mid and card["confirmed"] == 1 and card["available_slots"] == 3
    assert card["group"]["join_requires_approval"] is True
    assert api.get("/public/discover/matches", {}, params={"city": "Recife"})["total"] == 0
    assert api.get("/public/discover/matches", {}, params={"q": "bessa"})["total"] == 1
    assert api.get("/public/discover/cities", {}) == [{"city": "João Pessoa", "communities": 1}]

    comm = api.get(f"/public/discover/groups/{gid}", {})
    assert comm["members_count"] == 3 and comm["open_matches"] == 1 and comm["viewer"] is None
    raw = str(comm) + str(api.get(f"/public/discover/matches/{mid}", {}))
    assert "Jogador0" not in raw and "Admin" not in raw and "invite_token" not in raw

    # lotada some da busca padrão, mas aparece com with_spots=false
    for p in players[1:]:
        api.post(f"/matches/{mid}/confirm", p, json={})
    api.patch(f"/matches/{mid}", admin, json={"slots": 2})
    assert api.get("/public/discover/matches", {})["total"] == 0
    assert api.get("/public/discover/matches", {}, params={"with_spots": False})["total"] == 1

    # só peladas: comunidade sem peladas na busca
    api.patch(f"{G}/{gid}", admin, json={"discover_matches": False})
    assert api.get("/public/discover/matches", {}, params={"with_spots": False})["total"] == 0
    assert api.get("/public/discover/groups", {})["total"] == 1


def test_join_open_match_with_and_without_approval(apiclient, clock, db):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    gid = group["id"]
    api.patch(f"{G}/{gid}", admin, json={"discoverable": True})
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(slots=4))["id"]

    stranger = api.user("Visitante")
    api.post(f"/discover/matches/{mid}/join", {}, 401, json={})
    res = api.post(f"/discover/matches/{mid}/join", stranger, json={"as_goalkeeper": False})
    assert res["member_status"] == "pending" and res["confirmation"] is None
    avisos = [n for n in api.get("/notifications", admin)["items"] if n["type"] == "membership"]
    assert avisos and "quer jogar" in avisos[0]["body"]
    detail = api.get(f"/public/discover/matches/{mid}", stranger)
    assert detail["viewer"]["member_status"] == "pending"

    # admin aprova; agora o "quero jogar" confirma direto
    api.patch(f"{G}/{gid}/members/{res['member_id']}", admin, json={"status": "active"})
    res = api.post(f"/discover/matches/{mid}/join", stranger, json={})
    assert res["member_status"] == "active"
    assert res["confirmation"]["participation"]["status"] == "confirmed"
    assert api.get(f"/public/discover/matches/{mid}", stranger)["viewer"]["participation_status"] == "confirmed"

    # comunidade que não exige aprovação: entra e confirma na hora
    api.patch(f"{G}/{gid}", admin, json={"public_join_requires_approval": False})
    other = api.user("Outro")
    res = api.post(f"/discover/matches/{mid}/join", other, json={})
    assert res["member_status"] == "active" and res["confirmation"]["participation"]["status"] == "confirmed"
    joined = api.post(f"/discover/groups/{gid}/join", api.user("Terceiro"))
    assert joined["status"] == "active"


def test_public_invite_preview_and_suspended_group_hidden(apiclient, clock):
    api = apiclient
    admin, group, _ = setup_group(api, 1)
    gid = group["id"]
    token = group["invite_token"]
    api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload())
    preview = api.get(f"/public/invites/{token}", {})
    assert preview["name"] == "Pelada dos Amigos" and preview["members_count"] == 2
    assert preview["next_match"]["available_slots"] == 4 and preview["viewer"] is None
    assert api.get(f"/public/invites/{token}", admin)["viewer"]["member_status"] == "active"
    api.get("/public/invites/token-invalido", {}, 404)

    api.patch(f"{G}/{gid}", admin, json={"discoverable": True})
    assert api.get("/public/discover/groups", {})["total"] == 1
    root = api.user("Root")
    from app.core.db import SessionLocal
    from app.models import User

    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.email == root["_email"]))
        u.is_platform_admin = True
        s.commit()
    api.post(f"/admin/groups/{gid}/suspend", root, json={"reason": "Teste"})
    assert api.get("/public/discover/groups", {})["total"] == 0
    api.get(f"/public/invites/{token}", {}, 404)
