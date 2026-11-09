"""Exportação do financeiro em CSV, para quem fecha as contas numa planilha.

O formato segue o Excel em português: separador `;`, vírgula decimal, datas dd/mm/aaaa no horário de Brasília e
UTF-8 com BOM (sem ele o Excel embaralha os acentos). Textos livres (nome do jogador, observações) que começam
com `=`, `+`, `-` ou `@` recebem um apóstrofo na frente para não virarem fórmula ao abrir a planilha.
"""

import csv
import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.domain.enums import PaymentStatus
from app.models import FootballGroup, Match, Payment, User
from app.services import audit, payments
from app.services.stats import BR_TZ

ExportView = Literal["payments", "debtors"]

STATUS_LABELS = {
    PaymentStatus.PENDING: "Pendente",
    PaymentStatus.PAID: "Pago",
    PaymentStatus.EXEMPT: "Isento",
    PaymentStatus.REFUNDED: "Estornado",
}
METHOD_LABELS = {"pix": "Pix", "dinheiro": "Dinheiro", "transferencia": "Transferência", "outro": "Outro"}
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Sim" if value else "Não"
    if isinstance(value, Decimal):
        return f"{value:.2f}".replace(".", ",")
    if isinstance(value, datetime):
        return value.astimezone(BR_TZ).strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    text = str(value)
    if text.startswith(_FORMULA_START):
        text = "'" + text
    return text


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([cell(v) for v in row])
    return "﻿" + buf.getvalue()


def _payment_rows(db: Session, group_id: int, filters: dict) -> list[list]:
    stmt = payments.payments_stmt(group_id, **filters).order_by(Match.starts_at.desc(), Payment.id)
    items = db.scalars(stmt).unique().all()
    confirmer_ids = {p.confirmed_by_id for p in items if p.confirmed_by_id}
    names = (
        dict(db.execute(select(User.id, User.name).where(User.id.in_(confirmer_ids))).tuples().all())
        if confirmer_ids
        else {}
    )
    return [
        [
            p.match.starts_at.astimezone(BR_TZ).date(),
            p.match.name,
            p.member.display_name,
            Decimal(p.amount_due),
            Decimal(p.amount_paid),
            payments.outstanding(p),
            STATUS_LABELS.get(p.status, p.status),
            p.due_date,
            p.paid_at,
            METHOD_LABELS.get(p.method or "", p.method),
            p.reported_paid_at,
            names.get(p.confirmed_by_id) if p.confirmed_by_id else None,
            p.notes,
        ]
        for p in items
    ]


PAYMENT_HEADER = [
    "Data da pelada", "Pelada", "Jogador", "Valor devido", "Valor pago", "Em aberto", "Situação", "Vencimento",
    "Pago em", "Forma de pagamento", "Jogador informou pagamento em", "Confirmado por", "Observações",
]
DEBTOR_HEADER = ["Jogador", "Peladas em aberto", "Valor em aberto", "Pelada mais antiga"]


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:40] or "grupo"


def export_csv(
    db: Session, group: FootballGroup, view: ExportView, filters: dict, actor: User
) -> tuple[str, str, int]:
    """Gera o CSV pedido. Retorna (conteúdo, nome do arquivo, quantidade de linhas). A exportação é auditada."""
    if view == "debtors":
        debtors = payments.debtors(db, group.id)
        rows = [
            [d["name"], d["pending_count"], d["outstanding"], d["oldest_match_date"].astimezone(BR_TZ).date()]
            for d in debtors
        ]
        content = _csv(DEBTOR_HEADER, rows)
        prefix = "inadimplentes"
    else:
        rows = _payment_rows(db, group.id, filters)
        content = _csv(PAYMENT_HEADER, rows)
        prefix = "financeiro"
    today = utcnow().astimezone(BR_TZ).date().isoformat()
    filename = f"{prefix}-{_slug(group.name)}-{today}.csv"
    audit.record(
        db, action="finance.exported", entity_type="group", entity_id=group.id, actor_user_id=actor.id,
        group_id=group.id,
        after={"view": view, "rows": len(rows), **{k: v for k, v in filters.items() if v is not None}},
    )
    db.commit()
    return content, filename, len(rows)
