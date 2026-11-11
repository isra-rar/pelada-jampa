"""Espaços (dono da quadra): cadastro e equipe, vínculo com grupos, reserva de horários na agenda, aluguel e
painel."""

from datetime import timedelta

from sqlalchemy import select

from app.models import AuditLog, Match
from app.services.scheduler import run_jobs_once
from tests.conftest import T0, match_payload
from tests.test_api_flows import G, setup_group

V = "/venues"


def root(api) -> dict:
    """Conta Super-Admin (só gestão), criada como o script de produção faz."""
    from app.core.db import SessionLocal
    from app.services.users import ensure_super_admin

    with SessionLocal() as s:
        _, password, _ = ensure_super_admin(s, "root@example.com", "Root")
    r = api.c.post("/api/v1/auth/login", json={"email": "root@example.com", "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}", "_email": "root@example.com"}


def _venue(api, owner, expected=201, **kw):
    """O Super-Admin cadastra o espaço com `owner` como dono (DE)."""
    row = api.post("/admin/venues", root(api), expected, json={
        "name": "Arena Central", "address": "Rua da Bola, 10", "owner_name": owner["_name"],
        "owner_email": owner["_email"], **kw})
    if expected != 201:
        return row, None
    venue = api.get(f"{V}/{row['id']}", owner)
    court = api.post(f"{V}/{venue['id']}/courts", owner, 201,
                     json={"name": "Quadra 1", "modality": "society", "hourly_price": "120.00"})
    return venue, court


def _linked_group(api, owner, venue):
    """Grupo independente que pede vínculo pelo código e é aprovado."""
    admin, group, players = setup_group(api, 3)
    api.post(f"{G}/{group['id']}/venue/request", admin, 201, json={"code": venue["link_code"].lower()})
    link = api.get(f"{V}/{venue['id']}/groups", owner)[0]
    assert link["status"] == "pending" and link["admins"][0]["email"] == admin["_email"]
    api.post(f"{V}/{venue['id']}/groups/{link['id']}/approve", owner)
    return admin, group, players, link


def _types(api, headers):
    return [n["type"] for n in api.get("/notifications", headers)["items"]]


def test_venue_setup_staff_and_access(apiclient):
    api = apiclient
    owner = api.user("Dono")
    _venue(api, owner, 422, name="Errado", opening_hours={"0": ["23:00", "08:00"]})
    venue, court = _venue(api, owner, opening_hours={"0": ["08:00", "22:00"], "5": ["09:00", "24:00"]})
    assert venue["my_role"] == "owner" and len(venue["link_code"]) == 8
    assert venue["opening_hours"] == {"0": ["08:00", "22:00"], "5": ["09:00", "24:00"]}
    assert api.get(V, owner)[0]["courts"][0]["hourly_price"] == "120.00"

    stranger = api.user("Estranho")
    api.get(f"{V}/{venue['id']}", stranger, 404)
    manager = api.user("Gerente")
    api.post(f"{V}/{venue['id']}/staff", owner, 201, json={"email": manager["_email"], "name": "Gerente"})
    assert "venue" in _types(api, manager)
    assert api.get(f"{V}/{venue['id']}", manager)["my_role"] == "manager"
    api.post(f"{V}/{venue['id']}/staff", manager, 403, json={"email": "x@example.com", "name": "Xis"})
    api.patch(f"{V}/{venue['id']}/courts/{court['id']}", manager, json={"hourly_price": "150.00"})

    me = next(s for s in api.get(f"{V}/{venue['id']}/staff", owner) if s["role"] == "owner")
    err = api.patch(f"{V}/{venue['id']}/staff/{me['id']}", owner, 422, json={"role": "manager"})
    assert err["error"]["code"] == "last_owner"
    old_code = venue["link_code"]
    assert api.post(f"{V}/{venue['id']}/code/rotate", owner)["link_code"] != old_code


def test_link_booking_conflicts_and_calendar(apiclient):
    api = apiclient
    owner = api.user("Dono")
    venue, court = _venue(api, owner)
    vid = venue["id"]
    admin, group, players, link = _linked_group(api, owner, venue)
    gid = group["id"]
    assert "venue" in _types(api, admin)
    assert api.get(f"{G}/{gid}/venue", players[0])["venue"]["name"] == "Arena Central"
    err = api.post(f"{G}/{gid}/venue/request", admin, 409, json={"code": venue["link_code"]})
    assert err["error"]["code"] == "already_linked"

    payload = match_payload(location=None, court_id=court["id"])  # 4/11, 9h às 10h30 (Brasília)
    match = api.post(f"{G}/{gid}/matches", admin, 201, json=payload)
    assert match["location"] == "Arena Central · Quadra 1" and match["address"] == "Rua da Bola, 10"
    assert match["court_id"] == court["id"]

    overlap = match_payload(location=None, court_id=court["id"],
                            starts_at=(T0 + timedelta(days=2, minutes=60)).isoformat())
    err = api.post(f"{G}/{gid}/matches", admin, 409, json=overlap)
    assert err["error"]["code"] == "court_unavailable" and err["error"]["details"]["reason"] == "booked"
    late = match_payload(location=None, court_id=court["id"], starts_at=(T0 + timedelta(days=2, hours=13)).isoformat())
    assert api.post(f"{G}/{gid}/matches", admin, 409, json=late)["error"]["details"]["reason"] == "closed"

    err = api.post(f"{V}/{vid}/blocks", owner, 409, json={
        "court_id": court["id"], "starts_at": payload["starts_at"],
        "ends_at": (T0 + timedelta(days=2, hours=1)).isoformat(), "reason": "Manutenção"})
    assert err["error"]["code"] == "block_conflicts_match"
    block = api.post(f"{V}/{vid}/blocks", owner, 201, json={
        "court_id": court["id"], "starts_at": (T0 + timedelta(days=2, hours=3)).isoformat(),
        "ends_at": (T0 + timedelta(days=2, hours=5)).isoformat(), "reason": "Aniversário"})
    blocked = match_payload(location=None, court_id=court["id"],
                            starts_at=(T0 + timedelta(days=2, hours=4)).isoformat())
    assert api.post(f"{G}/{gid}/matches", admin, 409, json=blocked)["error"]["details"]["reason"] == "blocked"

    # outra pelada (de outro grupo, criada pelo espaço) na mesma quadra mais tarde
    other_admin = api.user("OutroAdmin")
    other = api.post(f"{V}/{vid}/groups", owner, 201, json={
        "name": "Racha da noite", "admin_email": other_admin["_email"], "admin_name": "Outro"})
    api.post(f"{G}/{other['group_id']}/matches", other_admin, 201, json=match_payload(
        location=None, court_id=court["id"], starts_at=(T0 + timedelta(days=2, hours=8)).isoformat()))

    cal = api.get(f"{V}/{vid}/calendar", owner, params={"start": "2026-11-04", "days": 1})
    day = cal["schedule"][0]
    items = day["courts"][0]["items"]
    assert [i["kind"] for i in items] == ["match", "block", "match"]
    assert items[0]["group_name"] == "Pelada dos Amigos" and items[0]["slots"] == 4
    assert day["opens_at"].startswith("2026-11-04T11:00") and day["closes_at"].startswith("2026-11-05T02:00")
    free = [(f["starts_at"][11:16], f["ends_at"][11:16]) for f in day["courts"][0]["free"]]
    assert free[0] == ("11:00", "12:00")  # 8h às 9h (Brasília)

    mine = api.get(f"{G}/{gid}/venue/calendar", admin, params={"start": "2026-11-04", "days": 1})
    titles = [(i["title"], i["mine"]) for i in mine["schedule"][0]["courts"][0]["items"]]
    assert titles == [("Pelada de quinta", True), ("Aniversário", False), ("Ocupado", False)]
    api.get(f"{G}/{gid}/venue/calendar", players[0], 403)

    api.call("DELETE", f"{V}/{vid}/blocks/{block['id']}", owner, 204)
    api.post(f"{G}/{gid}/matches", admin, 201, json=blocked)

    # mover a pelada para um horário ocupado é recusado
    err = api.patch(f"/matches/{match['id']}", admin, 409, json={
        "starts_at": (T0 + timedelta(days=2, hours=8)).isoformat(),
        "ends_at": (T0 + timedelta(days=2, hours=9)).isoformat()})
    assert err["error"]["details"]["reason"] == "booked"
    api.get(f"{V}/{vid}", api.user("Curioso"), 404)


def test_series_with_court_skips_unavailable_dates(apiclient, db):
    api = apiclient
    owner = api.user("Dono")
    venue, court = _venue(api, owner)
    admin, group, _, _ = _linked_group(api, owner, venue)
    gid = group["id"]
    # o espaço bloqueia a segunda semana
    api.post(f"{V}/{venue['id']}/blocks", owner, 201, json={
        "court_id": court["id"], "starts_at": (T0 + timedelta(days=8)).isoformat(),
        "ends_at": (T0 + timedelta(days=8, hours=3)).isoformat(), "reason": "Torneio"})
    base = match_payload(location=None)
    base.pop("starts_at")
    base.pop("duration_minutes")
    series_in = {**base, "court_id": court["id"], "recurrence": "weekly", "duration_minutes": 60,
                 "first_starts_at": (T0 + timedelta(days=1)).isoformat(), "generate_count": 1,
                 "auto_generate_ahead": 3}
    series = api.post(f"{G}/{gid}/series", admin, 201, json=series_in)
    assert series["court_id"] == court["id"] and series["location"] == "Arena Central · Quadra 1"
    err = api.post(f"{G}/{gid}/series/{series['id']}/generate", admin, 409, json={"count": 1})
    assert err["error"]["details"]["reason"] == "blocked"

    run_jobs_once()
    starts = sorted(m.starts_at for m in db.scalars(select(Match).where(Match.series_id == series["id"])).all())
    assert [(s - starts[0]).days for s in starts] == [0, 14, 21]
    skipped = db.scalars(select(AuditLog).where(AuditLog.action == "series.slot_skipped")).all()
    assert skipped and skipped[0].after["reason"] == "blocked"


def test_rent_dashboard_and_settlement(apiclient, clock):
    api = apiclient
    owner = api.user("Dono")
    venue, court = _venue(api, owner)
    vid = venue["id"]
    admin, group, players, link = _linked_group(api, owner, venue)
    gid = group["id"]
    match = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(location=None, court_id=court["id"]))
    for p in players[:1]:
        api.post(f"/matches/{match['id']}/confirm", p, json={})
    dash = api.get(f"{V}/{vid}/dashboard", owner)
    assert dash["matches_upcoming"] == 1 and dash["booked_hours"] == 1.5
    assert dash["at_risk"][0]["match_id"] == match["id"] and dash["at_risk"][0]["confirmed"] == 1

    clock.advance(days=2, hours=1)  # pelada começou: aluguel lançado (R$ 120/h × 1h30)
    rent = api.get(f"{V}/{vid}/rent", owner, params={"month": "2026-11"})
    assert rent["pending"] == "180.00" and rent["groups"][0]["pending"] == "180.00"
    charge = rent["items"][0]
    mine = api.get(f"{G}/{gid}/rent", admin)
    assert mine["pending_amount"] == "180.00" and mine["items"][0]["venue_name"] == "Arena Central"
    api.get(f"{G}/{gid}/rent", players[0], 403)

    api.post(f"{G}/{gid}/rent/{charge['id']}/report", admin)
    assert "venue" in _types(api, owner)
    assert api.get(f"{V}/{vid}/rent", owner, params={"month": "2026-11"})["groups"][0]["reported"] == 1
    dash = api.get(f"{V}/{vid}/dashboard", owner)
    assert dash["matches_held"] == 1 and dash["rent_overdue_total"] == "180.00"
    assert dash["overdue_groups"][0]["reported"] == 1 and dash["groups"][0]["fill_rate"] == 0.25

    paid = api.post(f"{V}/{vid}/rent/settle", owner, json={"charge_ids": [charge["id"]], "method": "pix"})
    assert paid[0]["status"] == "paid" and paid[0]["method"] == "pix"
    assert api.get(f"{G}/{gid}/rent", admin)["pending_amount"] == "0"
    dash = api.get(f"{V}/{vid}/dashboard", owner)
    assert dash["rent_received"] == "180.00" and dash["rent_overdue_total"] == "0"

    # preço combinado com o grupo vale para os próximos aluguéis
    api.patch(f"{V}/{vid}/groups/{link['id']}", owner, json={"rent_hourly_price": "100.00"})
    nxt = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(
        location=None, court_id=court["id"], starts_at=(T0 + timedelta(days=4)).isoformat()))
    clock.advance(days=2)
    run_jobs_once()
    items = api.get(f"{V}/{vid}/rent", owner, params={"month": "2026-11"})["items"]
    assert next(i for i in items if i["match_id"] == nxt["id"])["amount"] == "150.00"


def test_venue_created_group_admin_and_unlink(apiclient):
    api = apiclient
    owner = api.user("Dono")
    venue, court = _venue(api, owner)
    vid = venue["id"]
    link = api.post(f"{V}/{vid}/groups", owner, 201, json={
        "name": "Pelada da Arena", "admin_email": "novoadmin@example.com", "admin_name": "Novo"})
    assert link["origin"] == "venue" and link["status"] == "active"
    gid = link["group_id"]
    api.get(f"{G}/{gid}", owner, 404)  # o espaço não entra no grupo

    admin = api.user("NovoAdmin")  # cria a conta com o e-mail indicado pelo espaço
    assert api.get(f"{G}/{gid}", admin)["my_role"] == "admin"
    match = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(location=None, court_id=court["id"]))
    err = api.call("DELETE", f"{G}/{gid}/venue", admin, 403)
    assert err["error"]["code"] == "venue_owned_group"

    novo = api.user("Substituto")
    api.post(f"{V}/{vid}/groups/{link['id']}/admin", owner,
             json={"email": novo["_email"], "name": "Substituto", "replace": True})
    assert api.get(f"{G}/{gid}", novo)["my_role"] == "admin"
    assert api.get(f"{G}/{gid}", admin)["my_role"] == "player"

    ended = api.post(f"{V}/{vid}/groups/{link['id']}/end", owner)
    assert ended["status"] == "ended"
    freed = api.get(f"/matches/{match['id']}", novo)
    assert freed["court_id"] is None and freed["location"] == "Arena Central · Quadra 1"
    assert api.get(f"{G}/{gid}/venue", novo) == {"link_id": None, "status": None, "origin": None, "venue": None,
                                                  "rent_hourly_price": None}
    assert api.get(f"{V}/{vid}/calendar", owner, params={"start": "2026-11-04", "days": 1})[
        "schedule"][0]["courts"][0]["items"] == []
    # sem vínculo, a pelada na quadra fica com o horário pendente até o dono confirmar
    pending = api.post(f"{G}/{gid}/matches", novo, 201, json=match_payload(location=None, court_id=court["id"]))
    assert pending["booking_status"] == "pending"


def test_independent_group_can_leave_and_owner_cannot_delete_account(apiclient):
    api = apiclient
    owner = api.user("Dono")
    venue, court = _venue(api, owner)
    admin, group, _, link = _linked_group(api, owner, venue)
    api.call("DELETE", f"{G}/{group['id']}/venue", admin)
    assert "venue" in _types(api, owner)
    assert api.get(f"{V}/{venue['id']}/groups", owner, params={"status": "ended"})[0]["id"] == link["id"]

    err = api.post("/me/delete", owner, 422, json={"confirm": "EXCLUIR", "password": "senha-segura"})
    assert err["error"]["code"] == "sole_venue_owner"
    export = api.get("/me/export", owner)
    assert export["venues"][0]["venue"] == "Arena Central" and export["venues"][0]["role"] == "owner"


def test_hours_change_keeps_bookings_and_free_slots_start_now(apiclient, clock):
    api = apiclient
    owner = api.user("Dono")
    venue, court = _venue(api, owner)
    admin, group, _, _ = _linked_group(api, owner, venue)
    # pelada na quarta, 4/11, 9h (Brasília)
    api.post(f"{G}/{group['id']}/matches", admin, 201, json=match_payload(location=None, court_id=court["id"]))
    hours = {str(d): ["08:00", "23:00"] for d in range(7)}
    err = api.patch(f"{V}/{venue['id']}", owner, 422, json={"opening_hours": {**hours, "2": ["10:00", "23:00"]}})
    assert err["error"]["code"] == "hours_conflict" and len(err["error"]["details"]["matches"]) == 1
    api.patch(f"{V}/{venue['id']}", owner, json={"opening_hours": {**hours, "6": None}})  # domingo vazio: ok

    clock.set(T0 + timedelta(days=2, hours=3))  # quarta, 12h (Brasília)
    day = api.get(f"{V}/{venue['id']}/calendar", owner, params={"start": "2026-11-04", "days": 1})["schedule"][0]
    assert day["courts"][0]["free"][0]["starts_at"].startswith("2026-11-04T15:00")


def test_owner_finance_overview_export_and_isolation(apiclient, clock):
    api = apiclient
    owner = api.user("Dono")
    venue, court = _venue(api, owner)
    vid = venue["id"]
    admin, group, players, link = _linked_group(api, owner, venue)
    gid = group["id"]
    match = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(location=None, court_id=court["id"]))

    # a pelada marcada ainda não gerou aluguel: aparece só na previsão (R$ 120/h × 1h30)
    fin = api.get(f"{V}/{vid}/finance", owner)
    assert fin["upcoming_matches"] == 1 and fin["upcoming_expected"] == "180.00" and fin["expected"] == "0"

    clock.advance(days=2, hours=1)
    fin = api.get(f"{V}/{vid}/finance", owner, params={"months": 3})
    assert [m["month"] for m in fin["series"]] == ["2026-09", "2026-10", "2026-11"]
    assert fin["series"][-1]["pending"] == "180.00" and fin["overdue_total"] == "180.00"
    assert fin["aging"]["upto_30"] == "180.00" and fin["collection_rate"] == 0.0
    assert fin["by_court"][0]["expected"] == "180.00" and fin["debtors"][0]["group_name"] == group["name"]

    charge = api.get(f"{V}/{vid}/rent", owner, params={"month": "2026-11"})["items"][0]
    api.post(f"{V}/{vid}/rent/settle", owner, json={"charge_ids": [charge["id"]], "method": "pix"})
    fin = api.get(f"{V}/{vid}/finance", owner)
    assert fin["received"] == "180.00" and fin["pending"] == "0" and fin["collection_rate"] == 1.0
    assert fin["by_method"] == [{"method": "pix", "count": 1, "amount": "180.00"}] and fin["debtors"] == []
    assert fin["average_per_match"] == "180.00"

    csv = api.c.get(f"/api/v1{V}/{vid}/finance/export", params={"month": "2026-11"},
                    headers={"Authorization": owner["Authorization"]})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert "Pelada dos Amigos;Quadra 1" in csv.text and "180,00;Pago" in csv.text
    assert admin["_email"] not in csv.text  # nada de contato de administradores ou atletas
    assert db_audit(api, owner)  # exportação auditada

    # só o dono vê o financeiro: gerente e estranhos não
    manager = api.user("Gerente")
    api.post(f"{V}/{vid}/staff", owner, 201, json={"email": manager["_email"], "name": "Gerente"})
    api.get(f"{V}/{vid}/finance", manager, 403)
    api.c.get(f"/api/v1{V}/{vid}/finance/export", headers={"Authorization": manager["Authorization"]})
    assert api.c.get(f"/api/v1{V}/{vid}/finance/export",
                     headers={"Authorization": manager["Authorization"]}).status_code == 403
    api.get(f"{V}/{vid}/finance", api.user("Estranho"), 404)
    assert match["id"]


def db_audit(api, owner) -> bool:
    from app.core.db import SessionLocal

    with SessionLocal() as s:
        return s.scalar(select(AuditLog.id).where(AuditLog.action == "venue.finance_exported")) is not None


def test_owner_never_sees_other_venues_nor_groups_that_left(apiclient, clock):
    api = apiclient
    owner = api.user("Dono")
    venue, court = _venue(api, owner)
    vid = venue["id"]
    admin, group, _, link = _linked_group(api, owner, venue)
    api.post(f"{G}/{group['id']}/matches", admin, 201, json=match_payload(location=None, court_id=court["id"]))

    # outro espaço, com a sua própria pelada: o primeiro dono não enxerga nada dele
    rival = api.user("Rival")
    venue2, court2 = _venue(api, rival, name="Arena Rival")
    admin2 = api.user("AdminRival")
    link2 = api.post(f"{V}/{venue2['id']}/groups", rival, 201, json={
        "name": "Racha do Rival", "admin_email": admin2["_email"], "admin_name": "AdminRival"})
    api.post(f"{G}/{link2['group_id']}/matches", admin2, 201,
             json=match_payload(location=None, court_id=court2["id"]))
    clock.advance(days=2, hours=1)

    seen = api.get(f"{V}/{vid}/groups", owner)
    assert [g["group_id"] for g in seen] == [group["id"]]
    assert [a["email"] for a in seen[0]["admins"]] == [admin["_email"]]
    fin = api.get(f"{V}/{vid}/finance", owner)
    assert [d["group_id"] for d in fin["debtors"]] == [group["id"]] and fin["expected"] == "180.00"
    api.get(f"{V}/{venue2['id']}/finance", owner, 404)
    api.get(f"{V}/{venue2['id']}/groups", owner, 404)
    api.patch(f"{V}/{vid}/groups/{link2['id']}", owner, 404, json={"rent_hourly_price": "1.00"})
    api.post(f"{V}/{vid}/groups/{link2['id']}/admin", owner, 404,
             json={"email": "x@example.com", "name": "Xis"})
    other_charge = api.get(f"{V}/{venue2['id']}/rent", rival, params={"month": "2026-11"})["items"][0]
    api.patch(f"{V}/{vid}/rent/{other_charge['id']}", owner, 404, json={"status": "paid"})
    api.post(f"{V}/{vid}/rent/settle", owner, 404, json={"charge_ids": [other_charge["id"]]})

    # grupo que saiu do espaço: o dono mantém o histórico de valores, mas perde o contato dos administradores
    api.call("DELETE", f"{G}/{group['id']}/venue", admin)
    ended = api.get(f"{V}/{vid}/groups", owner, params={"status": "ended"})
    assert ended[0]["group_id"] == group["id"] and ended[0]["admins"] == []
