"""Web Push (VAPID). Opcional: sem chaves configuradas, apenas as notificações internas existem.

As notificações criadas numa transação entram numa fila da sessão e só são enviadas depois do
commit, em uma thread separada, para não atrasar a requisição nem enviar algo que foi desfeito.
"""

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import utcnow
from app.models import PushSubscription

logger = logging.getLogger("pelada.push")
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="push")
_QUEUE_KEY = "push_queue"


def is_enabled() -> bool:
    return bool(settings.vapid_public_key and settings.vapid_private_key)


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def target_url(group_id: int | None, match_id: int | None) -> str:
    if match_id is not None:
        return f"/partidas/{match_id}"
    if group_id is not None:
        return f"/grupos/{group_id}"
    return "/notificacoes"


def enqueue(db: Session, user_ids: list[int], title: str, body: str, url: str) -> None:
    if not is_enabled() or not user_ids:
        return
    db.info.setdefault(_QUEUE_KEY, []).append({"user_ids": list(user_ids), "title": title, "body": body, "url": url})


def subscribe(db: Session, user_id: int, endpoint: str, keys: dict, user_agent: str | None) -> PushSubscription:
    h = endpoint_hash(endpoint)
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == h))
    if sub is None:
        sub = PushSubscription(user_id=user_id, endpoint=endpoint, endpoint_hash=h, keys=keys, user_agent=user_agent)
        db.add(sub)
    else:  # mesmo navegador: atualiza chaves e dono
        sub.user_id, sub.keys, sub.user_agent = user_id, keys, user_agent
    db.commit()
    return sub


def unsubscribe(db: Session, user_id: int, endpoint: str) -> bool:
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == endpoint_hash(endpoint)))
    if sub is None or sub.user_id != user_id:
        return False
    db.delete(sub)
    db.commit()
    return True


def _send_batch(items: list[dict]) -> None:
    from pywebpush import WebPushException, webpush  # importação tardia: dependência só usada com push ativo

    from app.core.db import SessionLocal

    with SessionLocal() as db:
        for item in items:
            subs = db.scalars(select(PushSubscription).where(PushSubscription.user_id.in_(item["user_ids"]))).all()
            payload = json.dumps({"title": item["title"], "body": item["body"], "url": item["url"]})
            for sub in subs:
                try:
                    webpush(
                        subscription_info={"endpoint": sub.endpoint, "keys": sub.keys},
                        data=payload,
                        vapid_private_key=settings.vapid_private_key,
                        vapid_claims={"sub": settings.vapid_subject},
                        timeout=10,
                    )
                    sub.last_used_at = utcnow()
                except WebPushException as exc:
                    status = getattr(exc.response, "status_code", None)
                    if status in (404, 410):  # inscrição expirada ou revogada pelo navegador
                        db.delete(sub)
                    else:
                        logger.warning("Falha no push para a inscrição %s: %s", sub.id, exc)
                except Exception:  # noqa: BLE001 - erro de rede não pode derrubar o worker
                    logger.exception("Erro inesperado ao enviar push")
        db.commit()


def send_now(user_ids: list[int], title: str, body: str, url: str) -> None:
    """Envio direto (usado pelo teste de push); respeita a mesma thread de envio."""
    if is_enabled():
        _executor.submit(_send_batch, [{"user_ids": user_ids, "title": title, "body": body, "url": url}])


@event.listens_for(Session, "after_commit")
def _flush_queue(session: Session) -> None:
    items = session.info.pop(_QUEUE_KEY, None)
    if items:
        _executor.submit(_send_batch, items)


@event.listens_for(Session, "after_rollback")
def _drop_queue(session: Session) -> None:
    session.info.pop(_QUEUE_KEY, None)
