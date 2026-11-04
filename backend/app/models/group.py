from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow
from app.domain.enums import GoalkeeperRatingMode, GroupRole, MemberStatus, Modality, Position
from app.models._types import str_enum


def _default_draw_weights() -> dict:
    return {"average": 0.40, "tier": 0.25, "attributes": 0.20, "skills": 0.15}


class FootballGroup(Base):
    __tablename__ = "football_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    modality: Mapped[Modality] = mapped_column(str_enum(Modality), default=Modality.SOCIETY)
    invite_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- regras do grupo
    require_join_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    waitlist_auto_promote: Mapped[bool] = mapped_column(Boolean, default=True)
    waitlist_offer_expiry_minutes: Mapped[int] = mapped_column(Integer, default=120)
    skill_change_requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_manual_team_adjust: Mapped[bool] = mapped_column(Boolean, default=True)
    goalkeeper_rating_mode: Mapped[GoalkeeperRatingMode] = mapped_column(
        str_enum(GoalkeeperRatingMode), default=GoalkeeperRatingMode.OWN
    )

    # --- parâmetros de nota (calibráveis)
    initial_rating: Mapped[float] = mapped_column(Float, default=2.5)
    min_evaluations_to_consolidate: Mapped[int] = mapped_column(Integer, default=5)
    rating_match_weight: Mapped[float] = mapped_column(Float, default=0.30)
    provisional_match_weight: Mapped[float] = mapped_column(Float, default=0.50)
    min_evaluators_full_weight: Mapped[int] = mapped_column(Integer, default=3)
    outlier_threshold: Mapped[float] = mapped_column(Float, default=2.0)
    exclude_outliers: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- parâmetros do sorteio
    draw_weights: Mapped[dict] = mapped_column(JSON, default=_default_draw_weights)

    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class SpecialSkill(Base):
    """Habilidade especial. group_id nulo = habilidade padrão da plataforma."""

    __tablename__ = "special_skills"
    __table_args__ = (UniqueConstraint("group_id", "code", name="uq_skill_group_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("football_groups.id"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class GroupMember(Base):
    """Relação usuário-grupo. O perfil esportivo (notas) é específico por grupo."""

    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_member_group_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("football_groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[GroupRole] = mapped_column(str_enum(GroupRole), default=GroupRole.PLAYER)
    status: Mapped[MemberStatus] = mapped_column(str_enum(MemberStatus), default=MemberStatus.ACTIVE)
    nickname: Mapped[str | None] = mapped_column(String(60), nullable=True)
    preferred_position: Mapped[Position] = mapped_column(str_enum(Position), default=Position.ANY)
    special_skill_id: Mapped[int | None] = mapped_column(ForeignKey("special_skills.id"), nullable=True)
    pending_skill_id: Mapped[int | None] = mapped_column(ForeignKey("special_skills.id"), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", lazy="joined")
    special_skill = relationship("SpecialSkill", foreign_keys=[special_skill_id], lazy="joined")
    pending_skill = relationship("SpecialSkill", foreign_keys=[pending_skill_id], lazy="joined")

    @property
    def is_admin(self) -> bool:
        return self.role == GroupRole.ADMIN

    @property
    def display_name(self) -> str:
        return self.nickname or self.user.name
