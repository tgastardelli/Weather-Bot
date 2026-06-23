"""Walk-forward strategy repair backtest.

This report compares the current historical strategy against calibrated repair
variants. It remains paper-only and never writes artificial signals.
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

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.backtest import (
    HISTORICAL_TRADE_EXECUTION_PROXY,
    HISTORICAL_TRADE_PRICE_SAMPLING,
    PROFILES,
    HistoricalMarketPoint,
    Profile,
    TradeResult,
    _bootstrap_metrics,
    _concentration_metrics,
    _event_historical_probs_at,
    _historical_price_profile_trades,
    _is_recent_duplicate,
    _parse_hour_bucket,
    _trade_metrics,
    _trade_result,
)
from analysis.historical_validation import (
    MAX_TOP_5_ABS_PNL_SHARE,
    MIN_HISTORICAL_TRADES,
    parse_cities,
)
from analysis.operational_quarantine import is_operationally_quarantined, quarantine_payloads
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    City,
    Event,
    Market,
    MarketPriceHistoryPoint,
    MarketTradeHistoryPoint,
    StrategyCalibrationSegment,
    StrategyRepairRun,
)
from app.db.session import create_engine, create_session_factory
from app.strategy.edge import cost_per_share, net_edge
from app.strategy.probability_calibration import (
    DEFAULT_MIN_SAMPLES,
    DEFAULT_PROBABILITY_CAP,
    ProbabilityContext,
    WalkForwardMarketAwareCalibrator,
    WalkForwardProbabilityCalibrator,
    edge_bucket,
    price_bucket,
    probability_bucket,
)
from app.strategy.repair_decision import (
    RepairPolicyParams,
    RepairSegmentStats,
    evaluate_repair_policy,
)
from app.strategy.sizing import kelly_stake

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
REPAIR_SOURCE = "strategy_repair_historical_price_points"
RATE = Decimal("0.0001")
REPAIR_V2_ALPHAS = (0.25, 0.50, 0.75, 1.00)
REPAIR_V2_MIN_SAMPLES = (50, 100, 200)
REPAIR_V2_CAPS = (0.40, 0.50, 0.60, 0.70, 0.80)
REPAIR_V2_MIN_EDGES = (
    Decimal("0.02"),
    Decimal("0.04"),
    Decimal("0.06"),
    Decimal("0.08"),
)
REPAIR_V3_ALPHAS = (0.10, 0.15, 0.25)
REPAIR_V3_MIN_SAMPLES = (50, 100)
REPAIR_V3_CAPS = (0.20, 0.30, 0.40)
REPAIR_V3_MIN_EDGES = (Decimal("0.00"), Decimal("0.01"), Decimal("0.02"))
REPAIR_V4_ALPHAS = (0.02, 0.05, 0.08, 0.10, 0.15)
REPAIR_V4_MIN_SAMPLES = (50, 100, 200)
REPAIR_V4_CAPS = (0.10, 0.15, 0.20)
REPAIR_V4_MIN_EDGES = (
    Decimal("0.000"),
    Decimal("0.005"),
    Decimal("0.010"),
    Decimal("0.015"),
)
REPAIR_V4_PRICE_FLOORS = (Decimal("0.05"), Decimal("0.10"))
VariantName = str

ValidationScheme = Literal["fixed-holdout", "rolling-origin"]
DEFAULT_VALIDATION_SCHEME: ValidationScheme = "rolling-origin"
DEFAULT_FOLD_DAYS = 30
DEFAULT_MIN_TRAIN_DAYS = 90
DEFAULT_MIN_TRAIN_CANDIDATES = 10000
DEFAULT_MIN_FOLD_CANDIDATES = 1000
ROLLING_ORIGIN_MIN_FOLDS = 3
ROLLING_ORIGIN_CONCENTRATION = Decimal("0.40")


@dataclass(frozen=True)
class HistoricalCandidate:
    ts: datetime
    sampled_ts: datetime
    market_id: str
    event_id: str
    city_slug: str
    target_date: date
    price: Decimal
    raw_prob: float
    winner: bool
    bucket_kind: str
    bucket_label: str
    hours_to_close: float
    price_source: str


@dataclass(frozen=True)
class RepairVariant:
    name: VariantName
    calibrate: bool
    apply_segment_filters: bool
    repair_v2: bool = False
    repair_v3: bool = False
    repair_v4: bool = False
    alpha: float = 1.0
    min_samples: int = DEFAULT_MIN_SAMPLES
    probability_cap: float = DEFAULT_PROBABILITY_CAP
    min_edge_net: Decimal | None = None
    segment_scope: Literal["fallback", "specific_only"] = "fallback"
    price_floor: Decimal | None = None

    @property
    def market_aware(self) -> bool:
        return self.repair_v2 or self.repair_v3 or self.repair_v4

    @property
    def policy_version(self) -> str:
        if self.repair_v4:
            return "repair_v4"
        if self.repair_v3:
            return "repair_v3"
        if self.repair_v2:
            return "repair_v2"
        return "legacy"


REPAIR_VARIANTS: tuple[RepairVariant, ...] = (
    RepairVariant("calibrated_cap", calibrate=True, apply_segment_filters=False),
    RepairVariant("calibrated_filtered", calibrate=True, apply_segment_filters=True),
)


@dataclass(frozen=True)
class RollingOriginConfig:
    fold_days: int = DEFAULT_FOLD_DAYS
    min_train_days: int = DEFAULT_MIN_TRAIN_DAYS
    min_train_candidates: int = DEFAULT_MIN_TRAIN_CANDIDATES
    min_fold_candidates: int = DEFAULT_MIN_FOLD_CANDIDATES
    min_folds: int = ROLLING_ORIGIN_MIN_FOLDS


@dataclass(frozen=True)
class FoldWindow:
    index: int
    fold_start: date
    fold_end: date


@dataclass
class RollingOriginResult:
    selected_variant: RepairVariant | None
    oos_trades: dict[Profile, list[TradeResult]]
    folds: list[dict[str, object]]
    fold_count: int
    train_variant_payloads: list[dict[str, object]]
    selected_segments: list[dict[str, object]]
    selection_train_size: int
    candidate_counts_by_month: dict[str, int]
    market_history_span: dict[str, str] | None
    selection_reason: str | None = None


def _repair_v2_variants() -> list[RepairVariant]:
    variants: list[RepairVariant] = []
    for alpha in REPAIR_V2_ALPHAS:
        for min_samples in REPAIR_V2_MIN_SAMPLES:
            for cap in REPAIR_V2_CAPS:
                for min_edge in REPAIR_V2_MIN_EDGES:
                    variants.append(
                        RepairVariant(
                            name=(
                                "repair_v2"
                                f"_a{alpha:.2f}"
                                f"_n{min_samples}"
                                f"_cap{cap:.2f}"
                                f"_edge{min_edge}"
                            ).replace(".", "_"),
                            calibrate=True,
                            apply_segment_filters=True,
                            repair_v2=True,
                            alpha=alpha,
                            min_samples=min_samples,
                            probability_cap=cap,
                            min_edge_net=min_edge,
                        )
                    )
    return variants


def _repair_v3_variants() -> list[RepairVariant]:
    variants: list[RepairVariant] = []
    for alpha in REPAIR_V3_ALPHAS:
        for min_samples in REPAIR_V3_MIN_SAMPLES:
            for cap in REPAIR_V3_CAPS:
                for min_edge in REPAIR_V3_MIN_EDGES:
                    variants.append(
                        RepairVariant(
                            name=(
                                "repair_v3"
                                f"_a{alpha:.2f}"
                                f"_n{min_samples}"
                                f"_cap{cap:.2f}"
                                f"_edge{min_edge}"
                            ).replace(".", "_"),
                            calibrate=True,
                            apply_segment_filters=True,
                            repair_v3=True,
                            alpha=alpha,
                            min_samples=min_samples,
                            probability_cap=cap,
                            min_edge_net=min_edge,
                            segment_scope="specific_only",
                        )
                    )
    return variants


def _repair_v4_variants() -> list[RepairVariant]:
    variants: list[RepairVariant] = []
    for alpha in REPAIR_V4_ALPHAS:
        for min_samples in REPAIR_V4_MIN_SAMPLES:
            for cap in REPAIR_V4_CAPS:
                for min_edge in REPAIR_V4_MIN_EDGES:
                    for price_floor in REPAIR_V4_PRICE_FLOORS:
                        variants.append(
                            RepairVariant(
                                name=(
                                    "repair_v4"
                                    f"_a{alpha:.2f}"
                                    f"_n{min_samples}"
                                    f"_cap{cap:.2f}"
                                    f"_edge{min_edge}"
                                    f"_floor{price_floor}"
                                ).replace(".", "_"),
                                calibrate=True,
                                apply_segment_filters=True,
                                repair_v4=True,
                                alpha=alpha,
                                min_samples=min_samples,
                                probability_cap=cap,
                                min_edge_net=min_edge,
                                segment_scope="specific_only",
                                price_floor=price_floor,
                            )
                        )
    return variants


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except ValueError:
        return None


def _required_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(str(value))


def _required_float(value: object) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return float(str(value))


async def _historical_price_rows(
    session: AsyncSession, settings: Settings
) -> tuple[
    list[tuple[HistoricalMarketPoint, Market, Event, City]],
    dict[str, int],
    dict[str, int],
]:
    start = datetime.now(UTC).date() - timedelta(days=settings.validation_history_days)
    trade_filters = [
        Market.winner.is_not(None),
        Event.end_date.is_not(None),
        Event.target_date >= start,
        MarketTradeHistoryPoint.ts <= Event.end_date,
    ]
    price_filters = [
        Market.winner.is_not(None),
        Event.end_date.is_not(None),
        Event.target_date >= start,
        MarketPriceHistoryPoint.ts <= Event.end_date,
    ]
    if settings.cities is not None:
        trade_filters.append(Event.city_slug.in_(settings.cities))
        price_filters.append(Event.city_slug.in_(settings.cities))

    raw_trade_count_query = (
        select(func.count(MarketTradeHistoryPoint.id))
        .select_from(MarketTradeHistoryPoint)
        .join(Market, MarketTradeHistoryPoint.market_id == Market.id)
        .join(Event, Market.event_id == Event.id)
        .join(City, Event.city_slug == City.slug)
        .where(*trade_filters)
    )
    raw_price_count_query = (
        select(func.count(MarketPriceHistoryPoint.id))
        .select_from(MarketPriceHistoryPoint)
        .join(Market, MarketPriceHistoryPoint.market_id == Market.id)
        .join(Event, Market.event_id == Event.id)
        .join(City, Event.city_slug == City.slug)
        .where(*price_filters)
    )
    raw_source_counts = {
        "data_api_trades": int((await session.execute(raw_trade_count_query)).scalar_one() or 0),
        "clob_prices_history": int(
            (await session.execute(raw_price_count_query)).scalar_one() or 0
        ),
    }

    trade_bucket = func.strftime("%Y-%m-%d %H:00:00", MarketTradeHistoryPoint.ts)
    trade_rank = func.row_number().over(
        partition_by=(MarketTradeHistoryPoint.market_id, trade_bucket),
        order_by=(MarketTradeHistoryPoint.ts.desc(), MarketTradeHistoryPoint.id.desc()),
    )
    sampled_trade_ids = (
        select(
            MarketTradeHistoryPoint.id.label("trade_id"),
            trade_bucket.label("sampled_ts"),
            trade_rank.label("trade_rank"),
        )
        .select_from(MarketTradeHistoryPoint)
        .join(Market, MarketTradeHistoryPoint.market_id == Market.id)
        .join(Event, Market.event_id == Event.id)
        .join(City, Event.city_slug == City.slug)
        .where(*trade_filters)
    ).subquery()

    trade_rows = (
        await session.execute(
            select(MarketTradeHistoryPoint, Market, Event, City, sampled_trade_ids.c.sampled_ts)
            .select_from(MarketTradeHistoryPoint)
            .join(sampled_trade_ids, MarketTradeHistoryPoint.id == sampled_trade_ids.c.trade_id)
            .join(Market, MarketTradeHistoryPoint.market_id == Market.id)
            .join(Event, Market.event_id == Event.id)
            .join(City, Event.city_slug == City.slug)
            .where(sampled_trade_ids.c.trade_rank == 1)
            .order_by(MarketTradeHistoryPoint.ts, Market.id)
        )
    ).all()
    trade_market_ids = {row.market_id for row, _, _, _, _ in trade_rows}

    price_query: Select[tuple[MarketPriceHistoryPoint, Market, Event, City]] = (
        select(MarketPriceHistoryPoint, Market, Event, City)
        .select_from(MarketPriceHistoryPoint)
        .join(Market, MarketPriceHistoryPoint.market_id == Market.id)
        .join(Event, Market.event_id == Event.id)
        .join(City, Event.city_slug == City.slug)
        .where(*price_filters)
        .order_by(MarketPriceHistoryPoint.ts, Market.id)
    )
    price_rows = (await session.execute(price_query)).all()
    fallback_price_rows = [
        (row, market, event, city)
        for row, market, event, city in price_rows
        if row.market_id not in trade_market_ids
    ]
    sampled_source_counts = {
        "data_api_trades": len(trade_rows),
        "clob_prices_history": len(fallback_price_rows),
    }

    rows: list[tuple[HistoricalMarketPoint, Market, Event, City]] = [
        (
            HistoricalMarketPoint(
                ts=row.ts,
                sampled_ts=_parse_hour_bucket(sampled_ts, row.ts),
                market_id=row.market_id,
                price=row.price,
                source="data_api_trades",
            ),
            market,
            event,
            city,
        )
        for row, market, event, city, sampled_ts in trade_rows
    ]
    rows.extend(
        (
            HistoricalMarketPoint(
                ts=row.ts,
                sampled_ts=row.ts,
                market_id=row.market_id,
                price=row.price,
                source="clob_prices_history",
            ),
            market,
            event,
            city,
        )
        for row, market, event, city in fallback_price_rows
    )
    rows.sort(key=lambda item: (item[2].target_date, item[0].ts, item[0].market_id))
    return rows, raw_source_counts, sampled_source_counts


async def _historical_candidates(
    session: AsyncSession, settings: Settings
) -> tuple[list[HistoricalCandidate], int, dict[str, int], dict[str, int], dict[str, int]]:
    rows, raw_source_counts, sampled_source_counts = await _historical_price_rows(
        session, settings
    )
    prob_cache: dict[tuple[str, datetime], object] = {}
    bias_cache: dict[tuple[str, date, int], float] = {}
    candidates: list[HistoricalCandidate] = []
    source_counts: dict[str, int] = {"data_api_trades": 0, "clob_prices_history": 0}

    for price_row, market, event_row, city in rows:
        if event_row.end_date is None or market.winner is None:
            continue
        hours_to_close = (event_row.end_date - price_row.ts).total_seconds() / 3600.0
        if not (settings.min_hours_to_close <= hours_to_close <= settings.max_hours_to_close):
            continue
        if not (Decimal(0) < price_row.price < Decimal(1)):
            continue

        cache_key = (event_row.id, price_row.sampled_ts)
        if cache_key not in prob_cache:
            prob_cache[cache_key] = await _event_historical_probs_at(
                session, settings, event_row, city, price_row.sampled_ts, bias_cache
            )
        event_probs = prob_cache[cache_key]
        if event_probs is None:
            continue
        prob = event_probs.probs_by_market.get(market.id)  # type: ignore[attr-defined]
        if prob is None:
            continue

        source_counts[price_row.source] = source_counts.get(price_row.source, 0) + 1
        candidates.append(
            HistoricalCandidate(
                ts=price_row.ts,
                sampled_ts=price_row.sampled_ts,
                market_id=market.id,
                event_id=event_row.id,
                city_slug=city.slug,
                target_date=event_row.target_date,
                price=price_row.price,
                raw_prob=prob,
                winner=market.winner,
                bucket_kind=market.bucket_kind,
                bucket_label=market.group_item_title,
                hours_to_close=hours_to_close,
                price_source=price_row.source,
            )
        )
    return (
        candidates,
        len(candidates),
        source_counts,
        raw_source_counts,
        sampled_source_counts,
    )


def _filter_reason(
    candidate: HistoricalCandidate,
    *,
    variant: RepairVariant,
    fee_rate: Decimal,
    calibrated_prob: float,
    raw_edge: Decimal,
    calibrated_edge: Decimal,
    calibration_samples: int,
    calibration_segment_key: str | None,
    calibration_observed_rate: float | None,
    calibration_brier_delta: float | None,
) -> str | None:
    px_bucket = price_bucket(candidate.price)
    raw_prob_bucket = probability_bucket(candidate.raw_prob)
    raw_edge_bucket = edge_bucket(raw_edge)
    specific_eligible = (
        variant.repair_v3
        and calibration_segment_key is not None
        and calibration_segment_key.startswith("specific|")
        and calibration_samples >= variant.min_samples
        and calibration_brier_delta is not None
        and calibration_brier_delta > 0
    )
    if px_bucket == "0.00-0.05":
        if (
            specific_eligible
            and calibration_observed_rate is not None
            and Decimal(str(calibration_observed_rate))
            > cost_per_share(candidate.price, fee_rate)
        ):
            return None
        return "price_bucket_0_00_0_05"
    if candidate.bucket_kind == "above" and raw_prob_bucket == "0.9-1.0":
        if specific_eligible:
            return None
        return "above_raw_prob_0_9_1_0"
    if (
        raw_edge_bucket == "0.75+"
        and calibration_samples >= DEFAULT_MIN_SAMPLES
        and candidate.raw_prob - calibrated_prob > 0.15
    ):
        return "extreme_edge_overconfidence"
    if calibrated_edge < Decimal(0):
        return "negative_calibrated_edge"
    return None


def _variant_policy_params(variant: RepairVariant, min_edge_net: Decimal) -> RepairPolicyParams:
    return RepairPolicyParams(
        policy_name=variant.name,
        policy_version=variant.policy_version,
        alpha=variant.alpha,
        probability_cap=variant.probability_cap,
        min_samples=variant.min_samples,
        min_edge_net=min_edge_net,
        segment_scope=variant.segment_scope,
        price_floor=variant.price_floor,
    )


def _segment_stats_from_calibration(
    calibration_segment_key: str | None,
    *,
    n: int,
    wins: int,
    observed_rate: float | None,
    brier_delta: float | None,
    pnl: Decimal,
) -> RepairSegmentStats | None:
    if calibration_segment_key is None or observed_rate is None:
        return None
    return RepairSegmentStats(
        segment_key=calibration_segment_key,
        n=n,
        wins=wins,
        observed_rate=observed_rate,
        brier_delta=brier_delta,
        pnl=pnl,
    )


def _segment_row_final_eligible(row: dict[str, object], *, price_floor: Decimal | None) -> bool:
    if row.get("eligible") is not True:
        return False
    try:
        stats = RepairSegmentStats(
            segment_key=str(row["segment_key"]),
            n=_required_int(row["n"]),
            wins=_required_int(row["wins"]),
            observed_rate=_required_float(row["observed_rate"]),
            brier_delta=(
                None if row.get("brier_delta") is None else _required_float(row["brier_delta"])
            ),
            pnl=Decimal(str(row["pnl"])),
        )
    except (KeyError, ValueError):
        return False
    avg_cost = stats.avg_cost_per_share
    if avg_cost is None or Decimal(str(stats.observed_rate)) <= avg_cost:
        return False
    if price_floor is not None and "|0.00-0.05|" in stats.segment_key:
        return False
    return True


def _simulate_variant(
    candidates: list[HistoricalCandidate],
    settings: Settings,
    variant: RepairVariant,
    *,
    blocked_city_slugs: set[str] | None = None,
    evaluation_start: date | None = None,
    include_decision_audit: bool = False,
) -> tuple[dict[Profile, list[TradeResult]], dict[str, object]]:
    legacy_calibrator = WalkForwardProbabilityCalibrator(
        min_samples=variant.min_samples,
        probability_cap=variant.probability_cap,
    )
    market_calibrator = WalkForwardMarketAwareCalibrator(
        min_samples=variant.min_samples,
        probability_cap=variant.probability_cap,
        alpha=variant.alpha,
        fee_rate=settings.taker_fee_rate,
        segment_scope=variant.segment_scope,
    )
    trades: dict[Profile, list[TradeResult]] = {"longshot": [], "max_edge": []}
    last_signals: dict[tuple[str, Profile], tuple[datetime, Decimal]] = {}
    exposure_by_market_day: defaultdict[tuple[str, object], Decimal] = defaultdict(Decimal)
    blocked_counts: Counter[str] = Counter()
    calibration_sources: Counter[str] = Counter()
    capped_count = 0
    min_edge_net = (
        variant.min_edge_net if variant.min_edge_net is not None else settings.min_edge_net
    )
    policy_params = _variant_policy_params(variant, min_edge_net)
    blocked_city_slugs = blocked_city_slugs or set()
    traded_segment_keys: set[str] = set()
    decision_rows: list[dict[str, object]] = []

    for candidate in candidates:
        in_evaluation_window = (
            evaluation_start is None or candidate.target_date >= evaluation_start
        )
        context = ProbabilityContext(
            city_slug=candidate.city_slug,
            bucket_kind=candidate.bucket_kind,
            model_prob=candidate.raw_prob,
            market_price=candidate.price,
            hours_to_close=candidate.hours_to_close,
            target_date=candidate.target_date,
        )
        if variant.market_aware:
            calibration = market_calibrator.calibrate(context)
            calibrated_prob = calibration.calibrated_prob
            calibration_sources[calibration.source] += 1
            capped_count += 1 if calibration.capped else 0
            is_eligible = calibration.eligible
            ineligible_reason = calibration.reason
            calibration_samples = calibration.n_samples
            calibration_segment_key = calibration.segment_key
            calibration_observed_rate = calibration.observed_rate
            calibration_brier_delta = calibration.brier_delta
            calibration_wins = calibration.wins
            calibration_pnl = calibration.pnl
        else:
            legacy = legacy_calibrator.calibrate(context)
            calibrated_prob = legacy.calibrated_prob if variant.calibrate else candidate.raw_prob
            calibration_sources[legacy.source if variant.calibrate else "raw"] += 1
            capped_count += 1 if variant.calibrate and legacy.capped else 0
            is_eligible = True
            ineligible_reason = None
            calibration_samples = legacy.n_samples
            calibration_segment_key = None
            calibration_observed_rate = legacy.observed_rate
            calibration_brier_delta = None
            calibration_wins = 0
            calibration_pnl = Decimal("0")

        raw_edge = net_edge(candidate.raw_prob, candidate.price, settings.taker_fee_rate)
        calibrated_edge = net_edge(calibrated_prob, candidate.price, settings.taker_fee_rate)
        if not in_evaluation_window:
            _observe_variant(
                variant,
                legacy_calibrator,
                market_calibrator,
                context,
                candidate.winner,
                calibrated_prob,
            )
            continue
        if variant.repair_v4:
            segment_stats = _segment_stats_from_calibration(
                calibration_segment_key,
                n=calibration_samples,
                wins=calibration_wins,
                observed_rate=calibration_observed_rate,
                brier_delta=calibration_brier_delta,
                pnl=calibration_pnl,
            )
            decision = evaluate_repair_policy(
                params=policy_params,
                context=context,
                fee_rate=settings.taker_fee_rate,
                segment=segment_stats,
                global_rate=market_calibrator.global_rate(default=candidate.raw_prob),
            )
            calibrated_prob = decision.calibrated_prob
            calibrated_edge = decision.edge_net
            is_eligible = decision.eligible
            ineligible_reason = decision.reason
            calibration_segment_key = decision.segment_key
            calibration_samples = decision.n_samples
            if include_decision_audit and len(decision_rows) < 200:
                decision_rows.append(
                    {
                        "policy_name": decision.policy_name,
                        "segment_key": decision.segment_key,
                        "eligible": decision.eligible,
                        "reason": decision.reason,
                        "raw_prob": decision.raw_prob,
                        "calibrated_prob": decision.calibrated_prob,
                        "market_price": str(candidate.price),
                        "edge_net": str(decision.edge_net),
                        "ts": candidate.ts.isoformat(),
                        "market_id": candidate.market_id,
                    }
                )
            if not decision.eligible:
                blocked_counts[f"repair_v4_{decision.reason or 'ineligible'}"] += 1
                _observe_variant(
                    variant,
                    legacy_calibrator,
                    market_calibrator,
                    context,
                    candidate.winner,
                    calibrated_prob,
                )
                continue
        if variant.repair_v4 and candidate.city_slug in blocked_city_slugs:
            blocked_counts["city_needs_review"] += 1
            _observe_variant(
                variant,
                legacy_calibrator,
                market_calibrator,
                context,
                candidate.winner,
                calibrated_prob,
            )
            continue
        if variant.repair_v3 and candidate.city_slug in blocked_city_slugs:
            blocked_counts["city_needs_review"] += 1
            _observe_variant(
                variant,
                legacy_calibrator,
                market_calibrator,
                context,
                candidate.winner,
                calibrated_prob,
            )
            continue
        if variant.apply_segment_filters:
            reason = _filter_reason(
                candidate,
                variant=variant,
                fee_rate=settings.taker_fee_rate,
                calibrated_prob=calibrated_prob,
                raw_edge=raw_edge,
                calibrated_edge=calibrated_edge,
                calibration_samples=calibration_samples,
                calibration_segment_key=calibration_segment_key,
                calibration_observed_rate=calibration_observed_rate,
                calibration_brier_delta=calibration_brier_delta,
            )
            if reason is not None:
                blocked_counts[reason] += 1
                _observe_variant(
                    variant,
                    legacy_calibrator,
                    market_calibrator,
                    context,
                    candidate.winner,
                    calibrated_prob,
                )
                continue
        if not is_eligible:
            blocked_counts[f"{variant.policy_version}_{ineligible_reason or 'ineligible'}"] += 1
            _observe_variant(
                variant,
                legacy_calibrator,
                market_calibrator,
                context,
                candidate.winner,
                calibrated_prob,
            )
            continue

        if calibrated_edge < min_edge_net:
            blocked_counts["min_edge_net"] += 1
            _observe_variant(
                variant,
                legacy_calibrator,
                market_calibrator,
                context,
                candidate.winner,
                calibrated_prob,
            )
            continue

        cost = cost_per_share(candidate.price, settings.taker_fee_rate)
        stake = kelly_stake(
            calibrated_prob,
            cost,
            bankroll=settings.bankroll,
            kelly_multiplier=settings.kelly_fraction,
            max_stake_per_order=settings.max_stake_per_order,
        )
        if stake <= 0:
            blocked_counts["kelly_stake_zero"] += 1
            _observe_variant(
                variant,
                legacy_calibrator,
                market_calibrator,
                context,
                candidate.winner,
                calibrated_prob,
            )
            continue

        profiles: list[Profile] = ["max_edge"]
        if candidate.price <= settings.longshot_max_price:
            profiles.append("longshot")

        for profile in profiles:
            if _is_recent_duplicate(
                last_signals,
                market_id=candidate.market_id,
                profile=profile,
                ts=candidate.ts,
                edge_net=calibrated_edge,
            ):
                blocked_counts[f"{profile}_duplicate"] += 1
                continue
            exposure_key = (candidate.market_id, candidate.ts.date())
            if (
                exposure_by_market_day[exposure_key] + stake
                > settings.max_exposure_per_market
            ):
                blocked_counts[f"{profile}_max_exposure_per_market"] += 1
                continue
            trade = _trade_result(
                ts=candidate.ts,
                stake=stake,
                market_price=candidate.price,
                model_prob=calibrated_prob,
                winner=candidate.winner,
                fee_rate=settings.taker_fee_rate,
                market_id=candidate.market_id,
                event_id=candidate.event_id,
                city_slug=candidate.city_slug,
                target_date=candidate.target_date,
                bucket_kind=candidate.bucket_kind,
                bucket_label=candidate.bucket_label,
                edge_net=calibrated_edge,
                hours_to_close=candidate.hours_to_close,
                price_source=candidate.price_source,
            )
            if trade is None:
                continue
            exposure_by_market_day[exposure_key] += stake
            last_signals[(candidate.market_id, profile)] = (candidate.ts, calibrated_edge)
            trades[profile].append(trade)
            if profile == "max_edge" and calibration_segment_key is not None:
                traded_segment_keys.add(calibration_segment_key)

        _observe_variant(
            variant,
            legacy_calibrator,
            market_calibrator,
            context,
            candidate.winner,
            calibrated_prob,
        )

    segment_rows = market_calibrator.snapshot_segments() if variant.market_aware else []
    eligible_segments = sum(1 for row in segment_rows if row.get("eligible") is True)
    if variant.repair_v4:
        for row in segment_rows:
            row["final_eligible"] = _segment_row_final_eligible(
                row,
                price_floor=variant.price_floor,
            )
        final_eligible_segments = sum(
            1 for row in segment_rows if row.get("final_eligible") is True
        )
    else:
        final_eligible_segments = eligible_segments
    metadata = {
        "blocked_counts": dict(blocked_counts),
        "calibration_sources": dict(calibration_sources),
        "capped_probabilities": capped_count,
        "min_calibration_samples": variant.min_samples,
        "probability_cap": variant.probability_cap,
        "policy_name": variant.name,
        "alpha": variant.alpha if variant.market_aware else None,
        "policy_version": variant.policy_version,
        "segment_scope": variant.segment_scope,
        "min_edge_net": str(min_edge_net),
        "repair_v2": variant.repair_v2,
        "repair_v3": variant.repair_v3,
        "repair_v4": variant.repair_v4,
        "price_floor": str(variant.price_floor) if variant.price_floor is not None else None,
        "low_price_mode": "diagnostic_only" if variant.repair_v4 else None,
        "eligible_segments": eligible_segments,
        "final_eligible_segments": final_eligible_segments,
        "walk_forward_traded_segments": len(traded_segment_keys),
        "traded_segments": len(traded_segment_keys),
        "traded_segment_keys": sorted(traded_segment_keys),
        "total_segments": len(segment_rows),
        "decision_audit_sample": decision_rows,
        "_segments": segment_rows,
    }
    return trades, metadata


def _observe_variant(
    variant: RepairVariant,
    legacy_calibrator: WalkForwardProbabilityCalibrator,
    market_calibrator: WalkForwardMarketAwareCalibrator,
    context: ProbabilityContext,
    winner: bool,
    calibrated_prob: float,
) -> None:
    outcome = 1.0 if winner else 0.0
    if variant.market_aware:
        market_calibrator.observe(context, outcome, calibrated_prob)
    else:
        legacy_calibrator.observe(context, outcome)


def _profile_payload(trades: list[TradeResult]) -> dict[str, object]:
    return {
        **_trade_metrics(trades),
        **_bootstrap_metrics(trades),
        **_concentration_metrics(trades),
    }


def _variant_payload(
    *,
    name: str,
    trades_by_profile: dict[Profile, list[TradeResult]],
    metadata: dict[str, object],
) -> dict[str, object]:
    return {
        "name": name,
        "profiles": {
            profile: _profile_payload(trades_by_profile[profile]) for profile in PROFILES
        },
        **metadata,
    }


def _variant_score(variant: dict[str, object]) -> tuple[int, Decimal, Decimal, float]:
    profiles = variant.get("profiles")
    if not isinstance(profiles, dict):
        return (0, Decimal("-999999"), Decimal("-999999"), -999999.0)
    max_edge = profiles.get("max_edge")
    if not isinstance(max_edge, dict):
        return (0, Decimal("-999999"), Decimal("-999999"), -999999.0)
    n_trades = int(max_edge.get("n_resolved_trades") or 0)
    if n_trades <= 0:
        return (-1, Decimal("-999999"), Decimal("-999999"), -999999.0)
    pnl = _as_decimal(max_edge.get("total_pnl")) or Decimal("-999999")
    brier_delta = max_edge.get("brier_delta")
    brier_value = brier_delta if isinstance(brier_delta, int | float) else -999999.0
    concentration = _as_decimal(max_edge.get("top_5_abs_pnl_share"))
    pnl_ci_high = _as_decimal(max_edge.get("pnl_ci_high"))
    gates = 0
    gates += 1 if brier_value > 0 else 0
    gates += 1 if pnl > 0 else 0
    gates += 1 if n_trades >= MIN_HISTORICAL_TRADES else 0
    gates += 1 if concentration is not None and concentration <= MAX_TOP_5_ABS_PNL_SHARE else 0
    gates += 1 if pnl_ci_high is not None and pnl_ci_high >= 0 else 0
    return (gates, Decimal(str(brier_value)), pnl, float(brier_value))


def _variant_has_max_edge_trades(variant: dict[str, object]) -> bool:
    profiles = variant.get("profiles")
    if not isinstance(profiles, dict):
        return False
    max_edge = profiles.get("max_edge")
    if not isinstance(max_edge, dict):
        return False
    return int(max_edge.get("n_resolved_trades") or 0) > 0


async def _city_quality_gate(
    session: AsyncSession,
    settings: Settings,
) -> tuple[bool, dict[str, object]]:
    selected = settings.cities
    query = select(City).where(City.active.is_(True))
    if selected is not None:
        query = query.where(City.slug.in_(selected))
    rows = list((await session.execute(query)).scalars().all())
    missing = sorted(set(selected or []) - {city.slug for city in rows})
    needs_review = sorted(city.slug for city in rows if city.needs_review)
    quarantined = sorted(city.slug for city in rows if is_operationally_quarantined(city.slug))
    return not missing and not needs_review and not quarantined, {
        "missing_cities": missing,
        "needs_review": needs_review,
        "operational_quarantine": quarantine_payloads(set(quarantined)),
    }


def _gates(
    best_variant: dict[str, object],
    city_quality: tuple[bool, dict[str, object]],
    *,
    concentration_threshold: Decimal = MAX_TOP_5_ABS_PNL_SHARE,
) -> dict[str, object]:
    profiles = best_variant.get("profiles") if isinstance(best_variant, dict) else {}
    max_edge = profiles.get("max_edge") if isinstance(profiles, dict) else {}
    max_edge = max_edge if isinstance(max_edge, dict) else {}
    n_trades = int(max_edge.get("n_resolved_trades") or 0)
    pnl = _as_decimal(max_edge.get("total_pnl")) or Decimal("0")
    brier_delta = max_edge.get("brier_delta")
    brier_pass = isinstance(brier_delta, int | float) and float(brier_delta) > 0
    concentration = _as_decimal(max_edge.get("top_5_abs_pnl_share"))
    pnl_ci_high = _as_decimal(max_edge.get("pnl_ci_high"))
    city_pass, city_value = city_quality
    return {
        "max_edge_brier": {
            "passed": brier_pass,
            "value": {"brier_delta": brier_delta},
            "required": {"brier_delta_gt": 0},
        },
        "historical_pnl": {
            "passed": pnl > 0,
            "value": {"max_edge_total_pnl": str(pnl)},
            "required": {"total_pnl_gt": "0"},
        },
        "historical_trades": {
            "passed": n_trades >= MIN_HISTORICAL_TRADES,
            "value": {"max_edge_trades": n_trades},
            "required": {"min_trades": MIN_HISTORICAL_TRADES},
        },
        "concentration": {
            "passed": concentration is not None
            and concentration <= concentration_threshold,
            "value": {
                "top_5_abs_pnl_share": str(concentration) if concentration is not None else None
            },
            "required": {"top_5_abs_pnl_share_lte": str(concentration_threshold)},
        },
        "bootstrap": {
            "passed": pnl_ci_high is not None and pnl_ci_high >= 0,
            "value": {"pnl_ci_high": str(pnl_ci_high) if pnl_ci_high is not None else None},
            "required": {"pnl_ci_high_gte": "0"},
        },
        "city_quality": {
            "passed": city_pass,
            "value": city_value,
            "required": {"needs_review": [], "missing_cities": []},
        },
    }


def _status(gates: dict[str, object], *, no_edge_status: bool = False) -> str:
    if not gates["historical_trades"]["passed"]:  # type: ignore[index]
        return "INSUFFICIENT_HISTORY"
    if all(bool(gate["passed"]) for gate in gates.values() if isinstance(gate, dict)):
        return "PROMISING"
    if no_edge_status:
        return "NO_HISTORICAL_EDGE"
    return "NEEDS_MODEL_REPAIR"


def _repair_variants_for(policy_version: str | None) -> list[RepairVariant]:
    if policy_version == "repair_v2":
        return _repair_v2_variants()
    if policy_version == "repair_v3":
        return _repair_v3_variants()
    if policy_version == "repair_v4":
        return _repair_v4_variants()
    return [*_repair_v2_variants(), *_repair_v3_variants(), *_repair_v4_variants()]


def _month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _candidate_counts_by_month(candidates: list[HistoricalCandidate]) -> dict[str, int]:
    counter: Counter[str] = Counter(_month_key(candidate.target_date) for candidate in candidates)
    return dict(sorted(counter.items()))


def _market_history_span(candidates: list[HistoricalCandidate]) -> dict[str, str] | None:
    if not candidates:
        return None
    dates = [candidate.target_date for candidate in candidates]
    return {"start": min(dates).isoformat(), "end": max(dates).isoformat()}


def _fold_windows(
    span_start: date, span_end: date, config: RollingOriginConfig
) -> list[FoldWindow]:
    windows: list[FoldWindow] = []
    fold_start = span_start + timedelta(days=config.min_train_days)
    index = 0
    while fold_start <= span_end:
        windows.append(
            FoldWindow(
                index=index,
                fold_start=fold_start,
                fold_end=fold_start + timedelta(days=config.fold_days),
            )
        )
        fold_start = fold_start + timedelta(days=config.fold_days)
        index += 1
    return windows


def _fold_validity(
    *,
    train_part: list[HistoricalCandidate],
    n_fold: int,
    train_days: int,
    config: RollingOriginConfig,
) -> tuple[bool, str | None]:
    if train_days < config.min_train_days or len(train_part) < config.min_train_candidates:
        return False, "insufficient_train"
    if n_fold < config.min_fold_candidates:
        return False, "insufficient_fold"
    return True, None


def _rolling_origin_evaluation(
    candidates: list[HistoricalCandidate],
    settings: Settings,
    selected_variants: list[RepairVariant],
    *,
    blocked_city_slugs: set[str],
    config: RollingOriginConfig,
) -> RollingOriginResult:
    span = _market_history_span(candidates)
    counts_by_month = _candidate_counts_by_month(candidates)
    oos_trades: dict[Profile, list[TradeResult]] = {"longshot": [], "max_edge": []}
    folds_info: list[dict[str, object]] = []
    if span is None or not selected_variants:
        return RollingOriginResult(
            selected_variant=None,
            oos_trades=oos_trades,
            folds=folds_info,
            fold_count=0,
            train_variant_payloads=[],
            selected_segments=[],
            selection_train_size=0,
            candidate_counts_by_month=counts_by_month,
            market_history_span=span,
        )

    span_start = date.fromisoformat(span["start"])
    span_end = date.fromisoformat(span["end"])
    windows = _fold_windows(span_start, span_end, config)

    fold_specs: list[tuple[FoldWindow, list[HistoricalCandidate], int, bool, str | None]] = []
    first_valid_index: int | None = None
    for window in windows:
        train_part = [c for c in candidates if c.target_date < window.fold_start]
        n_fold = sum(
            1 for c in candidates if window.fold_start <= c.target_date < window.fold_end
        )
        train_days = (window.fold_start - span_start).days
        valid, reason = _fold_validity(
            train_part=train_part, n_fold=n_fold, train_days=train_days, config=config
        )
        if valid and first_valid_index is None:
            first_valid_index = window.index
        fold_specs.append((window, train_part, n_fold, valid, reason))

    selected_variant: RepairVariant | None = None
    train_variant_payloads: list[dict[str, object]] = []
    selection_train_size = 0
    selection_reason: str | None = None
    if first_valid_index is not None:
        first_window = windows[first_valid_index]
        selection_train = [c for c in candidates if c.target_date < first_window.fold_start]
        selection_train_size = len(selection_train)
        scored_rows: list[tuple[RepairVariant, dict[str, object]]] = []
        for variant in selected_variants:
            trades, metadata = _simulate_variant(
                selection_train,
                settings,
                variant,
                blocked_city_slugs=blocked_city_slugs,
            )
            metadata.pop("_segments", None)
            payload = _variant_payload(
                name=variant.name,
                trades_by_profile=trades,
                metadata={
                    **metadata,
                    "apply_segment_filters": variant.apply_segment_filters,
                    "calibrate": variant.calibrate,
                    "validation_split": "train",
                },
            )
            scored_rows.append((variant, payload))
            train_variant_payloads.append(payload)
        selectable_rows = [
            (variant, payload)
            for variant, payload in scored_rows
            if _variant_has_max_edge_trades(payload)
        ]
        if selectable_rows:
            selected_variant = max(
                selectable_rows,
                key=lambda item: _variant_score(item[1]),
            )[0]
        else:
            selection_reason = "no_selectable_train_variant"

    elif fold_specs:
        selection_reason = "no_valid_fold"

    selected_segments: list[dict[str, object]] = []
    valid_fold_count = 0
    for window, train_part, n_fold, valid, reason in fold_specs:
        entry: dict[str, object] = {
            "index": window.index,
            "train_window": {
                "start": span_start.isoformat(),
                "end": window.fold_start.isoformat(),
            },
            "fold_window": {
                "start": window.fold_start.isoformat(),
                "end": window.fold_end.isoformat(),
            },
            "n_train": len(train_part),
            "n_fold_candidates": n_fold,
            "valid": valid,
            "reason": reason,
            "n_oos_trades": 0,
            "pnl": "0",
            "brier_delta": None,
        }
        if valid and selected_variant is not None:
            fold_input = [c for c in candidates if c.target_date < window.fold_end]
            trades, metadata = _simulate_variant(
                fold_input,
                settings,
                selected_variant,
                blocked_city_slugs=blocked_city_slugs,
                evaluation_start=window.fold_start,
            )
            segment_rows = metadata.pop("_segments", [])
            if isinstance(segment_rows, list):
                selected_segments = [row for row in segment_rows if isinstance(row, dict)]
            for profile in PROFILES:
                oos_trades[profile].extend(trades[profile])
            max_edge_payload = _profile_payload(trades["max_edge"])
            entry["n_oos_trades"] = max_edge_payload.get("n_resolved_trades")
            entry["pnl"] = max_edge_payload.get("total_pnl")
            entry["brier_delta"] = max_edge_payload.get("brier_delta")
            valid_fold_count += 1
        folds_info.append(entry)

    return RollingOriginResult(
        selected_variant=selected_variant,
        oos_trades=oos_trades,
        folds=folds_info,
        fold_count=valid_fold_count,
        train_variant_payloads=train_variant_payloads,
        selected_segments=selected_segments,
        selection_train_size=selection_train_size,
        candidate_counts_by_month=counts_by_month,
        market_history_span=span,
        selection_reason=selection_reason,
    )


def _rolling_origin_gates(
    best_variant: dict[str, object],
    city_quality: tuple[bool, dict[str, object]],
    fold_count: int,
    *,
    min_folds: int,
) -> dict[str, object]:
    gates = _gates(
        best_variant,
        city_quality,
        concentration_threshold=ROLLING_ORIGIN_CONCENTRATION,
    )
    gates["valid_folds"] = {
        "passed": fold_count >= min_folds,
        "value": {"valid_folds": fold_count},
        "required": {"min_valid_folds": min_folds},
    }
    return gates


def _rolling_origin_status(
    gates: dict[str, object], fold_count: int, *, min_folds: int
) -> tuple[str, str | None]:
    if fold_count < min_folds:
        return "INSUFFICIENT_HISTORY", "few_valid_folds"
    if all(bool(gate["passed"]) for gate in gates.values() if isinstance(gate, dict)):
        return "PROMISING", None
    if not gates["historical_trades"]["passed"]:  # type: ignore[index]
        return "NO_HISTORICAL_EDGE", "insufficient_oos_trades"
    return "NO_HISTORICAL_EDGE", "no_oos_edge"


async def _persist_repair_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_at: datetime,
    status: str,
    window_start: date,
    window_end: date,
    cities: list[str] | None,
    summary: dict[str, object],
    baseline: dict[str, object],
    variants: list[dict[str, object]],
    best_variant: dict[str, object],
    gates: dict[str, object],
    best_policy_name: str,
    best_segments: list[dict[str, object]],
) -> StrategyRepairRun:
    async with session_factory() as session, session.begin():
        row = StrategyRepairRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            cities_json=_json(cities or []),
            summary_json=_json(summary),
            baseline_json=_json(baseline),
            variants_json=_json(variants),
            best_variant_json=_json(best_variant),
            gates_json=_json(gates),
        )
        session.add(row)
        await session.flush()
        for segment in best_segments:
            session.add(
                StrategyCalibrationSegment(
                    run_id=row.id,
                    policy_name=best_policy_name,
                    segment_key=str(segment["segment_key"]),
                    n=_required_int(segment["n"]),
                    wins=_required_int(segment["wins"]),
                    observed_rate=_required_float(segment["observed_rate"]),
                    brier_delta=(
                        None
                        if segment.get("brier_delta") is None
                        else _required_float(segment["brier_delta"])
                    ),
                    pnl=Decimal(str(segment["pnl"])),
                    eligible=(
                        segment.get("final_eligible")
                        if best_policy_name.startswith("repair_v4")
                        else segment.get("eligible")
                    )
                    is True,
                    alpha=_required_float(segment["alpha"]),
                    cap=_required_float(segment["cap"]),
                    min_samples=_required_int(segment["min_samples"]),
                )
            )
        logger.info(
            "strategy repair: status=%s best=%s pnl=%s",
            status,
            best_variant["name"],
            summary.get("best_variant_pnl"),
        )
        return row


async def _generate_rolling_origin_report(
    session_factory: async_sessionmaker[AsyncSession],
    run_settings: Settings,
    *,
    run_at: datetime,
    window_start: date,
    window_end: date,
    selected_cities: list[str] | None,
    policy_version: str | None,
    config: RollingOriginConfig,
) -> StrategyRepairRun:
    async with session_factory() as session:
        (
            baseline_trades,
            _baseline_candidates,
            _baseline_sources,
            _baseline_raw_sources,
            _baseline_sampled_sources,
        ) = await _historical_price_profile_trades(session, run_settings)
        (
            candidates,
            n_candidate_price_points,
            price_source_counts,
            raw_price_source_counts,
            sampled_price_source_counts,
        ) = await _historical_candidates(session, run_settings)
        city_quality = await _city_quality_gate(session, run_settings)

    blocked_city_slugs: set[str] = set()
    needs_review_value = city_quality[1].get("needs_review")
    if isinstance(needs_review_value, list):
        blocked_city_slugs = {str(city) for city in needs_review_value}

    baseline = _variant_payload(
        name="baseline",
        trades_by_profile=baseline_trades,
        metadata={
            "calibration_sources": {"raw": n_candidate_price_points},
            "blocked_counts": {},
            "is_baseline": True,
        },
    )

    selected_variants = _repair_variants_for(policy_version)
    rolling = _rolling_origin_evaluation(
        candidates,
        run_settings,
        selected_variants,
        blocked_city_slugs=blocked_city_slugs,
        config=config,
    )
    selected_variant = rolling.selected_variant
    selected_name = selected_variant.name if selected_variant is not None else "rolling_origin"
    min_edge_net = (
        selected_variant.min_edge_net
        if selected_variant is not None and selected_variant.min_edge_net is not None
        else run_settings.min_edge_net
    )
    oos_metadata: dict[str, object] = {
        "policy_name": selected_name,
        "policy_version": (
            selected_variant.policy_version if selected_variant is not None else policy_version
        ),
        "alpha": selected_variant.alpha if selected_variant is not None else None,
        "probability_cap": (
            selected_variant.probability_cap
            if selected_variant is not None
            else DEFAULT_PROBABILITY_CAP
        ),
        "min_calibration_samples": (
            selected_variant.min_samples if selected_variant is not None else DEFAULT_MIN_SAMPLES
        ),
        "min_edge_net": str(min_edge_net),
        "segment_scope": (
            selected_variant.segment_scope if selected_variant is not None else "specific_only"
        ),
        "price_floor": (
            str(selected_variant.price_floor)
            if selected_variant is not None and selected_variant.price_floor is not None
            else None
        ),
        "low_price_mode": "diagnostic_only",
        "apply_segment_filters": (
            selected_variant.apply_segment_filters if selected_variant is not None else True
        ),
        "calibrate": selected_variant.calibrate if selected_variant is not None else True,
        "validation_split": "oos",
        "repair_v4": selected_variant.repair_v4 if selected_variant is not None else False,
        "blocked_counts": {},
        "calibration_sources": {},
    }
    best_variant = _variant_payload(
        name=f"{selected_name}_oos",
        trades_by_profile=rolling.oos_trades,
        metadata=oos_metadata,
    )
    best_variant = {
        **best_variant,
        "policy_name": selected_name,
        "execution_proxy": HISTORICAL_TRADE_EXECUTION_PROXY,
        "price_sampling": HISTORICAL_TRADE_PRICE_SAMPLING,
        "n_candidate_price_points": n_candidate_price_points,
        "n_raw_price_points": sum(raw_price_source_counts.values()),
        "n_sampled_price_points": sum(sampled_price_source_counts.values()),
        "price_source_counts": price_source_counts,
        "price_source_raw_counts": raw_price_source_counts,
        "price_source_sampled_counts": sampled_price_source_counts,
        "validation_scheme": "rolling-origin",
        "market_history_span": rolling.market_history_span,
        "selected_policy_name": selected_name,
    }

    variants = [baseline, *rolling.train_variant_payloads, best_variant]
    gates = _rolling_origin_gates(
        best_variant, city_quality, rolling.fold_count, min_folds=config.min_folds
    )
    status, insufficient_reason = _rolling_origin_status(
        gates, rolling.fold_count, min_folds=config.min_folds
    )

    baseline_max_edge = baseline["profiles"]["max_edge"]  # type: ignore[index]
    best_max_edge = best_variant["profiles"]["max_edge"]  # type: ignore[index]
    summary = {
        "preferred_profile": "max_edge",
        "best_variant": best_variant["name"],
        "baseline_pnl": baseline_max_edge["total_pnl"],
        "best_variant_pnl": best_max_edge["total_pnl"],
        "baseline_brier_delta": baseline_max_edge["brier_delta"],
        "best_variant_brier_delta": best_max_edge["brier_delta"],
        "probability_cap": best_variant.get("probability_cap", DEFAULT_PROBABILITY_CAP),
        "min_calibration_samples": best_variant.get(
            "min_calibration_samples", DEFAULT_MIN_SAMPLES
        ),
        "policy_name": selected_name,
        "policy_version": best_variant.get("policy_version"),
        "alpha": best_variant.get("alpha"),
        "min_edge_net": best_variant.get("min_edge_net"),
        "eligible_segments": best_variant.get("eligible_segments"),
        "traded_segments": best_variant.get("traded_segments"),
        "total_segments": best_variant.get("total_segments"),
        "price_floor": best_variant.get("price_floor"),
        "low_price_mode": best_variant.get("low_price_mode"),
        "final_eligible_segments": best_variant.get("final_eligible_segments"),
        "walk_forward_traded_segments": best_variant.get("walk_forward_traded_segments"),
        "validation_scheme": "rolling-origin",
        "market_history_span": rolling.market_history_span,
        "candidate_counts_by_month": rolling.candidate_counts_by_month,
        "fold_count": rolling.fold_count,
        "fold_days": config.fold_days,
        "min_train_days": config.min_train_days,
        "selection_train_size": rolling.selection_train_size,
        "selection_reason": rolling.selection_reason,
        "folds": rolling.folds,
        "oos_profiles": best_variant.get("profiles"),
        "selected_policy_name": selected_name,
        "insufficient_reason": rolling.selection_reason or insufficient_reason,
        "no_edge_reason": (
            rolling.selection_reason or insufficient_reason
            if status == "NO_HISTORICAL_EDGE"
            else None
        ),
    }

    best_segments = rolling.selected_segments if status != "INSUFFICIENT_HISTORY" else []
    return await _persist_repair_run(
        session_factory,
        run_at=run_at,
        status=status,
        window_start=window_start,
        window_end=window_end,
        cities=selected_cities or run_settings.cities,
        summary=summary,
        baseline=baseline,
        variants=variants,
        best_variant=best_variant,
        gates=gates,
        best_policy_name=selected_name,
        best_segments=best_segments,
    )


async def generate_strategy_repair_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
    holdout_days: int | None = None,
    policy_version: str | None = "repair_v4",
    validation_scheme: ValidationScheme = DEFAULT_VALIDATION_SCHEME,
    fold_days: int = DEFAULT_FOLD_DAYS,
    min_train_days: int = DEFAULT_MIN_TRAIN_DAYS,
    min_train_candidates: int = DEFAULT_MIN_TRAIN_CANDIDATES,
    min_fold_candidates: int = DEFAULT_MIN_FOLD_CANDIDATES,
) -> StrategyRepairRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    selected_cities = cities if cities is not None else settings.cities
    run_settings = settings.model_copy(
        update={"cities": selected_cities, "validation_history_days": history_days}
    )
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)

    if validation_scheme == "rolling-origin":
        return await _generate_rolling_origin_report(
            session_factory,
            run_settings,
            run_at=run_at,
            window_start=window_start,
            window_end=window_end,
            selected_cities=selected_cities,
            policy_version=policy_version,
            config=RollingOriginConfig(
                fold_days=fold_days,
                min_train_days=min_train_days,
                min_train_candidates=min_train_candidates,
                min_fold_candidates=min_fold_candidates,
            ),
        )

    holdout_start = (
        window_end - timedelta(days=holdout_days)
        if holdout_days is not None and holdout_days > 0
        else None
    )

    async with session_factory() as session:
        (
            baseline_trades,
            _baseline_candidates,
            _baseline_sources,
            _baseline_raw_sources,
            _baseline_sampled_sources,
        ) = await _historical_price_profile_trades(session, run_settings)
        (
            candidates,
            n_candidate_price_points,
            price_source_counts,
            raw_price_source_counts,
            sampled_price_source_counts,
        ) = await _historical_candidates(session, run_settings)
        city_quality = await _city_quality_gate(session, run_settings)
    city_quality_value = city_quality[1]
    blocked_city_slugs = set()
    needs_review_value = city_quality_value.get("needs_review")
    if isinstance(needs_review_value, list):
        blocked_city_slugs = {str(city) for city in needs_review_value}

    baseline = _variant_payload(
        name="baseline",
        trades_by_profile=baseline_trades,
        metadata={
            "calibration_sources": {"raw": n_candidate_price_points},
            "blocked_counts": {},
            "is_baseline": True,
        },
    )
    variants = [baseline]
    segments_by_variant: dict[str, list[dict[str, object]]] = {}
    selected_variants = _repair_variants_for(policy_version)
    if policy_version is None:
        selected_variants = [*REPAIR_VARIANTS, *selected_variants]
    elif policy_version == "legacy":
        selected_variants = list(REPAIR_VARIANTS)

    train_candidates = (
        [candidate for candidate in candidates if candidate.target_date < holdout_start]
        if holdout_start is not None
        else candidates
    )
    train_variant_rows: list[tuple[RepairVariant, dict[str, object]]] = []
    for variant in selected_variants:
        repaired_trades, metadata = _simulate_variant(
            train_candidates,
            run_settings,
            variant,
            blocked_city_slugs=blocked_city_slugs,
        )
        segment_rows = metadata.pop("_segments", [])
        if isinstance(segment_rows, list):
            segments_by_variant[variant.name] = [
                row for row in segment_rows if isinstance(row, dict)
            ]
        variant_payload = _variant_payload(
            name=variant.name,
            trades_by_profile=repaired_trades,
            metadata={
                **metadata,
                "apply_segment_filters": variant.apply_segment_filters,
                "calibrate": variant.calibrate,
                "validation_split": "train" if holdout_start is not None else "full",
            },
        )
        train_variant_rows.append((variant, variant_payload))
        variants.append(variant_payload)

    if holdout_start is not None and policy_version == "repair_v4":
        selected_variant, train_best_variant = max(
            train_variant_rows,
            key=lambda item: _variant_score(item[1]),
            default=(selected_variants[0], baseline),
        )
        repaired_trades, metadata = _simulate_variant(
            candidates,
            run_settings,
            selected_variant,
            blocked_city_slugs=blocked_city_slugs,
            evaluation_start=holdout_start,
            include_decision_audit=True,
        )
        segment_rows = metadata.pop("_segments", [])
        if isinstance(segment_rows, list):
            segments_by_variant[selected_variant.name] = [
                row for row in segment_rows if isinstance(row, dict)
            ]
        best_variant = _variant_payload(
            name=f"{selected_variant.name}_holdout",
            trades_by_profile=repaired_trades,
            metadata={
                **metadata,
                "policy_name": selected_variant.name,
                "apply_segment_filters": selected_variant.apply_segment_filters,
                "calibrate": selected_variant.calibrate,
                "validation_split": "holdout",
                "train_variant": train_best_variant,
            },
        )
        variants.append(best_variant)
    else:
        repair_variants = [
            variant for _variant, variant in train_variant_rows if variant["name"] != "baseline"
        ]
        best_variant = max(repair_variants, key=_variant_score, default=baseline)
    best_policy_name = str(best_variant.get("policy_name") or best_variant["name"])
    best_segments = segments_by_variant.get(best_policy_name, [])
    gates = _gates(
        best_variant,
        city_quality,
        concentration_threshold=Decimal("0.40")
        if policy_version == "repair_v4" and holdout_start is not None
        else MAX_TOP_5_ABS_PNL_SHARE,
    )
    status = _status(
        gates,
        no_edge_status=policy_version == "repair_v4" and holdout_start is not None,
    )
    best_variant = {
        **best_variant,
        "policy_name": best_policy_name,
        "execution_proxy": HISTORICAL_TRADE_EXECUTION_PROXY,
        "price_sampling": HISTORICAL_TRADE_PRICE_SAMPLING,
        "n_candidate_price_points": n_candidate_price_points,
        "n_raw_price_points": sum(raw_price_source_counts.values()),
        "n_sampled_price_points": sum(sampled_price_source_counts.values()),
        "price_source_counts": price_source_counts,
        "price_source_raw_counts": raw_price_source_counts,
        "price_source_sampled_counts": sampled_price_source_counts,
        "validation_scheme": "holdout" if holdout_start is not None else "walk_forward",
        "train_window": (
            {
                "start": window_start.isoformat(),
                "end": (holdout_start - timedelta(days=1)).isoformat(),
            }
            if holdout_start is not None
            else None
        ),
        "holdout_window": (
            {"start": holdout_start.isoformat(), "end": window_end.isoformat()}
            if holdout_start is not None
            else None
        ),
    }
    baseline_max_edge = baseline["profiles"]["max_edge"]  # type: ignore[index]
    best_max_edge = best_variant["profiles"]["max_edge"]  # type: ignore[index]
    summary = {
        "preferred_profile": "max_edge",
        "best_variant": best_variant["name"],
        "baseline_pnl": baseline_max_edge["total_pnl"],
        "best_variant_pnl": best_max_edge["total_pnl"],
        "baseline_brier_delta": baseline_max_edge["brier_delta"],
        "best_variant_brier_delta": best_max_edge["brier_delta"],
        "probability_cap": best_variant.get("probability_cap", DEFAULT_PROBABILITY_CAP),
        "min_calibration_samples": best_variant.get(
            "min_calibration_samples", DEFAULT_MIN_SAMPLES
        ),
        "policy_name": best_policy_name,
        "policy_version": best_variant.get("policy_version"),
        "alpha": best_variant.get("alpha"),
        "min_edge_net": best_variant.get("min_edge_net"),
        "eligible_segments": best_variant.get("eligible_segments"),
        "traded_segments": best_variant.get("traded_segments"),
        "total_segments": best_variant.get("total_segments"),
        "validation_scheme": best_variant.get("validation_scheme"),
        "train_window": best_variant.get("train_window"),
        "holdout_window": best_variant.get("holdout_window"),
        "price_floor": best_variant.get("price_floor"),
        "low_price_mode": best_variant.get("low_price_mode"),
        "final_eligible_segments": best_variant.get("final_eligible_segments"),
        "walk_forward_traded_segments": best_variant.get("walk_forward_traded_segments"),
        "no_edge_reason": None
        if status == "PROMISING"
        else "holdout_gates_failed"
        if policy_version == "repair_v4" and holdout_start is not None
        else None,
    }

    async with session_factory() as session, session.begin():
        row = StrategyRepairRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            cities_json=_json(selected_cities or run_settings.cities or []),
            summary_json=_json(summary),
            baseline_json=_json(baseline),
            variants_json=_json(variants),
            best_variant_json=_json(best_variant),
            gates_json=_json(gates),
        )
        session.add(row)
        await session.flush()
        for segment in best_segments:
            session.add(
                StrategyCalibrationSegment(
                    run_id=row.id,
                    policy_name=best_policy_name,
                    segment_key=str(segment["segment_key"]),
                    n=_required_int(segment["n"]),
                    wins=_required_int(segment["wins"]),
                    observed_rate=_required_float(segment["observed_rate"]),
                    brier_delta=(
                        None
                        if segment.get("brier_delta") is None
                        else _required_float(segment["brier_delta"])
                    ),
                    pnl=Decimal(str(segment["pnl"])),
                    eligible=(
                        segment.get("final_eligible")
                        if best_policy_name.startswith("repair_v4")
                        else segment.get("eligible")
                    )
                    is True,
                    alpha=_required_float(segment["alpha"]),
                    cap=_required_float(segment["cap"]),
                    min_samples=_required_int(segment["min_samples"]),
                )
            )
        logger.info(
            "strategy repair: status=%s best=%s pnl=%s",
            status,
            best_variant["name"],
            summary["best_variant_pnl"],
        )
        return row


async def run(
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
    holdout_days: int | None = None,
    policy_version: str | None = "repair_v4",
    validation_scheme: ValidationScheme = DEFAULT_VALIDATION_SCHEME,
    fold_days: int = DEFAULT_FOLD_DAYS,
    min_train_days: int = DEFAULT_MIN_TRAIN_DAYS,
    min_train_candidates: int = DEFAULT_MIN_TRAIN_CANDIDATES,
    min_fold_candidates: int = DEFAULT_MIN_FOLD_CANDIDATES,
) -> StrategyRepairRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_strategy_repair_report(
            session_factory,
            settings,
            cities=cities,
            days=days,
            holdout_days=holdout_days,
            policy_version=policy_version,
            validation_scheme=validation_scheme,
            fold_days=fold_days,
            min_train_days=min_train_days,
            min_train_candidates=min_train_candidates,
            min_fold_candidates=min_fold_candidates,
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run strategy repair validation.")
    parser.add_argument("--cities", help="Comma-separated city slugs.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--holdout-days", type=int, default=None)
    parser.add_argument(
        "--policy-version",
        choices=["repair_v2", "repair_v3", "repair_v4"],
        default="repair_v4",
    )
    parser.add_argument(
        "--validation-scheme",
        choices=["fixed-holdout", "rolling-origin"],
        default=DEFAULT_VALIDATION_SCHEME,
    )
    parser.add_argument("--fold-days", type=int, default=DEFAULT_FOLD_DAYS)
    parser.add_argument("--min-train-days", type=int, default=DEFAULT_MIN_TRAIN_DAYS)
    parser.add_argument(
        "--min-train-candidates", type=int, default=DEFAULT_MIN_TRAIN_CANDIDATES
    )
    parser.add_argument(
        "--min-fold-candidates", type=int, default=DEFAULT_MIN_FOLD_CANDIDATES
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def _run_to_jsonable(row: StrategyRepairRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "cities": json.loads(row.cities_json),
        "summary": json.loads(row.summary_json),
        "baseline": json.loads(row.baseline_json),
        "variants": json.loads(row.variants_json),
        "best_variant": json.loads(row.best_variant_json),
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
            holdout_days=args.holdout_days,
            policy_version=args.policy_version,
            validation_scheme=args.validation_scheme,
            fold_days=args.fold_days,
            min_train_days=args.min_train_days,
            min_train_candidates=args.min_train_candidates,
            min_fold_candidates=args.min_fold_candidates,
        )
    )
    if args.json:
        print(json.dumps(_run_to_jsonable(row), sort_keys=True))


if __name__ == "__main__":
    main()
