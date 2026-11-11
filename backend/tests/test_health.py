import asyncio
import time

from app.core.config import settings
from app.services import scheduler


def test_health_ok_com_agendador_desligado(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok", "scheduler": "disabled"}


def test_health_acusa_agendador_parado(client, monkeypatch):
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    monkeypatch.setattr(scheduler, "heartbeat", scheduler.Heartbeat())
    assert client.get("/health").status_code == 503  # nunca iniciou

    agora = time.monotonic()
    monkeypatch.setattr(scheduler, "heartbeat", scheduler.Heartbeat(started_at=agora - 3600, last_ok_at=agora - 3600))
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["scheduler"] == "stale"

    monkeypatch.setattr(scheduler, "heartbeat", scheduler.Heartbeat(started_at=agora - 3600, last_ok_at=agora))
    assert client.get("/health").json()["scheduler"] == "ok"


def test_agendador_sobrevive_a_falha_no_ciclo(monkeypatch):
    monkeypatch.setattr(settings, "scheduler_interval_seconds", 0.01)
    monkeypatch.setattr(scheduler, "heartbeat", scheduler.Heartbeat())
    chamadas = []

    def ciclo():
        chamadas.append(1)
        if len(chamadas) == 1:
            raise RuntimeError("banco fora do ar")
        return {}

    monkeypatch.setattr(scheduler, "run_jobs_once", ciclo)

    async def rodar():
        stop = asyncio.Event()
        task = asyncio.create_task(scheduler.scheduler_loop(stop))
        while len(chamadas) < 3:
            await asyncio.sleep(0.01)
        stop.set()
        await task

    asyncio.run(rodar())
    assert len(chamadas) >= 3
    assert scheduler.heartbeat.last_ok_at is not None
