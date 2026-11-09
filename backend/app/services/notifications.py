"""Notificações internas. Ponto único de saída: também despacha Web Push quando configurado."""

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import NotFound
from app.domain.enums import NotificationType
from app.models import Notification
from app.services import push

_BRASILIA = timezone(timedelta(hours=-3))


def when(dt: datetime) -> str:
    """Data e hora no horário de Brasília para textos de aviso (ex.: "07/11 21:00")."""
    return dt.astimezone(_BRASILIA).strftime("%d/%m %H:%M")


def notify(
    db: Session,
    user_ids: Iterable[int],
    type_: NotificationType,
    title: str,
    body: str,
    *,
    group_id: int | None = None,
    match_id: int | None = None,
    url: str | None = None,
) -> int:
    """`url` define para onde o push leva (padrão: a pelada ou o grupo)."""
    unique = list(dict.fromkeys(user_ids))  # remove duplicados preservando ordem
    for uid in unique:
        db.add(Notification(user_id=uid, type=type_, title=title, body=body, group_id=group_id, match_id=match_id))
    push.enqueue(db, unique, title, body, url or push.target_url(group_id, match_id))
    return len(unique)


def list_for_user(db: Session, user_id: int, *, unread_only: bool, page: int, size: int):
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).offset((page - 1) * size).limit(size)
    ).all()
    return list(items), total


def unread_count(db: Session, user_id: int) -> int:
    return (
        db.scalar(
            select(func.count()).where(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
        or 0
    )


def mark_read(db: Session, user_id: int, notification_id: int) -> Notification:
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user_id:
        raise NotFound("Notificação não encontrada")
    if n.read_at is None:
        n.read_at = utcnow()
    db.commit()
    return n


def mark_all_read(db: Session, user_id: int) -> int:
    result = db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    db.commit()
    return result.rowcount or 0
