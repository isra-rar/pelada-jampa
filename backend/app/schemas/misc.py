from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.domain.enums import AttendanceStatus, NotificationType
from app.schemas.common import ORMModel
from app.schemas.draw import DrawSummaryOut
from app.schemas.finance import DebtorOut
from app.schemas.match import DebtOut, MatchOut


class NotificationOut(ORMModel):
    id: int
    type: NotificationType
    title: str
    body: str
    group_id: int | None
    match_id: int | None
    read_at: datetime | None
    created_at: datetime


class AuditOut(ORMModel):
    id: int
    group_id: int | None
    actor_user_id: int | None
    action: str
    entity_type: str
    entity_id: int | None
    before: dict | None
    after: dict | None
    reason: str | None
    created_at: datetime


class RecentAbsence(BaseModel):
    member_id: int
    name: str
    match_id: int
    match_date: datetime


class AdminDashboard(BaseModel):
    group_id: int
    next_match: MatchOut | None
    total_players: int
    confirmed: int
    pending_confirmation: int
    waitlist: int
    recent_absences: list[RecentAbsence]
    debtors: list[DebtorOut]
    total_received: Decimal
    total_pending: Decimal
    evaluations_open: list[MatchOut]
    recent_draws: list[DrawSummaryOut]
    quiz_not_answered: int = 0  # membros ativos que ainda não responderam o quiz (dispensados não contam)
    levels_to_review: int = 0  # quizzes respondidos aguardando o administrador confirmar o nível


class HomeGroupItem(BaseModel):
    group_id: int
    group_name: str
    next_match: MatchOut | None
    my_status: AttendanceStatus | None
    debts: list[DebtOut]
    pending_evaluations: int


class HomeOut(BaseModel):
    groups: list[HomeGroupItem]
    unread_notifications: int
    recent_notifications: list[NotificationOut]
