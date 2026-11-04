from fastapi import APIRouter, status

from app.api.deps import DB, CurrentUser
from app.core.errors import NotFound
from app.domain.enums import DrawStatus
from app.schemas.draw import DrawAdjustIn, DrawCompareOut, DrawCreate, DrawOut, DrawSummaryOut
from app.services import draws
from app.services.access import get_group, match_for_admin, match_for_member

router = APIRouter(tags=["Sorteio de times"])


@router.post("/matches/{match_id}/draws", response_model=DrawOut, status_code=status.HTTP_201_CREATED)
def run_draw(match_id: int, data: DrawCreate, user: CurrentUser, db: DB):
    """Executa (ou repete) o sorteio inteligente. Cada execução fica registrada no histórico.

    Endpoint síncrono: roda no threadpool, com limite de tempo configurável, sem bloquear o event loop.
    """
    match, _ = match_for_admin(db, match_id, user)
    return draws.draw_out(draws.create_draw(db, get_group(db, match.group_id), match, data, user))


@router.get("/matches/{match_id}/draws", response_model=list[DrawSummaryOut])
def list_draws(match_id: int, user: CurrentUser, db: DB):
    match, member = match_for_member(db, match_id, user)
    return [draws.summary_out(d) for d in draws.list_draws(db, match, include_unpublished=member.is_admin)]


@router.get("/matches/{match_id}/teams", response_model=DrawOut)
def published_teams(match_id: int, user: CurrentUser, db: DB):
    """Times publicados da pelada."""
    match, _ = match_for_member(db, match_id, user)
    published = draws.list_draws(db, match, include_unpublished=False)
    if not published:
        raise NotFound("Times ainda não publicados")
    return draws.draw_out(published[0])


@router.get("/matches/{match_id}/draws/{draw_id}", response_model=DrawOut)
def get_draw(match_id: int, draw_id: int, user: CurrentUser, db: DB):
    match, member = match_for_member(db, match_id, user)
    draw = draws.get_draw(db, match, draw_id)
    if draw.status != DrawStatus.PUBLISHED and not member.is_admin:
        raise NotFound("Sorteio não encontrado")
    return draws.draw_out(draw)


@router.get("/matches/{match_id}/draws/{draw_id}/compare/{other_id}", response_model=DrawCompareOut)
def compare(match_id: int, draw_id: int, other_id: int, user: CurrentUser, db: DB):
    match, _ = match_for_admin(db, match_id, user)
    return draws.compare(draws.get_draw(db, match, draw_id), draws.get_draw(db, match, other_id))


@router.patch("/matches/{match_id}/draws/{draw_id}", response_model=DrawOut)
def adjust(match_id: int, draw_id: int, data: DrawAdjustIn, user: CurrentUser, db: DB):
    """Ajuste manual (move jogadores entre times); métricas são recalculadas."""
    match, _ = match_for_admin(db, match_id, user)
    draw = draws.get_draw(db, match, draw_id)
    return draws.draw_out(draws.adjust(db, get_group(db, match.group_id), match, draw, data.moves, data.reason, user))


@router.post("/matches/{match_id}/draws/{draw_id}/publish", response_model=DrawOut)
def publish(match_id: int, draw_id: int, user: CurrentUser, db: DB):
    match, _ = match_for_admin(db, match_id, user)
    draw = draws.get_draw(db, match, draw_id)
    return draws.draw_out(draws.publish(db, get_group(db, match.group_id), match, draw, user))

