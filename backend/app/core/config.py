from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "dev-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PELADA_", extra="ignore")

    app_name: str = "Pelada+ API"
    environment: str = "development"  # development | test | production
    database_url: str = "sqlite:///./pelada.db"
    jwt_secret: str = DEFAULT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 60
    draw_time_limit_seconds: float = 2.0
    evaluation_reminder_minutes_before: int = 60
    confirmation_reminder_hours_before: int = 12
    cors_origins: list[str] = ["http://localhost:4200"]
    cors_origin_regex: str | None = r"^http://(localhost|127\.0\.0\.1):\d+$"

    @model_validator(mode="after")
    def _check_secret(self) -> "Settings":
        if self.environment == "production" and self.jwt_secret == DEFAULT_SECRET:
            raise ValueError("PELADA_JWT_SECRET deve ser definido em produção")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
