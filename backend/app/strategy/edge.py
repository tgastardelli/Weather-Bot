"""Edge liquido de fees (weather_fees: 5% taker-only).

Convencao Polymarket:
    fee_por_share = rate * p * (1 - p)
Comprar YES a preco `ask` custa `ask + fee(ask)` por share; EV liquido por
share = p_model - ask - fee. Edge bruto abaixo da fee = trade perdedor.
"""

from decimal import Decimal

FEE_PRECISION = Decimal("0.00001")


def taker_fee_per_share(price: Decimal, fee_rate: Decimal) -> Decimal:
    return (fee_rate * price * (Decimal(1) - price)).quantize(FEE_PRECISION)


def gross_edge(model_prob: float, price: Decimal) -> Decimal:
    return (Decimal(str(model_prob)) - price).quantize(FEE_PRECISION)


def net_edge(model_prob: float, price: Decimal, fee_rate: Decimal) -> Decimal:
    return (gross_edge(model_prob, price) - taker_fee_per_share(price, fee_rate)).quantize(
        FEE_PRECISION
    )


def cost_per_share(price: Decimal, fee_rate: Decimal) -> Decimal:
    """Custo efetivo de 1 share YES comprado como taker (preco + fee)."""
    return (price + taker_fee_per_share(price, fee_rate)).quantize(FEE_PRECISION)
