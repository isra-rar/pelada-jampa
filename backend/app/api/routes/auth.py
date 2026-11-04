from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import DB, CurrentUser
from app.core.config import settings
from app.core.security import create_access_token
from app.schemas.auth import LoginIn, RegisterIn, TokenOut, UserOut, UserUpdate
from app.schemas.misc import HomeOut
from app.services import dashboard, users

router = APIRouter(tags=["Autenticação e usuário"])


def _token(user_id: int) -> TokenOut:
    return TokenOut(access_token=create_access_token(user_id), expires_in=settings.access_token_expire_minutes * 60)


@router.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, db: DB):
    """Cadastro. Se o administrador já adicionou o e-mail, a conta é reivindicada."""
    return users.register(db, data)


@router.post("/auth/login", response_model=TokenOut)
def login(data: LoginIn, db: DB):
    return _token(users.authenticate(db, data.email, data.password).id)


@router.post("/auth/token", response_model=TokenOut, include_in_schema=True)
def token(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DB):
    """Login no formato OAuth2 (usado pelo botão Authorize do Swagger). `username` = e-mail."""
    return _token(users.authenticate(db, form.username, form.password).id)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.patch("/me", response_model=UserOut)
def update_me(data: UserUpdate, user: CurrentUser, db: DB):
    return users.update_me(db, user, data)


@router.get("/me/home", response_model=HomeOut)
def home(user: CurrentUser, db: DB):
    """Tela inicial: próxima pelada por grupo, estado da confirmação, pendências e atividades."""
    return dashboard.home(db, user)
