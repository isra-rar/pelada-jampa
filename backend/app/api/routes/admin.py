"""Administração da plataforma (suporte, configurações globais e indicadores)."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Query, status
from sqlalchemy import func, or_, select

from app.api.deps import DB, Paging, PlatformAdmin, page_of
from app.core.errors import BusinessRuleError, NotFound
from app.domain.enums import GroupRole, MemberStatus, VenueRole
from app.models import FootballGroup, GroupMember, User, Venue, VenueStaff
from app.schemas.admin import (
    AdminGroupDetail,
    AdminGroupRow,
    AdminGroupStatus,
    AdminMemberRoleIn,
    GroupReactivateIn,
    GroupSuspendIn,
)
from app.schemas.auth import AdminUserOut, PlatformUserUpdate, UserOut
from app.schemas.common import Page
from app.schemas.draw import DrawCalibrationOut
from app.schemas.metrics import PlatformMetricsOut
from app.schemas.platform import PlatformSettingsOut, PlatformSettingsUpdate
from app.schemas.venue import (
    AdminVenueCreate,
    AdminVenueRow,
    AdminVenueUpdate,
    FinanceOut,
    OwnerIn,
    VenueDashboardOut,
    VenuesMetricsOut,
)
from app.services import admin_groups, audit, metrics, platform, venue_finance, venues
from app.services import draw_calibration as draw_calibration_service
from app.services.access import get_group, get_member_in_group
from app.services.users import real_users

router = APIRouter(prefix="/admin", tags=["Administração da plataforma"])


_OWNERS = select(VenueStaff.user_id).where(VenueStaff.role == VenueRole.OWNER, VenueStaff.is_active.is_(True))
_GROUP_ADMINS = select(GroupMember.user_id).where(GroupMember.role == GroupRole.ADMIN,
                                                  GroupMember.status == MemberStatus.ACTIVE)


@router.get("/users", response_model=Page[AdminUserOut])
def list_users(
    admin: PlatformAdmin, db: DB, paging: Paging, search: str | None = None,
    role: Literal["super_admin", "de", "admin"] | None = Query(
        default=None, description="super_admin, de (dono de espaço) ou admin (de pelada)"),
):
    """Contas com os papéis de cada uma. Os mais recentes primeiro."""
    stmt = select(User).where(real_users())  # convidados dos membros não são contas
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(or_(func.lower(User.name).like(like), func.lower(User.email).like(like)))
    if role == "super_admin":
        stmt = stmt.where(User.is_platform_admin.is_(True))
    elif role == "de":
        stmt = stmt.where(User.id.in_(_OWNERS))
    elif role == "admin":
        stmt = stmt.where(User.id.in_(_GROUP_ADMINS))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(
        stmt.order_by(User.id.desc()).offset((paging.page - 1) * paging.size).limit(paging.size)
    ).all())
    ids = [u.id for u in items]
    venues_by_user: dict[int, list[str]] = {}
    groups_by_user: dict[int, list[str]] = {}
    if ids:
        for user_id, name in db.execute(
            select(VenueStaff.user_id, Venue.name).join(Venue, Venue.id == VenueStaff.venue_id)
            .where(VenueStaff.user_id.in_(ids), VenueStaff.role == VenueRole.OWNER, VenueStaff.is_active.is_(True))
            .order_by(Venue.name)
        ).all():
            venues_by_user.setdefault(user_id, []).append(name)
        for user_id, name in db.execute(
            select(GroupMember.user_id, FootballGroup.name).join(FootballGroup, FootballGroup.id == GroupMember.group_id)
            .where(GroupMember.user_id.in_(ids), GroupMember.role == GroupRole.ADMIN,
                   GroupMember.status == MemberStatus.ACTIVE)
            .order_by(FootballGroup.name)
        ).all():
            groups_by_user.setdefault(user_id, []).append(name)
    rows = [{**UserOut.model_validate(u).model_dump(), "venues_owned": venues_by_user.get(u.id, []),
             "groups_admin": groups_by_user.get(u.id, [])} for u in items]
    return page_of(rows, total, paging)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, data: PlatformUserUpdate, admin: PlatformAdmin, db: DB):
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("Usuário não encontrado")
    if user.id == admin.id and data.is_platform_admin is False:
        raise BusinessRuleError("Você não pode remover seu próprio acesso administrativo")
    if user.is_deleted:
        raise BusinessRuleError("Conta excluída pelo titular não pode ser reativada", code="user_deleted")
    fields = ["is_active", "is_platform_admin"]
    before = audit.snapshot(user, fields)
    for key, value in data.model_dump(exclude_none=True).items():
        setattr(user, key, value)
    audit.record(db, action="platform.user_updated", entity_type="user", entity_id=user.id,
                 actor_user_id=admin.id, group_id=None, before=before, after=audit.snapshot(user, fields))
    db.commit()
    return user


@router.get("/venues", response_model=Page[AdminVenueRow])
def list_venues(admin: PlatformAdmin, db: DB, paging: Paging, search: str | None = None):
    """Espaços (mais recentes primeiro) com os donos (DE), quadras, grupos vinculados e horários pendentes. Busca
    pelo nome do espaço ou do dono."""
    items, total = venues.admin_list_venues(db, search, paging.page, paging.size)
    return page_of(items, total, paging)


@router.get("/venues/metrics", response_model=VenuesMetricsOut)
def venues_metrics(
    admin: PlatformAdmin, db: DB, paging: Paging,
    since: date | None = Query(default=None, description="Padrão: início do mês"),
    until: date | None = Query(default=None, description="Padrão: fim do mês"),
):
    """Métricas de cada espaço no período (peladas, ocupação, aluguel, jogadores), da maior receita para a menor,
    paginadas; os totais valem para todos os espaços."""
    return venue_finance.venues_metrics(db, since, until, paging.page, paging.size)


@router.get("/venues/{venue_id}/dashboard", response_model=VenueDashboardOut)
def venue_dashboard(
    venue_id: int, admin: PlatformAdmin, db: DB,
    since: date | None = Query(default=None), until: date | None = Query(default=None),
):
    """Painel do espaço, o mesmo que o dono vê."""
    return venue_finance.dashboard(db, venues.get_venue(db, venue_id), since, until)


@router.get("/venues/{venue_id}/finance", response_model=FinanceOut)
def venue_finance_overview(venue_id: int, admin: PlatformAdmin, db: DB,
                           months: int = Query(default=6, ge=1, le=24)):
    """Financeiro consolidado do espaço, o mesmo que o dono vê."""
    return venue_finance.finance_overview(db, venues.get_venue(db, venue_id), months)


@router.post("/venues", response_model=AdminVenueRow, status_code=status.HTTP_201_CREATED)
def create_venue(data: AdminVenueCreate, admin: PlatformAdmin, db: DB):
    """Cadastra o espaço e define o dono (DE) pelo e-mail. Quem ainda não tem conta passa a ter acesso ao criar a
    conta com o mesmo e-mail."""
    return venues.admin_venue_out(db, venues.create_venue(db, admin, data))


@router.patch("/venues/{venue_id}", response_model=AdminVenueRow)
def update_venue(venue_id: int, data: AdminVenueUpdate, admin: PlatformAdmin, db: DB):
    venue = venues.admin_update_venue(db, venues.get_venue(db, venue_id), data, admin)
    return venues.admin_venue_out(db, venue)


@router.post("/venues/{venue_id}/owners", response_model=AdminVenueRow, status_code=status.HTTP_201_CREATED)
def add_venue_owner(venue_id: int, data: OwnerIn, admin: PlatformAdmin, db: DB):
    """Define mais um dono (DE) do espaço."""
    venue = venues.get_venue(db, venue_id)
    venues.admin_add_owner(db, venue, data.email, data.name, admin)
    return venues.admin_venue_out(db, venue)


@router.delete("/venues/{venue_id}/owners/{staff_id}", response_model=AdminVenueRow)
def remove_venue_owner(venue_id: int, staff_id: int, admin: PlatformAdmin, db: DB):
    """Tira um dono (DE) do espaço; o espaço mantém ao menos um."""
    venue = venues.get_venue(db, venue_id)
    venues.admin_remove_owner(db, venue, staff_id, admin)
    return venues.admin_venue_out(db, venue)


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
