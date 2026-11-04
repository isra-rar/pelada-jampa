"""Administração da plataforma (suporte)."""

from fastapi import APIRouter
from sqlalchemy import func, or_, select

from app.api.deps import DB, Paging, PlatformAdmin, page_of
from app.core.errors import BusinessRuleError, NotFound
from app.models import FootballGroup, User
from app.schemas.auth import PlatformUserUpdate, UserOut
from app.schemas.common import Page
from app.services import audit

router = APIRouter(prefix="/admin", tags=["Administração da plataforma"])


@router.get("/users", response_model=Page[UserOut])
def list_users(admin: PlatformAdmin, db: DB, paging: Paging, search: str | None = None):
    stmt = select(User)
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(or_(func.lower(User.name).like(like), func.lower(User.email).like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(User.id).offset((paging.page - 1) * paging.size).limit(paging.size)).all()
    return page_of(list(items), total, paging)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, data: PlatformUserUpdate, admin: PlatformAdmin, db: DB):
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("Usuário não encontrado")
    if user.id == admin.id and data.is_platform_admin is False:
        raise BusinessRuleError("Você não pode remover seu próprio acesso administrativo")
    before = audit.snapshot(user, ["is_active", "is_platform_admin"])
    for key, value in data.model_dump(exclude_none=True).items():
        setattr(user, key, value)
    audit.record(db, action="platform.user_updated", entity_type="user", entity_id=user.id,
                 actor_user_id=admin.id, group_id=None, before=before,
                 after=audit.snapshot(user, ["is_active", "is_platform_admin"]))
    db.commit()
    return user


@router.get("/groups")
def list_groups(admin: PlatformAdmin, db: DB, paging: Paging):
    total = db.scalar(select(func.count()).select_from(FootballGroup)) or 0
    rows = db.scalars(
        select(FootballGroup).order_by(FootballGroup.id).offset((paging.page - 1) * paging.size).limit(paging.size)
    ).all()
    items = [{"id": g.id, "name": g.name, "is_active": g.is_active, "created_at": g.created_at} for g in rows]
    return page_of(items, total, paging)
