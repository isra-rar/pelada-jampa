from fastapi import APIRouter, Query, status

from app.api.deps import DB, CurrentUser, Paging, page_of
from app.core.errors import Forbidden
from app.domain.enums import PaymentStatus
from app.schemas.common import Page
from app.schemas.finance import ChargeCreate, DebtorOut, FinanceSummary, PaymentOut, PaymentUpdate, SettleIn
from app.services import payments
from app.services.access import get_group, get_member_in_group, require_admin, require_member

router = APIRouter(tags=["Financeiro"])


@router.get("/groups/{group_id}/payments", response_model=Page[PaymentOut])
def list_payments(
    group_id: int, user: CurrentUser, db: DB, paging: Paging,
    match_id: int | None = None, member_id: int | None = None,
    status_: PaymentStatus | None = Query(default=None, alias="status"),
    reported: bool | None = Query(default=None, description="true: jogador informou que pagou"),
):
    """Administradores veem tudo; jogadores veem apenas os próprios lançamentos. Ordem: pelada mais recente
    primeiro."""
    me = require_member(db, group_id, user)
    if not me.is_admin:
        if member_id is not None and member_id != me.id:
            raise Forbidden("Você só pode consultar seus próprios pagamentos")
        member_id = me.id
    items, total = payments.list_payments(db, group_id, match_id=match_id, member_id=member_id, status=status_,
                                          reported=reported, page=paging.page, size=paging.size)
    return page_of([payments.payment_out(p) for p in items], total, paging)


@router.post("/groups/{group_id}/payments", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
def create_charge(group_id: int, data: ChargeCreate, user: CurrentUser, db: DB):
    require_admin(db, group_id, user)
    return payments.payment_out(payments.create_charge(db, get_group(db, group_id), data, user))


@router.patch("/groups/{group_id}/payments/{payment_id}", response_model=PaymentOut)
def update_payment(group_id: int, payment_id: int, data: PaymentUpdate, user: CurrentUser, db: DB):
    """Registra/corrige pagamento (valores absolutos → reenvio não duplica). Auditado."""
    require_admin(db, group_id, user)
    return payments.payment_out(payments.update_payment(db, get_group(db, group_id), payment_id, data, user))


@router.post("/groups/{group_id}/payments/settle", response_model=list[PaymentOut])
def settle(group_id: int, data: SettleIn, user: CurrentUser, db: DB):
    """Quita uma ou várias pendências de uma vez (transação única)."""
    require_admin(db, group_id, user)
    rows = payments.settle(db, get_group(db, group_id), data.payment_ids, data.method, data.paid_at, data.notes, user)
    return [payments.payment_out(p) for p in rows]


@router.get("/groups/{group_id}/finance/summary", response_model=FinanceSummary)
def summary(group_id: int, user: CurrentUser, db: DB, match_id: int | None = None):
    require_admin(db, group_id, user)
    return payments.summary(db, group_id, match_id)


@router.get("/groups/{group_id}/finance/debtors", response_model=list[DebtorOut])
def debtors(group_id: int, user: CurrentUser, db: DB):
    require_admin(db, group_id, user)
    return payments.debtors(db, group_id)


@router.get("/groups/{group_id}/members/{member_id}/debts")
def member_debts(group_id: int, member_id: int, user: CurrentUser, db: DB):
    me = require_member(db, group_id, user)
    member = get_member_in_group(db, group_id, member_id)
    if not (me.is_admin or me.id == member.id):
        raise Forbidden("Você só pode consultar suas próprias pendências")
    return [payments.debt_out(p) for p in payments.pending_debts(db, group_id, member.id)]


@router.post("/groups/{group_id}/payments/{payment_id}/report", response_model=PaymentOut)
def report_payment(group_id: int, payment_id: int, user: CurrentUser, db: DB):
    """Jogador informa que já pagou (por fora do app); os administradores são avisados para confirmar."""
    me = require_member(db, group_id, user)
    return payments.payment_out(payments.report_paid(db, get_group(db, group_id), payment_id, me))
