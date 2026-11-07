from datetime import timedelta

import bcrypt
import jwt

from app.core.config import settings
from app.core.db import utcnow


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int, expires_minutes: int | None = None) -> str:
    now = utcnow()
    exp = now + timedelta(minutes=expires_minutes or settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "iat": int(now.timestamp()), "exp": int(exp.timestamp()), "type": "access"}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> int | None:
    claims = decode_access_claims(token)
    return claims[0] if claims else None


def decode_access_claims(token: str) -> tuple[int, int] | None:
    """Retorna (user_id, iat) de um token de acesso válido."""
    try:
        # expiração validada abaixo com o relógio único da aplicação
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False, "require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    if int(payload["exp"]) <= int(utcnow().timestamp()):
        return None
    try:
        return int(payload["sub"]), int(payload.get("iat", 0))
    except (KeyError, ValueError, TypeError):
        return None
