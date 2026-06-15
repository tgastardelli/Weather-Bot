"""Position sizing por Kelly fracionario com caps de risco.

Para compra de YES a custo efetivo c (com fee) que paga 1.00:
    b = (1 - c) / c
    f* = (p * b - (1 - p)) / b
    stake = min(f* * kelly_fraction * bankroll, max_stake_per_order)
"""

from decimal import ROUND_DOWN, Decimal

CENT = Decimal("0.01")


def kelly_fraction_raw(model_prob: float, cost: Decimal) -> Decimal:
    """Fracao de Kelly pura (0 quando nao ha edge ou custo degenerado)."""
    if cost <= 0 or cost >= 1:
        return Decimal(0)
    p = Decimal(str(model_prob))
    b = (Decimal(1) - cost) / cost
    f = (p * b - (Decimal(1) - p)) / b
    return max(f, Decimal(0))


def kelly_stake(
    model_prob: float,
    cost: Decimal,
    *,
    bankroll: Decimal,
    kelly_multiplier: Decimal,
    max_stake_per_order: Decimal,
) -> Decimal:
    f = kelly_fraction_raw(model_prob, cost)
    stake = f * kelly_multiplier * bankroll
    stake = min(stake, max_stake_per_order, bankroll)
    return stake.quantize(CENT, rounding=ROUND_DOWN)
