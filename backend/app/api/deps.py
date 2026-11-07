from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import Forbidden, Unauthorized
from app.core.security import decode_access_claims
from app.domain.enums import MemberStatus
from app.models import FootballGroup, GroupMember, Match, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)

DB = Annotated[Session, Depends(get_db)]


def get_current_user(db: DB, token: Annotated[str | None, Depends(oauth2_scheme)]) -> User:
    if not token:
        raise Unauthorized("Autenticação necessária")
    claims = decode_access_claims(token)
    if claims is None:
        raise Unauthorized("Token inválido ou expirado", code="invalid_token")
    user_id, issued_at = claims
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("Usuário inativo ou inexistente", code="invalid_token")
    if user.password_changed_at is not None and issued_at < int(user.password_changed_at.timestamp()):
        raise Unauthorized("Sessão encerrada após troca de senha. Entre novamente.", code="invalid_token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_platform_admin(user: CurrentUser) -> User:
    if not user.is_platform_admin:
        raise Forbidden("Apenas administradores da plataforma")
    return user


PlatformAdmin = Annotated[User, Depends(require_platform_admin)]

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _path_int(request: Request, name: str) -> int | None:
    try:
        return int(request.path_params[name])
    except (KeyError, TypeError, ValueError):
        return None


def block_writes_in_suspended_group(request: Request, db: DB, user: CurrentUser) -> None:
    """Grupo suspenso pela administração da plataforma fica somente leitura para os membros.

    Aplicada nas rotas com escopo de grupo (identificado por `group_id` ou `match_id` no caminho). Para quem não
    participa do grupo nada é revelado aqui: a própria rota responde 404.
    """
    if request.method in _READ_METHODS:
        return
    group_id = _path_int(request, "group_id")
    if group_id is None:
        match_id = _path_int(request, "match_id")
        if match_id is None:
            return
        group_id = db.scalar(select(Match.group_id).where(Match.id == match_id))
        if group_id is None:
            return
    row = db.execute(
        select(FootballGroup.suspended_at, FootballGroup.suspension_reason)
        .join(GroupMember, GroupMember.group_id == FootballGroup.id)
        .where(FootballGroup.id == group_id, GroupMember.user_id == user.id,
               GroupMember.status == MemberStatus.ACTIVE)
    ).first()
    if row is not None and row.suspended_at is not None:
        raise Forbidden(
            "Grupo suspenso pela administração da plataforma. Ele fica disponível só para consulta.",
            code="group_suspended", details={"reason": row.suspension_reason},
        )


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
