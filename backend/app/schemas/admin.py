"""Gestão de grupos pela administração da plataforma."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.enums import GroupRole, Modality

AdminGroupStatus = Literal["active", "inactive", "suspended", "no_admin"]


class AdminGroupRow(BaseModel):
    id: int
    name: str
    modality: Modality
    is_active: bool
    suspended_at: datetime | None
    created_at: datetime
    members: int  # membros ativos
    admins: int  # administradores ativos (0 com membros ativos = grupo órfão)
    last_match_at: datetime | None  # última pelada realizada


class AdminGroupMember(BaseModel):
    member_id: int
    user_id: int
    name: str
    email: str
    role: GroupRole
    joined_at: datetime


class AdminGroupEvent(BaseModel):
    id: int
    action: str
    actor_name: str | None
    reason: str | None
    created_at: datetime


class AdminGroupDetail(AdminGroupRow):
    description: str | None
    suspension_reason: str | None
    creator_name: str | None
    creator_email: str | None
    pending_requests: int  # solicitações de entrada aguardando aprovação
    inactive_members: int
    removed_members: int
    matches_upcoming: int
    matches_held: int  # já começaram (publicadas ou encerradas)
    matches_cancelled: int
    next_match_at: datetime | None
    active_series: int
    active_members: list[AdminGroupMember]  # administradores primeiro
    history: list[AdminGroupEvent]  # ações da administração da plataforma neste grupo


class GroupSuspendIn(BaseModel):
    reason: str = Field(min_length=5, max_length=300, description="Mostrado aos membros do grupo")


class GroupReactivateIn(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class AdminMemberRoleIn(BaseModel):
    role: GroupRole
    reason: str | None = Field(default=None, max_length=300)
