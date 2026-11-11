from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.group import MemberSelfAssessment
from app.schemas.match import MatchPlayerOut


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
