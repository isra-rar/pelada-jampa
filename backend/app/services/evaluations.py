"""Avaliação pós-pelada (PRD §7): janela, regras antimanipulação e consolidação das notas."""

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import BusinessRuleError, Conflict, Forbidden, NotFound
from app.domain import rating as R
from app.domain.enums import (
    AttendanceStatus,
    EvaluationDecision,
    EvaluationStatus,
    NotificationType,
    RatingKind,
)
from app.models import Evaluation, FootballGroup, GroupMember, Match, MatchPlayer, User
from app.schemas.evaluation import EvaluationItem
from app.services import audit, notifications, ratings
from app.services.access import get_group


def _kind(mp: MatchPlayer) -> RatingKind:
    return RatingKind.GOALKEEPER if mp.is_goalkeeper else RatingKind.FIELD


def _present(db: Session, match: Match) -> dict[int, MatchPlayer]:
    rows = db.scalars(
        select(MatchPlayer).where(MatchPlayer.match_id == match.id, MatchPlayer.status == AttendanceStatus.PRESENT)
    ).all()
    return {mp.member_id: mp for mp in rows}


def refresh_window(db: Session, group: FootballGroup, match: Match) -> None:
    """Encerra automaticamente a janela vencida (verificação preguiçosa além do agendador)."""
    if match.evaluation_status == EvaluationStatus.OPEN and match.evaluation_closes_at <= utcnow():
        close_window(db, group, match.id, actor_user_id=None, force=False)
        db.refresh(match)


def _require_evaluator(present: dict[int, MatchPlayer], member: GroupMember) -> MatchPlayer:
    mp = present.get(member.id)
    if mp is None:
        raise Forbidden("Apenas jogadores com presença registrada podem avaliar", code="not_present")
    return mp


def _evaluated_by(db: Session, match_id: int, evaluator_id: int) -> set[int]:
    return set(
        db.scalars(
            select(Evaluation.evaluated_member_id).where(
                Evaluation.match_id == match_id, Evaluation.evaluator_member_id == evaluator_id
            )
        ).all()
    )


def targets(db: Session, group: FootballGroup, match: Match, member: GroupMember) -> dict:
    refresh_window(db, group, match)
    present = _present(db, match)
    _require_evaluator(present, member)
    done = _evaluated_by(db, match.id, member.id)
    items = []
    for mid, mp in present.items():
        if mid == member.id:
            continue
        kind = _kind(mp)
        keys = ratings.keys_for(group, kind)
        items.append(
            {
                "member_id": mid,
                "name": mp.member.display_name,
                "photo_url": mp.member.user.photo_url,
                "kind": kind,
                "attributes": list(keys),
                "labels": {k: R.ATTRIBUTE_LABELS[k] for k in keys},
                "already_evaluated": mid in done,
            }
        )
    items.sort(key=lambda i: (i["already_evaluated"], i["name"]))
    remaining = None
    if match.evaluation_status == EvaluationStatus.OPEN:
        remaining = max(0, int((match.evaluation_closes_at - utcnow()).total_seconds()))
    return {
        "match_id": match.id,
        "evaluation_status": match.evaluation_status,
        "closes_at": match.evaluation_closes_at,
        "seconds_remaining": remaining,
        "targets": items,
        "done": len(done),
        "total": len(items),
    }


def _refresh_flags(db: Session, group: FootballGroup, match_id: int, target_id: int) -> list[Evaluation]:
    evals = list(
        db.scalars(
            select(Evaluation)
            .where(Evaluation.match_id == match_id, Evaluation.evaluated_member_id == target_id)
            .order_by(Evaluation.id)
        ).all()
    )
    if not evals:
        return evals
    kind = evals[0].kind
    keys = ratings.keys_for(group, kind)
    flags = R.detect_outliers([e.scores for e in evals], keys, group.outlier_threshold)
    for e, flagged in zip(evals, flags, strict=True):
        e.is_flagged = flagged
        e.flag_reason = (
            f"Nota difere mais de {group.outlier_threshold:g} ponto(s) da mediana dos demais avaliadores"
            if flagged
            else None
        )
    return evals


def submit(db: Session, group: FootballGroup, match: Match, member: GroupMember, items: list[EvaluationItem]):
    refresh_window(db, group, match)
    if match.evaluation_status != EvaluationStatus.OPEN:
        raise BusinessRuleError("A janela de avaliação não está aberta", code="evaluation_closed")
    present = _present(db, match)
    _require_evaluator(present, member)
    already = _evaluated_by(db, match.id, member.id)
    seen: set[int] = set()
    to_create: list[Evaluation] = []
    for item in items:
        tid = item.evaluated_member_id
        if tid == member.id:
            raise BusinessRuleError("Não é permitido avaliar a si próprio", code="self_evaluation")
        if tid not in present:
            raise BusinessRuleError(f"Jogador {tid} não esteve presente nesta pelada", code="target_not_present")
        if tid in seen or tid in already:
            raise Conflict(f"Jogador {tid} já foi avaliado por você nesta pelada", code="duplicate_evaluation")
        seen.add(tid)
        kind = _kind(present[tid])
        try:
            scores = R.validate_scores(item.scores, ratings.keys_for(group, kind))
        except ValueError as exc:
            raise BusinessRuleError(f"Jogador {tid}: {exc}", code="invalid_scores") from None
        to_create.append(
            Evaluation(
                match_id=match.id, evaluator_member_id=member.id, evaluated_member_id=tid, kind=kind, scores=scores
            )
        )
    db.add_all(to_create)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict("Avaliação duplicada", code="duplicate_evaluation") from None
    for tid in seen:
        _refresh_flags(db, group, match.id, tid)
    db.commit()
    done = len(already) + len(to_create)
    return {"created": len(to_create), "done": done, "total": len(present) - 1}


def progress(db: Session, group: FootballGroup, match: Match) -> dict:
    refresh_window(db, group, match)
    present = _present(db, match)
    n = len(present)
    per_evaluator = dict(
        db.execute(
            select(Evaluation.evaluator_member_id, func.count())
            .where(Evaluation.match_id == match.id)
            .group_by(Evaluation.evaluator_member_id)
        ).all()
    )
    flagged = db.scalar(
        select(func.count()).where(Evaluation.match_id == match.id, Evaluation.is_flagged.is_(True))
    ) or 0
    expected = n * (n - 1)
    submitted = sum(per_evaluator.values())
    evaluators = [
        {
            "member_id": mid,
            "name": mp.member.display_name,
            "done": per_evaluator.get(mid, 0),
            "total": n - 1,
            "completed": per_evaluator.get(mid, 0) >= n - 1,
        }
        for mid, mp in present.items()
    ]
    evaluators.sort(key=lambda e: (e["completed"], e["name"]))
    return {
        "match_id": match.id,
        "evaluation_status": match.evaluation_status,
        "opens_at": match.evaluation_opens_at,
        "closes_at": match.evaluation_closes_at,
        "present_players": n,
        "expected": expected,
        "submitted": submitted,
        "completion_rate": round(submitted / expected, 4) if expected else 0.0,
        "flagged": flagged,
        "evaluators": evaluators,
    }


def flagged(db: Session, group: FootballGroup, match: Match) -> list[dict]:
    evals = db.scalars(
        select(Evaluation).where(Evaluation.match_id == match.id, Evaluation.is_flagged.is_(True)).order_by(Evaluation.id)
    ).all()
    present = _present(db, match)
    out = []
    for e in evals:
        keys = ratings.keys_for(group, e.kind)
        peers = db.scalars(
            select(Evaluation.scores).where(
                Evaluation.match_id == match.id,
                Evaluation.evaluated_member_id == e.evaluated_member_id,
                Evaluation.id != e.id,
            )
        ).all()
        mp = present.get(e.evaluated_member_id)
        out.append(
            {
                "id": e.id,
                "evaluated_member_id": e.evaluated_member_id,
                "evaluated_name": mp.member.display_name if mp else str(e.evaluated_member_id),
                "kind": e.kind,
                "scores": e.scores,
                "peer_median": R.peer_median(list(peers), keys),
                "flag_reason": e.flag_reason,
                "admin_decision": e.admin_decision,
                "counted": e.counted,
                "created_at": e.created_at,
            }
        )
    return out


def decide(
    db: Session, group: FootballGroup, match: Match, evaluation_id: int, decision: EvaluationDecision,
    reason: str, actor: User,
) -> Evaluation:
    e = db.get(Evaluation, evaluation_id)
    if e is None or e.match_id != match.id:
        raise NotFound("Avaliação não encontrada")
    if match.evaluation_status != EvaluationStatus.OPEN:
        raise BusinessRuleError("Decisões só podem ser tomadas com a janela aberta", code="evaluation_closed")
    before = {"admin_decision": e.admin_decision}
    e.admin_decision = decision
    audit.record(db, action="evaluation.decision", entity_type="evaluation", entity_id=e.id, actor_user_id=actor.id,
                 group_id=group.id, before=before, after={"admin_decision": decision}, reason=reason)
    db.commit()
    return e


def _counts(e: Evaluation, group: FootballGroup) -> bool:
    if e.admin_decision == EvaluationDecision.EXCLUDE:
        return False
    if e.admin_decision == EvaluationDecision.INCLUDE:
        return True
    return not (e.is_flagged and group.exclude_outliers)


def close_window(
    db: Session, group: FootballGroup, match_id: int, *, actor_user_id: int | None, force: bool
) -> bool:
    """Encerra a janela e consolida as notas. Idempotente: só a primeira chamada efetiva tem efeito."""
    match = db.scalar(select(Match).where(Match.id == match_id).with_for_update())
    if match is None or match.evaluation_status != EvaluationStatus.OPEN:
        return False
    if not force and match.evaluation_closes_at > utcnow():
        return False
    present = _present(db, match)
    summary: dict[int, dict] = {}
    for target_id, mp in present.items():
        evals = _refresh_flags(db, group, match.id, target_id)
        by_kind: dict[RatingKind, list[dict]] = defaultdict(list)
        for e in evals:
            e.counted = _counts(e, group)
            if e.counted:
                by_kind[e.kind].append(e.scores)
        for kind, score_sets in by_kind.items():
            ratings.apply_match_result(db, group, mp.member, kind, match.id, score_sets)
        summary[target_id] = {
            "received": len(evals),
            "counted": sum(1 for e in evals if e.counted),
        }
    match.evaluation_status = EvaluationStatus.CLOSED
    match.evaluation_closed_at = utcnow()
    audit.record(db, action="evaluation.closed", entity_type="match", entity_id=match.id,
                 actor_user_id=actor_user_id, group_id=group.id,
                 after={"forced": force, "players": summary})
    db.commit()
    return True


def received_summary(db: Session, group: FootballGroup, match: Match, member: GroupMember) -> dict:
    """Resumo anônimo do que o jogador recebeu. Só é exibido após o encerramento da janela."""
    if match.evaluation_status != EvaluationStatus.CLOSED:
        raise BusinessRuleError("Resultados disponíveis após o encerramento da janela", code="evaluation_open")
    evals = db.scalars(
        select(Evaluation).where(Evaluation.match_id == match.id, Evaluation.evaluated_member_id == member.id)
    ).all()
    counted = [e for e in evals if e.counted]
    kind = evals[0].kind if evals else RatingKind.FIELD
    keys = ratings.keys_for(group, kind)
    return {
        "match_id": match.id,
        "kind": kind,
        "count": len(evals),
        "counted": len(counted),
        "average_scores": {k: round(v, 2) for k, v in R.consolidate([e.scores for e in counted], keys).items()}
        if counted
        else None,
    }


# ------------------------------------------------------------------ tarefas periódicas


def close_due_windows(db: Session) -> int:
    ids = db.scalars(
        select(Match.id).where(
            Match.evaluation_status == EvaluationStatus.OPEN, Match.evaluation_closes_at <= utcnow()
        )
    ).all()
    closed = 0
    for mid in ids:
        match = db.get(Match, mid)
        if close_window(db, get_group(db, match.group_id), mid, actor_user_id=None, force=False):
            closed += 1
    return closed


def send_closing_reminders(db: Session) -> int:
    limit = utcnow() + timedelta(minutes=settings.evaluation_reminder_minutes_before)
    matches = db.scalars(
        select(Match).where(
            Match.evaluation_status == EvaluationStatus.OPEN,
            Match.evaluation_reminder_sent_at.is_(None),
            Match.evaluation_closes_at <= limit,
        )
    ).all()
    sent = 0
    for match in matches:
        present = _present(db, match)
        done = dict(
            db.execute(
                select(Evaluation.evaluator_member_id, func.count())
                .where(Evaluation.match_id == match.id)
                .group_by(Evaluation.evaluator_member_id)
            ).all()
        )
        pending_users = [mp.member.user_id for mid, mp in present.items() if done.get(mid, 0) < len(present) - 1]
        sent += notifications.notify(
            db, pending_users, NotificationType.EVALUATION_CLOSING, f"Avaliações de {match.name} encerrando",
            f"Faltam poucos minutos. Prazo: {match.evaluation_closes_at.strftime('%d/%m %H:%M UTC')}.",
            group_id=match.group_id, match_id=match.id,
        )
        match.evaluation_reminder_sent_at = utcnow()
    db.commit()
    return sent


def pending_count_for_member(db: Session, member: GroupMember) -> int:
    """Quantas avaliações o jogador ainda pode fazer em janelas abertas."""
    open_matches = db.scalars(
        select(Match)
        .join(MatchPlayer, MatchPlayer.match_id == Match.id)
        .where(
            Match.group_id == member.group_id,
            Match.evaluation_status == EvaluationStatus.OPEN,
            Match.evaluation_closes_at > utcnow(),
            MatchPlayer.member_id == member.id,
            MatchPlayer.status == AttendanceStatus.PRESENT,
        )
    ).all()
    total = 0
    for match in open_matches:
        present = _present(db, match)
        total += max(0, len(present) - 1 - len(_evaluated_by(db, match.id, member.id)))
    return total
