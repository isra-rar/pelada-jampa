from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.domain.enums import GoalkeeperRatingMode, GroupRole, MemberStatus, Modality, Position
from app.schemas.common import ORMModel

_WEIGHT_KEYS = {"average", "tier", "attributes", "skills"}


class GroupSettings(BaseModel):
    require_join_approval: bool | None = None
    waitlist_auto_promote: bool | None = None
    waitlist_offer_expiry_minutes: int | None = Field(default=None, ge=5, le=60 * 24 * 7)
    skill_change_requires_approval: bool | None = None
    allow_manual_team_adjust: bool | None = None
    goalkeeper_rating_mode: GoalkeeperRatingMode | None = None
    initial_rating: float | None = Field(default=None, ge=0, le=5)
    min_evaluations_to_consolidate: int | None = Field(default=None, ge=1, le=100)
    rating_match_weight: float | None = Field(default=None, gt=0, le=1)
    provisional_match_weight: float | None = Field(default=None, gt=0, le=1)
    min_evaluators_full_weight: int | None = Field(default=None, ge=1, le=50)
    outlier_threshold: float | None = Field(default=None, gt=0, le=5)
    exclude_outliers: bool | None = None
    draw_weights: dict[str, float] | None = None
    charge_no_shows: bool | None = None
    charge_late_cancellations: bool | None = None
    public_ranking_enabled: bool | None = None

    @field_validator("draw_weights")
    @classmethod
    def _weights(cls, v: dict[str, float] | None):
        if v is None:
            return v
        unknown = set(v) - _WEIGHT_KEYS
        if unknown:
            raise ValueError(f"Pesos desconhecidos: {sorted(unknown)}")
        if any(w < 0 for w in v.values()):
            raise ValueError("Pesos não podem ser negativos")
        return v


class GroupCreate(GroupSettings):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    modality: Modality = Modality.SOCIETY


class GroupUpdate(GroupSettings):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    modality: Modality | None = None
    is_active: bool | None = None


class GroupOut(ORMModel):
    id: int
    name: str
    description: str | None
    modality: Modality
    is_active: bool
    require_join_approval: bool
    waitlist_auto_promote: bool
    waitlist_offer_expiry_minutes: int
    skill_change_requires_approval: bool
    allow_manual_team_adjust: bool
    goalkeeper_rating_mode: GoalkeeperRatingMode
    initial_rating: float
    min_evaluations_to_consolidate: int
    rating_match_weight: float
    provisional_match_weight: float
    min_evaluators_full_weight: int
    outlier_threshold: float
    exclude_outliers: bool
    draw_weights: dict[str, float]
    charge_no_shows: bool = False
    charge_late_cancellations: bool = False
    public_ranking_enabled: bool = False
    public_token: str | None = None
    suspended_at: datetime | None = None
    suspension_reason: str | None = None
    created_at: datetime
    my_role: GroupRole | None = None
    my_member_id: int | None = None
    invite_token: str | None = None  # apenas para administradores


class InviteOut(BaseModel):
    invite_token: str
    group_id: int


class JoinOut(BaseModel):
    group_id: int
    member_id: int
    status: MemberStatus


class SkillOut(ORMModel):
    id: int
    code: str
    name: str
    description: str
    group_id: int | None
    is_active: bool


class SkillCreate(BaseModel):
    code: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=2, max_length=60)
    description: str = Field(min_length=2, max_length=200)


class SkillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=60)
    description: str | None = Field(default=None, min_length=2, max_length=200)
    is_active: bool | None = None


class MemberOut(BaseModel):
    id: int
    user_id: int
    name: str
    nickname: str | None
    photo_url: str | None
    email: str | None = None  # visível para administradores e o próprio usuário
    role: GroupRole
    status: MemberStatus
    preferred_position: Position
    special_skill: SkillOut | None
    pending_skill: SkillOut | None
    joined_at: datetime


class MemberAdd(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=120)
    role: GroupRole = GroupRole.PLAYER
    preferred_position: Position = Position.ANY


class MemberAdminUpdate(BaseModel):
    role: GroupRole | None = None
    status: MemberStatus | None = None
    nickname: str | None = Field(default=None, max_length=60)
    preferred_position: Position | None = None
    reason: str | None = Field(default=None, max_length=300)


class MyMemberUpdate(BaseModel):
    nickname: str | None = Field(default=None, max_length=60)
    preferred_position: Position | None = None


class SkillChoice(BaseModel):
    skill_id: int


class SkillDecision(BaseModel):
    approve: bool
