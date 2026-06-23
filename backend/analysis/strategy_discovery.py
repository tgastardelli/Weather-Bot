"""Strategy discovery research before shadow paper or live trading.

Discovery intentionally remains diagnostic. It may find a candidate worth
shadow-paper validation, but it never writes signals or execution records and it
never changes live-readiness gates.
"""

import argparse
import asyncio
import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.backtest import (
    TradeResult,
    _bootstrap_metrics,
    _concentration_metrics,
    _is_recent_duplicate,
    _trade_metrics,
    _trade_result,
)
from analysis.historical_validation import MIN_HISTORICAL_TRADES, parse_cities
from analysis.operational_quarantine import (
    is_operationally_quarantined,
    quarantine_payloads,
    split_operational_cities,
)
from analysis.strategy_repair import HistoricalCandidate, _historical_candidates
from app.config import Settings, get_settings
from app.db.models import Base, CalibrationMetric, City, StrategyDiscoveryRun
from app.db.session import create_engine, create_session_factory
from app.strategy.edge import cost_per_share, net_edge
from app.strategy.probability_calibration import (
    ProbabilityContext,
    calibration_keys,
    hours_to_close_bucket,
    price_bucket,
    probability_bucket,
    segment_key,
)
from app.strategy.sizing import kelly_stake

logger = logging.getLogger(__name__)

DISCOVERY_SOURCE = "strategy_discovery_historical_price_points"
EXECUTION_PROXY = "historical_last_trade_no_book_depth"
PRICE_SAMPLING = "last_trade_per_market_per_60m_bucket"
DEFAULT_UNIVERSE = "research"
MAX_TOP_CITY_PNL_SHARE = Decimal("0.80")
MAX_TOP_5_ABS_PNL_SHARE = Decimal("0.40")
DEFAULT_FOLD_DAYS = 30
DEFAULT_MIN_TRAIN_DAYS = 90
DEFAULT_MIN_FOLDS = 3
DEFAULT_MIN_FOLD_CANDIDATES = 20
DEFAULT_MIN_TRAIN_CANDIDATES = 100
SAMPLE_LIMIT = 12

FamilyName = Literal[
    "model_value",
    "market_anchor",
    "tail_value",
    "bucket_specialist",
    "avoid_overconfidence",
    "tail_surprise_city",
    "bucket_mispricing",
    "time_window_specialist",
    "market_implied_baseline",
    "no_trade_filter",
    "inverse_model_value",
    "market_follow",
    "buy_no_value",
    "time_decay_specialist",
    "resolution_source_specialist",
    "market_extreme_fade",
    "city_season_specialist",
    "forecast_error_regime",
    "dallas_fast_lane",
]
DiscoverySide = Literal["YES", "NO"]


@dataclass(frozen=True)
class DiscoveryVariant:
    name: str
    family: FamilyName
    min_samples: int
    min_edge_net: Decimal
    probability_cap: float
    alpha: float = 1.0
    max_price: Decimal | None = None
    require_brier_positive: bool = True
    avoid_overconfidence: bool = False
    moderate_only: bool = False
    research_only_diagnostic: bool = False
    side: DiscoverySide = "YES"


@dataclass
class SegmentStats:
    key: str
    n: int = 0
    wins: int = 0
    brier_model_sum: float = 0.0
    brier_market_sum: float = 0.0
    pnl: Decimal = Decimal("0")
    cost_sum: Decimal = Decimal("0")
    no_brier_model_sum: float = 0.0
    no_brier_market_sum: float = 0.0
    no_pnl: Decimal = Decimal("0")
    no_cost_sum: Decimal = Decimal("0")

    def add(self, candidate: HistoricalCandidate, fee_rate: Decimal) -> None:
        outcome = 1.0 if candidate.winner else 0.0
        settlement = Decimal("1") if candidate.winner else Decimal("0")
        cost = cost_per_share(candidate.price, fee_rate)
        no_price = (Decimal("1") - candidate.price).quantize(Decimal("0.00001"))
        no_cost = cost_per_share(no_price, fee_rate)
        no_outcome = 0.0 if candidate.winner else 1.0
        no_model = 1.0 - candidate.raw_prob
        no_market = float(no_price)
        no_settlement = Decimal("0") if candidate.winner else Decimal("1")
        self.n += 1
        self.wins += 1 if candidate.winner else 0
        self.brier_model_sum += (candidate.raw_prob - outcome) ** 2
        self.brier_market_sum += (float(candidate.price) - outcome) ** 2
        self.pnl += (settlement - cost).quantize(Decimal("0.00001"))
        self.cost_sum += cost
        self.no_brier_model_sum += (no_model - no_outcome) ** 2
        self.no_brier_market_sum += (no_market - no_outcome) ** 2
        self.no_pnl += (no_settlement - no_cost).quantize(Decimal("0.00001"))
        self.no_cost_sum += no_cost

    @property
    def observed_rate(self) -> float:
        return self.wins / self.n if self.n > 0 else 0.0

    @property
    def brier_delta(self) -> float | None:
        if self.n <= 0:
            return None
        return (self.brier_market_sum / self.n) - (self.brier_model_sum / self.n)

    @property
    def avg_cost(self) -> Decimal | None:
        if self.n <= 0:
            return None
        return (self.cost_sum / Decimal(self.n)).quantize(Decimal("0.00001"))

    def observed_rate_for(self, side: DiscoverySide) -> float:
        return 1 - self.observed_rate if side == "NO" else self.observed_rate

    def brier_delta_for(self, side: DiscoverySide) -> float | None:
        if self.n <= 0:
            return None
        if side == "NO":
            return (self.no_brier_market_sum / self.n) - (self.no_brier_model_sum / self.n)
        return self.brier_delta

    def pnl_for(self, side: DiscoverySide) -> Decimal:
        return self.no_pnl if side == "NO" else self.pnl

    def avg_cost_for(self, side: DiscoverySide) -> Decimal | None:
        if self.n <= 0:
            return None
        if side == "NO":
            return (self.no_cost_sum / Decimal(self.n)).quantize(Decimal("0.00001"))
        return self.avg_cost


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


def _context(candidate: HistoricalCandidate) -> ProbabilityContext:
    return ProbabilityContext(
        city_slug=candidate.city_slug,
        bucket_kind=candidate.bucket_kind,
        model_prob=candidate.raw_prob,
        market_price=candidate.price,
        hours_to_close=candidate.hours_to_close,
        target_date=candidate.target_date,
    )


def _specific_segment_key(candidate: HistoricalCandidate) -> str:
    return segment_key(calibration_keys(_context(candidate))[0])


def _variant_segment_key(candidate: HistoricalCandidate, variant: DiscoveryVariant) -> str:
    prob_bucket = probability_bucket(candidate.raw_prob) or "unknown"
    decision_price = _decision_price(candidate, variant)
    px_bucket = price_bucket(decision_price) or "unknown"
    hours_bucket = hours_to_close_bucket(candidate.hours_to_close) or "unknown"
    month_bucket = f"month-{candidate.target_date.month:02d}"
    if variant.family == "city_season_specialist":
        return "|".join(
            (
                "city_season",
                candidate.city_slug,
                month_bucket,
                candidate.bucket_kind,
                prob_bucket,
                px_bucket,
            )
        )
    if variant.family == "time_to_close_specialist":
        return "|".join(
            (
                "time_to_close",
                candidate.city_slug,
                candidate.bucket_kind,
                prob_bucket,
                px_bucket,
                hours_bucket,
            )
        )
    if variant.family == "forecast_error_regime":
        direction = (
            "model_hot"
            if candidate.raw_prob - float(candidate.price) >= 0.15
            else "model_cold"
            if float(candidate.price) - candidate.raw_prob >= 0.15
            else "model_neutral"
        )
        return "|".join((direction, candidate.city_slug, month_bucket, candidate.bucket_kind))
    if variant.family == "market_extreme_fade":
        return "|".join(("market_extreme", candidate.city_slug, candidate.bucket_kind, px_bucket))
    if variant.family == "dallas_fast_lane":
        return "|".join(("dallas_fast_lane", candidate.city_slug, candidate.bucket_kind, px_bucket))
    return _specific_segment_key(candidate)


def _variants(discovery_version: str = "v1") -> list[DiscoveryVariant]:
    variants: list[DiscoveryVariant] = []
    if discovery_version == "v4":
        for min_samples in (30, 50):
            for min_edge in (Decimal("0.000"), Decimal("0.005")):
                variants.extend(
                    [
                        DiscoveryVariant(
                            name=f"buy_no_value_n{min_samples}_edge{min_edge}".replace(
                                ".", "_"
                            ),
                            family="buy_no_value",
                            min_samples=min_samples,
                            min_edge_net=min_edge,
                            probability_cap=0.80,
                            require_brier_positive=False,
                            side="NO",
                        ),
                        DiscoveryVariant(
                            name=f"market_follow_n{min_samples}_edge{min_edge}".replace(
                                ".", "_"
                            ),
                            family="market_follow",
                            min_samples=min_samples,
                            min_edge_net=min_edge,
                            probability_cap=0.95,
                            require_brier_positive=False,
                        ),
                        DiscoveryVariant(
                            name=f"market_extreme_fade_n{min_samples}_edge{min_edge}".replace(
                                ".", "_"
                            ),
                            family="market_extreme_fade",
                            min_samples=min_samples,
                            min_edge_net=min_edge,
                            probability_cap=0.90,
                            require_brier_positive=False,
                            avoid_overconfidence=True,
                            side="NO",
                        ),
                        DiscoveryVariant(
                            name=f"city_season_specialist_n{min_samples}_edge{min_edge}".replace(
                                ".", "_"
                            ),
                            family="city_season_specialist",
                            min_samples=min_samples,
                            min_edge_net=min_edge,
                            probability_cap=0.75,
                            require_brier_positive=False,
                        ),
                        DiscoveryVariant(
                            name=f"time_to_close_specialist_n{min_samples}_edge{min_edge}".replace(
                                ".", "_"
                            ),
                            family="time_to_close_specialist",
                            min_samples=min_samples,
                            min_edge_net=min_edge,
                            probability_cap=0.75,
                            moderate_only=True,
                        ),
                        DiscoveryVariant(
                            name=f"forecast_error_regime_n{min_samples}_edge{min_edge}".replace(
                                ".", "_"
                            ),
                            family="forecast_error_regime",
                            min_samples=min_samples,
                            min_edge_net=min_edge,
                            probability_cap=0.70,
                            require_brier_positive=False,
                        ),
                        DiscoveryVariant(
                            name=f"dallas_fast_lane_n{min_samples}_edge{min_edge}".replace(
                                ".", "_"
                            ),
                            family="dallas_fast_lane",
                            min_samples=min_samples,
                            min_edge_net=min_edge,
                            probability_cap=0.80,
                            require_brier_positive=False,
                        ),
                    ]
                )
        return variants
    core_min_samples = (30, 50) if discovery_version in {"v3", "v4"} else (30, 50, 100)
    core_min_edges = (
        (Decimal("0.000"), Decimal("0.005"))
        if discovery_version in {"v3", "v4"}
        else (Decimal("0.000"), Decimal("0.005"), Decimal("0.010"))
    )
    core_alphas = (0.25, 0.50) if discovery_version in {"v3", "v4"} else (0.25, 0.50, 0.75)
    for min_samples in core_min_samples:
        for min_edge in core_min_edges:
            variants.append(
                DiscoveryVariant(
                    name=f"model_value_n{min_samples}_edge{min_edge}".replace(".", "_"),
                    family="model_value",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.80,
                    require_brier_positive=False,
                )
            )
            for alpha in core_alphas:
                variants.append(
                    DiscoveryVariant(
                        name=(
                            f"market_anchor_a{alpha:.2f}_n{min_samples}_edge{min_edge}"
                        ).replace(".", "_"),
                        family="market_anchor",
                        min_samples=min_samples,
                        min_edge_net=min_edge,
                        probability_cap=0.80,
                        alpha=alpha,
                    )
                )
            variants.append(
                DiscoveryVariant(
                    name=f"tail_value_n{min_samples}_edge{min_edge}".replace(".", "_"),
                    family="tail_value",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.80,
                    max_price=Decimal("0.20"),
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=f"bucket_specialist_n{min_samples}_edge{min_edge}".replace(".", "_"),
                    family="bucket_specialist",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.80,
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=f"avoid_overconfidence_n{min_samples}_edge{min_edge}".replace(
                        ".", "_"
                    ),
                    family="avoid_overconfidence",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.70,
                    avoid_overconfidence=True,
                )
            )
    if discovery_version not in {"v2", "v3", "v4"}:
        return variants

    v2_min_samples = (30, 50) if discovery_version in {"v3", "v4"} else (30, 50, 100)
    v2_min_edges = (
        (Decimal("0.000"), Decimal("0.005"))
        if discovery_version in {"v3", "v4"}
        else (Decimal("0.000"), Decimal("0.005"), Decimal("0.010"))
    )
    for min_samples in v2_min_samples:
        for min_edge in v2_min_edges:
            variants.append(
                DiscoveryVariant(
                    name=f"tail_surprise_city_n{min_samples}_edge{min_edge}".replace(".", "_"),
                    family="tail_surprise_city",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.60,
                    max_price=Decimal("0.40"),
                    require_brier_positive=False,
                    moderate_only=True,
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=f"bucket_mispricing_n{min_samples}_edge{min_edge}".replace(".", "_"),
                    family="bucket_mispricing",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.70,
                    moderate_only=True,
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=f"time_window_specialist_n{min_samples}_edge{min_edge}".replace(
                        ".", "_"
                    ),
                    family="time_window_specialist",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.70,
                    moderate_only=True,
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=f"market_implied_baseline_n{min_samples}_edge{min_edge}".replace(
                        ".", "_"
                    ),
                    family="market_implied_baseline",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.70,
                    alpha=0.15,
                    moderate_only=True,
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=f"no_trade_filter_n{min_samples}_edge{min_edge}".replace(".", "_"),
                    family="no_trade_filter",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.50,
                    require_brier_positive=False,
                    avoid_overconfidence=True,
                    research_only_diagnostic=True,
                )
            )
    if discovery_version not in {"v3", "v4"}:
        return variants

    for min_samples in (30, 50):
        for min_edge in (Decimal("0.000"), Decimal("0.005")):
            variants.append(
                DiscoveryVariant(
                    name=f"inverse_model_value_n{min_samples}_edge{min_edge}".replace(".", "_"),
                    family="inverse_model_value",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.80,
                    require_brier_positive=False,
                    avoid_overconfidence=True,
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=f"market_follow_n{min_samples}_edge{min_edge}".replace(".", "_"),
                    family="market_follow",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.95,
                    require_brier_positive=False,
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=f"buy_no_value_n{min_samples}_edge{min_edge}".replace(".", "_"),
                    family="buy_no_value",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.80,
                    require_brier_positive=False,
                    side="NO",
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=f"time_decay_specialist_n{min_samples}_edge{min_edge}".replace(".", "_"),
                    family="time_decay_specialist",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.70,
                    moderate_only=True,
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=(
                        f"resolution_source_specialist_n{min_samples}_edge{min_edge}"
                    ).replace(".", "_"),
                    family="resolution_source_specialist",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.75,
                )
            )
    if discovery_version != "v4":
        return variants

    for min_samples in (20, 30, 50):
        for min_edge in (Decimal("0.000"), Decimal("0.0025"), Decimal("0.005")):
            variants.append(
                DiscoveryVariant(
                    name=f"market_extreme_fade_n{min_samples}_edge{min_edge}".replace(".", "_"),
                    family="market_extreme_fade",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.90,
                    require_brier_positive=False,
                    avoid_overconfidence=True,
                    side="NO",
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=f"city_season_specialist_n{min_samples}_edge{min_edge}".replace(
                        ".", "_"
                    ),
                    family="city_season_specialist",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.75,
                    require_brier_positive=False,
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=f"time_to_close_specialist_n{min_samples}_edge{min_edge}".replace(
                        ".", "_"
                    ),
                    family="time_to_close_specialist",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.75,
                    moderate_only=True,
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=f"forecast_error_regime_n{min_samples}_edge{min_edge}".replace(
                        ".", "_"
                    ),
                    family="forecast_error_regime",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.70,
                    require_brier_positive=False,
                )
            )
            variants.append(
                DiscoveryVariant(
                    name=f"dallas_fast_lane_n{min_samples}_edge{min_edge}".replace(".", "_"),
                    family="dallas_fast_lane",
                    min_samples=min_samples,
                    min_edge_net=min_edge,
                    probability_cap=0.80,
                    require_brier_positive=False,
                )
            )
    return variants


async def _research_cities(
    session: AsyncSession,
    settings: Settings,
    *,
    universe: str,
    include_research_only: bool = False,
) -> tuple[list[str], dict[str, object]]:
    if universe not in {DEFAULT_UNIVERSE, "poc", "expanded-poc"}:
        selected = settings.cities or []
        quarantined: list[str] = []
        if universe == "ranked-live":
            selected, quarantined = split_operational_cities(selected)
        return selected, {
            "universe": universe,
            "eligible": selected,
            "live_eligible": selected,
            "research_only": [],
            "selected_cities": selected,
            "excluded_needs_review": [],
            "excluded_low_samples": [],
            "operational_candidates": selected,
            "excluded_quarantined": quarantined,
            "operational_quarantine": quarantine_payloads(set(quarantined)),
        }

    calibration_rows = (
        await session.execute(
            select(CalibrationMetric.city_slug, func.max(CalibrationMetric.n_samples))
            .group_by(CalibrationMetric.city_slug)
        )
    ).all()
    sample_by_city = {str(city): int(samples or 0) for city, samples in calibration_rows}
    cities = (
        await session.execute(select(City).where(City.active.is_(True)).order_by(City.slug))
    ).scalars().all()
    research_only = sorted(
        city.slug
        for city in cities
        if (city.needs_review or is_operationally_quarantined(city.slug))
        and sample_by_city.get(city.slug, 0) >= settings.validation_min_samples
    )
    excluded_review = sorted(
        city.slug
        for city in cities
        if city.needs_review and city.slug not in research_only
    )
    excluded_quarantined = sorted(
        city.slug
        for city in cities
        if is_operationally_quarantined(city.slug) and city.slug not in research_only
    )
    excluded_low_samples = sorted(
        city.slug
        for city in cities
        if not city.needs_review
        and not is_operationally_quarantined(city.slug)
        and sample_by_city.get(city.slug, 0) < settings.validation_min_samples
    )
    eligible = sorted(
        city.slug
        for city in cities
        if not city.needs_review
        and not is_operationally_quarantined(city.slug)
        and sample_by_city.get(city.slug, 0) >= settings.validation_min_samples
    )
    selected = eligible + (
        research_only if universe in {"poc", "expanded-poc"} and include_research_only else []
    )
    return selected, {
        "universe": universe,
        "discovery_universe_mode": (
            "poc_include_research_only"
            if universe in {"poc", "expanded-poc"} and include_research_only
            else "live_eligible_only"
        ),
        "eligible": eligible,
        "live_eligible": eligible,
        "research_only": research_only,
        "selected_cities": selected,
        "operational_candidates": eligible,
        "excluded_needs_review": excluded_review,
        "excluded_quarantined": excluded_quarantined,
        "excluded_low_samples": excluded_low_samples,
        "operational_quarantine": quarantine_payloads(set(research_only + excluded_quarantined)),
        "min_forecast_observed_pairs": settings.validation_min_samples,
    }


def _build_segments(
    candidates: list[HistoricalCandidate], fee_rate: Decimal, variant: DiscoveryVariant
) -> dict[str, SegmentStats]:
    segments: dict[str, SegmentStats] = {}
    for candidate in candidates:
        key = _variant_segment_key(candidate, variant)
        segment = segments.setdefault(key, SegmentStats(key=key))
        segment.add(candidate, fee_rate)
    return segments


def _calibrated_probability(
    candidate: HistoricalCandidate,
    segment: SegmentStats,
    variant: DiscoveryVariant,
) -> float:
    empirical = segment.observed_rate
    if variant.family in {"market_anchor", "market_implied_baseline"}:
        p = float(candidate.price) + variant.alpha * (empirical - float(candidate.price))
    elif variant.family == "model_value":
        p = (candidate.raw_prob + empirical) / 2
    elif variant.family == "inverse_model_value":
        p = (1 - candidate.raw_prob + empirical) / 2
    elif variant.family == "market_follow":
        p = (float(candidate.price) + empirical) / 2
    elif variant.family == "buy_no_value":
        no_empirical = 1 - empirical
        no_raw = 1 - candidate.raw_prob
        p = (no_raw + no_empirical) / 2
    elif variant.family == "market_extreme_fade":
        p = (1 - float(candidate.price) + (1 - empirical)) / 2
    elif variant.family == "dallas_fast_lane":
        p = (candidate.raw_prob + empirical) / 2 if candidate.city_slug == "dallas" else 0.0
    elif variant.family == "no_trade_filter":
        p = min(float(candidate.price), empirical)
    else:
        p = empirical
    return min(max(p, 0.0), variant.probability_cap)


def _decision_price(candidate: HistoricalCandidate, variant: DiscoveryVariant) -> Decimal:
    if variant.side == "NO":
        return (Decimal("1") - candidate.price).quantize(Decimal("0.00001"))
    return candidate.price


def _decision_winner(candidate: HistoricalCandidate, variant: DiscoveryVariant) -> bool:
    return not candidate.winner if variant.side == "NO" else candidate.winner


def _reason(
    candidate: HistoricalCandidate,
    segment: SegmentStats | None,
    variant: DiscoveryVariant,
    fee_rate: Decimal,
) -> str | None:
    if segment is None:
        return "no_segment"
    if segment.n < variant.min_samples:
        return "min_samples"
    if variant.research_only_diagnostic:
        return "diagnostic_filter_family"
    if variant.max_price is not None and candidate.price > variant.max_price:
        return "price_above_family_max"
    if variant.moderate_only:
        if not (0.30 <= candidate.raw_prob <= 0.70):
            return "raw_prob_not_moderate"
        if not (Decimal("0.05") <= candidate.price <= Decimal("0.40")):
            return "price_not_moderate"
        if not (6.0 <= candidate.hours_to_close <= 48.0):
            return "time_window_not_moderate"
    if variant.family == "dallas_fast_lane" and candidate.city_slug != "dallas":
        return "not_dallas_fast_lane"
    if (
        variant.family == "market_extreme_fade"
        and Decimal("0.05") < candidate.price < Decimal("0.95")
    ):
        return "price_not_extreme"
    if variant.require_brier_positive:
        brier_delta = segment.brier_delta_for(variant.side)
        if brier_delta is None or brier_delta <= 0:
            return "segment_brier"
    if segment.pnl_for(variant.side) <= Decimal("0"):
        return "segment_pnl"
    avg_cost = segment.avg_cost_for(variant.side)
    observed_rate = Decimal(str(segment.observed_rate_for(variant.side)))
    if avg_cost is None or observed_rate <= avg_cost:
        return "segment_cost"
    if variant.avoid_overconfidence and candidate.raw_prob - segment.observed_rate > 0.15:
        return "model_overconfidence"
    p = _calibrated_probability(candidate, segment, variant)
    edge = net_edge(p, _decision_price(candidate, variant), fee_rate)
    if edge < 0:
        return "negative_edge"
    if edge < variant.min_edge_net:
        return "min_edge_net"
    return None


def _simulate(
    candidates: list[HistoricalCandidate],
    segments: dict[str, SegmentStats],
    variant: DiscoveryVariant,
    settings: Settings,
) -> tuple[list[TradeResult], dict[str, object]]:
    trades: list[TradeResult] = []
    blocked: Counter[str] = Counter()
    samples: list[dict[str, object]] = []
    last_signals: dict[tuple[str, str], tuple[datetime, Decimal]] = {}
    exposure_by_market_day: defaultdict[tuple[str, object], Decimal] = defaultdict(Decimal)

    for candidate in candidates:
        key = _variant_segment_key(candidate, variant)
        segment = segments.get(key)
        reason = _reason(candidate, segment, variant, settings.taker_fee_rate)
        p = (
            _calibrated_probability(candidate, segment, variant)
            if segment is not None
            else candidate.raw_prob
        )
        decision_price = _decision_price(candidate, variant)
        decision_winner = _decision_winner(candidate, variant)
        edge = net_edge(p, decision_price, settings.taker_fee_rate)
        stake = Decimal("0")
        if reason is None:
            stake = kelly_stake(
                p,
                cost_per_share(decision_price, settings.taker_fee_rate),
                bankroll=settings.bankroll,
                kelly_multiplier=settings.kelly_fraction,
                max_stake_per_order=settings.max_stake_per_order,
            )
            if stake <= 0:
                reason = "kelly_stake_zero"
            elif _is_recent_duplicate(
                last_signals,
                market_id=candidate.market_id,
                profile="max_edge",
                ts=candidate.ts,
                edge_net=edge,
            ):
                reason = "duplicate"
            elif (
                exposure_by_market_day[(candidate.market_id, candidate.ts.date())] + stake
                > settings.max_exposure_per_market
            ):
                reason = "max_exposure"
            else:
                trade = _trade_result(
                    ts=candidate.ts,
                    stake=stake,
                    market_price=decision_price,
                    model_prob=p,
                    winner=decision_winner,
                    fee_rate=settings.taker_fee_rate,
                    market_id=candidate.market_id,
                    event_id=candidate.event_id,
                    city_slug=candidate.city_slug,
                    target_date=candidate.target_date,
                    bucket_kind=candidate.bucket_kind,
                    bucket_label=candidate.bucket_label,
                    edge_net=edge,
                    hours_to_close=candidate.hours_to_close,
                    price_source=candidate.price_source,
                )
                if trade is not None:
                    trades.append(trade)
                    last_signals[(candidate.market_id, "max_edge")] = (candidate.ts, edge)
                    exposure_by_market_day[(candidate.market_id, candidate.ts.date())] += stake

        if reason is not None:
            blocked[reason] += 1
        if len(samples) < SAMPLE_LIMIT:
            samples.append(
                {
                    "ts": candidate.ts.isoformat(),
                    "city_slug": candidate.city_slug,
                    "market_id": candidate.market_id,
                    "segment_key": key,
                    "market_price": str(decision_price),
                    "raw_prob": candidate.raw_prob,
                    "calibrated_prob": p,
                    "edge_net": str(edge),
                    "reason": reason,
                    "would_trade": reason is None,
                    "family": variant.family,
                    "side": variant.side,
                }
            )
    return trades, {"blocked_counts": dict(sorted(blocked.items())), "samples": samples}


def _profile_payload(trades: list[TradeResult]) -> dict[str, object]:
    return {
        **_trade_metrics(trades),
        **_bootstrap_metrics(trades),
        **_concentration_metrics(trades),
        "city_pnl_share": _city_pnl_share(trades),
        "traded_cities": sorted(
            {trade.city_slug for trade in trades if trade.city_slug is not None}
        ),
    }


def _city_pnl_share(trades: list[TradeResult]) -> dict[str, object]:
    by_city: defaultdict[str, Decimal] = defaultdict(Decimal)
    for trade in trades:
        if trade.city_slug is not None:
            by_city[trade.city_slug] += trade.pnl
    total_abs = sum((abs(value) for value in by_city.values()), Decimal("0"))
    if total_abs <= 0:
        return {"top_city": None, "top_city_abs_pnl_share": None, "city_count": len(by_city)}
    city, pnl = max(by_city.items(), key=lambda item: abs(item[1]))
    return {
        "top_city": city,
        "top_city_abs_pnl_share": str((abs(pnl) / total_abs).quantize(Decimal("0.0001"))),
        "city_count": len(by_city),
    }


def _variant_payload(
    variant: DiscoveryVariant,
    trades: list[TradeResult],
    metadata: dict[str, object],
    *,
    include_bootstrap: bool = True,
) -> dict[str, object]:
    profile = _profile_payload(trades) if include_bootstrap else {
        **_trade_metrics(trades),
        **_concentration_metrics(trades),
        "city_pnl_share": _city_pnl_share(trades),
        "traded_cities": sorted(
            {trade.city_slug for trade in trades if trade.city_slug is not None}
        ),
    }
    return {
        "name": variant.name,
        "family": variant.family,
        "min_samples": variant.min_samples,
        "min_edge_net": str(variant.min_edge_net),
        "probability_cap": variant.probability_cap,
        "alpha": variant.alpha,
        "max_price": str(variant.max_price) if variant.max_price is not None else None,
        "side": variant.side,
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
    gates += 1 if concentration <= MAX_TOP_5_ABS_PNL_SHARE else 0
    gates += 1 if bootstrap_high >= 0 else 0
    return (gates, pnl, brier_value, n)


def _fold_windows(
    candidates: list[HistoricalCandidate],
    *,
    min_train_days: int = DEFAULT_MIN_TRAIN_DAYS,
    fold_days: int = DEFAULT_FOLD_DAYS,
) -> list[tuple[date, date]]:
    if not candidates:
        return []
    start = min(candidate.target_date for candidate in candidates) + timedelta(
        days=min_train_days
    )
    end = max(candidate.target_date for candidate in candidates)
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        fold_end = min(cursor + timedelta(days=fold_days - 1), end)
        windows.append((cursor, fold_end))
        cursor = fold_end + timedelta(days=1)
    return windows


def _rolling_origin(
    candidates: list[HistoricalCandidate],
    settings: Settings,
    *,
    discovery_version: str = "v1",
) -> tuple[dict[str, object] | None, list[dict[str, object]], dict[str, object]]:
    variants = _variants(discovery_version)
    folds: list[dict[str, object]] = []
    oos_trades: list[TradeResult] = []
    selected_counter: Counter[str] = Counter()
    last_payload_by_family: dict[str, dict[str, object]] = {}
    valid_folds = 0

    for index, (fold_start, fold_end) in enumerate(_fold_windows(candidates)):
        train = [candidate for candidate in candidates if candidate.target_date < fold_start]
        fold = [
            candidate
            for candidate in candidates
            if fold_start <= candidate.target_date <= fold_end
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
            train_segments = _build_segments(train, settings.taker_fee_rate, variant)
            trades, metadata = _simulate(train, train_segments, variant, settings)
            train_payloads.append(
                _variant_payload(variant, trades, metadata, include_bootstrap=False)
            )
        selected = max(train_payloads, key=_score)
        selected_variant = next(
            variant for variant in variants if variant.name == selected["name"]
        )
        selected_segments = _build_segments(train, settings.taker_fee_rate, selected_variant)
        fold_trades, fold_metadata = _simulate(
            fold, selected_segments, selected_variant, settings
        )
        fold_payload = _variant_payload(selected_variant, fold_trades, fold_metadata)
        oos_trades.extend(fold_trades)
        selected_counter[selected_variant.family] += 1
        last_payload_by_family[selected_variant.family] = fold_payload
        valid_folds += 1
        folds.append(
            {
                "index": index,
                "fold_window": {"start": fold_start.isoformat(), "end": fold_end.isoformat()},
                "valid": True,
                "selected_family": selected_variant.family,
                "selected_variant": selected_variant.name,
                "n_train": len(train),
                "n_fold_candidates": len(fold),
                "n_oos_trades": len(fold_trades),
                "pnl": fold_payload["profile"]["total_pnl"],  # type: ignore[index]
                "brier_delta": fold_payload["profile"]["brier_delta"],  # type: ignore[index]
            }
        )

    if not oos_trades:
        best_family = None
    else:
        best_name = selected_counter.most_common(1)[0][0]
        best_family = {
            "name": f"discovery_oos_{best_name}",
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
    universe_health: dict[str, object],
    discovery_version: str = "v1",
) -> dict[str, object]:
    profile = best_family.get("profile") if isinstance(best_family, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    best_family_name = str(best_family.get("family") or "") if isinstance(best_family, dict) else ""
    n = int(profile.get("n_resolved_trades") or 0)
    pnl = Decimal(str(profile.get("total_pnl") or "0"))
    brier = profile.get("brier_delta")
    brier_pass = isinstance(brier, int | float) and float(brier) > 0
    concentration = Decimal(str(profile.get("top_5_abs_pnl_share") or "999"))
    bootstrap_high = Decimal(str(profile.get("pnl_ci_high") or "-999999"))
    city_pnl_share = profile.get("city_pnl_share")
    top_city_share = (
        Decimal(str(city_pnl_share.get("top_city_abs_pnl_share") or "999"))
        if isinstance(city_pnl_share, dict)
        else Decimal("999")
    )
    selected_cities_raw = universe_health.get("selected_cities", universe_health.get("eligible"))
    no_city_eligible = isinstance(selected_cities_raw, list) and len(selected_cities_raw) == 0
    traded_cities_raw = profile.get("traded_cities")
    traded_cities = (
        {str(city) for city in traded_cities_raw}
        if isinstance(traded_cities_raw, list)
        else set()
    )
    research_only_raw = universe_health.get("research_only")
    research_only = (
        {str(city) for city in research_only_raw}
        if isinstance(research_only_raw, list)
        else set()
    )
    live_eligible_raw = universe_health.get("live_eligible")
    live_eligible = (
        {str(city) for city in live_eligible_raw}
        if isinstance(live_eligible_raw, list)
        else set()
    )
    quarantined_raw = universe_health.get("excluded_quarantined")
    quarantined = (
        {str(city) for city in quarantined_raw}
        if isinstance(quarantined_raw, list)
        else set()
    )
    quarantine_payload_raw = universe_health.get("operational_quarantine")
    if isinstance(quarantine_payload_raw, list):
        for item in quarantine_payload_raw:
            if isinstance(item, dict) and isinstance(item.get("city_slug"), str):
                quarantined.add(str(item["city_slug"]))
    only_research_city_edge = bool(traded_cities) and traded_cities.isdisjoint(live_eligible)
    traded_quarantined = traded_cities & quarantined
    flexible_discovery = discovery_version in {"v2", "v3", "v4"}
    candidate_trade_min = 30 if flexible_discovery else MIN_HISTORICAL_TRADES
    candidate_fold_min = 2 if flexible_discovery else DEFAULT_MIN_FOLDS
    candidate_edge_pass = (brier_pass or pnl > 0) if flexible_discovery else brier_pass
    dallas_fast_lane_pass = (
        best_family_name == "dallas_fast_lane"
        and traded_cities == {"dallas"}
        and n >= 100
    )
    return {
        "diagnostic_candidate": {
            "passed": (
                n >= candidate_trade_min
                and valid_folds >= candidate_fold_min
                and candidate_edge_pass
            ),
            "value": {
                "n_resolved_trades": n,
                "valid_folds": valid_folds,
                "brier_delta": brier,
                "total_pnl": str(pnl),
            },
            "required": {
                "min_trades": candidate_trade_min,
                "min_valid_folds": candidate_fold_min,
                "brier_or_pnl_positive": True,
            },
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
            "passed": concentration <= MAX_TOP_5_ABS_PNL_SHARE,
            "value": {"top_5_abs_pnl_share": str(concentration)},
            "required": {"top_5_abs_pnl_share_lte": str(MAX_TOP_5_ABS_PNL_SHARE)},
        },
        "bootstrap": {
            "passed": bootstrap_high >= 0,
            "value": {"pnl_ci_high": str(bootstrap_high)},
            "required": {"pnl_ci_high_gte": "0"},
        },
        "folds": {
            "passed": valid_folds >= DEFAULT_MIN_FOLDS,
            "value": {"valid_folds": valid_folds},
            "required": {"min_valid_folds": DEFAULT_MIN_FOLDS},
        },
        "city_diversification": {
            "passed": top_city_share <= MAX_TOP_CITY_PNL_SHARE or dallas_fast_lane_pass,
            "value": {"top_city_abs_pnl_share": str(top_city_share)},
            "required": {
                "top_city_abs_pnl_share_lte": str(MAX_TOP_CITY_PNL_SHARE),
                "dallas_fast_lane_exception": "dallas only with >=100 OOS trades",
            },
        },
        "research_only_cap": {
            "passed": not only_research_city_edge,
            "value": {
                "traded_cities": sorted(traded_cities),
                "research_only": sorted(research_only),
                "live_eligible": sorted(live_eligible),
            },
            "required": "READY_FOR_SHADOW_PAPER cannot depend only on research_only cities",
        },
        "operational_quarantine": {
            "passed": not traded_quarantined,
            "value": {
                "traded_quarantined_cities": sorted(traded_quarantined),
                "quarantine": quarantine_payloads(traded_quarantined),
            },
            "required": "quarantined cities cannot approve shadow/paper/live",
        },
        "universe_health": {
            "passed": not no_city_eligible,
            "value": universe_health,
            "required": {"selected_research_cities_gt": 0},
        },
        "live_release": {
            "passed": False,
            "value": "diagnostic_only",
            "required": "strategy_repair PROMISING plus measurement READY_FOR_LIVE_REVIEW",
        },
    }


def _status(gates: dict[str, object]) -> str:
    universe = gates.get("universe_health")
    if isinstance(universe, dict) and universe.get("passed") is not True:
        return "DATA_REVIEW"
    shadow_gates = [
        gate
        for key, gate in gates.items()
        if key not in {"live_release", "diagnostic_candidate"} and isinstance(gate, dict)
    ]
    if all(gate.get("passed") is True for gate in shadow_gates):
        return "READY_FOR_SHADOW_PAPER"
    diagnostic = gates.get("diagnostic_candidate")
    if isinstance(diagnostic, dict) and diagnostic.get("passed") is True:
        return "DISCOVERY_CANDIDATE"
    if (
        gates["oos_brier"]["passed"] is True  # type: ignore[index]
        and gates["folds"]["passed"] is True  # type: ignore[index]
    ):
        return "DISCOVERY_CANDIDATE"
    return "NO_EDGE_FOUND"


async def generate_strategy_discovery_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
    universe: str = DEFAULT_UNIVERSE,
    discovery_version: str = "v1",
    include_research_only: bool = False,
) -> StrategyDiscoveryRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)

    async with session_factory() as session:
        selected_cities, universe_health = await _research_cities(
            session,
            settings,
            universe=universe,
            include_research_only=include_research_only,
        )
        if cities is not None:
            selected_cities = cities
            excluded_quarantined: list[str] = []
            if universe == "ranked-live":
                selected_cities, excluded_quarantined = split_operational_cities(cities)
            universe_health = {
                **universe_health,
                "requested_cities": cities,
                "selected_cities": selected_cities,
                "operational_candidates": selected_cities,
                "live_eligible": selected_cities
                if universe == "ranked-live"
                else universe_health.get("live_eligible", []),
                "excluded_quarantined": excluded_quarantined,
                "operational_quarantine": quarantine_payloads(set(excluded_quarantined)),
            }
        run_settings = settings.model_copy(
            update={"cities": selected_cities, "validation_history_days": history_days}
        )
        candidates, n_candidates, source_counts, raw_counts, sampled_counts = (
            await _historical_candidates(session, run_settings)
        )

    best_family, folds, rolling_summary = _rolling_origin(
        candidates,
        run_settings,
        discovery_version=discovery_version,
    )
    valid_folds = int(rolling_summary.get("valid_folds") or 0)
    gates = _gates(
        best_family,
        valid_folds=valid_folds,
        universe_health=universe_health,
        discovery_version=discovery_version,
    )
    status = _status(gates)
    summary = {
        "source": DISCOVERY_SOURCE,
        "source_universe": "city_edge_ranking" if universe == "ranked-live" else universe,
        "discovery_version": discovery_version,
        "universe": universe,
        "include_research_only": include_research_only,
        "diagnostic_only": True,
        "cannot_approve_live": True,
        "live_eligible_cities": universe_health.get("live_eligible", []),
        "research_only_cities": universe_health.get("research_only", []),
        "requested_cities": universe_health.get("requested_cities"),
        "operational_candidates": universe_health.get("operational_candidates", []),
        "excluded_quarantined": universe_health.get("excluded_quarantined", []),
        "operational_quarantine": universe_health.get("operational_quarantine", []),
        "selected_cities": universe_health.get("selected_cities", selected_cities),
        "n_candidate_price_points": n_candidates,
        "price_source_counts": source_counts,
        "price_source_raw_counts": raw_counts,
        "price_source_sampled_counts": sampled_counts,
        "execution_proxy": EXECUTION_PROXY,
        "price_sampling": PRICE_SAMPLING,
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
            "activate_shadow_paper"
            if status == "READY_FOR_SHADOW_PAPER"
            else "review_discovery_candidate"
            if status == "DISCOVERY_CANDIDATE"
            else "review_weather_hypothesis"
        ),
        **rolling_summary,
    }
    families = {
        "tested": sorted({variant.family for variant in _variants(discovery_version)}),
        "selected_families": rolling_summary.get("selected_families", {}),
    }

    async with session_factory() as session, session.begin():
        row = StrategyDiscoveryRun(
            run_at=run_at,
            status=status,
            universe=universe,
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
        logger.info(
            "strategy discovery: status=%s best=%s candidates=%d",
            status,
            summary["best_family"],
            n_candidates,
        )
        return row


async def run(
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
    universe: str = DEFAULT_UNIVERSE,
    discovery_version: str = "v1",
    include_research_only: bool = False,
) -> StrategyDiscoveryRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_strategy_discovery_report(
            session_factory,
            settings,
            cities=cities,
            days=days,
            universe=universe,
            discovery_version=discovery_version,
            include_research_only=include_research_only,
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run diagnostic strategy discovery.")
    parser.add_argument("--cities", help="Comma-separated city slugs.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--universe", default=DEFAULT_UNIVERSE)
    parser.add_argument("--discovery-version", default="v1", choices=["v1", "v2", "v3", "v4"])
    parser.add_argument("--include-research-only", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def _run_to_jsonable(row: StrategyDiscoveryRun) -> dict[str, object]:
    folds = json.loads(row.folds_json)
    fold_rows = folds if isinstance(folds, list) else []
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "universe": row.universe,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "cities": json.loads(row.cities_json),
        "summary": json.loads(row.summary_json),
        "families": json.loads(row.families_json),
        "best_family": json.loads(row.best_family_json),
        "folds_count": len(fold_rows),
        "folds": fold_rows[-12:],
        "gates": json.loads(row.gates_json),
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.json else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    row = asyncio.run(
        run(
            get_settings(),
            cities=parse_cities(args.cities),
            days=args.days,
            universe=args.universe,
            discovery_version=args.discovery_version,
            include_research_only=args.include_research_only,
        )
    )
    if args.json:
        print(json.dumps(_run_to_jsonable(row), sort_keys=True))


if __name__ == "__main__":
    main()
