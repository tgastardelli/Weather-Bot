"""Walk-forward probability calibration helpers.

The calibrator is intentionally small and deterministic: observations for a
target date are only committed when a later target date is evaluated, preventing
same-day or future leakage.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from app.strategy.edge import cost_per_share

DEFAULT_MIN_SAMPLES = 50
DEFAULT_PROBABILITY_CAP = 0.80
DEFAULT_SMOOTHING_PRIOR = 20


@dataclass(frozen=True)
class ProbabilityContext:
    city_slug: str
    bucket_kind: str
    model_prob: float
    market_price: Decimal
    hours_to_close: float
    target_date: date


@dataclass(frozen=True)
class CalibrationResult:
    raw_prob: float
    calibrated_prob: float
    source: str
    n_samples: int
    observed_rate: float | None
    capped: bool


@dataclass(frozen=True)
class MarketAwareCalibrationResult:
    raw_prob: float
    calibrated_prob: float
    source: str
    segment_key: str | None
    n_samples: int
    wins: int
    observed_rate: float | None
    brier_delta: float | None
    pnl: Decimal
    eligible: bool
    capped: bool
    alpha: float
    cap: float
    min_samples: int
    reason: str | None


@dataclass
class _Aggregate:
    n: int = 0
    wins: int = 0

    def add(self, outcome: float) -> None:
        self.n += 1
        self.wins += 1 if outcome >= 0.5 else 0

    @property
    def rate(self) -> float:
        return self.wins / self.n if self.n > 0 else 0.0


@dataclass
class _MarketAwareAggregate:
    n: int = 0
    wins: int = 0
    brier_model_sum: float = 0.0
    brier_market_sum: float = 0.0
    pnl: Decimal = Decimal("0")

    def add(
        self,
        *,
        outcome: float,
        model_prob: float,
        market_price: Decimal,
        pnl: Decimal,
    ) -> None:
        self.n += 1
        self.wins += 1 if outcome >= 0.5 else 0
        market_prob = float(market_price)
        self.brier_model_sum += (model_prob - outcome) ** 2
        self.brier_market_sum += (market_prob - outcome) ** 2
        self.pnl += pnl

    @property
    def rate(self) -> float:
        return self.wins / self.n if self.n > 0 else 0.0

    @property
    def brier_delta(self) -> float | None:
        if self.n <= 0:
            return None
        empirical_brier = self.rate * (1.0 - self.rate)
        return (self.brier_market_sum / self.n) - empirical_brier


def probability_bucket(probability: float | None) -> str | None:
    if probability is None:
        return None
    idx = min(max(int(probability * 10), 0), 9)
    return f"{idx / 10:.1f}-{(idx + 1) / 10:.1f}"


def price_bucket(price: Decimal | None) -> str | None:
    if price is None:
        return None
    buckets = [
        (Decimal("0.00"), Decimal("0.05")),
        (Decimal("0.05"), Decimal("0.10")),
        (Decimal("0.10"), Decimal("0.20")),
        (Decimal("0.20"), Decimal("0.40")),
        (Decimal("0.40"), Decimal("0.60")),
        (Decimal("0.60"), Decimal("0.80")),
        (Decimal("0.80"), Decimal("0.95")),
        (Decimal("0.95"), Decimal("1.00")),
    ]
    for low, high in buckets:
        if low <= price < high:
            return f"{low}-{high}"
    return "out_of_range"


def edge_bucket(edge: Decimal | None) -> str | None:
    if edge is None:
        return None
    buckets = [
        (Decimal("0.00"), Decimal("0.10")),
        (Decimal("0.10"), Decimal("0.25")),
        (Decimal("0.25"), Decimal("0.50")),
        (Decimal("0.50"), Decimal("0.75")),
    ]
    for low, high in buckets:
        if low <= edge < high:
            return f"{low}-{high}"
    return "0.75+"


def hours_to_close_bucket(hours_to_close: float | None) -> str | None:
    if hours_to_close is None:
        return None
    if hours_to_close < 6:
        return "0-6h"
    if hours_to_close < 12:
        return "6-12h"
    if hours_to_close < 24:
        return "12-24h"
    if hours_to_close < 48:
        return "24-48h"
    return "48h+"


def calibration_keys(context: ProbabilityContext) -> list[tuple[str, ...]]:
    prob_bucket = probability_bucket(context.model_prob) or "unknown"
    px_bucket = price_bucket(context.market_price) or "unknown"
    hours_bucket = hours_to_close_bucket(context.hours_to_close) or "unknown"
    return [
        (
            "specific",
            context.city_slug,
            context.bucket_kind,
            prob_bucket,
            px_bucket,
            hours_bucket,
        ),
        ("city_prob", context.city_slug, prob_bucket),
        ("prob", prob_bucket),
        ("global",),
    ]


def segment_key(key: tuple[str, ...]) -> str:
    return "|".join(key)


class WalkForwardProbabilityCalibrator:
    """Empirical segment calibrator with target-date walk-forward boundaries."""

    def __init__(
        self,
        *,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        probability_cap: float = DEFAULT_PROBABILITY_CAP,
    ) -> None:
        self.min_samples = min_samples
        self.probability_cap = probability_cap
        self._pending_by_date: defaultdict[date, list[tuple[ProbabilityContext, float]]] = (
            defaultdict(list)
        )
        self._aggregates: defaultdict[tuple[str, ...], _Aggregate] = defaultdict(_Aggregate)

    def calibrate(self, context: ProbabilityContext) -> CalibrationResult:
        self._commit_before(context.target_date)
        selected_row = self._select(context)
        if selected_row is None:
            observed_rate = None
            source = "raw_capped"
            n_samples = 0
            calibrated = context.model_prob
        else:
            selected_key, selected = selected_row
            observed_rate = selected.rate
            source = selected_key[0]
            n_samples = selected.n
            calibrated = observed_rate

        capped = calibrated > self.probability_cap
        calibrated = min(max(calibrated, 0.0), self.probability_cap)
        return CalibrationResult(
            raw_prob=context.model_prob,
            calibrated_prob=calibrated,
            source=source,
            n_samples=n_samples,
            observed_rate=observed_rate,
            capped=capped,
        )

    def observe(self, context: ProbabilityContext, outcome: float) -> None:
        self._pending_by_date[context.target_date].append((context, outcome))

    def _commit_before(self, target_date: date) -> None:
        ready_dates = [row_date for row_date in self._pending_by_date if row_date < target_date]
        for row_date in sorted(ready_dates):
            observations = self._pending_by_date.pop(row_date)
            for context, outcome in observations:
                for key in self._keys(context):
                    self._aggregates[key].add(outcome)

    def _select(self, context: ProbabilityContext) -> tuple[tuple[str, ...], _Aggregate] | None:
        for key in self._keys(context):
            aggregate = self._aggregates.get(key)
            if aggregate is not None and aggregate.n >= self.min_samples:
                return key, aggregate
        return None

    def _keys(self, context: ProbabilityContext) -> list[tuple[str, ...]]:
        return calibration_keys(context)


class WalkForwardMarketAwareCalibrator:
    """Market-anchored empirical calibrator with walk-forward segment eligibility."""

    def __init__(
        self,
        *,
        min_samples: int,
        probability_cap: float,
        alpha: float,
        fee_rate: Decimal,
        segment_scope: Literal["fallback", "specific_only"] = "fallback",
        smoothing_prior: int = DEFAULT_SMOOTHING_PRIOR,
    ) -> None:
        self.min_samples = min_samples
        self.probability_cap = probability_cap
        self.alpha = alpha
        self.fee_rate = fee_rate
        self.segment_scope = segment_scope
        self.smoothing_prior = smoothing_prior
        self._pending_by_date: defaultdict[
            date, list[tuple[ProbabilityContext, float, float]]
        ] = defaultdict(list)
        self._aggregates: defaultdict[tuple[str, ...], _MarketAwareAggregate] = defaultdict(
            _MarketAwareAggregate
        )

    def calibrate(self, context: ProbabilityContext) -> MarketAwareCalibrationResult:
        self._commit_before(context.target_date)
        selected_row = self._select(context)
        global_rate = self._global_rate(default=context.model_prob)
        if selected_row is None:
            selected_key = None
            selected = None
            smoothed = context.model_prob
            source = "no_segment"
        else:
            selected_key, selected = selected_row
            smoothed = (
                selected.wins + (self.smoothing_prior * global_rate)
            ) / (selected.n + self.smoothing_prior)
            source = selected_key[0]

        market_price = float(context.market_price)
        anchored = market_price + self.alpha * (smoothed - market_price)
        clipped = min(max(anchored, 0.0), self.probability_cap)
        capped = clipped < anchored

        reason: str | None
        if selected is None:
            reason = "min_samples"
            eligible = False
            n_samples = 0
            wins = 0
            observed_rate = None
            brier_delta = None
            pnl = Decimal("0")
            selected_segment_key = None
        else:
            assert selected_key is not None
            n_samples = selected.n
            wins = selected.wins
            observed_rate = selected.rate
            brier_delta = selected.brier_delta
            pnl = selected.pnl.quantize(Decimal("0.00001"))
            selected_segment_key = segment_key(selected_key)
            reason = self._ineligible_reason(selected_key, selected)
            eligible = reason is None

        return MarketAwareCalibrationResult(
            raw_prob=context.model_prob,
            calibrated_prob=clipped,
            source=source,
            segment_key=selected_segment_key,
            n_samples=n_samples,
            wins=wins,
            observed_rate=observed_rate,
            brier_delta=brier_delta,
            pnl=pnl,
            eligible=eligible,
            capped=capped,
            alpha=self.alpha,
            cap=self.probability_cap,
            min_samples=self.min_samples,
            reason=reason,
        )

    def observe(self, context: ProbabilityContext, outcome: float, model_prob: float) -> None:
        self._pending_by_date[context.target_date].append((context, outcome, model_prob))

    def snapshot_segments(self) -> list[dict[str, object]]:
        self._commit_before(date.max)
        rows: list[dict[str, object]] = []
        ordered = sorted(self._aggregates.items(), key=lambda item: segment_key(item[0]))
        for key, aggregate in ordered:
            brier_delta = aggregate.brier_delta
            reason = self._ineligible_reason(key, aggregate)
            rows.append(
                {
                    "segment_key": segment_key(key),
                    "n": aggregate.n,
                    "wins": aggregate.wins,
                    "observed_rate": aggregate.rate,
                    "brier_delta": brier_delta,
                    "pnl": str(aggregate.pnl.quantize(Decimal("0.00001"))),
                    "eligible": reason is None,
                    "reason": reason,
                    "alpha": self.alpha,
                    "cap": self.probability_cap,
                    "min_samples": self.min_samples,
                    "segment_scope": self.segment_scope,
                }
            )
        return rows

    def global_rate(self, *, default: float) -> float:
        return self._global_rate(default=default)

    def _commit_before(self, target_date: date) -> None:
        ready_dates = [row_date for row_date in self._pending_by_date if row_date < target_date]
        for row_date in sorted(ready_dates):
            observations = self._pending_by_date.pop(row_date)
            for context, outcome, model_prob in observations:
                pnl = self._unit_pnl(context.market_price, outcome)
                for key in calibration_keys(context):
                    self._aggregates[key].add(
                        outcome=outcome,
                        model_prob=model_prob,
                        market_price=context.market_price,
                        pnl=pnl,
                    )

    def _select(
        self, context: ProbabilityContext
    ) -> tuple[tuple[str, ...], _MarketAwareAggregate] | None:
        for key in self._keys(context):
            aggregate = self._aggregates.get(key)
            if aggregate is not None and aggregate.n >= self.min_samples:
                return key, aggregate
        return None

    def _keys(self, context: ProbabilityContext) -> list[tuple[str, ...]]:
        keys = calibration_keys(context)
        if self.segment_scope == "specific_only":
            return keys[:1]
        return keys

    def _global_rate(self, *, default: float) -> float:
        aggregate = self._aggregates.get(("global",))
        if aggregate is None or aggregate.n <= 0:
            return default
        return aggregate.rate

    def _ineligible_reason(
        self, key: tuple[str, ...], aggregate: _MarketAwareAggregate
    ) -> str | None:
        if self.segment_scope == "specific_only" and key[0] != "specific":
            return "segment_scope"
        if aggregate.n < self.min_samples:
            return "min_samples"
        brier_delta = aggregate.brier_delta
        if brier_delta is None or brier_delta <= 0:
            return "segment_brier"
        if aggregate.pnl <= Decimal("0"):
            return "segment_pnl"
        return None

    def _unit_pnl(self, market_price: Decimal, outcome: float) -> Decimal:
        settlement = Decimal("1") if outcome >= 0.5 else Decimal("0")
        return (settlement - cost_per_share(market_price, self.fee_rate)).quantize(
            Decimal("0.00001")
        )
