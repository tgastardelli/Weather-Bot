"""Audit the latest discovery candidate before repair_v5 or shadow paper.

This module is diagnostic-only. It never writes signals, paper orders, fills,
or live-readiness approvals.
"""

import argparse
import asyncio
import json
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.backtest import (
    Profile,
    TradeResult,
    _bootstrap_metrics,
    _concentration_metrics,
    _is_recent_duplicate,
    _trade_metrics,
    _trade_result,
)
from analysis.historical_validation import MIN_HISTORICAL_TRADES
from analysis.strategy_discovery import (
    DEFAULT_MIN_FOLD_CANDIDATES,
    DEFAULT_MIN_TRAIN_CANDIDATES,
    MAX_TOP_CITY_PNL_SHARE,
    DiscoveryVariant,
    SegmentStats,
    _build_segments,
    _calibrated_probability,
    _fold_windows,
    _profile_payload,
    _reason,
    _research_cities,
    _score,
    _specific_segment_key,
    _variant_payload,
    _variants,
)
from analysis.strategy_repair import HistoricalCandidate, _historical_candidates
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    City,
    DailyObservedMax,
    DiscoveryCandidateAuditRun,
    Event,
    Market,
    PaperFill,
    PaperOrder,
    Signal,
    StrategyDiscoveryRun,
)
from app.db.session import create_engine, create_session_factory
from app.strategy.edge import cost_per_share, net_edge
from app.strategy.sizing import kelly_stake

logger = logging.getLogger(__name__)

AUDIT_SOURCE = "discovery_candidate_audit"
MAX_TOP_CITY_SHARE = MAX_TOP_CITY_PNL_SHARE
MIN_SINGLE_CITY_TRADES = 100
MIN_VALID_FOLDS = 6
SAMPLE_LIMIT = 12
_UNIT_C_RE = re.compile(r"(?:°|\bdeg\.?\s*)C\b|celsius", re.IGNORECASE)
_UNIT_F_RE = re.compile(r"(?:°|\bdeg\.?\s*)F\b|fahrenheit", re.IGNORECASE)


@dataclass(frozen=True)
class AuditDecision:
    candidate: HistoricalCandidate
    segment_key: str
    calibrated_prob: float
    edge_net: Decimal
    reason: str | None
    trade: TradeResult | None


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


def _loads(raw: str | None) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


async def _latest_discovery_candidate(session: AsyncSession) -> StrategyDiscoveryRun | None:
    return (
        await session.execute(
            select(StrategyDiscoveryRun)
            .where(StrategyDiscoveryRun.status == "DISCOVERY_CANDIDATE")
            .order_by(StrategyDiscoveryRun.run_at.desc(), StrategyDiscoveryRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _simulate_with_decisions(
    candidates: list[HistoricalCandidate],
    segments: dict[str, SegmentStats],
    variant: DiscoveryVariant,
    settings: Settings,
) -> tuple[list[TradeResult], list[AuditDecision], dict[str, object]]:
    trades: list[TradeResult] = []
    decisions: list[AuditDecision] = []
    blocked: Counter[str] = Counter()
    last_signals: dict[tuple[str, Profile], tuple[datetime, Decimal]] = {}
    exposure_by_market_day: defaultdict[tuple[str, object], Decimal] = defaultdict(Decimal)

    for candidate in candidates:
        segment_key = _specific_segment_key(candidate)
        segment = segments.get(segment_key)
        reason = _reason(candidate, segment, variant, settings.taker_fee_rate)
        calibrated_prob = (
            _calibrated_probability(candidate, segment, variant)
            if segment is not None
            else candidate.raw_prob
        )
        edge = net_edge(calibrated_prob, candidate.price, settings.taker_fee_rate)
        trade: TradeResult | None = None
        if reason is None:
            stake = kelly_stake(
                calibrated_prob,
                cost_per_share(candidate.price, settings.taker_fee_rate),
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
        decisions.append(
            AuditDecision(
                candidate=candidate,
                segment_key=segment_key,
                calibrated_prob=calibrated_prob,
                edge_net=edge,
                reason=reason,
                trade=trade,
            )
        )

    return trades, decisions, {"blocked_counts": dict(sorted(blocked.items()))}


def _rolling_audit(
    candidates: list[HistoricalCandidate],
    settings: Settings,
    *,
    discovery_version: str,
) -> tuple[list[TradeResult], list[AuditDecision], list[dict[str, object]], dict[str, object]]:
    variants = _variants(discovery_version)
    all_trades: list[TradeResult] = []
    all_decisions: list[AuditDecision] = []
    folds: list[dict[str, object]] = []
    selected_families: Counter[str] = Counter()
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
                    "valid": False,
                    "reason": "insufficient_candidates",
                    "fold_window": {"start": fold_start.isoformat(), "end": fold_end.isoformat()},
                    "n_train": len(train),
                    "n_fold_candidates": len(fold),
                }
            )
            continue

        train_segments = _build_segments(train, settings.taker_fee_rate)
        train_payloads: list[dict[str, object]] = []
        for variant in variants:
            train_trades, _, train_metadata = _simulate_with_decisions(
                train, train_segments, variant, settings
            )
            train_payloads.append(
                _variant_payload(
                    variant,
                    train_trades,
                    train_metadata,
                    include_bootstrap=False,
                )
            )
        selected = max(train_payloads, key=_score)
        selected_variant = next(
            variant for variant in variants if variant.name == selected["name"]
        )
        fold_trades, fold_decisions, fold_metadata = _simulate_with_decisions(
            fold, train_segments, selected_variant, settings
        )
        valid_folds += 1
        selected_families[selected_variant.family] += 1
        all_trades.extend(fold_trades)
        all_decisions.extend(fold_decisions)
        fold_profile = _profile_payload(fold_trades)
        folds.append(
            {
                "index": index,
                "valid": True,
                "selected_family": selected_variant.family,
                "selected_variant": selected_variant.name,
                "fold_window": {"start": fold_start.isoformat(), "end": fold_end.isoformat()},
                "n_train": len(train),
                "n_fold_candidates": len(fold),
                "n_oos_trades": len(fold_trades),
                "pnl": fold_profile["total_pnl"],
                "brier_delta": fold_profile["brier_delta"],
                "blocked_counts": fold_metadata["blocked_counts"],
            }
        )

    return all_trades, all_decisions, folds, {
        "valid_folds": valid_folds,
        "fold_count": len(folds),
        "selected_families": dict(selected_families),
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
        "by_city_pnl": {
            key: str(value.quantize(Decimal("0.01"))) for key, value in by_city.items()
        },
    }


def _group_metrics(
    decisions: list[AuditDecision],
    key_fn: Callable[[AuditDecision], object],
    *,
    limit: int = 20,
) -> list[dict[str, object]]:
    groups: defaultdict[str, list[TradeResult]] = defaultdict(list)
    for decision in decisions:
        if decision.trade is None:
            continue
        key = key_fn(decision)
        if key is not None:
            groups[str(key)].append(decision.trade)
    rows = [
        {
            "segment": key,
            **_trade_metrics(trades),
            **_concentration_metrics(trades),
        }
        for key, trades in groups.items()
    ]
    rows.sort(key=lambda row: Decimal(str(row.get("total_pnl") or "0")))
    return rows[:limit]


def _timing_audit(decisions: list[AuditDecision]) -> dict[str, object]:
    effective = [decision for decision in decisions if decision.trade is not None]
    invalid = [
        decision
        for decision in effective
        if decision.candidate.hours_to_close < 0 or decision.candidate.ts is None
    ]
    raw_after_close = sum(1 for decision in decisions if decision.candidate.hours_to_close < 0)
    return {
        "valid": len(invalid) == 0,
        "effective_after_close": len(invalid),
        "raw_discardable_after_close": raw_after_close - len(invalid),
        "sample": [
            {
                "ts": decision.candidate.ts.isoformat(),
                "market_id": decision.candidate.market_id,
                "city_slug": decision.candidate.city_slug,
                "hours_to_close": decision.candidate.hours_to_close,
                "reason": decision.reason,
                "would_trade": decision.trade is not None,
            }
            for decision in invalid[:SAMPLE_LIMIT]
        ],
    }


def _to_market_unit(value_c: float, unit: str) -> Decimal:
    value = Decimal(str(value_c))
    if unit == "F":
        value = (value * Decimal(9) / Decimal(5)) + Decimal(32)
    return value


def _market_unit(market: Market, fallback_unit: str) -> str:
    text = f"{market.group_item_title or ''} {market.question or ''}"
    if _UNIT_C_RE.search(text):
        return "C"
    if _UNIT_F_RE.search(text):
        return "F"
    return "F" if fallback_unit == "F" else "C"


def _round_observed(value: Decimal, rounding: str) -> Decimal:
    if rounding == "floor":
        return value.to_integral_value(rounding=ROUND_FLOOR)
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _market_expected_winner(market: Market, observed_value: Decimal) -> bool | None:
    if market.bucket_kind == "below" and market.bucket_high is not None:
        return observed_value <= market.bucket_high
    if market.bucket_kind == "above" and market.bucket_low is not None:
        return observed_value >= market.bucket_low
    if market.bucket_kind == "exact" and market.bucket_low is not None:
        return observed_value == market.bucket_low
    if (
        market.bucket_kind == "range"
        and market.bucket_low is not None
        and market.bucket_high is not None
    ):
        return market.bucket_low <= observed_value <= market.bucket_high
    return None


def _int_value(value: object) -> int:
    return int(value) if isinstance(value, int) else 0


async def _city_resolution_audit(
    session: AsyncSession,
    *,
    traded_cities: set[str],
    research_only: set[str],
    window_start: date,
    window_end: date,
) -> dict[str, object]:
    audited_cities = sorted(traded_cities & research_only)
    if not audited_cities:
        return {"valid": True, "audited_cities": [], "cities": [], "issues": []}

    city_rows = (
        await session.execute(select(City).where(City.slug.in_(audited_cities)))
    ).scalars().all()
    city_by_slug = {city.slug: city for city in city_rows}
    events = (
        await session.execute(
            select(Event)
            .where(
                Event.city_slug.in_(audited_cities),
                Event.target_date >= window_start,
                Event.target_date <= window_end,
            )
            .order_by(Event.city_slug, Event.target_date)
        )
    ).scalars().all()
    event_by_id = {event.id: event for event in events}
    markets = (
        await session.execute(
            select(Market).where(Market.event_id.in_([event.id for event in events]))
        )
    ).scalars().all()
    observed_rows = (
        await session.execute(
            select(DailyObservedMax).where(
                DailyObservedMax.city_slug.in_(audited_cities),
                DailyObservedMax.target_date >= window_start,
                DailyObservedMax.target_date <= window_end,
                DailyObservedMax.source.in_(["resolution", "era5", "metar"]),
            )
        )
    ).scalars().all()
    source_counts: dict[str, Counter[str]] = {city: Counter() for city in audited_cities}
    priority = {"resolution": 3, "era5": 2, "metar": 1}
    observed: dict[tuple[str, date], DailyObservedMax] = {}
    for observed_row in observed_rows:
        source_counts.setdefault(observed_row.city_slug, Counter())[observed_row.source] += 1
        key = (observed_row.city_slug, observed_row.target_date)
        current = observed.get(key)
        if current is None or priority.get(observed_row.source, 0) > priority.get(
            current.source, 0
        ):
            observed[key] = observed_row

    totals: dict[str, dict[str, object]] = {
        city: {
            "city_slug": city,
            "needs_review": city_by_slug[city].needs_review if city in city_by_slug else True,
            "station_code": city_by_slug[city].station_code if city in city_by_slug else None,
            "unit": city_by_slug[city].unit if city in city_by_slug else None,
            "rounding": city_by_slug[city].rounding if city in city_by_slug else None,
            "resolution_source": (
                city_by_slug[city].resolution_source if city in city_by_slug else None
            ),
            "audited_markets": 0,
            "mismatches": 0,
            "missing_observations": 0,
            "unknown_bucket_shape": 0,
            "observed_source_counts": dict(source_counts.get(city, Counter())),
            "resolution_points": source_counts.get(city, Counter()).get("resolution", 0),
            "resolution_source_used": None,
            "used_observed_source_counts": {},
            "sample_mismatches": [],
        }
        for city in audited_cities
    }

    for market in markets:
        event = event_by_id.get(market.event_id)
        if event is None:
            continue
        city = city_by_slug.get(event.city_slug)
        city_totals = totals[event.city_slug]
        obs = observed.get((event.city_slug, event.target_date))
        if obs is None:
            city_totals["missing_observations"] = (
                _int_value(city_totals["missing_observations"]) + 1
            )
            continue
        if city is None:
            continue
        used_counts = city_totals["used_observed_source_counts"]
        if isinstance(used_counts, dict):
            used_counts[obs.source] = int(used_counts.get(obs.source, 0)) + 1
            city_totals["resolution_source_used"] = max(
                used_counts,
                key=lambda key: priority.get(str(key), 0),
            )
        market_unit = _market_unit(market, city.unit)
        observed_value = _round_observed(_to_market_unit(obs.tmax_c, market_unit), city.rounding)
        expected = _market_expected_winner(market, observed_value)
        if expected is None or market.winner is None:
            city_totals["unknown_bucket_shape"] = (
                _int_value(city_totals["unknown_bucket_shape"]) + 1
            )
            continue
        city_totals["audited_markets"] = _int_value(city_totals["audited_markets"]) + 1
        if expected != market.winner:
            city_totals["mismatches"] = _int_value(city_totals["mismatches"]) + 1
            sample = city_totals["sample_mismatches"]
            if isinstance(sample, list) and len(sample) < SAMPLE_LIMIT:
                sample.append(
                    {
                        "event_id": event.id,
                        "target_date": event.target_date.isoformat(),
                        "market_id": market.id,
                        "bucket": market.group_item_title,
                        "market_unit": market_unit,
                        "observed_value": str(observed_value),
                        "expected_winner": expected,
                        "market_winner": market.winner,
                    }
                )

    city_payloads = list(totals.values())
    valid = all(
        _int_value(row["audited_markets"]) > 0
        and _int_value(row["mismatches"]) == 0
        and _int_value(row["missing_observations"]) == 0
        for row in city_payloads
    )
    issues: list[str] = []
    for row in city_payloads:
        if _int_value(row["audited_markets"]) <= 0:
            issues.append(f"{row['city_slug']}:no_audited_markets")
        if _int_value(row["mismatches"]) > 0:
            issues.append(f"{row['city_slug']}:winner_mismatch")
        if _int_value(row["missing_observations"]) > 0:
            issues.append(f"{row['city_slug']}:missing_observations")
    return {
        "valid": valid,
        "audited_cities": audited_cities,
        "cities": city_payloads,
        "issues": issues,
    }


def _gate(passed: bool, *, value: object, required: object) -> dict[str, object]:
    return {"passed": passed, "value": value, "required": required}


def _status(gates: dict[str, dict[str, object]]) -> str:
    if gates["discovery_candidate"]["passed"] is not True:
        return "DATA_REVIEW"
    if (
        gates["timing"]["passed"] is not True
        or gates["resolution"]["passed"] is not True
    ):
        return "DATA_REVIEW"
    required = [
        key
        for key in gates
        if key
        not in {
            "live_release",
        }
    ]
    if all(gates[key]["passed"] is True for key in required):
        return "READY_FOR_REPAIR_V5"
    return "CANDIDATE_REVIEW"


async def generate_discovery_candidate_audit_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    days: int | None = None,
) -> DiscoveryCandidateAuditRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)

    async with session_factory() as session:
        discovery = await _latest_discovery_candidate(session)
        if discovery is None:
            async with session_factory() as write_session, write_session.begin():
                return await _persist_empty(write_session, run_at, window_start, window_end)

        summary_raw = _loads(discovery.summary_json)
        discovery_version = str(summary_raw.get("discovery_version") or "v2")
        include_research_only = bool(summary_raw.get("include_research_only"))
        universe = str(discovery.universe)
        selected_cities_raw = _loads(discovery.cities_json)
        selected_cities = (
            [str(city) for city in selected_cities_raw]
            if isinstance(selected_cities_raw, list)
            else []
        )
        _, universe_health = await _research_cities(
            session,
            settings,
            universe=universe,
            include_research_only=include_research_only,
        )
        run_settings = settings.model_copy(
            update={"cities": selected_cities, "validation_history_days": history_days}
        )
        candidates, n_candidates, source_counts, raw_counts, sampled_counts = (
            await _historical_candidates(session, run_settings)
        )
        artifact_counts_before = await _artifact_counts(session)

    trades, decisions, folds, rolling_summary = _rolling_audit(
        candidates,
        run_settings,
        discovery_version=discovery_version,
    )
    profile = {
        **_profile_payload(trades),
        **_bootstrap_metrics(trades),
        **_concentration_metrics(trades),
    }
    city_share = _city_pnl_share(trades)
    profile["city_pnl_share"] = city_share
    profile_traded_cities = profile.get("traded_cities", [])
    traded_cities = (
        {str(city) for city in profile_traded_cities if isinstance(city, str)}
        if isinstance(profile_traded_cities, list)
        else set()
    )
    if not traded_cities:
        traded_cities = {trade.city_slug for trade in trades if trade.city_slug is not None}
    research_only_raw = universe_health.get("research_only")
    research_only = (
        {str(city) for city in research_only_raw}
        if isinstance(research_only_raw, list)
        else set()
    )

    timing = _timing_audit(decisions)
    async with session_factory() as session:
        resolution = await _city_resolution_audit(
            session,
            traded_cities=traded_cities,
            research_only=research_only,
            window_start=window_start,
            window_end=window_end,
        )
        artifact_counts_after = await _artifact_counts(session)

    concentration = {
        "profile": profile,
        "city_pnl_share": city_share,
        "top_city": city_share.get("top_city"),
        "top_city_abs_pnl_share": city_share.get("top_city_abs_pnl_share"),
        "traded_cities": sorted(traded_cities),
        "research_only_traded_cities": sorted(traded_cities & research_only),
    }
    segments = {
        "by_city": _group_metrics(decisions, lambda decision: decision.candidate.city_slug),
        "by_bucket_kind": _group_metrics(
            decisions, lambda decision: decision.candidate.bucket_kind
        ),
        "by_segment": _group_metrics(decisions, lambda decision: decision.segment_key),
        "by_hours_to_close": _group_metrics(
            decisions, lambda decision: _hours_bucket(decision.candidate.hours_to_close)
        ),
        "blocked_counts": _decision_blockers(decisions),
        "samples": _decision_samples(decisions),
    }
    n_trades = int(profile.get("n_resolved_trades") or 0)
    brier_delta = profile.get("brier_delta")
    total_pnl = Decimal(str(profile.get("total_pnl") or "0"))
    pnl_ci_low_raw = profile.get("pnl_ci_low")
    pnl_ci_low = Decimal(str(pnl_ci_low_raw)) if pnl_ci_low_raw is not None else None
    top_city_share = Decimal(str(city_share.get("top_city_abs_pnl_share") or "999"))
    city_count = int(city_share.get("city_count") or 0)
    min_trade_requirement = (
        MIN_SINGLE_CITY_TRADES if city_count <= 1 else MIN_HISTORICAL_TRADES
    )
    if top_city_share > MAX_TOP_CITY_SHARE:
        min_trade_requirement = max(min_trade_requirement, MIN_SINGLE_CITY_TRADES)

    gates = {
        "discovery_candidate": _gate(
            discovery.status == "DISCOVERY_CANDIDATE",
            value={"status": discovery.status, "run_id": discovery.id},
            required="DISCOVERY_CANDIDATE",
        ),
        "oos_brier": _gate(
            isinstance(brier_delta, int | float) and float(brier_delta) > 0,
            value={"brier_delta": brier_delta},
            required={"brier_delta_gt": 0},
        ),
        "oos_pnl": _gate(
            total_pnl > 0,
            value={"total_pnl": str(total_pnl)},
            required={"total_pnl_gt": "0"},
        ),
        "oos_trades": _gate(
            n_trades >= min_trade_requirement,
            value={"n_resolved_trades": n_trades},
            required={"min_trades": min_trade_requirement},
        ),
        "folds": _gate(
            int(rolling_summary["valid_folds"]) >= MIN_VALID_FOLDS,
            value={"valid_folds": rolling_summary["valid_folds"]},
            required={"min_valid_folds": MIN_VALID_FOLDS},
        ),
        "bootstrap": _gate(
            pnl_ci_low is not None and pnl_ci_low >= 0,
            value={"pnl_ci_low": str(pnl_ci_low) if pnl_ci_low is not None else None},
            required={"pnl_ci_low_gte": "0"},
        ),
        "city_concentration": _gate(
            top_city_share <= MAX_TOP_CITY_SHARE,
            value={
                "top_city": city_share.get("top_city"),
                "top_city_abs_pnl_share": str(top_city_share),
            },
            required={"top_city_abs_pnl_share_lte": str(MAX_TOP_CITY_SHARE)},
        ),
        "resolution": _gate(
            resolution.get("valid") is True,
            value=resolution,
            required=(
                "research_only traded cities must have reconstructed winners "
                "matching Market.winner"
            ),
        ),
        "timing": _gate(
            timing.get("valid") is True,
            value=timing,
            required="no effective candidate after market close",
        ),
        "trading_artifacts_unchanged": _gate(
            artifact_counts_before == artifact_counts_after,
            value={"before": artifact_counts_before, "after": artifact_counts_after},
            required="audit must not create signals/orders/fills",
        ),
        "live_release": _gate(
            False,
            value="diagnostic_only",
            required="repair_v5 PROMISING plus measurement READY_FOR_LIVE_REVIEW",
        ),
    }
    status = _status(gates)
    summary = {
        "source": AUDIT_SOURCE,
        "diagnostic_only": True,
        "cannot_approve_live": True,
        "discovery_run_id": discovery.id,
        "discovery_status": discovery.status,
        "discovery_version": discovery_version,
        "best_family": summary_raw.get("best_family"),
        "n_candidate_price_points": n_candidates,
        "price_source_counts": source_counts,
        "price_source_raw_counts": raw_counts,
        "price_source_sampled_counts": sampled_counts,
        "next_action": (
            "implement_repair_v5"
            if status == "READY_FOR_REPAIR_V5"
            else "audit_candidate_blockers"
        ),
        **rolling_summary,
    }

    async with session_factory() as session, session.begin():
        row = DiscoveryCandidateAuditRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            discovery_run_id=discovery.id,
            cities_json=_json(selected_cities),
            summary_json=_json(summary),
            concentration_json=_json(concentration),
            folds_json=_json(folds),
            city_resolution_json=_json(resolution),
            timing_json=_json(timing),
            segments_json=_json(segments),
            gates_json=_json(gates),
        )
        session.add(row)
        await session.flush()
        logger.info("discovery candidate audit: status=%s discovery=%s", status, discovery.id)
        return row


async def _persist_empty(
    session: AsyncSession,
    run_at: datetime,
    window_start: date,
    window_end: date,
) -> DiscoveryCandidateAuditRun:
    gates = {
        "discovery_candidate": _gate(
            False,
            value={"status": None},
            required="latest StrategyDiscoveryRun.status = DISCOVERY_CANDIDATE",
        ),
        "live_release": _gate(
            False,
            value="diagnostic_only",
            required="repair_v5 PROMISING plus measurement READY_FOR_LIVE_REVIEW",
        ),
    }
    row = DiscoveryCandidateAuditRun(
        run_at=run_at,
        status="DATA_REVIEW",
        window_start=window_start,
        window_end=window_end,
        discovery_run_id=None,
        cities_json="[]",
        summary_json=_json(
            {
                "source": AUDIT_SOURCE,
                "diagnostic_only": True,
                "cannot_approve_live": True,
                "next_action": "run_strategy_discovery",
            }
        ),
        concentration_json="{}",
        folds_json="[]",
        city_resolution_json="{}",
        timing_json="{}",
        segments_json="{}",
        gates_json=_json(gates),
    )
    session.add(row)
    await session.flush()
    return row


async def _artifact_counts(session: AsyncSession) -> dict[str, int]:
    return {
        "signals": len((await session.execute(select(Signal.id))).scalars().all()),
        "paper_orders": len((await session.execute(select(PaperOrder.id))).scalars().all()),
        "paper_fills": len((await session.execute(select(PaperFill.id))).scalars().all()),
    }


def _decision_blockers(decisions: list[AuditDecision]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for decision in decisions:
        if decision.reason is not None:
            counter[decision.reason] += 1
    return dict(counter.most_common())


def _decision_samples(decisions: list[AuditDecision]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for decision in decisions:
        if len(rows) >= SAMPLE_LIMIT:
            break
        if decision.trade is None and decision.reason is None:
            continue
        rows.append(
            {
                "ts": decision.candidate.ts.isoformat(),
                "city_slug": decision.candidate.city_slug,
                "market_id": decision.candidate.market_id,
                "segment_key": decision.segment_key,
                "market_price": str(decision.candidate.price),
                "raw_prob": decision.candidate.raw_prob,
                "calibrated_prob": decision.calibrated_prob,
                "edge_net": str(decision.edge_net),
                "cost_per_share": str(
                    cost_per_share(decision.candidate.price, get_settings().taker_fee_rate)
                ),
                "hours_to_close": decision.candidate.hours_to_close,
                "reason": decision.reason,
                "would_trade": decision.trade is not None,
            }
        )
    return rows


def _hours_bucket(hours: float) -> str:
    if hours < 0:
        return "after_close"
    if hours < 6:
        return "0-6h"
    if hours < 12:
        return "6-12h"
    if hours < 24:
        return "12-24h"
    if hours < 48:
        return "24-48h"
    return "48h+"


async def run(settings: Settings, *, days: int | None = None) -> DiscoveryCandidateAuditRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_discovery_candidate_audit_report(
            session_factory,
            settings,
            days=days,
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run discovery candidate audit.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def _run_to_jsonable(row: DiscoveryCandidateAuditRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "discovery_run_id": row.discovery_run_id,
        "cities": json.loads(row.cities_json),
        "summary": json.loads(row.summary_json),
        "concentration": json.loads(row.concentration_json),
        "folds": json.loads(row.folds_json),
        "city_resolution": json.loads(row.city_resolution_json),
        "timing": json.loads(row.timing_json),
        "segments": json.loads(row.segments_json),
        "gates": json.loads(row.gates_json),
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.json else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    row = asyncio.run(run(get_settings(), days=args.days))
    if args.json:
        print(json.dumps(_run_to_jsonable(row), sort_keys=True))


if __name__ == "__main__":
    main()
