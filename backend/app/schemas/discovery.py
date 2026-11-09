"""Descoberta de peladas abertas, comunidades públicas e convite público (v3.0).

Nada aqui expõe jogadores: só o que o grupo escolheu tornar público (nome, descrição, cidade, bairro, local e
horário das peladas, vagas e valor) e contagens.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.domain.enums import AttendanceStatus, MemberStatus, Modality
from app.schemas.match import ConfirmOut


class CommunityRef(BaseModel):
    id: int
    name: str
    city: str | None
    neighborhood: str | None
    join_requires_approval: bool


class OpenMatchOut(BaseModel):
    id: int
    name: str
    modality: Modality
    location: str
    address: str | None
    starts_at: datetime
    ends_at: datetime
    price: Decimal
    split_cost: bool = False  # custo total (campo + goleiro/juiz contratados) dividido entre quem jogar
    price_estimate: Decimal | None = None  # por jogador com a pelada cheia
    slots: int
    confirmed: int
    available_slots: int
    confirmation_deadline: datetime
    field_players_per_team: int
    goalkeepers_per_team: int
    group: CommunityRef


class ViewerOut(BaseModel):
    """Situação de quem está vendo a página (só quando está logado)."""

    member_status: MemberStatus | None = None
    member_id: int | None = None
    participation_status: AttendanceStatus | None = None


class OpenMatchDetailOut(OpenMatchOut):
    description: str | None
    duration_minutes: int
    viewer: ViewerOut | None = None


class NextMatchRef(BaseModel):
    id: int
    starts_at: datetime
    location: str
    available_slots: int


class CommunityOut(BaseModel):
    id: int
    name: str
    description: str | None
    modality: Modality
    city: str | None
    neighborhood: str | None
    members_count: int
    join_requires_approval: bool
    open_matches: int
    next_match: NextMatchRef | None
    public_ranking_token: str | None


class CommunityDetailOut(CommunityOut):
    created_at: datetime
    upcoming_matches: list[OpenMatchOut]
    viewer: ViewerOut | None = None


class CityOut(BaseModel):
    city: str
    communities: int


class InvitePreviewOut(BaseModel):
    """Prévia do convite para quem ainda não entrou (página pública do link de convite)."""

    group_id: int
    name: str
    description: str | None
    modality: Modality
    city: str | None
    neighborhood: str | None
    members_count: int
    requires_approval: bool
    next_match: NextMatchRef | None
    viewer: ViewerOut | None = None


class DiscoverJoinIn(BaseModel):
    as_goalkeeper: bool = False


class DiscoverJoinOut(BaseModel):
    group_id: int
    member_id: int
    member_status: MemberStatus
    # preenchido quando o jogador já está ativo no grupo e a confirmação foi registrada
    confirmation: ConfirmOut | None = None
    message: str
