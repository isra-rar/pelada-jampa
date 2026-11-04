from enum import StrEnum


class GroupRole(StrEnum):
    ADMIN = "admin"
    PLAYER = "player"


class MemberStatus(StrEnum):
    PENDING = "pending"  # solicitação aguardando aprovação
    ACTIVE = "active"
    INACTIVE = "inactive"
    REMOVED = "removed"


class Position(StrEnum):
    GOALKEEPER = "goalkeeper"
    DEFENDER = "defender"
    MIDFIELDER = "midfielder"
    FORWARD = "forward"
    ANY = "any"


class Modality(StrEnum):
    SOCIETY = "society"
    FUTSAL = "futsal"
    CAMPO = "campo"
    OUTRA = "outra"


class Recurrence(StrEnum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


class MatchStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class EvaluationStatus(StrEnum):
    NOT_OPEN = "not_open"
    OPEN = "open"
    CLOSED = "closed"


class AttendanceStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    WAITLIST = "waitlist"
    CANCELLED = "cancelled"
    PRESENT = "present"
    ABSENT = "absent"


class RatingKind(StrEnum):
    FIELD = "field"
    GOALKEEPER = "goalkeeper"


class GoalkeeperRatingMode(StrEnum):
    OWN = "own"  # atributos próprios de goleiro
    GENERAL = "general"  # mesmos cinco atributos, classificação separada


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    EXEMPT = "exempt"
    REFUNDED = "refunded"


class DrawStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DISCARDED = "discarded"


class EvaluationDecision(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"


class NotificationType(StrEnum):
    MATCH_CREATED = "match_created"
    CONFIRMATION_DEADLINE = "confirmation_deadline"
    SPOT_AVAILABLE = "spot_available"
    CONFIRMATION_REGISTERED = "confirmation_registered"
    DRAW_DONE = "draw_done"
    PAYMENT_PENDING = "payment_pending"
    EVALUATION_OPEN = "evaluation_open"
    EVALUATION_CLOSING = "evaluation_closing"
    TEAMS_PUBLISHED = "teams_published"
    MATCH_CANCELLED = "match_cancelled"
    MEMBERSHIP = "membership"
