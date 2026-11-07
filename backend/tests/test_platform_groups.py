"""Gestão de grupos pela administração da plataforma: consulta, suspensão (somente leitura) e suporte a grupos
sem administrador."""

from sqlalchemy import update

from app.domain.enums import MemberStatus
from app.models import GroupMember
from app.services.scheduler import run_jobs_once
from tests.conftest import match_payload
from tests.test_api_flows import G, setup_group
from tests.test_completion import _make_platform_admin


def _support(api, db):
    h = api.user("Suporte")
    _make_platform_admin(db, h)
    return h


def _types(api, headers) -> list[str]:
    return [n["type"] for n in api.get("/notifications", headers)["items"]]


def test_list_filters_and_detail(apiclient, db):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    api.post(G, players[0], 201, json={"name": "Racha de sábado"})
    support = _support(api, db)

    api.get("/admin/groups", admin, 403)
    listed = api.get("/admin/groups", support)
    assert listed["total"] == 2
    row = next(g for g in listed["items"] if g["id"] == gid)
    assert row["members"] == 3 and row["admins"] == 1 and row["last_match_at"] is None
    assert api.get("/admin/groups", support, params={"search": "racha"})["total"] == 1

    api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload())
    detail = api.get(f"/admin/groups/{gid}", support)
    assert detail["creator_email"] == admin["_email"]
    assert detail["matches_upcoming"] == 1 and detail["next_match_at"] is not None
    assert [m["role"] for m in detail["active_members"]] == ["admin", "player", "player"]
    assert detail["history"] == []
    api.get("/admin/groups/999", support, 404)


def test_suspension_makes_group_read_only(apiclient, clock, db):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload())["id"]
    support = _support(api, db)
    outsider = api.user("Curioso")

    api.post(f"/admin/groups/{gid}/suspend", support, 422, json={"reason": "x"})  # motivo obrigatório
    out = api.post(f"/admin/groups/{gid}/suspend", support, json={"reason": "Denúncia de cobrança abusiva"})
    assert out["suspended_at"] and not out["is_active"]
    assert out["suspension_reason"] == "Denúncia de cobrança abusiva"
    assert out["history"][0]["action"] == "platform.group_suspended"
    err = api.post(f"/admin/groups/{gid}/suspend", support, 422, json={"reason": "De novo, por engano"})
    assert err["error"]["code"] == "already_suspended"
    assert "group_status" in _types(api, players[0]) and "group_status" in _types(api, admin)

    # leitura continua; qualquer alteração é barrada para os membros
    seen = api.get(f"{G}/{gid}", players[0])
    assert seen["suspended_at"] and seen["suspension_reason"] == "Denúncia de cobrança abusiva"
    api.get(f"{G}/{gid}/matches", players[0])
    err = api.post(f"/matches/{mid}/confirm", players[0], 403, json={})
    assert err["error"]["code"] == "group_suspended"
    assert err["error"]["details"]["reason"] == "Denúncia de cobrança abusiva"
    api.patch(f"{G}/{gid}", admin, 403, json={"is_active": True})
    api.post(f"/invites/{group['invite_token']}/join", outsider, 404)
    api.post(f"/matches/{mid}/confirm", outsider, 404, json={})  # quem não participa não descobre nada
    assert api.get(f"/admin/groups/{gid}", support)["matches_upcoming"] == 1

    # grupo suspenso não recebe lembrete de confirmação
    clock.advance(hours=36)
    run_jobs_once()
    assert "confirmation_deadline" not in _types(api, players[1])

    api.post(f"/admin/groups/{gid}/reactivate", support, json={"reason": "Situação esclarecida"})
    assert api.get(f"{G}/{gid}", players[0])["suspended_at"] is None
    api.post(f"/matches/{mid}/confirm", players[0], json={})
    run_jobs_once()
    assert "confirmation_deadline" in _types(api, players[1])
    err = api.post(f"/admin/groups/{gid}/reactivate", support, 422, json={})
    assert err["error"]["code"] == "already_active"

    actions = [e["action"] for e in api.get(f"{G}/{gid}/audit", admin)["items"]]
    assert "platform.group_suspended" in actions and "platform.group_reactivated" in actions


def test_support_assigns_admin_to_orphan_group(apiclient, db):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    support = _support(api, db)
    # o único administrador saiu do grupo (ex.: ajuste feito direto no banco)
    db.execute(update(GroupMember).where(GroupMember.id == admin["_member_id"]).values(status=MemberStatus.REMOVED))
    db.commit()

    orphans = api.get("/admin/groups", support, params={"status": "no_admin"})
    assert [g["id"] for g in orphans["items"]] == [gid]

    api.patch(f"/admin/groups/{gid}/members/{admin['_member_id']}", support, 422, json={"role": "admin"})
    out = api.patch(f"/admin/groups/{gid}/members/{players[0]['_member_id']}", support,
                    json={"role": "admin", "reason": "Pedido dos jogadores"})
    assert out["admins"] == 1 and out["history"][0]["action"] == "platform.group_role_changed"
    assert api.get(f"{G}/{gid}", players[0])["my_role"] == "admin"
    assert "membership" in _types(api, players[0])
    assert api.get("/admin/groups", support, params={"status": "no_admin"})["total"] == 0

    err = api.patch(f"/admin/groups/{gid}/members/{players[0]['_member_id']}", support, 422,
                    json={"role": "player"})
    assert err["error"]["code"] == "last_admin"
    api.patch(f"/admin/groups/{gid}/members/{players[0]['_member_id']}", players[0], 403, json={"role": "player"})


def test_reactivating_empty_group_is_refused(apiclient, db):
    api = apiclient
    admin, group, _ = setup_group(api, 0)
    gid = group["id"]
    support = _support(api, db)
    api.post("/me/delete", admin, json={"confirm": "EXCLUIR", "password": "senha-segura"})
    assert api.get("/admin/groups", support, params={"status": "inactive"})["total"] == 1
    err = api.post(f"/admin/groups/{gid}/reactivate", support, 422, json={})
    assert err["error"]["code"] == "group_without_members"
