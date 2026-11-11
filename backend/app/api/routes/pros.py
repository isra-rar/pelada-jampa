"""Comércio de goleiros e juízes: anúncio, agenda, busca e contratação para peladas."""

from datetime import date, time
from decimal import Decimal

from fastapi import APIRouter, Query, status

from app.api.deps import DB, CurrentUser, Paging, page_of
from app.core.errors import NotFound
from app.domain.enums import HireRole, HireStatus, Modality
from app.schemas.common import Page
from app.schemas.pro import (
    BookableMatchOut,
    HireCreate,
    HireDecision,
    HireOut,
    ProCardOut,
    ProProfileIn,
    ProProfileOut,
)
from app.services import pros
from app.services.access import get_group, match_for_admin

router = APIRouter(tags=["Goleiros e juízes de aluguel"])
public_router = APIRouter(prefix="/public", tags=["Público (sem login)"])


# ------------------------------------------------------------------ busca (pública)


@public_router.get("/pros", response_model=Page[ProCardOut])
def search_pros(
    db: DB, paging: Paging,
    role: HireRole = Query(description="goalkeeper ou referee"),
    city: str | None = Query(default=None, max_length=80),
    modality: Modality | None = None,
    day: date | None = Query(default=None, alias="date", description="Só quem tem agenda nesta data"),
    start: time | None = Query(default=None, description="Horário inicial (Brasília), com `date`"),
    end: time | None = Query(default=None, description="Horário final (Brasília), com `date`"),
    max_price: Decimal | None = Query(default=None, ge=0),
    q: str | None = Query(default=None, max_length=80, description="Nome, descrição ou bairro"),
):
    """Goleiros ou juízes com anúncio ativo. Com data (e horário), só quem tem agenda livre. Mais baratos primeiro."""
    items, total = pros.search(db, role=role, city=city, modality=modality, day=day, start=start, end=end,
                               max_price=max_price, q=q, page=paging.page, size=paging.size)
    return page_of(items, total, paging)


@public_router.get("/pros/{pro_id}", response_model=ProCardOut)
def pro_card(pro_id: int, db: DB):
    return pros.public_card(db, pros.get_public(db, pro_id))


# ------------------------------------------------------------------ meu anúncio


@router.get("/pros/me", response_model=ProProfileOut)
def my_profile(user: CurrentUser, db: DB):
    profile = pros.my_profile(db, user)
    if profile is None:
        raise NotFound("Você ainda não tem anúncio", code="no_profile")
    return pros.profile_out(db, profile)


@router.put("/pros/me", response_model=ProProfileOut)
def save_profile(data: ProProfileIn, user: CurrentUser, db: DB):
    """Cria ou atualiza o anúncio. `availability` substitui a agenda inteira (omitido mantém a atual)."""
    return pros.profile_out(db, pros.upsert_profile(db, user, data))


@router.get("/pros/me/hires", response_model=Page[HireOut])
def my_hires(user: CurrentUser, db: DB, paging: Paging,
             status_: HireStatus | None = Query(default=None, alias="status")):
    """Pedidos recebidos (pelada mais recente primeiro), paginados."""
    items, total = pros.list_for_pro(db, user, status_, page=paging.page, size=paging.size)
    return page_of([pros.hire_out(db, h, viewer="pro") for h in items], total, paging)


@router.post("/hires/{hire_id}/accept", response_model=HireOut)
def accept_hire(hire_id: int, data: HireDecision, user: CurrentUser, db: DB):
    """Aceita: entra na pelada como goleiro (vai para o sorteio) ou juiz."""
    return pros.hire_out(db, pros.accept(db, user, hire_id, data.note), viewer="pro")


@router.post("/hires/{hire_id}/decline", response_model=HireOut)
def decline_hire(hire_id: int, data: HireDecision, user: CurrentUser, db: DB):
    return pros.hire_out(db, pros.decline(db, user, hire_id, data.note), viewer="pro")


@router.post("/hires/{hire_id}/cancel", response_model=HireOut)
def cancel_hire_as_pro(hire_id: int, data: HireDecision, user: CurrentUser, db: DB):
    """O profissional desiste (antes de a pelada começar); sai da pelada e o organizador é avisado."""
    return pros.hire_out(db, pros.cancel_by_pro(db, user, hire_id, data.note), viewer="pro")


@router.get("/pros/{pro_id}/bookable-matches", response_model=list[BookableMatchOut])
def bookable_matches(pro_id: int, user: CurrentUser, db: DB):
    """Peladas futuras dos grupos que você administra, para escolher onde contratar este profissional."""
    return pros.bookable_matches(db, user, pros.get_public(db, pro_id))


# ------------------------------------------------------------------ contratação (administrador da pelada)


@router.post("/matches/{match_id}/hires", response_model=HireOut, status_code=status.HTTP_201_CREATED)
def request_hire(match_id: int, data: HireCreate, user: CurrentUser, db: DB):
    """Envia o pedido ao profissional. O valor (padrão: o do anúncio) será rateado entre os jogadores presentes."""
    match, _ = match_for_admin(db, match_id, user)
    hire = pros.request_hire(db, get_group(db, match.group_id), match, data, user)
    return pros.hire_out(db, hire, viewer="admin")


@router.get("/matches/{match_id}/hires", response_model=list[HireOut])
def match_hires(match_id: int, user: CurrentUser, db: DB):
    match, _ = match_for_admin(db, match_id, user)
    return [pros.hire_out(db, h, viewer="admin") for h in pros.list_for_match(db, match)]


@router.post("/matches/{match_id}/hires/{hire_id}/cancel", response_model=HireOut)
def cancel_hire(match_id: int, hire_id: int, data: HireDecision, user: CurrentUser, db: DB):
    match, _ = match_for_admin(db, match_id, user)
    return pros.hire_out(db, pros.cancel_by_admin(db, match, hire_id, user, data.note), viewer="admin")
