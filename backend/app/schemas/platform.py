from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.domain import rating as R
from app.schemas.group import _WEIGHT_KEYS


class PlatformGroupDefaults(BaseModel):
    """Regras de nota e sorteio aplicadas a grupos criados daqui em diante."""

    initial_rating: float | None = Field(default=None, ge=0, le=5)
    min_evaluations_to_consolidate: int | None = Field(default=None, ge=1, le=100)
    rating_match_weight: float | None = Field(default=None, gt=0, le=1)
    provisional_match_weight: float | None = Field(default=None, gt=0, le=1)
    min_evaluators_full_weight: int | None = Field(default=None, ge=1, le=50)
    outlier_threshold: float | None = Field(default=None, gt=0, le=5)
    exclude_outliers: bool | None = None
    draw_weights: dict[str, float] | None = None

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


class PlatformSettingsUpdate(BaseModel):
    star_thresholds: dict[str, float] | None = None
    group_defaults: PlatformGroupDefaults | None = None
    reason: str | None = Field(default=None, max_length=300)

    @field_validator("star_thresholds")
    @classmethod
    def _thresholds(cls, v: dict[str, float] | None):
        return None if v is None else R.validate_thresholds(v)


class PlatformSettingsOut(BaseModel):
    star_thresholds: dict[str, float]  # {"5": 4.5, "4": 3.5, ...}: média mínima de cada nível
    star_labels: dict[str, str]
    group_defaults: dict
    updated_at: datetime | None
    recalculated_ratings: int | None = None  # preenchido quando a mudança de limites recalcula estrelas
