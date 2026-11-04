from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, Conflict, Unauthorized
from app.core.security import hash_password, verify_password
from app.models import User
from app.schemas.auth import RegisterIn, UserUpdate


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
        if not verify_password(data.current_password or "", user.password_hash):
            raise BusinessRuleError("Senha atual incorreta", code="invalid_password")
        user.password_hash = hash_password(data.new_password)
    for field in ("name", "phone", "photo_url"):
        value = getattr(data, field)
        if value is not None:
            setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user
