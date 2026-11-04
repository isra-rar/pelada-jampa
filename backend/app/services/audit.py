from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditLog


def snapshot(obj: Any, fields: list[str]) -> dict:
    return jsonable_encoder({f: getattr(obj, f) for f in fields})


def record(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: int | None,
    actor_user_id: int | None,
    group_id: int | None,
    before: dict | None = None,
    after: dict | None = None,
    reason: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        group_id=group_id,
        before=jsonable_encoder(before) if before is not None else None,
        after=jsonable_encoder(after) if after is not None else None,
        reason=reason,
    )
    db.add(entry)
    return entry


def list_group_audit(
    db: Session,
    group_id: int,
    *,
    entity_type: str | None,
    entity_id: int | None,
    page: int,
    size: int,
) -> tuple[list[AuditLog], int]:
    stmt = select(AuditLog).where(AuditLog.group_id == group_id)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset((page - 1) * size).limit(size)
    ).all()
    return list(items), total
