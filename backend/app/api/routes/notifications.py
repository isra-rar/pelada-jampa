from fastapi import APIRouter

from app.api.deps import DB, CurrentUser, Paging, page_of
from app.schemas.common import Message, Page
from app.schemas.misc import NotificationOut
from app.services import notifications

router = APIRouter(prefix="/notifications", tags=["Notificações"])


@router.get("", response_model=Page[NotificationOut])
def list_notifications(user: CurrentUser, db: DB, paging: Paging, unread_only: bool = False):
    items, total = notifications.list_for_user(db, user.id, unread_only=unread_only, page=paging.page,
                                               size=paging.size)
    return page_of(items, total, paging)


@router.post("/{notification_id}/read", response_model=NotificationOut)
def read(notification_id: int, user: CurrentUser, db: DB):
    return notifications.mark_read(db, user.id, notification_id)


@router.post("/read-all", response_model=Message)
def read_all(user: CurrentUser, db: DB):
    n = notifications.mark_all_read(db, user.id)
    return {"message": f"{n} notificação(ões) marcada(s) como lida(s)"}
