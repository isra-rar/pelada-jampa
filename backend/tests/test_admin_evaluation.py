"""Administrador do grupo pode avaliar a pelada mesmo sem ter jogado (configurável)."""

from app.core.config import settings
from tests.test_api_flows import G, play_match, setup_group

GOOD = {"finishing": 4, "passing": 4, "dribbling": 4, "defense": 4, "physical": 4}


def test_admin_without_presence_can_evaluate(apiclient, clock, monkeypatch):
    api = apiclient
    admin, group, players = setup_group(api, 4)
    mid, _ = play_match(api, clock, admin, group, players)
    ids = [p["_member_id"] for p in players]
    api.post(f"/matches/{mid}/presence", admin, json={"present": ids[:3], "absent": [ids[3]]})

    # o admin não jogou: avalia todos os presentes (e não aparece como alvo)
    t = api.get(f"/matches/{mid}/evaluations/targets", admin)
    assert t["total"] == 3 and {x["member_id"] for x in t["targets"]} == set(ids[:3])
    out = api.post(f"/matches/{mid}/evaluations", admin, 201,
                   json={"items": [{"evaluated_member_id": i, "scores": GOOD} for i in ids[:3]]})
    assert out == {"created": 3, "done": 3, "total": 3}
    r = api.post(f"/matches/{mid}/evaluations", admin, 422,
                 json={"items": [{"evaluated_member_id": admin["_member_id"], "scores": GOOD}]})
    assert r["error"]["code"] in ("self_evaluation", "target_not_present")

    # avaliações do admin não distorcem o progresso dos jogadores
    prog = api.get(f"/matches/{mid}/evaluations/progress", admin)
    assert prog["submitted"] == 0 and prog["completion_rate"] == 0.0

    # jogador ausente continua sem avaliar
    api.get(f"/matches/{mid}/evaluations/targets", players[3], 403)

    # a nota do admin conta na consolidação
    api.post(f"/matches/{mid}/evaluations/close", admin)
    prof = api.get(f"{G}/{group['id']}/players/{ids[0]}", admin)
    assert prof["ratings"][0]["valid_evaluations_count"] == 1

    # desligando a opção, o admin volta a ser bloqueado
    monkeypatch.setattr(settings, "admins_can_evaluate", False)
    r = api.get(f"/matches/{mid}/evaluations/targets", admin, 403)
    assert r["error"]["code"] == "not_present"
