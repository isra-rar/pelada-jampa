"""Uma pelada por vez: a próxima do grupo só abre confirmações quando a anterior encerra (ou é cancelada).

Com recorrência o app deixa várias peladas agendadas à frente; elas ficam publicadas, mas "travadas" até chegar a
vez delas. Ao encerrar a pelada atual, quem ainda não respondeu a próxima é avisado de que ela abriu.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.domain.enums import AttendanceStatus, MatchStatus, MemberStatus, NotificationType
from app.models import FootballGroup, GroupMember, Match, MatchPlayer
from app.services import notifications


def blocking_match(db: Session, match: Match) -> Match | None:
    """Pelada anterior do grupo ainda em aberto (publicada, sem encerrar). Enquanto existir, esta fica travada."""
    if match.status != MatchStatus.PUBLISHED or match.id is None:
        return None
    return db.scalar(
        select(Match)
        .where(
            Match.group_id == match.group_id,
            Match.id != match.id,
            Match.status == MatchStatus.PUBLISHED,
            Match.starts_at < match.starts_at,
        )
        .order_by(Match.starts_at.desc())
        .limit(1)
    )


def locked_message(previous: Match) -> str:
    return f"As confirmações abrem quando a pelada de {notifications.when(previous.starts_at)} encerrar"


def require_unlocked(db: Session, match: Match) -> None:
    previous = blocking_match(db, match)
    if previous is not None:
        raise BusinessRuleError(locked_message(previous), code="match_locked")


def notify_next_opened(db: Session, group: FootballGroup, closed: Match) -> None:
    """Depois que `closed` encerra ou é cancelada, avisa que a próxima pelada do grupo abriu as confirmações."""
    nxt = db.scalar(
        select(Match)
        .where(
            Match.group_id == group.id,
            Match.id != closed.id,
            Match.status == MatchStatus.PUBLISHED,
            Match.starts_at > closed.starts_at,
        )
        .order_by(Match.starts_at)
        .limit(1)
    )
    if nxt is None or blocking_match(db, nxt) is not None:
        return
    users = db.scalars(
        select(GroupMember.user_id)
        .join(MatchPlayer, MatchPlayer.member_id == GroupMember.id)
        .where(
            MatchPlayer.match_id == nxt.id,
            MatchPlayer.status == AttendanceStatus.PENDING,
            GroupMember.status == MemberStatus.ACTIVE,
        )
    ).all()
    notifications.notify(
        db, users, NotificationType.MATCH_CREATED, f"Confirmações abertas: {nxt.name}",
        f"{notifications.when(nxt.starts_at)} em {nxt.location}. Confirme sua presença até "
        f"{notifications.when(nxt.confirmation_deadline)}.",
        group_id=group.id, match_id=nxt.id,
    )
