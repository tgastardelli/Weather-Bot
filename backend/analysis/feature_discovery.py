"""Feature-based historical discovery for operable weather-market edge.

This module is diagnostic-only: it never creates signals, paper orders, fills,
or live-readiness approvals.
"""

import argparse
import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.backtest import TradeResult, _is_recent_duplicate, _trade_result
from analysis.historical_validation import (
    MIN_HISTORICAL_TRADES,
    parse_cities,
)
from analysis.operational_quarantine import quarantine_payloads, split_operational_cities
from analysis.strategy_discovery import (
    DEFAULT_MIN_FOLD_CANDIDATES,
    DEFAULT_MIN_TRAIN_CANDIDATES,
    EXECUTION_PROXY,
    PRICE_SAMPLING,
    _fold_windows,
    _profile_payload,
)
from analysis.strategy_repair import HistoricalCandidate, _historical_candidates
from app.config import Settings, get_settings
from app.db.models import Base, FeatureDiscoveryRun
from app.db.session import create_engine, create_session_factory
from app.strategy.edge import cost_per_share, net_edge
from app.strategy.probability_calibration import hours_to_close_bucket, price_bucket
from app.strategy.sizing import kelly_stake

logger = logging.getLogger(__name__)

FEATURE_DISCOVERY_SOURCE = "feature_discovery_historical_price_points"
FEATURE_MAX_TOP_5_ABS_PNL_SHARE = Decimal("0.40")
FeatureFamily = Literal[
    "ensemble_confidence_value",
    "forecast_revision_value",
    "market_momentum_fade",
    "threshold_distance_specialist",
    "city_error_regime_specialist",
    "buy_no_feature_value",
]
FeatureSide = Literal["YES", "NO"]


@dataclass(frozen=True)
class FeatureVariant:
    name: str
    family: FeatureFamily
    side: FeatureSide
    min_samples: int
    min_edge_net: Decimal
    probability_cap: float


@dataclass(frozen=True)
class FeatureCandidate:
    base: HistoricalCandidate
    threshold_distance_bucket: str
    ensemble_spread_bucket: str
    forecast_revision_bucket: str
    lead_time_bucket: str
    season_month: str
    market_price_bucket: str
    price_momentum_6h_bucket: str
    price_momentum_24h_bucket: str
    city_error_regime: str
    price_momentum_6h: Decimal
    price_momentum_24h: Decimal
    forecast_revision: float


@dataclass
class FeatureSegmentStats:
    key: str
    n: int = 0
    wins: int = 0
    yes_brier_model_sum: float = 0.0
    yes_brier_market_sum: float = 0.0
    no_brier_model_sum: float = 0.0
    no_brier_market_sum: float = 0.0
    yes_pnl: Decimal = Decimal("0")
    no_pnl: Decimal = Decimal("0")
    yes_cost_sum: Decimal = Decimal("0")
    no_cost_sum: Decimal = Decimal("0")

    def add(self, candidate: FeatureCandidate, fee_rate: Decimal) -> None:
        base = candidate.base
        outcome = 1.0 if base.winner else 0.0
        yes_cost = cost_per_share(base.price, fee_rate)
        no_price = (Decimal("1") - base.price).quantize(Decimal("0.00001"))
        no_cost = cost_per_share(no_price, fee_rate)
        no_model = 1.0 - base.raw_prob
        no_outcome = 1.0 - outcome
        self.n += 1
        self.wins += 1 if base.winner else 0
        self.yes_brier_model_sum += (base.raw_prob - outcome) ** 2
        self.yes_brier_market_sum += (float(base.price) - outcome) ** 2
        self.no_brier_model_sum += (no_model - no_outcome) ** 2
        self.no_brier_market_sum += (float(no_price) - no_outcome) ** 2
        self.yes_pnl += ((Decimal("1") if base.winner else Decimal("0")) - yes_cost).quantize(
            Decimal("0.00001")
        )
        self.no_pnl += ((Decimal("0") if base.winner else Decimal("1")) - no_cost).quantize(
            Decimal("0.00001")
        )
        self.yes_cost_sum += yes_cost
        self.no_cost_sum += no_cost

    def observed_rate_for(self, side: FeatureSide) -> float:
        if self.n == 0:
            return 0.0
        yes = self.wins / self.n
        return yes if side == "YES" else 1.0 - yes

    def brier_delta_for(self, side: FeatureSide) -> float | None:
        if self.n == 0:
            return None
        if side == "YES":
            return (self.yes_brier_market_sum - self.yes_brier_model_sum) / self.n
        return (self.no_brier_market_sum - self.no_brier_model_sum) / self.n

    def pnl_for(self, side: FeatureSide) -> Decimal:
        return self.yes_pnl if side == "YES" else self.no_pnl

    def avg_cost_for(self, side: FeatureSide) -> Decimal | None:
        if self.n == 0:
            return None
        costs = self.yes_cost_sum if side == "YES" else self.no_cost_sum
        return (costs / Decimal(self.n)).quantize(Decimal("0.00001"))


def _json(value: object) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _bucket_signed(value: Decimal | float, *, small: Decimal = Decimal("0.03")) -> str:
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    if decimal_value >= small:
        return "up"
    if decimal_value <= -small:
        return "down"
    return "flat"


def _threshold_distance_bucket(candidate: HistoricalCandidate) -> str:
    distance = abs(Decimal(str(candidate.raw_prob)) - candidate.price)
    if distance < Decimal("0.05"):
        return "near"
    if distance < Decimal("0.15"):
        return "mid"
    return "far"


def _ensemble_spread_bucket(candidate: HistoricalCandidate) -> str:
    uncertainty = Decimal(str(min(candidate.raw_prob, 1.0 - candidate.raw_prob)))
    if uncertainty <= Decimal("0.10"):
        return "tight"
    if uncertainty <= Decimal("0.30"):
        return "medium"
    return "wide"


def _city_error_regime(candidate: HistoricalCandidate) -> str:
    delta = Decimal(str(candidate.raw_prob)) - candidate.price
    if delta >= Decimal("0.15"):
        return "model_hot"
    if delta <= Decimal("-0.15"):
        return "model_cold"
    return "model_neutral"


def _enrich_candidates(candidates: list[HistoricalCandidate]) -> list[FeatureCandidate]:
    by_market: dict[str, list[HistoricalCandidate]] = {}
    for candidate in sorted(candidates, key=lambda item: (item.market_id, item.ts)):
        by_market.setdefault(candidate.market_id, []).append(candidate)

    enriched: list[FeatureCandidate] = []
    for market_candidates in by_market.values():
        previous: list[HistoricalCandidate] = []
        for candidate in market_candidates:
            prev_any = previous[-1] if previous else None
            prev_6h = next(
                (
                    item
                    for item in reversed(previous)
                    if 0 <= (candidate.ts - item.ts).total_seconds() <= 6 * 3600
                ),
                None,
            )
            prev_24h = next(
                (
                    item
                    for item in reversed(previous)
                    if 0 <= (candidate.ts - item.ts).total_seconds() <= 24 * 3600
                ),
                None,
            )
            momentum_6h = candidate.price - (prev_6h.price if prev_6h else candidate.price)
            momentum_24h = candidate.price - (
                prev_24h.price if prev_24h else candidate.price
            )
            revision = candidate.raw_prob - (prev_any.raw_prob if prev_any else candidate.raw_prob)
            enriched.append(
                FeatureCandidate(
                    base=candidate,
                    threshold_distance_bucket=_threshold_distance_bucket(candidate),
                    ensemble_spread_bucket=_ensemble_spread_bucket(candidate),
                    forecast_revision_bucket=_bucket_signed(revision),
                    lead_time_bucket=hours_to_close_bucket(candidate.hours_to_close)
                    or "unknown",
                    season_month=f"month-{candidate.target_date.month:02d}",
                    market_price_bucket=price_bucket(candidate.price) or "unknown",
                    price_momentum_6h_bucket=_bucket_signed(momentum_6h),
                    price_momentum_24h_bucket=_bucket_signed(momentum_24h),
                    city_error_regime=_city_error_regime(candidate),
                    price_momentum_6h=momentum_6h,
                    price_momentum_24h=momentum_24h,
                    forecast_revision=revision,
                )
            )
            previous.append(candidate)
    return sorted(
        enriched,
        key=lambda item: (item.base.target_date, item.base.ts, item.base.market_id),
    )


def _variants() -> list[FeatureVariant]:
    variants: list[FeatureVariant] = []
    for min_samples in (30, 50):
        for min_edge in (Decimal("0.000"), Decimal("0.005")):
            for family, side, cap in (
                ("ensemble_confidence_value", "YES", 0.75),
                ("forecast_revision_value", "YES", 0.75),
                ("market_momentum_fade", "NO", 0.80),
                ("threshold_distance_specialist", "YES", 0.75),
                ("city_error_regime_specialist", "YES", 0.75),
                ("buy_no_feature_value", "NO", 0.80),
            ):
                variants.append(
                    FeatureVariant(
                        name=(
                            f"{family}_{side.lower()}_n{min_samples}_edge{min_edge}"
                        ).replace(".", "_"),
                        family=family,  # type: ignore[arg-type]
                        side=side,  # type: ignore[arg-type]
                        min_samples=min_samples,
                        min_edge_net=min_edge,
                        probability_cap=cap,
                    )
                )
    return variants


def _segment_key(candidate: FeatureCandidate, variant: FeatureVariant) -> str:
    base = candidate.base
    if variant.family == "ensemble_confidence_value":
        return "|".join(
            (
                "ensemble_confidence",
                base.city_slug,
                candidate.ensemble_spread_bucket,
                candidate.threshold_distance_bucket,
                base.bucket_kind,
            )
        )
    if variant.family == "forecast_revision_value":
        return "|".join(
            (
                "forecast_revision",
                base.city_slug,
                candidate.forecast_revision_bucket,
                candidate.lead_time_bucket,
                base.bucket_kind,
            )
        )
    if variant.family == "market_momentum_fade":
        return "|".join(
            (
                "market_momentum_fade",
                base.city_slug,
                candidate.price_momentum_6h_bucket,
                candidate.price_momentum_24h_bucket,
                candidate.market_price_bucket,
            )
        )
    if variant.family == "threshold_distance_specialist":
        return "|".join(
            (
                "threshold_distance",
                base.city_slug,
                candidate.threshold_distance_bucket,
                candidate.market_price_bucket,
                base.bucket_kind,
            )
        )
    if variant.family == "city_error_regime_specialist":
        return "|".join(
            (
                "city_error_regime",
                base.city_slug,
                candidate.city_error_regime,
                candidate.season_month,
                base.bucket_kind,
            )
        )
    return "|".join(
        (
            "buy_no_feature",
            base.city_slug,
            candidate.city_error_regime,
            candidate.market_price_bucket,
            candidate.lead_time_bucket,
        )
    )


def _build_segments(
    candidates: list[FeatureCandidate], fee_rate: Decimal, variant: FeatureVariant
) -> dict[str, FeatureSegmentStats]:
    segments: dict[str, FeatureSegmentStats] = {}
    for candidate in candidates:
        key = _segment_key(candidate, variant)
        segment = segments.setdefault(key, FeatureSegmentStats(key=key))
        segment.add(candidate, fee_rate)
    return segments


def _decision_price(candidate: FeatureCandidate, variant: FeatureVariant) -> Decimal:
    if variant.side == "NO":
        return (Decimal("1") - candidate.base.price).quantize(Decimal("0.00001"))
    return candidate.base.price


def _decision_winner(candidate: FeatureCandidate, variant: FeatureVariant) -> bool:
    return not candidate.base.winner if variant.side == "NO" else candidate.base.winner


def _calibrated_probability(
    candidate: FeatureCandidate, segment: FeatureSegmentStats, variant: FeatureVariant
) -> float:
    base = candidate.base
    empirical = segment.observed_rate_for(variant.side)
    if variant.family == "market_momentum_fade":
        anchor = 1.0 - float(base.price)
    elif variant.side == "NO":
        anchor = 1.0 - base.raw_prob
    elif variant.family == "forecast_revision_value":
        anchor = min(max(base.raw_prob + candidate.forecast_revision, 0.0), 1.0)
    else:
        anchor = base.raw_prob
    return min(max((anchor + empirical) / 2.0, 0.0), variant.probability_cap)


def _reason(
    candidate: FeatureCandidate,
    segment: FeatureSegmentStats | None,
    variant: FeatureVariant,
    fee_rate: Decimal,
) -> str | None:
    if segment is None:
        return "no_segment"
    if segment.n < variant.min_samples:
        return "min_samples"
    brier_delta = segment.brier_delta_for(variant.side)
    if brier_delta is None or brier_delta <= 0:
        return "segment_brier"
    if segment.pnl_for(variant.side) <= Decimal("0"):
        return "segment_pnl"
    avg_cost = segment.avg_cost_for(variant.side)
    observed_rate = Decimal(str(segment.observed_rate_for(variant.side)))
    if avg_cost is None or observed_rate <= avg_cost:
        return "segment_cost"
    if variant.family == "ensemble_confidence_value" and candidate.ensemble_spread_bucket == "wide":
        return "ensemble_spread_wide"
    if (
        variant.family == "forecast_revision_value"
        and candidate.forecast_revision_bucket == "flat"
    ):
        return "forecast_revision_flat"
    if (
        variant.family == "market_momentum_fade"
        and candidate.price_momentum_6h_bucket == "flat"
        and candidate.price_momentum_24h_bucket == "flat"
    ):
        return "market_momentum_flat"
    probability = _calibrated_probability(candidate, segment, variant)
    edge = net_edge(probability, _decision_price(candidate, variant), fee_rate)
    if edge < 0:
        return "negative_edge"
    if edge < variant.min_edge_net:
        return "min_edge_net"
    return None


def _simulate(
    candidates: list[FeatureCandidate],
    segments: dict[str, FeatureSegmentStats],
    variant: FeatureVariant,
    settings: Settings,
) -> tuple[list[TradeResult], dict[str, object]]:
    trades: list[TradeResult] = []
    blocked: Counter[str] = Counter()
    samples: list[dict[str, object]] = []
    last_signals: dict[tuple[str, str], tuple[datetime, Decimal]] = {}
    exposure_by_market_day: dict[tuple[str, date], Decimal] = {}

    for candidate in candidates:
        segment_key = _segment_key(candidate, variant)
        segment = segments.get(segment_key)
        reason = _reason(candidate, segment, variant, settings.taker_fee_rate)
        probability = (
            _calibrated_probability(candidate, segment, variant)
            if segment is not None
            else candidate.base.raw_prob
        )
        decision_price = _decision_price(candidate, variant)
        edge = net_edge(probability, decision_price, settings.taker_fee_rate)
        if len(samples) < 12:
            samples.append(
                {
                    "ts": candidate.base.ts.isoformat(),
                    "city_slug": candidate.base.city_slug,
                    "market_id": candidate.base.market_id,
                    "family": variant.family,
                    "side": variant.side,
                    "segment_key": segment_key,
                    "market_price": str(decision_price),
                    "raw_prob": candidate.base.raw_prob,
                    "calibrated_prob": probability,
                    "edge_net": str(edge),
                    "reason": reason,
                    "would_trade": reason is None,
                    "features": {
                        "threshold_distance": candidate.threshold_distance_bucket,
                        "ensemble_spread": candidate.ensemble_spread_bucket,
                        "forecast_revision": candidate.forecast_revision_bucket,
                        "lead_time": candidate.lead_time_bucket,
                        "price_momentum_6h": candidate.price_momentum_6h_bucket,
                        "price_momentum_24h": candidate.price_momentum_24h_bucket,
                        "city_error_regime": candidate.city_error_regime,
                    },
                }
            )
        if reason is not None:
            blocked[reason] += 1
            continue

        stake = kelly_stake(
            probability,
            cost_per_share(decision_price, settings.taker_fee_rate),
            bankroll=settings.bankroll,
            kelly_multiplier=settings.kelly_fraction,
            max_stake_per_order=settings.max_stake_per_order,
        )
        if stake <= Decimal("0"):
            blocked["kelly_stake_zero"] += 1
            continue
        if _is_recent_duplicate(
            last_signals,
            market_id=candidate.base.market_id,
            profile="max_edge",
            ts=candidate.base.ts,
            edge_net=edge,
        ):
            blocked["duplicate"] += 1
            continue
        exposure_key = (candidate.base.market_id, candidate.base.ts.date())
        if (
            exposure_by_market_day.get(exposure_key, Decimal("0")) + stake
            > settings.max_exposure_per_market
        ):
            blocked["max_exposure"] += 1
            continue
        trade = _trade_result(
            ts=candidate.base.ts,
            stake=stake,
            market_price=decision_price,
            model_prob=probability,
            winner=_decision_winner(candidate, variant),
            fee_rate=settings.taker_fee_rate,
            market_id=candidate.base.market_id,
            event_id=candidate.base.event_id,
            city_slug=candidate.base.city_slug,
            target_date=candidate.base.target_date,
            bucket_kind=candidate.base.bucket_kind,
            bucket_label=candidate.base.bucket_label,
            edge_net=edge,
            hours_to_close=candidate.base.hours_to_close,
            price_source=candidate.base.price_source,
        )
        if trade is None:
            blocked["invalid_trade"] += 1
            continue
        exposure_by_market_day[exposure_key] = exposure_by_market_day.get(
            exposure_key, Decimal("0")
        ) + stake
        last_signals[(candidate.base.market_id, "max_edge")] = (candidate.base.ts, edge)
        trades.append(trade)

    return trades, {"blocked_counts": dict(blocked), "samples": samples}


def _variant_payload(
    variant: FeatureVariant,
    trades: list[TradeResult],
    metadata: dict[str, object],
    *,
    include_bootstrap: bool = True,
) -> dict[str, object]:
    profile = _profile_payload(trades)
    return {
        "name": variant.name,
        "family": variant.family,
        "side": variant.side,
        "min_samples": variant.min_samples,
        "min_edge_net": str(variant.min_edge_net),
        "probability_cap": variant.probability_cap,
        "diagnostic_only": True,
        "cannot_approve_live": True,
        "profile": profile,
        **metadata,
    }


def _score(payload: dict[str, object]) -> tuple[int, Decimal, float, int]:
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        return (-1, Decimal("-999999"), -999999.0, 0)
    n = int(profile.get("n_resolved_trades") or 0)
    pnl = Decimal(str(profile.get("total_pnl") or "0"))
    brier = profile.get("brier_delta")
    brier_value = float(brier) if isinstance(brier, int | float) else -999999.0
    concentration = Decimal(str(profile.get("top_5_abs_pnl_share") or "999"))
    bootstrap_high = Decimal(str(profile.get("pnl_ci_high") or "-999999"))
    gates = 0
    gates += 1 if n >= MIN_HISTORICAL_TRADES else 0
    gates += 1 if brier_value > 0 else 0
    gates += 1 if pnl > 0 else 0
    gates += 1 if concentration <= FEATURE_MAX_TOP_5_ABS_PNL_SHARE else 0
    gates += 1 if bootstrap_high >= 0 else 0
    return (gates, pnl, brier_value, n)


def _rolling_origin(
    candidates: list[FeatureCandidate], settings: Settings
) -> tuple[dict[str, object] | None, list[dict[str, object]], dict[str, object]]:
    folds: list[dict[str, object]] = []
    oos_trades: list[TradeResult] = []
    selected_counter: Counter[str] = Counter()
    last_payload_by_family: dict[str, dict[str, object]] = {}
    valid_folds = 0
    variants = _variants()

    for index, (fold_start, fold_end) in enumerate(
        _fold_windows([candidate.base for candidate in candidates])
    ):
        train = [candidate for candidate in candidates if candidate.base.target_date < fold_start]
        fold = [
            candidate
            for candidate in candidates
            if fold_start <= candidate.base.target_date <= fold_end
        ]
        if len(train) < DEFAULT_MIN_TRAIN_CANDIDATES or len(fold) < DEFAULT_MIN_FOLD_CANDIDATES:
            folds.append(
                {
                    "index": index,
                    "fold_window": {"start": fold_start.isoformat(), "end": fold_end.isoformat()},
                    "valid": False,
                    "reason": "insufficient_candidates",
                    "n_train": len(train),
                    "n_fold_candidates": len(fold),
                }
            )
            continue

        train_payloads: list[dict[str, object]] = []
        for variant in variants:
            segments = _build_segments(train, settings.taker_fee_rate, variant)
            trades, metadata = _simulate(train, segments, variant, settings)
            train_payloads.append(
                _variant_payload(variant, trades, metadata, include_bootstrap=False)
            )
        selected = max(train_payloads, key=_score)
        selected_variant = next(
            variant for variant in variants if variant.name == selected["name"]
        )
        selected_segments = _build_segments(train, settings.taker_fee_rate, selected_variant)
        fold_trades, fold_metadata = _simulate(fold, selected_segments, selected_variant, settings)
        fold_payload = _variant_payload(selected_variant, fold_trades, fold_metadata)
        oos_trades.extend(fold_trades)
        selected_counter[selected_variant.family] += 1
        last_payload_by_family[selected_variant.family] = fold_payload
        valid_folds += 1
        profile = fold_payload["profile"]
        profile = profile if isinstance(profile, dict) else {}
        folds.append(
            {
                "index": index,
                "fold_window": {"start": fold_start.isoformat(), "end": fold_end.isoformat()},
                "valid": True,
                "selected_family": selected_variant.family,
                "selected_variant": selected_variant.name,
                "selected_side": selected_variant.side,
                "n_train": len(train),
                "n_fold_candidates": len(fold),
                "n_oos_trades": len(fold_trades),
                "pnl": profile.get("total_pnl"),
                "brier_delta": profile.get("brier_delta"),
            }
        )

    if not oos_trades:
        best_family = None
    else:
        best_name = selected_counter.most_common(1)[0][0]
        best_family = {
            "name": f"feature_oos_{best_name}",
            "family": best_name,
            "diagnostic_only": True,
            "cannot_approve_live": True,
            "profile": _profile_payload(oos_trades),
            "selected_folds": dict(selected_counter),
            "last_fold_payload": last_payload_by_family.get(best_name, {}),
        }
    summary = {
        "fold_count": len(folds),
        "valid_folds": valid_folds,
        "selected_families": dict(selected_counter),
    }
    return best_family, folds, summary


def _gates(
    best_family: dict[str, object] | None,
    *,
    valid_folds: int,
    selected_cities: list[str],
    quarantined: list[str],
) -> dict[str, object]:
    profile = best_family.get("profile") if isinstance(best_family, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    n = int(profile.get("n_resolved_trades") or 0)
    pnl = Decimal(str(profile.get("total_pnl") or "0"))
    brier = profile.get("brier_delta")
    brier_pass = isinstance(brier, int | float) and float(brier) > 0
    concentration = Decimal(str(profile.get("top_5_abs_pnl_share") or "999"))
    bootstrap_high = Decimal(str(profile.get("pnl_ci_high") or "-999999"))
    traded_cities_raw = profile.get("traded_cities")
    traded_cities = (
        {str(city) for city in traded_cities_raw}
        if isinstance(traded_cities_raw, list)
        else set()
    )
    traded_quarantined = traded_cities & set(quarantined)
    return {
        "feature_candidate": {
            "passed": n >= 30 and valid_folds >= 2 and (brier_pass or pnl > 0),
            "value": {
                "n_resolved_trades": n,
                "valid_folds": valid_folds,
                "brier_delta": brier,
                "total_pnl": str(pnl),
            },
            "required": {"min_trades": 30, "min_valid_folds": 2, "brier_or_pnl_positive": True},
        },
        "oos_trades": {
            "passed": n >= MIN_HISTORICAL_TRADES,
            "value": {"n_resolved_trades": n},
            "required": {"min_trades": MIN_HISTORICAL_TRADES},
        },
        "oos_brier": {
            "passed": brier_pass,
            "value": {"brier_delta": brier},
            "required": {"brier_delta_gt": 0},
        },
        "oos_pnl": {
            "passed": pnl > 0,
            "value": {"total_pnl": str(pnl)},
            "required": {"total_pnl_gt": "0"},
        },
        "concentration": {
            "passed": concentration <= FEATURE_MAX_TOP_5_ABS_PNL_SHARE,
            "value": {"top_5_abs_pnl_share": str(concentration)},
            "required": {"top_5_abs_pnl_share_lte": str(FEATURE_MAX_TOP_5_ABS_PNL_SHARE)},
        },
        "bootstrap": {
            "passed": bootstrap_high >= 0,
            "value": {"pnl_ci_high": str(bootstrap_high)},
            "required": {"pnl_ci_high_gte": "0"},
        },
        "folds": {
            "passed": valid_folds >= 3,
            "value": {"valid_folds": valid_folds},
            "required": {"min_valid_folds": 3},
        },
        "city_diversification": {
            "passed": True,
            "value": {"traded_cities": sorted(traded_cities)},
            "required": {"diagnostic": "review top city concentration before repair_v5"},
        },
        "operational_quarantine": {
            "passed": not traded_quarantined,
            "value": {
                "traded_quarantined_cities": sorted(traded_quarantined),
                "quarantine": quarantine_payloads(traded_quarantined),
            },
            "required": "quarantined cities cannot approve repair/shadow/live",
        },
        "universe_health": {
            "passed": bool(selected_cities),
            "value": {"selected_cities": selected_cities, "excluded_quarantined": quarantined},
            "required": {"selected_cities_gt": 0},
        },
        "live_release": {
            "passed": False,
            "value": "diagnostic_only",
            "required": "repair_v5 PROMISING plus measurement READY_FOR_LIVE_REVIEW",
        },
    }


def _status(gates: dict[str, object]) -> str:
    universe = gates.get("universe_health")
    if isinstance(universe, dict) and universe.get("passed") is not True:
        return "DATA_REVIEW"
    shadow_gates = [
        gate
        for key, gate in gates.items()
        if key not in {"live_release", "feature_candidate", "city_diversification"}
        and isinstance(gate, dict)
    ]
    if all(gate.get("passed") is True for gate in shadow_gates):
        return "READY_FOR_REPAIR_V5"
    candidate = gates.get("feature_candidate")
    if isinstance(candidate, dict) and candidate.get("passed") is True:
        return "FEATURE_CANDIDATE"
    return "NO_FEATURE_EDGE"


async def generate_feature_discovery_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
) -> FeatureDiscoveryRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)
    selected_cities = cities or settings.cities or []
    selected_cities, excluded_quarantined = split_operational_cities(selected_cities)
    run_settings = settings.model_copy(
        update={"cities": selected_cities, "validation_history_days": history_days}
    )

    async with session_factory() as session:
        raw_candidates, n_candidates, source_counts, raw_counts, sampled_counts = (
            await _historical_candidates(session, run_settings)
        )
    feature_candidates = _enrich_candidates(raw_candidates)
    best_family, folds, rolling_summary = _rolling_origin(feature_candidates, run_settings)
    valid_folds = int(rolling_summary.get("valid_folds") or 0)
    gates = _gates(
        best_family,
        valid_folds=valid_folds,
        selected_cities=selected_cities,
        quarantined=excluded_quarantined,
    )
    status = _status(gates)
    summary = {
        "source": FEATURE_DISCOVERY_SOURCE,
        "diagnostic_only": True,
        "cannot_approve_live": True,
        "selected_cities": selected_cities,
        "excluded_quarantined": excluded_quarantined,
        "n_candidate_price_points": n_candidates,
        "n_feature_candidates": len(feature_candidates),
        "price_source_counts": source_counts,
        "price_source_raw_counts": raw_counts,
        "price_source_sampled_counts": sampled_counts,
        "execution_proxy": EXECUTION_PROXY,
        "price_sampling": PRICE_SAMPLING,
        "features": [
            "threshold_distance",
            "ensemble_spread",
            "forecast_revision",
            "lead_time_bucket",
            "season_month",
            "bucket_kind",
            "market_price_bucket",
            "price_momentum_6h",
            "price_momentum_24h",
            "city_error_regime",
        ],
        "best_family": best_family.get("family") if isinstance(best_family, dict) else None,
        "best_family_pnl": (
            best_family.get("profile", {}).get("total_pnl")
            if isinstance(best_family, dict)
            else None
        ),
        "best_family_brier_delta": (
            best_family.get("profile", {}).get("brier_delta")
            if isinstance(best_family, dict)
            else None
        ),
        "next_action": (
            "create_repair_v5"
            if status == "READY_FOR_REPAIR_V5"
            else "audit_feature_candidate"
            if status == "FEATURE_CANDIDATE"
            else "review_weather_features_or_market_type"
        ),
        **rolling_summary,
    }
    families = {
        "tested": sorted({variant.family for variant in _variants()}),
        "selected_families": rolling_summary.get("selected_families", {}),
    }

    async with session_factory() as session, session.begin():
        row = FeatureDiscoveryRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            cities_json=_json(selected_cities),
            summary_json=_json(summary),
            families_json=_json(families),
            best_family_json=_json(best_family or {}),
            folds_json=_json(folds),
            gates_json=_json(gates),
        )
        session.add(row)
        await session.flush()
        logger.info("feature discovery: status=%s candidates=%d", status, n_candidates)
        return row


async def run(
    settings: Settings, *, cities: list[str] | None = None, days: int | None = None
) -> FeatureDiscoveryRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_feature_discovery_report(
            session_factory, settings, cities=cities, days=days
        )
    finally:
        await engine.dispose()


def _row_payload(row: FeatureDiscoveryRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "cities": json.loads(row.cities_json),
        "summary": json.loads(row.summary_json),
        "families": json.loads(row.families_json),
        "best_family": json.loads(row.best_family_json),
        "folds": json.loads(row.folds_json),
        "gates": json.loads(row.gates_json),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run feature-based strategy discovery.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--cities", type=str, default=None)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()
    settings = get_settings()
    cities = parse_cities(args.cities)
    row = asyncio.run(run(settings, cities=cities, days=args.days))
    if args.json:
        print(json.dumps(_row_payload(row), sort_keys=True))
    else:
        print(f"feature discovery status={row.status} run_id={row.id}")


if __name__ == "__main__":
    main()
