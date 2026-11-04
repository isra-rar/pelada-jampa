"""Painel do administrador (PRD §10) e tela inicial do jogador (PRD §13.1)."""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.domain.enums import AttendanceStatus, EvaluationStatus, MemberStatus
from app.models import FootballGroup, GroupMember, Match, MatchPlayer, User
from app.services import draws, evaluations, matches, notifications, payments
from app.services.groups import list_user_groups


def admin_dashboard(db: Session, group: FootballGroup, member: GroupMember) -> dict:
    nxt = matches.next_match(db, group.id)
    counts = matches.attendance_counts(db, nxt) if nxt else {}
    total_players = db.scalar(
        select(func.count()).where(GroupMember.group_id == group.id, GroupMember.status == MemberStatus.ACTIVE)
    ) or 0
    since = utcnow() - timedelta(days=30)
    absences = db.execute(
        select(MatchPlayer, Match)
        .join(Match, Match.id == MatchPlayer.match_id)
        .where(Match.group_id == group.id, MatchPlayer.status == AttendanceStatus.ABSENT, Match.starts_at >= since)
        .order_by(Match.starts_at.desc())
        .limit(20)
    ).all()
    finance = payments.summary(db, group.id)
    open_evals = db.scalars(
        select(Match).where(Match.group_id == group.id, Match.evaluation_status == EvaluationStatus.OPEN)
        .order_by(Match.evaluation_closes_at)
    ).all()
    return {
        "group_id": group.id,
        "next_match": matches.match_out(db, nxt, member) if nxt else None,
        "total_players": total_players,
        "confirmed": counts.get("confirmed", 0),
        "pending_confirmation": counts.get("pending", 0),
        "waitlist": counts.get("waitlist", 0),
        "recent_absences": [
            {"member_id": mp.member_id, "name": mp.member.display_name, "match_id": m.id, "match_date": m.starts_at}
            for mp, m in absences
        ],
        "debtors": payments.debtors(db, group.id)[:20],
        "total_received": finance["received"],
        "total_pending": finance["pending"],
        "evaluations_open": [matches.match_out(db, m) for m in open_evals],
        "recent_draws": [draws.summary_out(d) for d in draws.recent_draws(db, group.id)],
    }


def home(db: Session, user: User) -> dict:
    items = []
    for group, member in list_user_groups(db, user):
        nxt = matches.next_match(db, group.id)
        my_status = None
        if nxt:
            my_status = db.scalar(
                select(MatchPlayer.status).where(MatchPlayer.match_id == nxt.id, MatchPlayer.member_id == member.id)
            )
        items.append(
            {
                "group_id": group.id,
                "group_name": group.name,
                "next_match": matches.match_out(db, nxt, member) if nxt else None,
                "my_status": my_status,
                "debts": [payments.debt_out(p) for p in payments.pending_debts(db, group.id, member.id)],
                "pending_evaluations": evaluations.pending_count_for_member(db, member),
            }
        )
    recent, _ = notifications.list_for_user(db, user.id, unread_only=False, page=1, size=10)
    return {
        "groups": items,
        "unread_notifications": notifications.unread_count(db, user.id),
        "recent_notifications": recent,
    }
