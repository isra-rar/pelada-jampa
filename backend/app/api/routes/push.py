from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentUser
from app.core.config import settings
from app.core.errors import BusinessRuleError
from app.schemas.common import Message
from app.services import push

router = APIRouter(prefix="/push", tags=["Notificações push"])


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


class SubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000, pattern=r"^https://")
    keys: PushKeys


class UnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)


class PushConfigOut(BaseModel):
    enabled: bool
    public_key: str | None


@router.get("/config", response_model=PushConfigOut)
def config():
    """Chave pública VAPID usada pelo navegador para se inscrever."""
    return {"enabled": push.is_enabled(), "public_key": settings.vapid_public_key if push.is_enabled() else None}


@router.post("/subscriptions", response_model=Message, status_code=status.HTTP_201_CREATED)
def subscribe(data: SubscriptionIn, request: Request, user: CurrentUser, db: DB):
    if not push.is_enabled():
        raise BusinessRuleError("Notificações push não estão habilitadas neste servidor", code="push_disabled")
    push.subscribe(db, user.id, data.endpoint, data.keys.model_dump(), request.headers.get("user-agent", "")[:300])
    return {"message": "Notificações push ativadas neste dispositivo"}


@router.post("/subscriptions/remove", response_model=Message)
def unsubscribe(data: UnsubscribeIn, user: CurrentUser, db: DB):
    removed = push.unsubscribe(db, user.id, data.endpoint)
    return {"message": "Notificações push desativadas" if removed else "Inscrição não encontrada"}


@router.post("/test", response_model=Message)
def test(user: CurrentUser):
    if not push.is_enabled():
        raise BusinessRuleError("Notificações push não estão habilitadas neste servidor", code="push_disabled")
    push.send_now([user.id], "Pelada+", "Notificações push funcionando!", "/notificacoes")
    return {"message": "Notificação de teste enviada"}
