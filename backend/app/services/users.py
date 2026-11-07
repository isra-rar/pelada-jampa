import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import BusinessRuleError, Conflict, Unauthorized
from app.core.security import hash_password, verify_password
from app.models import PasswordResetToken, User
from app.schemas.auth import RegisterIn, UserUpdate
from app.services import mailer
from app.services.google_auth import GoogleIdentity


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == normalize_email(email)))


def register(db: Session, data: RegisterIn) -> User:
    user = get_by_email(db, data.email)
    if user is not None:
        if user.is_claimed:
            raise Conflict("E-mail já cadastrado", code="email_taken")
        # conta criada por um administrador: o jogador reivindica definindo a senha
        user.password_hash = hash_password(data.password)
        user.name = data.name
        user.phone = data.phone or user.phone
    else:
        user = User(
            email=normalize_email(data.email),
            name=data.name,
            phone=data.phone,
            password_hash=hash_password(data.password),
        )
        db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    user = get_by_email(db, email)
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise Unauthorized("E-mail ou senha inválidos", code="invalid_credentials")
    return user


def get_or_create_placeholder(db: Session, email: str, name: str) -> User:
    user = get_by_email(db, email)
    if user is None:
        user = User(email=normalize_email(email), name=name, password_hash=None)
        db.add(user)
        db.flush()
    return user


def update_me(db: Session, user: User, data: UserUpdate) -> User:
    if data.new_password:
        # conta só-Google ainda não tem senha: pode criar uma sem informar a atual
        if user.has_password and not verify_password(data.current_password or "", user.password_hash):
            raise BusinessRuleError("Senha atual incorreta", code="invalid_password")
        user.password_hash = hash_password(data.new_password)
        user.password_changed_at = utcnow()
    for field in ("name", "phone", "photo_url"):
        value = getattr(data, field)
        if value is not None:
            setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


# ------------------------------------------------------------------ redefinição de senha

_RESET_COOLDOWN = timedelta(minutes=1)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def request_password_reset(db: Session, email: str) -> None:
    """Envia o link de redefinição. Não revela se o e-mail existe (resposta sempre igual)."""
    user = get_by_email(db, email)
    if user is None or not user.is_active:
        return
    now = utcnow()
    recent = db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.created_at > now - _RESET_COOLDOWN,
        )
    )
    if recent is not None:
        return  # evita disparos repetidos em sequência
    token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=_hash_token(token),
        expires_at=now + timedelta(minutes=settings.password_reset_expire_minutes),
    ))
    db.commit()
    link = f"{settings.frontend_url.rstrip('/')}/redefinir-senha?token={token}"
    mailer.send_email(
        user.email,
        "Redefinição de senha — Pelada+",
        f"Olá, {user.name}!\n\n"
        f"Recebemos um pedido para redefinir sua senha. Use o link abaixo em até "
        f"{settings.password_reset_expire_minutes} minutos:\n\n{link}\n\n"
        "Se você não fez esse pedido, ignore este e-mail: sua senha continua a mesma.",
    )


def confirm_password_reset(db: Session, token: str, new_password: str) -> User:
    now = utcnow()
    row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash_token(token)))
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise BusinessRuleError("Link de redefinição inválido ou expirado", code="invalid_reset_token")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise BusinessRuleError("Link de redefinição inválido ou expirado", code="invalid_reset_token")
    user.password_hash = hash_password(new_password)
    user.password_changed_at = now
    # o link é de uso único e invalida os demais pedidos pendentes
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    db.commit()
    db.refresh(user)
    return user


# ------------------------------------------------------------------ login com Google


def get_by_google_sub(db: Session, sub: str) -> User | None:
    return db.scalar(select(User).where(User.google_sub == sub))


def login_with_google(db: Session, identity: GoogleIdentity) -> User:
    """Entra com Google. Ordem: conta já vinculada → conta com o mesmo e-mail (verificado pelo
    Google) → nova conta. Contas criadas pelo administrador são reivindicadas neste momento."""
    user = get_by_google_sub(db, identity.sub)
    if user is None:
        user = get_by_email(db, identity.email)
        if user is not None:
            if not user.is_active:
                raise Unauthorized("Conta desativada", code="invalid_credentials")
            if user.google_sub is not None:
                # o e-mail pertence a outra conta Google já vinculada
                raise Conflict("Este e-mail já está ligado a outra conta Google", code="google_conflict")
            user.google_sub = identity.sub
        else:
            user = User(
                email=normalize_email(identity.email),
                name=(identity.name or identity.email.split("@")[0])[:120],
                google_sub=identity.sub,
                password_hash=None,
                is_active=True,
            )
            db.add(user)
    if not user.is_active:
        raise Unauthorized("Conta desativada", code="invalid_credentials")
    if not user.photo_url and identity.picture:
        user.photo_url = identity.picture[:500]
    db.commit()
    db.refresh(user)
    return user


def link_google(db: Session, user: User, identity: GoogleIdentity) -> User:
    other = get_by_google_sub(db, identity.sub)
    if other is not None and other.id != user.id:
        raise Conflict("Esta conta Google já está ligada a outro usuário", code="google_conflict")
    user.google_sub = identity.sub
    if not user.photo_url and identity.picture:
        user.photo_url = identity.picture[:500]
    db.commit()
    db.refresh(user)
    return user


def unlink_google(db: Session, user: User) -> User:
    if not user.has_password:
        raise BusinessRuleError("Crie uma senha antes de desconectar o Google, senão você perde o acesso",
                                code="password_required")
    user.google_sub = None
    db.commit()
    db.refresh(user)
    return user
