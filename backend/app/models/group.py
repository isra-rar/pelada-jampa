from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow
from app.domain.enums import GoalkeeperRatingMode, GroupRole, MemberStatus, Modality, Position
from app.models._types import str_enum


def _default_draw_weights() -> dict:
    return {"average": 0.40, "stars": 0.25, "attributes": 0.20, "skills": 0.15}


class FootballGroup(Base):
    __tablename__ = "football_groups"
    __table_args__ = (UniqueConstraint("public_token", name="uq_groups_public_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    modality: Mapped[Modality] = mapped_column(str_enum(Modality), default=Modality.SOCIETY)
    invite_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # suspensão pela administração da plataforma: o grupo fica inativo e somente leitura até ser reativado
    suspended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    suspension_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # --- penalidades por falta (PRD §20.1: decisão configurável pelo grupo)
    charge_no_shows: Mapped[bool] = mapped_column(Boolean, default=False)  # confirmou e faltou: paga
    charge_late_cancellations: Mapped[bool] = mapped_column(Boolean, default=False)  # desistiu fora do prazo: paga

    # --- ranking público (link sem login, somente estatísticas de jogo)
    public_ranking_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    public_token: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- lembrete de pendências: a cada N dias o jogador com dívida em aberto é avisado (0 = desligado)
    debt_reminder_days: Mapped[int] = mapped_column(Integer, default=3)

    # --- comunidade pública (descoberta): o grupo aparece em "Explorar" para quem não participa
    discoverable: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # peladas futuras com vaga aparecem na busca de peladas abertas
    discover_matches: Mapped[bool] = mapped_column(Boolean, default=True)
    # quem chega pela descoberta (sem convite) precisa de aprovação de um admin
    public_join_requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    neighborhood: Mapped[str | None] = mapped_column(String(80), nullable=True)

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
    # quiz de autoavaliação: pedido a todo membro ativo até responder (inclusive quem já estava no grupo), exceto
    # quem já tem perfil consolidado pelas avaliações (dispensado). Regras em app.services.level_quiz
    self_assessment: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # respostas (índice por atributo)
    self_assessed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    self_assessment_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)  # field | goalkeeper
    quiz_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)  # nível calculado pelas respostas
    declared_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)  # nível que o jogador diz ter
    # nível confirmado (ou ajustado) por um administrador depois do quiz
    confirmed_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # lembretes do quiz: último aviso (manual ou automático) e quantos automáticos já foram enviados
    quiz_reminded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    quiz_reminders_count: Mapped[int] = mapped_column(Integer, default=0)
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
