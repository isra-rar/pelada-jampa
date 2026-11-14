"""Duplicar pelada (sem jogadores) e ativar recorrência numa pelada avulsa."""

from datetime import datetime, timedelta

from tests.conftest import T0, match_payload
from tests.test_api_flows import G, setup_group


def test_duplicate_copies_settings_without_players(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 2)
    gid = group["id"]
    first = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(price="25.00", slots=6))
    api.post(f"/matches/{first['id']}/confirm", players[0], json={})

    new_start = datetime.fromisoformat(first["starts_at"].replace("Z", "+00:00")) + timedelta(days=7)
    copy = api.post(f"/matches/{first['id']}/duplicate", admin, 201, json={"starts_at": new_start.isoformat()})
    assert copy["id"] != first["id"] and copy["series_id"] is None
    assert (copy["name"], copy["price"], copy["slots"], copy["location"]) == (first["name"], "25.00", 6, first["location"])
    assert copy["duration_minutes"] == first["duration_minutes"] and copy["status"] == "published"
    assert copy["counts"]["confirmed"] == 0  # sem jogadores
    api.post(f"/matches/{first['id']}/duplicate", players[0], 403, json={"starts_at": new_start.isoformat()})


def test_duplicate_can_already_be_recurring(apiclient):
    api = apiclient
    admin, group, _ = setup_group(api, 1)
    first = api.post(f"{G}/{group['id']}/matches", admin, 201, json=match_payload())
    new_start = datetime.fromisoformat(first["starts_at"].replace("Z", "+00:00")) + timedelta(days=7)
    copy = api.post(f"/matches/{first['id']}/duplicate", admin, 201, json={
        "starts_at": new_start.isoformat(), "recurrence": "weekly", "keep_ahead": 2,
    })
    assert copy["series_id"] is not None
    series = api.get(f"{G}/{group['id']}/series", admin)
    assert len(series) == 1 and series[0]["recurrence"] == "weekly" and series[0]["auto_generate_ahead"] == 2
    listed = api.get(f"{G}/{group['id']}/matches", admin, params={"status": "published"})["items"]
    assert len(listed) == 4  # a original, a cópia e 2 geradas à frente


def test_make_recurring_on_existing_match(apiclient):
    api = apiclient
    admin, group, players = setup_group(api, 1)
    gid = group["id"]
    m = api.post(f"{G}/{gid}/matches", admin, 201, json=match_payload(price="30.00"))
    out = api.post(f"/matches/{m['id']}/recurrence", admin, json={"recurrence": "weekly", "keep_ahead": 2})
    assert out["series_id"] is not None and out["id"] == m["id"]
    series = api.get(f"{G}/{gid}/series", admin)[0]
    assert series["price"] == "30.00" and series["name"] == m["name"]
    nxt = api.get(f"{G}/{gid}/matches", admin, params={"status": "published"})["items"]
    assert len(nxt) == 3 and all(x["series_id"] == series["id"] for x in nxt)
    api.post(f"/matches/{m['id']}/recurrence", admin, 422, json={"recurrence": "weekly"})  # já recorrente
    api.post(f"/matches/{m['id']}/recurrence", players[0], 403, json={"recurrence": "weekly"})
