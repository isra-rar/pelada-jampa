"""Quiz de autoavaliação, nível declarado pelo jogador e confirmação do administrador."""

import pytest
from sqlalchemy import select

from app.domain import rating as R
from app.domain import self_assessment as SA
from app.models import GroupMember, PlayerRating
from app.services import level_quiz
from tests.conftest import match_payload
from tests.test_api_flows import G, setup_group

ALL_TOP = {"finishing": 4, "passing": 4, "dribbling": 4, "defense": 4, "physical": 4}
MIDDLE = {"finishing": 2, "passing": 2, "dribbling": 2, "defense": 2, "physical": 2}  # 3,0 → 3★


def quiz(answers=MIDDLE, declared=3):
    return {"answers": answers, "declared_stars": declared}


def test_domain_scores_and_validation():
    scores = SA.scores_from_answers({"finishing": 0, "passing": 1, "dribbling": 2, "defense": 3, "physical": 4})
    assert scores == {"finishing": 1.0, "passing": 2.0, "dribbling": 3.0, "defense": 4.0, "physical": 4.5}
    with pytest.raises(ValueError):
        SA.validate_answers({"finishing": 1})
    with pytest.raises(ValueError):
        SA.validate_answers({**ALL_TOP, "passing": 5})
    with pytest.raises(ValueError):
        SA.validate_answers({**ALL_TOP, "passing": True})


def test_domain_attributes_for_stars_keeps_profile():
    keys = R.FIELD_ATTRIBUTES
    attrs = {"finishing": 2.0, "passing": 3.0, "dribbling": 4.0, "defense": 3.0, "physical": 3.0}  # 3★
    assert R.attributes_for_stars(attrs, keys, 3) == attrs  # mesmo nível: nada muda
    up = R.attributes_for_stars(attrs, keys, 4)
    assert R.compute_stars(R.compute_average(up, keys)) == 4
    assert up["dribbling"] - up["finishing"] == pytest.approx(2.0)  # pontos fortes preservados
    top = R.attributes_for_stars(attrs, keys, 5)
    assert R.compute_stars(R.compute_average(top, keys)) == 5
    zero = R.attributes_for_stars(attrs, keys, 0)
    assert R.compute_stars(R.compute_average(zero, keys)) == 0
    assert R.target_average(4) == 4.0 and R.target_average(5) == 4.75 and R.target_average(0) == 0.25


def test_quiz_with_declared_level_and_admin_confirmation(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    me, mid = players[0], players[0]["_member_id"]

    assert api.get(f"{G}/{gid}", me)["my_self_assessment_pending"] is True
    assert group["my_self_assessment_pending"] is True  # quem cria o grupo também responde

    q = api.get(f"{G}/{gid}/self-assessment", me)
    assert q["pending"] and q["status"] == "not_answered" and len(q["questions"]) == 5
    assert {x["attribute"] for x in q["questions"]} == set(ALL_TOP)
    assert q["star_labels"]["5"] == "Craque"

    api.post(f"{G}/{gid}/self-assessment", me, 422, json={"answers": {"finishing": 2}, "declared_stars": 3})
    api.post(f"{G}/{gid}/self-assessment", me, 422, json={"answers": MIDDLE})  # nível declarado é obrigatório
    api.post(f"{G}/{gid}/self-assessment", me, 422, json=quiz(declared=6))

    out = api.post(f"{G}/{gid}/self-assessment", me, json=quiz(MIDDLE, declared=4))
    assert out["applied"] is True
    # até o admin confirmar, vale o nível calculado pelo quiz
    assert out["rating"]["average"] == 3.0 and out["rating"]["stars"] == 3
    sa = out["self_assessment"]
    assert sa["status"] == "to_review" and sa["quiz_stars"] == 3 and sa["declared_stars"] == 4
    assert sa["confirmed_stars"] is None
    assert api.get(f"{G}/{gid}", me)["my_self_assessment_pending"] is False
    api.post(f"{G}/{gid}/self-assessment", me, 422, json=quiz())  # só uma vez

    # admin recebe aviso para confirmar
    notes = api.get("/notifications", admin)["items"]
    assert any(n["title"] == "Nível para confirmar" and "4 estrelas" in n["body"] for n in notes)
    dash = api.get(f"{G}/{gid}/dashboard", admin)
    assert dash["levels_to_review"] == 1 and dash["quiz_not_answered"] == 2  # admin + o outro jogador

    # flag por membro: admin vê de todos; jogador comum só a própria
    by_id = {m["id"]: m for m in api.get(f"{G}/{gid}/members", admin)}
    assert by_id[mid]["self_assessment"]["status"] == "to_review"
    assert by_id[players[1]["_member_id"]]["self_assessment"]["status"] == "not_answered"
    seen = {m["id"]: m for m in api.get(f"{G}/{gid}/members", players[1])}
    assert seen[mid]["self_assessment"] is None
    assert seen[players[1]["_member_id"]]["self_assessment"]["status"] == "not_answered"

    # só admin confirma; quem não respondeu não tem o que confirmar
    api.post(f"{G}/{gid}/members/{mid}/level-review", me, 403, json={"stars": 4})
    api.post(f"{G}/{gid}/members/{players[1]['_member_id']}/level-review", admin, 422, json={"stars": 3})
    api.post(f"{G}/{gid}/members/{mid}/level-review", admin, 422, json={"stars": 7})

    review = api.post(f"{G}/{gid}/members/{mid}/level-review", admin, json={"stars": 4})
    assert review["applied"] is True and review["rating"]["stars"] == 4 and review["rating"]["is_provisional"]
    assert review["self_assessment"]["status"] == "confirmed" and review["self_assessment"]["confirmed_stars"] == 4
    hist = api.get(f"{G}/{gid}/players/{mid}/rating-history", me)["items"]
    assert hist[0]["reason"] == "level_confirmed" and hist[0]["stars_before"] == 3 and hist[0]["stars_after"] == 4
    assert any("confirmado: 4 estrelas" in n["body"] for n in api.get("/notifications", me)["items"])
    assert api.get(f"{G}/{gid}/dashboard", admin)["levels_to_review"] == 0


def test_admin_can_adjust_down_and_existing_members_are_asked(apiclient, db):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    gid, mid = group["id"], players[0]["_member_id"]
    # membro que já estava no grupo (sem quiz) continua com o quiz pendente
    member = db.scalar(select(GroupMember).where(GroupMember.id == mid))
    assert member.self_assessed_at is None and level_quiz.is_pending(db, member)

    api.post(f"{G}/{gid}/self-assessment", players[0], json=quiz(ALL_TOP, declared=5))
    review = api.post(f"{G}/{gid}/members/{mid}/level-review", admin, json={"stars": 2, "reason": "Exagerou"})
    assert review["rating"]["stars"] == 2
    notes = api.get("/notifications", players[0])["items"]
    assert any("ajustado para 2 estrelas" in n["body"] for n in notes)


def test_admin_rating_before_quiz_is_kept(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    gid, mid = group["id"], players[0]["_member_id"]
    api.put(f"{G}/{gid}/players/{mid}/provisional-rating", admin,
            json={"attributes": {k: 2.0 for k in ALL_TOP}})
    # o quiz continua pendente (a flag é sobre responder), mas não sobrescreve a nota do admin
    assert api.get(f"{G}/{gid}", players[0])["my_self_assessment_pending"] is True
    out = api.post(f"{G}/{gid}/self-assessment", players[0], json=quiz(ALL_TOP, declared=5))
    assert out["applied"] is False and out["rating"]["stars"] == 2
    assert out["self_assessment"]["quiz_stars"] == 5 and out["self_assessment"]["status"] == "to_review"


def test_quiz_after_approval_and_not_repeated_on_rejoin(apiclient):
    api = apiclient
    admin, group, _ = setup_group(api, 0, require_join_approval=True)
    gid = group["id"]
    h = api.user("Novato")
    joined = api.post(f"/invites/{group['invite_token']}/join", h)
    assert joined["status"] == "pending"
    api.patch(f"{G}/{gid}/members/{joined['member_id']}", admin, json={"status": "active"})
    assert api.get(f"{G}/{gid}", h)["my_self_assessment_pending"] is True
    api.post(f"{G}/{gid}/self-assessment", h, json=quiz())

    # removido e aprovado de novo: não responde outra vez
    api.patch(f"{G}/{gid}/members/{joined['member_id']}", admin, json={"status": "removed"})
    api.post(f"/invites/{group['invite_token']}/join", h)
    api.patch(f"{G}/{gid}/members/{joined['member_id']}", admin, json={"status": "active"})
    assert api.get(f"{G}/{gid}", h)["my_self_assessment_pending"] is False


def test_players_filter_by_stars(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    api.post(f"{G}/{gid}/self-assessment", players[0], json=quiz(ALL_TOP, declared=5))
    five = api.get(f"{G}/{gid}/players", admin, params={"stars": 5})
    assert [p["member_id"] for p in five] == [players[0]["_member_id"]] and five[0]["stars"] == 5
    assert len(api.get(f"{G}/{gid}/players", admin, params={"stars": 3})) == 2  # admin + jogador sem quiz
    api.get(f"{G}/{gid}/players", admin, 422, params={"stars": 6})


GK_ANSWERS = {"reflexes": 3, "positioning": 3, "rushing_out": 3, "distribution": 3, "one_on_one": 3}  # 4,0 → 4★


def _rating(db, member_id, kind="field"):
    db.expire_all()
    return db.scalar(select(PlayerRating).where(PlayerRating.member_id == member_id, PlayerRating.kind == kind))


def _level(api, admin, gid, member_id):
    return next(m for m in api.get(f"{G}/{gid}/members", admin) if m["id"] == member_id)["self_assessment"]


def test_goalkeeper_quiz_sets_position_and_goalkeeper_rating(apiclient, db):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    gid, mid = group["id"], players[0]["_member_id"]
    q = api.get(f"{G}/{gid}/self-assessment", players[0])
    assert q["is_goalkeeper"] is False and len(q["goalkeeper_questions"]) == 5
    assert {x["attribute"] for x in q["goalkeeper_questions"]} == set(GK_ANSWERS)
    # perguntas de goleiro exigem os atributos de goleiro
    api.post(f"{G}/{gid}/self-assessment", players[0], 422,
             json={"kind": "goalkeeper", "answers": MIDDLE, "declared_stars": 4})
    out = api.post(f"{G}/{gid}/self-assessment", players[0],
                   json={"kind": "goalkeeper", "answers": GK_ANSWERS, "declared_stars": 5})
    assert out["applied"] and out["rating"]["kind"] == "goalkeeper" and out["rating"]["stars"] == 4
    assert out["self_assessment"]["kind"] == "goalkeeper" and out["self_assessment"]["quiz_stars"] == 4
    me = next(m for m in api.get(f"{G}/{gid}/members", admin) if m["id"] == mid)
    assert me["preferred_position"] == "goalkeeper"
    assert _rating(db, mid, "field").stars == 3  # linha continua na nota inicial
    # admin confirma o nível de goleiro
    review = api.post(f"{G}/{gid}/members/{mid}/level-review", admin, json={"stars": 5})
    assert review["rating"]["kind"] == "goalkeeper" and review["rating"]["stars"] == 5


def test_goalkeeper_quiz_unavailable_in_general_mode(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 1, goalkeeper_rating_mode="general")
    gid = group["id"]
    assert api.get(f"{G}/{gid}/self-assessment", players[0])["goalkeeper_questions"] is None
    api.post(f"{G}/{gid}/self-assessment", players[0], 422,
             json={"kind": "goalkeeper", "answers": GK_ANSWERS, "declared_stars": 4})


def test_consolidated_members_are_exempt(apiclient, db):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid, vet = group["id"], players[0]["_member_id"]
    rating = _rating(db, vet)
    rating.is_provisional = False
    db.commit()
    assert api.get(f"{G}/{gid}", players[0])["my_self_assessment_pending"] is False
    assert api.get(f"{G}/{gid}/self-assessment", players[0])["status"] == "exempt"
    api.post(f"{G}/{gid}/self-assessment", players[0], 422, json=quiz())
    assert _level(api, admin, gid, vet)["status"] == "exempt"
    assert api.get(f"{G}/{gid}/dashboard", admin)["quiz_not_answered"] == 2  # admin + jogador novo


def test_sole_admin_is_auto_confirmed_and_admins_review_each_other(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    gid = group["id"]
    out = api.post(f"{G}/{gid}/self-assessment", admin, json=quiz(MIDDLE, declared=4))
    # único admin: o nível declarado vale na hora
    assert out["self_assessment"]["status"] == "confirmed" and out["rating"]["stars"] == 4

    # com outro admin, o nível precisa ser confirmado por ele
    other = players[0]
    api.patch(f"{G}/{gid}/members/{other['_member_id']}", admin, json={"role": "admin"})
    api.post(f"{G}/{gid}/self-assessment", other, json=quiz(MIDDLE, declared=5))
    assert _level(api, admin, gid, other["_member_id"])["status"] == "to_review"
    r = api.post(f"{G}/{gid}/members/{other['_member_id']}/level-review", other, 422, json={"stars": 5})
    assert r["error"]["code"] == "self_review_not_allowed"
    api.post(f"{G}/{gid}/members/{other['_member_id']}/level-review", admin, json={"stars": 4})
    # o primeiro admin também só pode ser revisado pelo outro agora
    api.post(f"{G}/{gid}/members/{admin['_member_id']}/level-review", admin, 422, json={"stars": 3})
    api.post(f"{G}/{gid}/members/{admin['_member_id']}/level-review", other, json={"stars": 3})


def test_manual_and_automatic_reminders(apiclient, clock, db):
    api = apiclient
    admin, group, players = setup_group(api, 3)
    gid = group["id"]
    api.post(f"{G}/{gid}/self-assessment", players[0], json=quiz())
    api.post(f"{G}/{gid}/self-assessment/remind", players[1], 403)
    assert api.post(f"{G}/{gid}/self-assessment/remind", admin)["reminded"] == 2  # o admin não se lembra
    assert api.post(f"{G}/{gid}/self-assessment/remind", admin)["reminded"] == 0  # espera 12 horas
    notes = api.get("/notifications", players[1])["items"]
    assert any(n["title"] == "Responda o quiz de nível" for n in notes)

    # automático: 1 dia depois de entrar, a cada 3 dias, no máximo 3 vezes, das 9h às 21h de Brasília
    clock.advance(hours=12)  # 21h em Brasília: fora do horário
    assert level_quiz.send_auto_reminders(db) == 0
    clock.advance(hours=13)  # 10h do dia seguinte; o lembrete manual foi há 25h (< 3 dias)
    assert level_quiz.send_auto_reminders(db) == 1  # só o admin, que nunca foi lembrado
    clock.advance(days=3)
    assert level_quiz.send_auto_reminders(db) == 3
    clock.advance(days=3)
    assert level_quiz.send_auto_reminders(db) == 3
    clock.advance(days=3)
    assert level_quiz.send_auto_reminders(db) == 2  # o admin já recebeu 3 automáticos; os jogadores, o 3º agora
    clock.advance(days=3)
    assert level_quiz.send_auto_reminders(db) == 0


def test_draw_warns_about_unconfirmed_levels(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 3)
    gid = group["id"]
    mid = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(slots=4, field_players_per_team=2))["id"]
    for p in [admin, *players]:
        api.post(f"/matches/{mid}/confirm", p, json={})
    api.post(f"{G}/{gid}/self-assessment", players[0], json=quiz())
    api.post(f"{G}/{gid}/members/{players[0]['_member_id']}/level-review", admin, json={"stars": 3})
    draw = api.post(f"/matches/{mid}/draws", admin, 201, json={"num_teams": 2, "seed": 1})
    assert any("3 jogador(es) com nível ainda não confirmado" in w for w in draw["warnings"])


def test_self_estimate_after_evaluated_matches(apiclient, db):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    gid, mid = group["id"], players[0]["_member_id"]
    api.post(f"{G}/{gid}/self-assessment", players[0], json=quiz(MIDDLE, declared=5))
    rating = _rating(db, mid)
    rating.matches_evaluated_count = 2
    db.commit()
    assert _level(api, admin, gid, mid)["self_estimate"] is None  # poucas peladas avaliadas
    rating = _rating(db, mid)
    rating.matches_evaluated_count = 3
    db.commit()
    level = _level(api, admin, gid, mid)
    assert level["self_estimate"] == "over" and level["current_stars"] == 3
    rating = _rating(db, mid)
    rating.stars = 5
    db.commit()
    assert _level(api, admin, gid, mid)["self_estimate"] == "accurate"


def test_export_and_account_deletion_cover_quiz(apiclient, db):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    gid, mid = group["id"], players[0]["_member_id"]
    api.post(f"{G}/{gid}/self-assessment", players[0], json=quiz(MIDDLE, declared=4))
    data = api.c.get("/api/v1/me/export", headers={"Authorization": players[0]["Authorization"]}).json()
    exported = data["groups"][0]["self_assessment"]
    assert exported["answers"] == MIDDLE and exported["declared_stars"] == 4 and exported["quiz_stars"] == 3
    api.post("/me/delete", players[0], json={"password": "senha-segura", "confirm": "EXCLUIR"})
    db.expire_all()
    assert db.get(GroupMember, mid).self_assessment is None
