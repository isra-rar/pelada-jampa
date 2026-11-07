"""Envio de e-mails. Sem SMTP configurado, a mensagem é registrada no log (útil em desenvolvimento)."""

import logging
import smtplib
from collections import deque
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger("pelada.mailer")

# últimas mensagens enviadas fora de produção (inspeção em testes e desenvolvimento)
OUTBOX: deque[dict] = deque(maxlen=50)


def send_email(to: str, subject: str, body: str) -> None:
    if settings.environment != "production":
        OUTBOX.append({"to": to, "subject": subject, "body": body})
    if not settings.smtp_host:
        logger.warning("SMTP não configurado; e-mail para %s não enviado.\nAssunto: %s\n%s", to, subject, body)
        return
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_tls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password or "")
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException):
        # falha de e-mail não deve derrubar a requisição; fica registrada para diagnóstico
        logger.exception("Falha ao enviar e-mail para %s", to)
