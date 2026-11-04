"""Controle financeiro manual (PRD §8). Operações transacionais e auditadas."""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import BusinessRuleError, Conflict, NotFound
from app.domain.enums import PaymentStatus
from app.models import FootballGroup, Match, Payment, User
from app.schemas.finance import ChargeCreate, PaymentUpdate
from app.services import audit
from app.services.access import get_member_in_group

ZERO = Decimal("0.00")
_AUDIT_FIELDS = ["amount_due", "amount_paid", "status", "due_date", "paid_at", "method", "confirmed_by_id", "notes"]


def outstanding(p: Payment) -> Decimal:
    if p.status != PaymentStatus.PENDING:
        return ZERO
    return max(ZERO, Decimal(p.amount_due) - Decimal(p.amount_paid))


def payment_out(p: Payment) -> dict:
    return {
        "id": p.id,
        "group_id": p.group_id,
        "match_id": p.match_id,
        "match_name": p.match.name,
        "match_date": p.match.starts_at,
        "member_id": p.member_id,
        "player_name": p.member.display_name,
        "amount_due": p.amount_due,
        "amount_paid": p.amount_paid,
        "outstanding": outstanding(p),
        "status": p.status,
        "due_date": p.due_date,
        "paid_at": p.paid_at,
        "method": p.method,
        "confirmed_by_id": p.confirmed_by_id,
        "notes": p.notes,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


def debt_out(p: Payment) -> dict:
    return {
        "payment_id": p.id,
        "match_id": p.match_id,
        "match_name": p.match.name,
        "match_date": p.match.starts_at,
        "amount_due": p.amount_due,
        "amount_paid": p.amount_paid,
        "outstanding": outstanding(p),
        "status": p.status.value,
    }


def get_payment(db: Session, group_id: int, payment_id: int, *, lock: bool = False) -> Payment:
    stmt = select(Payment).where(Payment.id == payment_id)
    if lock:
        stmt = stmt.with_for_update()
    p = db.scalar(stmt)
    if p is None or p.group_id != group_id:
        raise NotFound("Pagamento não encontrado")
    return p


def pending_debts(db: Session, group_id: int, member_id: int, exclude_match_id: int | None = None) -> list[Payment]:
    stmt = select(Payment).join(Match, Match.id == Payment.match_id).where(
        Payment.group_id == group_id, Payment.member_id == member_id, Payment.status == PaymentStatus.PENDING
    )
    if exclude_match_id is not None:
        stmt = stmt.where(Payment.match_id != exclude_match_id)
    return list(db.scalars(stmt.order_by(Match.starts_at)).all())


def members_with_debt(db: Session, group_id: int, member_ids: list[int], exclude_match_id: int | None) -> set[int]:
    if not member_ids:
        return set()
    stmt = select(Payment.member_id).where(
        Payment.group_id == group_id,
        Payment.member_id.in_(member_ids),
        Payment.status == PaymentStatus.PENDING,
    )
    if exclude_match_id is not None:
        stmt = stmt.where(Payment.match_id != exclude_match_id)
    return set(db.scalars(stmt).all())


def list_payments(
    db: Session,
    group_id: int,
    *,
    match_id: int | None = None,
    member_id: int | None = None,
    status: PaymentStatus | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[Payment], int]:
    stmt = select(Payment).join(Match, Match.id == Payment.match_id).where(Payment.group_id == group_id)
    if match_id is not None:
        stmt = stmt.where(Payment.match_id == match_id)
    if member_id is not None:
        stmt = stmt.where(Payment.member_id == member_id)
    if status is not None:
        stmt = stmt.where(Payment.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(Match.starts_at.desc(), Payment.id).offset((page - 1) * size).limit(size)
    ).unique().all()
    return list(items), total


def ensure_charge(db: Session, match: Match, member_id: int, amount: Decimal | None = None) -> tuple[Payment, bool]:
    existing = db.scalar(select(Payment).where(Payment.match_id == match.id, Payment.member_id == member_id))
    if existing is not None:
        return existing, False
    p = Payment(
        group_id=match.group_id,
        match_id=match.id,
        member_id=member_id,
        amount_due=amount if amount is not None else match.price,
        amount_paid=ZERO,
        status=PaymentStatus.PENDING,
        due_date=match.starts_at.date(),
    )
    db.add(p)
    db.flush()
    return p, True


def create_charge(db: Session, group: FootballGroup, data: ChargeCreate, actor: User) -> Payment:
    match = db.get(Match, data.match_id)
    if match is None or match.group_id != group.id:
        raise NotFound("Pelada não encontrada")
    get_member_in_group(db, group.id, data.member_id)
    try:
        p, created = ensure_charge(db, match, data.member_id, data.amount_due)
    except IntegrityError:
        db.rollback()
        raise Conflict("Já existe lançamento para este jogador nesta pelada", code="payment_exists") from None
    if not created:
        raise Conflict("Já existe lançamento para este jogador nesta pelada", code="payment_exists")
    if data.due_date:
        p.due_date = data.due_date
    p.notes = data.notes
    audit.record(db, action="payment.created", entity_type="payment", entity_id=p.id, actor_user_id=actor.id,
                 group_id=group.id, after=audit.snapshot(p, _AUDIT_FIELDS))
    db.commit()
    return p


def _mark_paid(p: Payment, actor: User, amount: Decimal | None, paid_at, method, notes):
    p.amount_paid = amount if amount is not None else p.amount_due
    p.status = PaymentStatus.PAID
    p.paid_at = paid_at or p.paid_at or utcnow()
    p.confirmed_by_id = actor.id
    if method is not None:
        p.method = method
    if notes is not None:
        p.notes = notes


def update_payment(db: Session, group: FootballGroup, payment_id: int, data: PaymentUpdate, actor: User) -> Payment:
    """Registra/corrige um pagamento. Valores absolutos tornam a operação idempotente."""
    p = get_payment(db, group.id, payment_id, lock=True)
    before = audit.snapshot(p, _AUDIT_FIELDS)
    if data.amount_due is not None:
        p.amount_due = data.amount_due
    if data.due_date is not None:
        p.due_date = data.due_date
    if data.method is not None:
        p.method = data.method
    if data.notes is not None:
        p.notes = data.notes
    if data.amount_paid is not None and data.amount_paid > Decimal(p.amount_due):
        raise BusinessRuleError("Valor pago maior que o valor devido", code="overpayment")

    if data.status == PaymentStatus.PAID:
        _mark_paid(p, actor, data.amount_paid, data.paid_at, None, None)
        if Decimal(p.amount_paid) < Decimal(p.amount_due):
            raise BusinessRuleError(
                "Para marcar como pago, o valor pago deve cobrir o valor devido (ou registre pagamento parcial)",
                code="partial_payment",
            )
    elif data.status == PaymentStatus.EXEMPT:
        p.status = PaymentStatus.EXEMPT
        p.confirmed_by_id = actor.id
    elif data.status == PaymentStatus.REFUNDED:
        if not data.reason:
            raise BusinessRuleError("Informe o motivo do estorno", code="reason_required")
        p.status = PaymentStatus.REFUNDED
        p.confirmed_by_id = actor.id
    elif data.status == PaymentStatus.PENDING:
        p.status = PaymentStatus.PENDING
        if data.amount_paid is not None:
            p.amount_paid = data.amount_paid
        p.paid_at = None
    elif data.amount_paid is not None:
        if data.amount_paid >= Decimal(p.amount_due):
            _mark_paid(p, actor, data.amount_paid, data.paid_at, None, None)
        else:
            p.amount_paid = data.amount_paid  # pagamento parcial: continua pendente
            p.status = PaymentStatus.PENDING if p.status == PaymentStatus.PAID else p.status
            p.confirmed_by_id = actor.id
    if p.status == PaymentStatus.PENDING and Decimal(p.amount_paid) >= Decimal(p.amount_due) > 0:
        _mark_paid(p, actor, p.amount_paid, data.paid_at, None, None)

    after = audit.snapshot(p, _AUDIT_FIELDS)
    if before != after:
        audit.record(db, action="payment.updated", entity_type="payment", entity_id=p.id, actor_user_id=actor.id,
                     group_id=group.id, before=before, after=after, reason=data.reason)
    db.commit()
    return p


def settle(db: Session, group: FootballGroup, payment_ids: list[int], method, paid_at, notes, actor: User):
    """Quitação de uma ou várias pendências em uma única transação."""
    ids = list(dict.fromkeys(payment_ids))
    rows = db.scalars(select(Payment).where(Payment.id.in_(ids)).with_for_update()).unique().all()
    found = {p.id: p for p in rows if p.group_id == group.id}
    missing = [i for i in ids if i not in found]
    if missing:
        raise NotFound(f"Pagamentos não encontrados: {missing}")
    result = []
    for pid in ids:
        p = found[pid]
        if p.status == PaymentStatus.PAID:
            result.append(p)  # idempotente
            continue
        if p.status != PaymentStatus.PENDING:
            raise BusinessRuleError(f"Pagamento {pid} não está pendente", code="not_pending")
        before = audit.snapshot(p, _AUDIT_FIELDS)
        _mark_paid(p, actor, None, paid_at, method, notes)
        audit.record(db, action="payment.settled", entity_type="payment", entity_id=p.id, actor_user_id=actor.id,
                     group_id=group.id, before=before, after=audit.snapshot(p, _AUDIT_FIELDS))
        result.append(p)
    db.commit()
    return result


def summary(db: Session, group_id: int, match_id: int | None = None) -> dict:
    stmt = select(Payment).where(Payment.group_id == group_id)
    if match_id is not None:
        stmt = stmt.where(Payment.match_id == match_id)
    rows = db.scalars(stmt).unique().all()
    expected = received = pending = ZERO
    counts = {s: 0 for s in PaymentStatus}
    debtors = set()
    for p in rows:
        counts[p.status] += 1
        if p.status in (PaymentStatus.EXEMPT, PaymentStatus.REFUNDED):
            continue
        expected += Decimal(p.amount_due)
        received += Decimal(p.amount_paid)
        if p.status == PaymentStatus.PENDING:
            pending += outstanding(p)
            debtors.add(p.member_id)
    return {
        "group_id": group_id,
        "match_id": match_id,
        "expected": expected,
        "received": received,
        "pending": pending,
        "exempt_count": counts[PaymentStatus.EXEMPT],
        "refunded_count": counts[PaymentStatus.REFUNDED],
        "pending_count": counts[PaymentStatus.PENDING],
        "paid_count": counts[PaymentStatus.PAID],
        "debtors_count": len(debtors),
    }


def debtors(db: Session, group_id: int) -> list[dict]:
    rows = db.scalars(
        select(Payment).where(Payment.group_id == group_id, Payment.status == PaymentStatus.PENDING)
    ).unique().all()
    by_member: dict[int, dict] = {}
    for p in rows:
        amount = outstanding(p)
        if amount <= 0:
            continue
        entry = by_member.setdefault(
            p.member_id,
            {"member_id": p.member_id, "name": p.member.display_name, "pending_count": 0, "outstanding": ZERO,
             "oldest_match_date": p.match.starts_at},
        )
        entry["pending_count"] += 1
        entry["outstanding"] += amount
        entry["oldest_match_date"] = min(entry["oldest_match_date"], p.match.starts_at)
    return sorted(by_member.values(), key=lambda d: (-d["outstanding"], d["name"]))


def member_finance(db: Session, group_id: int, member_id: int) -> dict:
    rows = db.scalars(select(Payment).where(Payment.group_id == group_id, Payment.member_id == member_id)).unique().all()
    pending = [p for p in rows if p.status == PaymentStatus.PENDING]
    return {
        "pending_count": len(pending),
        "pending_amount": sum((outstanding(p) for p in pending), ZERO),
        "paid_amount": sum((Decimal(p.amount_paid) for p in rows if p.status == PaymentStatus.PAID), ZERO),
    }
