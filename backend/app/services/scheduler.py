"""Tarefas periódicas: encerramento de avaliações, lembretes, expiração de ofertas de vaga, geração das
próximas ocorrências das peladas recorrentes e lançamento do aluguel das quadras.

Todas as tarefas são idempotentes; rodar em mais de uma instância não gera efeitos duplicados
porque o encerramento usa lock de linha e os lembretes marcam a data de envio.
"""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal, utcnow
from app.domain.enums import AttendanceStatus, MatchStatus, NotificationType
from app.models import FootballGroup, Match, MatchPlayer
from app.services import attendance, evaluations, matches, notifications, venue_finance

log = logging.getLogger("pelada.scheduler")


def send_confirmation_reminders(db: Session) -> int:
    now = utcnow()
    limit = now + timedelta(hours=settings.confirmation_reminder_hours_before)
    due = db.scalars(
        select(Match).join(FootballGroup, FootballGroup.id == Match.group_id).where(
            FootballGroup.suspended_at.is_(None),  # grupo suspenso: somente leitura, sem lembretes
            Match.status == MatchStatus.PUBLISHED,
            Match.confirmation_reminder_sent_at.is_(None),
            Match.confirmation_deadline > now,
            Match.confirmation_deadline <= limit,
        )
    ).all()
    sent = 0
    for match in due:
        users = [
            mp.member.user_id
            for mp in db.scalars(
                select(MatchPlayer).where(
                    MatchPlayer.match_id == match.id, MatchPlayer.status == AttendanceStatus.PENDING
                )
            ).all()
        ]
        sent += notifications.notify(
            db, users, NotificationType.CONFIRMATION_DEADLINE, f"Confirme presença em {match.name}",
            f"O prazo termina em {notifications.when(match.confirmation_deadline)}.",
            group_id=match.group_id, match_id=match.id,
        )
        match.confirmation_reminder_sent_at = now
    db.commit()
    return sent


def expire_waitlist_offers(db: Session) -> int:
    rows = db.execute(
        select(Match, FootballGroup)
        .join(FootballGroup, FootballGroup.id == Match.group_id)
        .join(MatchPlayer, MatchPlayer.match_id == Match.id)
        .where(
            FootballGroup.suspended_at.is_(None),
            Match.status == MatchStatus.PUBLISHED,
            Match.starts_at > utcnow(),
            MatchPlayer.promotion_offered_at.is_not(None),
        )
        .distinct()
    ).all()
    total = 0
    for match, group in rows:
        total += attendance.expire_offers(db, group, match)
    db.commit()
    return total


def run_jobs_once() -> dict:
    results = {}
    with SessionLocal() as db:
        for name, job in (
            ("evaluations_closed", evaluations.close_due_windows),
            ("evaluation_reminders", evaluations.send_closing_reminders),
            ("confirmation_reminders", send_confirmation_reminders),
            ("offers_expired", expire_waitlist_offers),
            ("series_generated", matches.auto_generate_due),
            ("rent_charges", venue_finance.sync_rent_job),
        ):
            try:
                results[name] = job(db)
            except Exception:  # pragma: no cover - log e segue para a próxima tarefa
                db.rollback()
                log.exception("Falha na tarefa %s", name)
    return results


async def scheduler_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.to_thread(run_jobs_once)
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.scheduler_interval_seconds)
        except TimeoutError:
            pass
