from typing import Annotated

from fastapi import Depends, Query
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import Forbidden, Unauthorized
from app.core.security import decode_access_token
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)

DB = Annotated[Session, Depends(get_db)]


def get_current_user(db: DB, token: Annotated[str | None, Depends(oauth2_scheme)]) -> User:
    if not token:
        raise Unauthorized("Autenticação necessária")
    user_id = decode_access_token(token)
    if user_id is None:
        raise Unauthorized("Token inválido ou expirado", code="invalid_token")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("Usuário inativo ou inexistente", code="invalid_token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_platform_admin(user: CurrentUser) -> User:
    if not user.is_platform_admin:
        raise Forbidden("Apenas administradores da plataforma")
    return user


PlatformAdmin = Annotated[User, Depends(require_platform_admin)]


class Pagination:
    def __init__(
        self,
        page: Annotated[int, Query(ge=1)] = 1,
        size: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        self.page = page
        self.size = size


Paging = Annotated[Pagination, Depends()]


def page_of(items, total: int, paging: Pagination) -> dict:
    return {"items": items, "total": total, "page": paging.page, "size": paging.size}
