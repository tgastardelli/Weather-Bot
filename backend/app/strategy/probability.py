"""Probabilidade por bucket a partir do ensemble (edge de calibração).

P(bucket) = fração de membros do ensemble que cai no bucket, após:
1. correção de viés da estação (bias_c, da tabela calibration_metrics);
2. inflação do spread (ensembles brutos são subdispersos);
3. conversão para a unidade do mercado e regra de arredondamento da cidade.
"""

from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from statistics import fmean
from typing import Literal

from app.polymarket.normalize import Bucket

Rounding = Literal["round", "floor"]


def celsius_to_unit(temp_c: float, unit: Literal["C", "F"]) -> float:
    return temp_c if unit == "C" else temp_c * 9.0 / 5.0 + 32.0


def adjust_members(
    members_tmax_c: list[float], bias_c: float = 0.0, spread_inflation: float = 1.0
) -> list[float]:
    """Aplica viés e infla o spread em torno da média do ensemble."""
    if not members_tmax_c:
        return []
    mean = fmean(members_tmax_c)
    return [mean + (m - mean) * spread_inflation + bias_c for m in members_tmax_c]


def to_market_value(temp_unit: float, rounding: Rounding) -> Decimal:
    """Mapeia a temperatura contínua para o valor inteiro usado nos buckets."""
    quantum = Decimal("1")
    raw = Decimal(str(temp_unit))
    if rounding == "floor":
        return raw.quantize(quantum, rounding=ROUND_FLOOR)
    return raw.quantize(quantum, rounding=ROUND_HALF_UP)


def bucket_probabilities(
    members_tmax_c: list[float],
    buckets: list[Bucket],
    *,
    unit: Literal["C", "F"],
    rounding: Rounding,
    bias_c: float = 0.0,
    spread_inflation: float = 1.0,
    clamp_epsilon: float = 0.0,
) -> list[float]:
    """Retorna P(bucket) alinhado à lista `buckets` (que particiona a reta)."""
    adjusted = adjust_members(members_tmax_c, bias_c, spread_inflation)
    if not adjusted:
        return [0.0 for _ in buckets]
    values = [to_market_value(celsius_to_unit(t, unit), rounding) for t in adjusted]
    n = len(values)
    probs: list[float] = []
    for bucket in buckets:
        hits = sum(1 for v in values if bucket.contains(v))
        p = hits / n
        if clamp_epsilon > 0.0:
            p = min(max(p, clamp_epsilon), 1.0 - clamp_epsilon)
        probs.append(p)
    return probs
