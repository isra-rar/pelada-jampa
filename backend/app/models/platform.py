from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UTCDateTime, utcnow


class PlatformSettings(Base):
    """Configurações globais da plataforma (linha única, id = 1).

    `tier_thresholds`: média mínima de cada Tier (o D começa em 0).
    `group_defaults`: valores iniciais das regras de nota e sorteio para grupos novos.
    """

    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tier_thresholds: Mapped[dict] = mapped_column(JSON)
    group_defaults: Mapped[dict] = mapped_column(JSON)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
