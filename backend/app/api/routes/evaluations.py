from fastapi import APIRouter

from app.api.deps import DB, CurrentUser
from app.core.errors import Forbidden
from app.schemas.common import Message
from app.schemas.evaluation import (
    EvaluationDecisionIn,
    EvaluationProgressOut,
    EvaluationSubmit,
    EvaluationSubmitOut,
    EvaluationTargetsOut,
    FlaggedEvaluationOut,
    ReceivedEvaluationsOut,
)
from app.services import evaluations
from app.services.access import get_group, get_member_in_group, match_for_admin, match_for_member

router = APIRouter(tags=["Avaliações"])


@router.get("/matches/{match_id}/evaluations/targets", response_model=EvaluationTargetsOut)
def targets(match_id: int, user: CurrentUser, db: DB):
    """Jogadores que posso avaliar, progresso e prazo restante."""
    match, member = match_for_member(db, match_id, user)
    return evaluations.targets(db, get_group(db, match.group_id), match, member)


@router.post("/matches/{match_id}/evaluations", response_model=EvaluationSubmitOut, status_code=201)
def submit(match_id: int, data: EvaluationSubmit, user: CurrentUser, db: DB):
    match, member = match_for_member(db, match_id, user)
    return evaluations.submit(db, get_group(db, match.group_id), match, member, data.items)


@router.get("/matches/{match_id}/evaluations/progress", response_model=EvaluationProgressOut)
def progress(match_id: int, user: CurrentUser, db: DB):
    """Indicadores de conclusão (administrador). Não expõe notas individuais."""
    match, _ = match_for_admin(db, match_id, user)
    return evaluations.progress(db, get_group(db, match.group_id), match)


@router.get("/matches/{match_id}/evaluations/flagged", response_model=list[FlaggedEvaluationOut])
def flagged(match_id: int, user: CurrentUser, db: DB):
    """Avaliações discrepantes sinalizadas para análise (avaliador anônimo)."""
    match, _ = match_for_admin(db, match_id, user)
    return evaluations.flagged(db, get_group(db, match.group_id), match)


@router.post("/matches/{match_id}/evaluations/{evaluation_id}/decision", response_model=Message)
def decide(match_id: int, evaluation_id: int, data: EvaluationDecisionIn, user: CurrentUser, db: DB):
    """Incluir/excluir uma avaliação sinalizada no cálculo (auditado)."""
    match, _ = match_for_admin(db, match_id, user)
    evaluations.decide(db, get_group(db, match.group_id), match, evaluation_id, data.decision, data.reason, user)
    return {"message": "Decisão registrada"}


@router.post("/matches/{match_id}/evaluations/close", response_model=Message)
def close(match_id: int, user: CurrentUser, db: DB):
    """Encerra a janela antecipadamente e consolida as notas. Idempotente."""
    match, _ = match_for_admin(db, match_id, user)
    closed = evaluations.close_window(db, get_group(db, match.group_id), match.id, actor_user_id=user.id, force=True)
    return {"message": "Avaliações encerradas e notas atualizadas" if closed else "Avaliações já estavam encerradas"}


@router.get("/matches/{match_id}/evaluations/received/{member_id}", response_model=ReceivedEvaluationsOut)
def received(match_id: int, member_id: int, user: CurrentUser, db: DB):
    """Resumo anônimo das avaliações recebidas (próprio jogador ou administrador)."""
    match, me = match_for_member(db, match_id, user)
    target = get_member_in_group(db, match.group_id, member_id)
    if not (me.is_admin or me.id == target.id):
        raise Forbidden("Disponível apenas para o próprio jogador e administradores")
    return evaluations.received_summary(db, get_group(db, match.group_id), match, target)
