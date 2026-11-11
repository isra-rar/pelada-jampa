"""Administração da plataforma (suporte, configurações globais e indicadores)."""

from datetime import date

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from app.api.deps import DB, Paging, PlatformAdmin, page_of
from app.core.errors import BusinessRuleError, NotFound
from app.models import User
from app.schemas.admin import (
    AdminGroupDetail,
    AdminGroupRow,
    AdminGroupStatus,
    AdminMemberRoleIn,
    GroupReactivateIn,
    GroupSuspendIn,
)
from app.schemas.auth import PlatformUserUpdate, UserOut
from app.schemas.common import Page
from app.schemas.draw import DrawCalibrationOut
from app.schemas.metrics import PlatformMetricsOut
from app.schemas.platform import PlatformSettingsOut, PlatformSettingsUpdate
from app.services import admin_groups, audit, metrics, platform
from app.services import draw_calibration as draw_calibration_service
from app.services.access import get_group, get_member_in_group

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
    if user.is_deleted:
        raise BusinessRuleError("Conta excluída pelo titular não pode ser reativada", code="user_deleted")
    before = audit.snapshot(user, ["is_active", "is_platform_admin"])
    for key, value in data.model_dump(exclude_none=True).items():
        setattr(user, key, value)
    audit.record(db, action="platform.user_updated", entity_type="user", entity_id=user.id,
                 actor_user_id=admin.id, group_id=None, before=before,
                 after=audit.snapshot(user, ["is_active", "is_platform_admin"]))
    db.commit()
    return user


@router.get("/groups", response_model=Page[AdminGroupRow])
def list_groups(
    admin: PlatformAdmin, db: DB, paging: Paging, search: str | None = None,
    status: AdminGroupStatus | None = Query(
        default=None, description="active, inactive (desativado pelo grupo), suspended ou no_admin (sem administrador)"
    ),
):
    """Grupos mais recentes primeiro, com membros e administradores ativos e a última pelada realizada."""
    items, total = admin_groups.list_groups(db, search=search, status=status, page=paging.page, size=paging.size)
    return page_of(items, total, paging)


@router.get("/groups/{group_id}", response_model=AdminGroupDetail)
def group_detail(group_id: int, admin: PlatformAdmin, db: DB):
    """Dados de suporte do grupo: responsáveis, membros ativos, peladas e ações da plataforma."""
    return admin_groups.detail(db, get_group(db, group_id))


@router.post("/groups/{group_id}/suspend", response_model=AdminGroupDetail)
def suspend_group(group_id: int, data: GroupSuspendIn, admin: PlatformAdmin, db: DB):
    """Suspende o grupo: fica inativo e somente leitura para os membros, que são avisados com o motivo."""
    group = admin_groups.suspend(db, get_group(db, group_id), admin, data.reason)
    return admin_groups.detail(db, group)


@router.post("/groups/{group_id}/reactivate", response_model=AdminGroupDetail)
def reactivate_group(group_id: int, data: GroupReactivateIn, admin: PlatformAdmin, db: DB):
    """Encerra a suspensão (ou reativa um grupo desativado). Exige ao menos um membro ativo."""
    group = admin_groups.reactivate(db, get_group(db, group_id), admin, data.reason)
    return admin_groups.detail(db, group)


@router.patch("/groups/{group_id}/members/{member_id}", response_model=AdminGroupDetail)
def set_group_member_role(group_id: int, member_id: int, data: AdminMemberRoleIn, admin: PlatformAdmin, db: DB):
    """Suporte: torna um membro ativo administrador do grupo (ou retira), mantendo ao menos um administrador."""
    group = get_group(db, group_id)
    admin_groups.set_member_role(db, group, get_member_in_group(db, group_id, member_id), data.role, admin,
                                 data.reason)
    return admin_groups.detail(db, group)


@router.get("/settings", response_model=PlatformSettingsOut)
def get_settings(admin: PlatformAdmin, db: DB):
    """Limites das estrelas e padrões de nota/sorteio aplicados a grupos novos."""
    return platform.settings_out(db)


@router.patch("/settings", response_model=PlatformSettingsOut)
def update_settings(data: PlatformSettingsUpdate, admin: PlatformAdmin, db: DB):
    """Altera as configurações globais. Mudar os limites das estrelas reclassifica todas as notas (as médias não
    mudam) e registra cada mudança no histórico do jogador. Os padrões só valem para grupos criados depois."""
    return platform.update(db, data, admin)


@router.get("/metrics", response_model=PlatformMetricsOut)
def platform_metrics(
    admin: PlatformAdmin, db: DB,
    since: date | None = Query(default=None, description="Data inicial (inclusiva); padrão: 30 dias"),
    until: date | None = Query(default=None, description="Data final (inclusiva); padrão: hoje"),
):
    """Usuários, grupos ativos, retenção de grupos e peladas realizadas no período."""
    return metrics.platform_metrics(db, since, until)


@router.get("/draws/calibration", response_model=DrawCalibrationOut)
def draw_calibration(
    admin: PlatformAdmin, db: DB,
    since: date | None = Query(default=None, description="Data inicial (inclusiva); padrão: 365 dias"),
    until: date | None = Query(default=None, description="Data final (inclusiva); padrão: hoje"),
):
    """Calibração do sorteio com os placares de todos os grupos, comparada aos pesos padrão de grupos novos.

    Para aplicar a sugestão, altere `group_defaults.draw_weights` em `PATCH /admin/settings`."""
    current = platform.group_defaults(db)["draw_weights"]
    return draw_calibration_service.calibration(db, None, current, since, until)
