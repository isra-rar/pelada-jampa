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
    # administradores do grupo podem avaliar a pelada mesmo sem presença registrada
    admins_can_evaluate: bool = True
    confirmation_reminder_hours_before: int = 12
    cors_origins: list[str] = ["http://localhost:4200"]
    cors_origin_regex: str | None = r"^http://(localhost|127\.0\.0\.1):\d+$"

    # --- URLs públicas (links em e-mails e arquivos enviados)
    frontend_url: str = "http://localhost:8080"
    public_base_url: str | None = None  # base da API para montar URLs de mídia; padrão: URL da requisição

    # --- e-mail (sem SMTP configurado, as mensagens são registradas no log)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "Pelada+ <nao-responda@pelada.local>"
    smtp_tls: bool = True
    password_reset_expire_minutes: int = 60

    # --- uploads
    media_dir: str = "./media"
    max_upload_bytes: int = 2 * 1024 * 1024

    # --- login com Google (Google Identity Services); sem client id o botão não aparece
    google_client_id: str | None = None

    # --- limite de tentativas em login, cadastro e senha (contadores em memória, por processo)
    rate_limit_enabled: bool = True

    # --- web push (opcional; sem chaves VAPID o push fica desativado)
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_subject: str = "mailto:admin@pelada.local"

    @model_validator(mode="after")
    def _check_secret(self) -> "Settings":
        if self.environment == "production" and self.jwt_secret == DEFAULT_SECRET:
            raise ValueError("PELADA_JWT_SECRET deve ser definido em produção")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
