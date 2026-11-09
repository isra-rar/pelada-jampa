"""Arredondamentos de dinheiro."""

from decimal import ROUND_CEILING, Decimal

CENT = Decimal("0.01")


def ceil_cents(value) -> Decimal:
    """Arredonda para cima no centavo: um rateio nunca fica abaixo do custo."""
    return Decimal(value).quantize(CENT, rounding=ROUND_CEILING)


def split(total, people: int) -> Decimal:
    """Parte de cada um num rateio (arredondada para cima); zero sem pessoas."""
    return ceil_cents(Decimal(total) / people) if people > 0 else Decimal("0")
