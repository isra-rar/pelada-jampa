from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.draw import DrawOut
from app.schemas.group import MemberSelfAssessment
from app.schemas.match import MatchPlayerOut
from app.schemas.stats import GameOut


class GuestCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    stars: int = Field(ge=0, le=5)  # nível que quem convidou acha que ele tem
    goalkeeper: bool = False


class GuestUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    goalkeeper: bool | None = None


class GuestOut(BaseModel):
    member_id: int
    name: str
    invited_by_member_id: int | None
    invited_by_name: str | None
    is_goalkeeper: bool
    stars: int | None  # nível atual
    average_display: float | None
    level: MemberSelfAssessment | None = None  # confirmação do nível: só para quem convidou e administradores
    can_manage: bool  # quem convidou ou administrador
    has_played: bool
    created_at: datetime


class GuestMatchIn(BaseModel):
    member_id: int
    as_goalkeeper: bool = False


class GuestMatchOut(BaseModel):
    participation: MatchPlayerOut
    warnings: list[str]


# ------------------------------------------------------------------ convidado sem login (link da pelada)


class GuestLinkOut(BaseModel):
    token: str
    expires_at: datetime


class ShareLinksOut(BaseModel):
    guest_token: str | None  # None quando a pelada não aceita mais convidado
    invite_token: str | None
    expires_at: datetime


class GuestJoinIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    stars: int = Field(default=2, ge=0, le=5)  # nível que o convidado acha que tem (o admin confirma)
    goalkeeper: bool = False


class GuestMatchCardOut(BaseModel):
    id: int
    name: str
    group_name: str
    modality: str
    location: str
    address: str | None
    starts_at: datetime
    ends_at: datetime
    status: str
    slots: int
    confirmed: int
    available_slots: int
    waitlist: int
    price: Decimal
    description: str | None


class GuestLinkPreviewOut(BaseModel):
    match: GuestMatchCardOut
    invited_by_name: str
    can_join: bool
    closed_reason: str | None


class GuestPassMe(BaseModel):
    name: str
    invited_by_name: str
    status: str | None
    waitlist_position: int | None
    is_goalkeeper: bool


class GuestPassParticipant(BaseModel):
    name: str
    photo_url: str | None
    status: str
    is_goalkeeper: bool
    is_referee: bool
    waitlist_position: int | None
    invited_by_name: str | None
    is_me: bool


class GuestPassPayment(BaseModel):
    amount_due: Decimal
    amount_paid: Decimal
    outstanding: Decimal
    status: str
    payer_name: str
    paid_at: datetime | None


class GuestPassOut(BaseModel):
    match: GuestMatchCardOut
    me: GuestPassMe
    can_leave: bool
    participants: list[GuestPassParticipant]
    teams: DrawOut | None
    games: list[GameOut]
    payment: GuestPassPayment | None


class GuestJoinOut(BaseModel):
    access_token: str
    view: GuestPassOut
    warnings: list[str]
