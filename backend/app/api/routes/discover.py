"""Descoberta de peladas abertas e comunidades; convite público (v3.0)."""

from datetime import date

from fastapi import APIRouter, Query

from app.api.deps import DB, CurrentUser, OptionalUser, Paging, page_of
from app.domain.enums import Modality
from app.schemas.common import Page
from app.schemas.discovery import (
    CityOut,
    CommunityDetailOut,
    CommunityOut,
    DiscoverJoinIn,
    DiscoverJoinOut,
    InvitePreviewOut,
    OpenMatchDetailOut,
    OpenMatchOut,
)
from app.schemas.group import JoinOut
from app.services import discovery

router = APIRouter(tags=["Descoberta e comunidades"])
public_router = APIRouter(prefix="/public", tags=["Público (sem login)"])


@public_router.get("/discover/matches", response_model=Page[OpenMatchOut])
def discover_matches(
    db: DB, paging: Paging,
    q: str | None = Query(default=None, max_length=80, description="Nome, local, endereço, grupo ou bairro"),
    city: str | None = Query(default=None, max_length=80),
    modality: Modality | None = None,
    since: date | None = Query(default=None, description="A partir desta data (inclusiva)"),
    until: date | None = Query(default=None, description="Até esta data (inclusiva)"),
    with_spots: bool = Query(default=True, description="false: inclui peladas lotadas (lista de espera)"),
):
    """Peladas abertas: futuras, publicadas e com confirmação aberta, de comunidades que aceitam gente de fora."""
    items, total = discovery.search_matches(db, q=q, city=city, modality=modality, since=since, until=until,
                                            with_spots=with_spots, page=paging.page, size=paging.size)
    return page_of(items, total, paging)


@public_router.get("/discover/matches/{match_id}", response_model=OpenMatchDetailOut)
def open_match(match_id: int, db: DB, user: OptionalUser):
    return discovery.match_detail(db, match_id, user)


@public_router.get("/discover/groups", response_model=Page[CommunityOut])
def discover_groups(
    db: DB, paging: Paging,
    q: str | None = Query(default=None, max_length=80),
    city: str | None = Query(default=None, max_length=80),
    modality: Modality | None = None,
):
    """Comunidades públicas (grupos que escolheram aparecer em Explorar)."""
    items, total = discovery.search_groups(db, q=q, city=city, modality=modality, page=paging.page,
                                           size=paging.size)
    return page_of(items, total, paging)


@public_router.get("/discover/groups/{group_id}", response_model=CommunityDetailOut)
def community(group_id: int, db: DB, user: OptionalUser):
    return discovery.community_detail(db, group_id, user)


@public_router.get("/discover/cities", response_model=list[CityOut])
def cities(db: DB):
    """Cidades com comunidades públicas (para o filtro)."""
    return discovery.cities(db)


@public_router.get("/invites/{token}", response_model=InvitePreviewOut)
def invite_preview(token: str, db: DB, user: OptionalUser):
    """Prévia do convite: o que é o grupo antes de entrar (sem lista de jogadores)."""
    return discovery.invite_preview(db, token, user)


@router.post("/discover/groups/{group_id}/join", response_model=JoinOut)
def join_community(group_id: int, user: CurrentUser, db: DB):
    """Pede para entrar numa comunidade pública (ou entra direto, se ela não exigir aprovação)."""
    member = discovery.join_community(db, user, group_id)
    return {"group_id": member.group_id, "member_id": member.id, "status": member.status}


@router.post("/discover/matches/{match_id}/join", response_model=DiscoverJoinOut)
def join_match(match_id: int, data: DiscoverJoinIn, user: CurrentUser, db: DB):
    """"Quero jogar": entra no grupo da pelada (ou pede aprovação) e, se já puder, confirma presença."""
    return discovery.join_match(db, user, match_id, data.as_goalkeeper)
