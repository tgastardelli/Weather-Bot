"""Hypothesis audit before introducing another repair policy.

This report is diagnostic only. It checks whether the historical edge failure is
caused by invalid timing, bucket/resolution inconsistencies, unstable segments,
or simply no stable historical edge.
"""

import argparse
import asyncio
import json
import logging
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.historical_validation import parse_cities
from analysis.strategy_repair import HistoricalCandidate, _historical_candidates
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    City,
    Event,
    HistoricalDiagnosticsRun,
    Market,
    MarketPriceHistoryPoint,
    MarketTradeHistoryPoint,
    Resolution,
    StrategyCalibrationSegment,
    StrategyHypothesisAuditRun,
    StrategyRepairRun,
)
from app.db.session import create_engine, create_session_factory
from app.strategy.edge import cost_per_share, net_edge
from app.strategy.probability_calibration import (
    ProbabilityContext,
    calibration_keys,
    edge_bucket,
    hours_to_close_bucket,
    price_bucket,
    probability_bucket,
    segment_key,
)
from app.strategy.repair_decision import (
    RepairPolicyParams,
    RepairSegmentStats,
    evaluate_repair_policy,
)
from app.strategy.sizing import kelly_stake

logger = logging.getLogger(__name__)

MAX_ROWS = 12
TARGET_CLOSE_UTC = time(12, 0, tzinfo=UTC)
DECISION_TRACE_SAMPLE = 24


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


def _loads(raw: str | None) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _candidate_segment_key(candidate: HistoricalCandidate) -> str:
    context = ProbabilityContext(
        city_slug=candidate.city_slug,
        bucket_kind=candidate.bucket_kind,
        model_prob=candidate.raw_prob,
        market_price=candidate.price,
        hours_to_close=candidate.hours_to_close,
        target_date=candidate.target_date,
    )
    return segment_key(calibration_keys(context)[0])


def _target_close(target_date: date) -> datetime:
    return datetime.combine(target_date + timedelta(days=1), TARGET_CLOSE_UTC)


def _count_rows(counter: Counter[str], *, limit: int = MAX_ROWS) -> list[dict[str, object]]:
    return [
        {"key": key, "count": count}
        for key, count in counter.most_common(limit)
    ]


def _candidate_breakdown(
    candidates: list[HistoricalCandidate], settings: Settings
) -> dict[str, object]:
    by_city: Counter[str] = Counter()
    by_bucket_kind: Counter[str] = Counter()
    by_price_bucket: Counter[str] = Counter()
    by_model_prob_bucket: Counter[str] = Counter()
    by_edge_bucket: Counter[str] = Counter()
    by_hours_to_close: Counter[str] = Counter()
    for candidate in candidates:
        edge = net_edge(candidate.raw_prob, candidate.price, settings.taker_fee_rate)
        by_city[candidate.city_slug] += 1
        by_bucket_kind[candidate.bucket_kind] += 1
        by_price_bucket[price_bucket(candidate.price) or "unknown"] += 1
        by_model_prob_bucket[probability_bucket(candidate.raw_prob) or "unknown"] += 1
        by_edge_bucket[edge_bucket(edge) or "unknown"] += 1
        by_hours_to_close[hours_to_close_bucket(candidate.hours_to_close) or "unknown"] += 1
    return {
        "by_city": _count_rows(by_city),
        "by_bucket_kind": _count_rows(by_bucket_kind),
        "by_price_bucket": _count_rows(by_price_bucket),
        "by_model_prob_bucket": _count_rows(by_model_prob_bucket),
        "by_edge_bucket": _count_rows(by_edge_bucket),
        "by_hours_to_close": _count_rows(by_hours_to_close),
    }


async def _latest_diagnostics(session: AsyncSession) -> HistoricalDiagnosticsRun | None:
    return (
        await session.execute(
            select(HistoricalDiagnosticsRun)
            .order_by(HistoricalDiagnosticsRun.run_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _latest_repair(session: AsyncSession) -> StrategyRepairRun | None:
    return (
        await session.execute(
            select(StrategyRepairRun).order_by(StrategyRepairRun.run_at.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def _blocked_city_slugs(session: AsyncSession, settings: Settings) -> set[str]:
    filters = [City.needs_review.is_(True)]
    if settings.cities is not None:
        filters.append(City.slug.in_(settings.cities))
    rows = (await session.execute(select(City.slug).where(*filters))).scalars().all()
    return {str(row) for row in rows}


async def _timing_audit(
    session: AsyncSession,
    settings: Settings,
    candidates: list[HistoricalCandidate],
) -> dict[str, object]:
    start = datetime.now(UTC).date() - timedelta(days=settings.validation_history_days)
    filters = [Event.target_date >= start]
    if settings.cities is not None:
        filters.append(Event.city_slug.in_(settings.cities))

    trade_rows = (
        await session.execute(
            select(MarketTradeHistoryPoint.ts, Event.end_date, Event.target_date)
            .select_from(MarketTradeHistoryPoint)
            .join(Market, MarketTradeHistoryPoint.market_id == Market.id)
            .join(Event, Market.event_id == Event.id)
            .where(*filters)
        )
    ).all()
    price_rows = (
        await session.execute(
            select(MarketPriceHistoryPoint.ts, Event.end_date, Event.target_date)
            .select_from(MarketPriceHistoryPoint)
            .join(Market, MarketPriceHistoryPoint.market_id == Market.id)
            .join(Event, Market.event_id == Event.id)
            .where(*filters)
        )
    ).all()

    def summarize(rows: list[tuple[datetime, datetime | None, date]]) -> dict[str, int]:
        missing_end = 0
        after_market_close = 0
        after_target_close = 0
        before_or_at_close = 0
        for ts, end_date, target_date in rows:
            if end_date is None:
                missing_end += 1
            elif ts > end_date:
                after_market_close += 1
            else:
                before_or_at_close += 1
            if ts > _target_close(target_date):
                after_target_close += 1
        return {
            "raw_points": len(rows),
            "before_or_at_market_close": before_or_at_close,
            "after_market_close": after_market_close,
            "after_target_close": after_target_close,
            "missing_market_close": missing_end,
        }

    trade_summary = summarize(list(trade_rows))
    price_summary = summarize(list(price_rows))
    candidate_after_market_close = sum(
        1 for candidate in candidates if candidate.hours_to_close < 0
    )
    raw_discardable_points = (
        trade_summary["after_market_close"] + price_summary["after_market_close"]
    )
    invalid = (
        candidate_after_market_close
        + trade_summary["missing_market_close"]
        + price_summary["missing_market_close"]
    )
    return {
        "valid": invalid == 0,
        "candidate_reject_reason": (
            "after_market_close" if raw_discardable_points > 0 else None
        ),
        "candidate_after_market_close": candidate_after_market_close,
        "raw_discardable_after_market_close": raw_discardable_points,
        "data_api_trades": trade_summary,
        "clob_prices_history": price_summary,
    }


async def _bucket_audit(session: AsyncSession, settings: Settings) -> dict[str, object]:
    start = datetime.now(UTC).date() - timedelta(days=settings.validation_history_days)
    filters = [Event.target_date >= start, Market.winner.is_not(None)]
    if settings.cities is not None:
        filters.append(Event.city_slug.in_(settings.cities))

    rows = (
        await session.execute(
            select(Event, Market, Resolution)
            .select_from(Event)
            .join(Market, Market.event_id == Event.id)
            .outerjoin(Resolution, Resolution.event_id == Event.id)
            .where(*filters)
            .order_by(Event.target_date, Event.id, Market.id)
        )
    ).all()
    by_event: defaultdict[str, list[tuple[Event, Market, Resolution | None]]] = defaultdict(list)
    for event, market, resolution in rows:
        by_event[event.id].append((event, market, resolution))

    issues: list[dict[str, object]] = []
    checked_events = 0
    for event_id, event_rows in by_event.items():
        checked_events += 1
        event = event_rows[0][0]
        markets = [row[1] for row in event_rows]
        resolution = event_rows[0][2]
        true_winners = [market for market in markets if market.winner is True]
        units = {
            "F" if "°F" in market.group_item_title else "C"
            for market in markets
            if "°" in market.group_item_title
        }
        reasons: list[str] = []
        if len(true_winners) != 1:
            reasons.append("winner_count_not_one")
        if resolution is not None and resolution.winner_market_id is not None:
            if len(true_winners) == 1 and resolution.winner_market_id != true_winners[0].id:
                reasons.append("resolution_winner_mismatch")
        if len(units) > 1:
            reasons.append("mixed_units")
        if any(
            market.bucket_kind not in {"below", "exact", "range", "above"}
            for market in markets
        ):
            reasons.append("unknown_bucket_kind")
        if any("°" not in market.group_item_title for market in markets):
            reasons.append("label_without_unit")
        if reasons:
            issues.append(
                {
                    "event_id": event_id,
                    "city_slug": event.city_slug,
                    "target_date": event.target_date.isoformat(),
                    "market_count": len(markets),
                    "winner_count": len(true_winners),
                    "resolution_winner_market_id": (
                        resolution.winner_market_id if resolution is not None else None
                    ),
                    "winner_market_id": true_winners[0].id if len(true_winners) == 1 else None,
                    "reasons": reasons,
                }
            )

    return {
        "valid": len(issues) == 0,
        "checked_events": checked_events,
        "issue_count": len(issues),
        "issues": issues[:MAX_ROWS],
    }


async def _eligible_segment_keys(
    session: AsyncSession,
    repair: StrategyRepairRun | None,
    policy_name: str | None,
) -> set[str]:
    if repair is None or policy_name is None:
        return set()
    rows = (
        await session.execute(
            select(StrategyCalibrationSegment.segment_key)
            .where(
                StrategyCalibrationSegment.run_id == repair.id,
                StrategyCalibrationSegment.policy_name == policy_name,
                StrategyCalibrationSegment.eligible.is_(True),
            )
            .order_by(StrategyCalibrationSegment.segment_key)
        )
    ).scalars().all()
    return {str(row) for row in rows}


async def _eligible_segment_stats(
    session: AsyncSession,
    repair: StrategyRepairRun | None,
    policy_name: str | None,
) -> dict[str, StrategyCalibrationSegment]:
    if repair is None or policy_name is None:
        return {}
    rows = (
        await session.execute(
            select(StrategyCalibrationSegment)
            .where(
                StrategyCalibrationSegment.run_id == repair.id,
                StrategyCalibrationSegment.policy_name == policy_name,
                StrategyCalibrationSegment.eligible.is_(True),
            )
            .order_by(StrategyCalibrationSegment.segment_key)
        )
    ).scalars().all()
    return {row.segment_key: row for row in rows}


def _fold_start(summary: dict[str, Any]) -> date | None:
    folds = summary.get("folds")
    if not isinstance(folds, list):
        return None
    for fold in folds:
        if not isinstance(fold, dict) or fold.get("valid") is not True:
            continue
        window = fold.get("fold_window")
        if isinstance(window, dict) and isinstance(window.get("start"), str):
            return date.fromisoformat(window["start"])
    return None


def _stability_audit(
    candidates: list[HistoricalCandidate],
    *,
    repair_summary: dict[str, Any],
    eligible_segments: set[str],
) -> dict[str, object]:
    start = _fold_start(repair_summary)
    selected_policy = repair_summary.get("selected_policy_name") or repair_summary.get(
        "policy_name"
    )
    train_counts: Counter[str] = Counter()
    oos_counts: Counter[str] = Counter()
    for candidate in candidates:
        key = _candidate_segment_key(candidate)
        if key not in eligible_segments:
            continue
        if start is not None and candidate.target_date >= start:
            oos_counts[key] += 1
        else:
            train_counts[key] += 1

    oos_profiles = repair_summary.get("oos_profiles")
    max_edge = oos_profiles.get("max_edge") if isinstance(oos_profiles, dict) else None
    oos_trades = (
        int(max_edge.get("n_resolved_trades") or 0) if isinstance(max_edge, dict) else 0
    )
    no_recurrence = len(eligible_segments) > 0 and sum(oos_counts.values()) == 0
    return {
        "selected_policy_name": selected_policy,
        "first_oos_fold_start": start.isoformat() if start is not None else None,
        "eligible_segments": len(eligible_segments),
        "train_candidates_in_eligible_segments": sum(train_counts.values()),
        "oos_candidates_in_eligible_segments": sum(oos_counts.values()),
        "oos_trades_in_selected_policy": oos_trades,
        "no_oos_segment_recurrence": no_recurrence,
        "top_train_segments": _count_rows(train_counts),
        "top_oos_segments": _count_rows(oos_counts),
    }


def _repair_policy_params(
    repair_summary: dict[str, Any],
    settings: Settings,
) -> RepairPolicyParams | None:
    policy_name = repair_summary.get("policy_name") or repair_summary.get(
        "selected_policy_name"
    )
    if not isinstance(policy_name, str):
        return None
    return RepairPolicyParams(
        policy_name=policy_name,
        policy_version=str(repair_summary.get("policy_version") or "repair_v4"),
        alpha=float(repair_summary.get("alpha") or 1.0),
        probability_cap=float(repair_summary.get("probability_cap") or 0.80),
        min_samples=int(repair_summary.get("min_calibration_samples") or 50),
        min_edge_net=Decimal(str(repair_summary.get("min_edge_net") or settings.min_edge_net)),
        segment_scope="specific_only",
        price_floor=(
            None
            if repair_summary.get("price_floor") is None
            else Decimal(str(repair_summary.get("price_floor")))
        ),
    )


def _segment_stats(row: StrategyCalibrationSegment) -> RepairSegmentStats:
    return RepairSegmentStats(
        segment_key=row.segment_key,
        n=row.n,
        wins=row.wins,
        observed_rate=row.observed_rate,
        brier_delta=row.brier_delta,
        pnl=row.pnl,
    )


def _global_rate_before_oos(
    candidates: list[HistoricalCandidate], oos_start: date | None
) -> float:
    train = [
        candidate
        for candidate in candidates
        if oos_start is None or candidate.target_date < oos_start
    ]
    if not train:
        return 0.5
    wins = sum(1 for candidate in train if candidate.winner)
    return wins / len(train)


def _trace_reason(reason: str | None) -> str:
    if reason is None:
        return "actionable"
    if reason == "low_price_diagnostic_only":
        return "low_price_diagnostic_only"
    if reason == "no_eligible_segment":
        return "segment_ineligible"
    return reason


def _decision_trace(
    candidates: list[HistoricalCandidate],
    *,
    repair_summary: dict[str, Any],
    segment_rows: dict[str, StrategyCalibrationSegment],
    settings: Settings,
    blocked_city_slugs: set[str],
) -> dict[str, object]:
    oos_start = _fold_start(repair_summary)
    params = _repair_policy_params(repair_summary, settings)
    if params is None:
        return {
            "enabled": False,
            "reason": "missing_policy",
            "oos_candidates": 0,
            "actionable_candidates": 0,
            "blocked_counts": {},
            "samples": [],
        }

    blocked: Counter[str] = Counter()
    samples: list[dict[str, object]] = []
    last_signals: dict[str, tuple[datetime, Decimal]] = {}
    exposure_by_market_day: defaultdict[tuple[str, date], Decimal] = defaultdict(Decimal)
    global_rate = _global_rate_before_oos(candidates, oos_start)
    oos_candidates = 0
    actionable = 0

    for candidate in candidates:
        if oos_start is not None and candidate.target_date < oos_start:
            continue
        segment = _candidate_segment_key(candidate)
        if segment not in segment_rows:
            continue
        oos_candidates += 1
        context = ProbabilityContext(
            city_slug=candidate.city_slug,
            bucket_kind=candidate.bucket_kind,
            model_prob=candidate.raw_prob,
            market_price=candidate.price,
            hours_to_close=candidate.hours_to_close,
            target_date=candidate.target_date,
        )
        decision = evaluate_repair_policy(
            params=params,
            context=context,
            fee_rate=settings.taker_fee_rate,
            segment=_segment_stats(segment_rows[segment]),
            global_rate=global_rate,
        )
        reason = _trace_reason(decision.reason)
        cost = cost_per_share(candidate.price, settings.taker_fee_rate)
        stake = Decimal("0")
        if decision.eligible:
            if candidate.city_slug in blocked_city_slugs:
                reason = "city_needs_review"
            elif decision.edge_net < 0:
                reason = "negative_calibrated_edge"
            else:
                stake = kelly_stake(
                    decision.calibrated_prob,
                    cost,
                    bankroll=settings.bankroll,
                    kelly_multiplier=settings.kelly_fraction,
                    max_stake_per_order=settings.max_stake_per_order,
                )
                if stake <= 0:
                    reason = "kelly_stake_zero"
                else:
                    previous = last_signals.get(candidate.market_id)
                    if previous is not None:
                        previous_ts, previous_edge = previous
                        recent = abs((candidate.ts - previous_ts).total_seconds()) <= 3600
                        similar = abs(decision.edge_net - previous_edge) <= Decimal("0.01")
                        if recent and similar:
                            reason = "duplicate"
                    exposure_key = (candidate.market_id, candidate.ts.date())
                    if (
                        reason == "actionable"
                        and exposure_by_market_day[exposure_key] + stake
                        > settings.max_exposure_per_market
                    ):
                        reason = "max_exposure"

        if reason == "actionable":
            actionable += 1
            last_signals[candidate.market_id] = (candidate.ts, decision.edge_net)
            exposure_by_market_day[(candidate.market_id, candidate.ts.date())] += stake
        else:
            blocked[reason] += 1

        if len(samples) < DECISION_TRACE_SAMPLE:
            samples.append(
                {
                    "ts": candidate.ts.isoformat(),
                    "market_id": candidate.market_id,
                    "city_slug": candidate.city_slug,
                    "target_date": candidate.target_date.isoformat(),
                    "market_price": str(candidate.price),
                    "raw_prob": candidate.raw_prob,
                    "calibrated_prob": decision.calibrated_prob,
                    "edge_net": str(decision.edge_net),
                    "cost_per_share": str(cost),
                    "hours_to_close": candidate.hours_to_close,
                    "segment_key": segment,
                    "reason": reason,
                    "stake": str(stake),
                }
            )

    return {
        "enabled": True,
        "policy_name": params.policy_name,
        "first_oos_fold_start": oos_start.isoformat() if oos_start else None,
        "oos_candidates": oos_candidates,
        "actionable_candidates": actionable,
        "blocked_counts": dict(sorted(blocked.items())),
        "samples": samples,
    }


def _diagnostic_extract(diagnostics: HistoricalDiagnosticsRun | None) -> dict[str, object]:
    if diagnostics is None:
        return {
            "run_id": None,
            "status": None,
            "top_losing_trades": [],
            "worst_segments": [],
            "overconfident_buckets": [],
        }
    recommendations = _loads(diagnostics.recommendations_json)
    calibration = _loads(diagnostics.calibration_json)
    max_edge_calibration = calibration.get("max_edge")
    overconfident = []
    if isinstance(max_edge_calibration, list):
        for row in max_edge_calibration:
            if not isinstance(row, dict):
                continue
            overconfidence = row.get("model_overconfidence")
            n_trades = int(row.get("n_trades") or 0)
            if isinstance(overconfidence, int | float) and overconfidence > 0.15 and n_trades >= 20:
                overconfident.append(row)
    return {
        "run_id": diagnostics.id,
        "status": diagnostics.status,
        "top_losing_trades": recommendations.get("top_losing_trades", [])[:MAX_ROWS],
        "worst_segments": recommendations.get("worst_segments", [])[:MAX_ROWS],
        "overconfident_buckets": overconfident[:MAX_ROWS],
    }


def _repair_extract(repair: StrategyRepairRun | None) -> dict[str, object]:
    if repair is None:
        return {"run_id": None, "status": None}
    summary = _loads(repair.summary_json)
    return {
        "run_id": repair.id,
        "status": repair.status,
        "policy_name": summary.get("policy_name"),
        "validation_scheme": summary.get("validation_scheme"),
        "fold_count": summary.get("fold_count"),
        "no_edge_reason": summary.get("no_edge_reason"),
        "best_variant_pnl": summary.get("best_variant_pnl"),
        "best_variant_brier_delta": summary.get("best_variant_brier_delta"),
    }


def _blockers(
    *,
    timing: dict[str, object],
    bucket_audit: dict[str, object],
    stability: dict[str, object],
    decision_trace: dict[str, object],
    diagnostics: dict[str, object],
    repair: StrategyRepairRun | None,
) -> list[str]:
    blockers: list[str] = []
    if timing.get("valid") is not True:
        blockers.append("timing_invalid")
    if bucket_audit.get("valid") is not True:
        blockers.append("bucket_mapping_suspect")
    if stability.get("no_oos_segment_recurrence") is True:
        blockers.append("no_oos_segment_recurrence")
    if (
        int(decision_trace.get("oos_candidates") or 0) > 0
        and int(decision_trace.get("actionable_candidates") or 0) == 0
    ):
        blockers.append("no_actionable_oos_edge")
    if diagnostics.get("overconfident_buckets"):
        blockers.append("model_overconfidence")
    if repair is None or repair.status != "PROMISING":
        blockers.append("no_historical_edge")
    return blockers


def _status(blockers: list[str]) -> str:
    if "timing_invalid" in blockers or "bucket_mapping_suspect" in blockers:
        return "DATA_REVIEW"
    if "no_oos_segment_recurrence" in blockers:
        return "NO_STABLE_HISTORICAL_EDGE"
    if "no_actionable_oos_edge" in blockers:
        return "NO_ACTIONABLE_OOS_EDGE"
    if "no_historical_edge" in blockers or "model_overconfidence" in blockers:
        return "NEEDS_MODEL_REPAIR"
    return "READY_FOR_REPAIR_V5"


async def generate_strategy_hypothesis_audit_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
) -> StrategyHypothesisAuditRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    selected_cities = cities if cities is not None else settings.cities
    run_settings = settings.model_copy(
        update={"cities": selected_cities, "validation_history_days": history_days}
    )
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)

    async with session_factory() as session:
        diagnostics = await _latest_diagnostics(session)
        repair = await _latest_repair(session)
        repair_summary = _loads(repair.summary_json if repair is not None else None)
        policy_name = repair_summary.get("policy_name")
        eligible_segments = await _eligible_segment_keys(
            session, repair, str(policy_name) if policy_name is not None else None
        )
        eligible_segment_rows = await _eligible_segment_stats(
            session, repair, str(policy_name) if policy_name is not None else None
        )
        candidates, n_candidates, source_counts, raw_counts, sampled_counts = (
            await _historical_candidates(session, run_settings)
        )
        timing = await _timing_audit(session, run_settings, candidates)
        bucket_audit = await _bucket_audit(session, run_settings)
        blocked_city_slugs = await _blocked_city_slugs(session, run_settings)

    stability = _stability_audit(
        candidates,
        repair_summary=repair_summary,
        eligible_segments=eligible_segments,
    )
    decision_trace = _decision_trace(
        candidates,
        repair_summary=repair_summary,
        segment_rows=eligible_segment_rows,
        settings=run_settings,
        blocked_city_slugs=blocked_city_slugs,
    )
    stability["decision_trace"] = decision_trace
    diagnostics_payload = _diagnostic_extract(diagnostics)
    repair_payload = _repair_extract(repair)
    blockers = _blockers(
        timing=timing,
        bucket_audit=bucket_audit,
        stability=stability,
        decision_trace=decision_trace,
        diagnostics=diagnostics_payload,
        repair=repair,
    )
    status = _status(blockers)
    segments = {
        "candidate_breakdown": _candidate_breakdown(candidates, run_settings),
        "worst_segments": diagnostics_payload["worst_segments"],
        "top_losing_trades": diagnostics_payload["top_losing_trades"],
        "overconfident_buckets": diagnostics_payload["overconfident_buckets"],
    }
    summary = {
        "diagnostics": diagnostics_payload,
        "strategy_repair": repair_payload,
        "n_candidate_price_points": n_candidates,
        "price_source_counts": source_counts,
        "price_source_raw_counts": raw_counts,
        "price_source_sampled_counts": sampled_counts,
        "execution_proxy": "historical_last_trade_no_book_depth",
        "price_sampling": "last_trade_per_market_per_60m_bucket",
        "decision_trace": {
            "oos_candidates": decision_trace.get("oos_candidates"),
            "actionable_candidates": decision_trace.get("actionable_candidates"),
            "blocked_counts": decision_trace.get("blocked_counts"),
        },
        "next_action": (
            "fix_historical_data"
            if status == "DATA_REVIEW"
            else "review_model_hypothesis"
            if status == "NO_STABLE_HISTORICAL_EDGE"
            else "review_oos_blockers"
            if status == "NO_ACTIONABLE_OOS_EDGE"
            else "repair_v5_candidate"
            if status == "READY_FOR_REPAIR_V5"
            else "model_repair"
        ),
    }

    async with session_factory() as session, session.begin():
        row = StrategyHypothesisAuditRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            cities_json=_json(selected_cities or run_settings.cities or []),
            summary_json=_json(summary),
            blockers_json=_json(blockers),
            timing_json=_json(timing),
            bucket_audit_json=_json(bucket_audit),
            stability_json=_json(stability),
            segments_json=_json(segments),
        )
        session.add(row)
        await session.flush()
        logger.info(
            "strategy hypothesis audit: status=%s blockers=%s candidates=%d",
            status,
            ",".join(blockers),
            n_candidates,
        )
        return row


async def run(
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
) -> StrategyHypothesisAuditRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_strategy_hypothesis_audit_report(
            session_factory, settings, cities=cities, days=days
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run strategy hypothesis audit.")
    parser.add_argument("--cities", help="Comma-separated city slugs.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def _run_to_jsonable(row: StrategyHypothesisAuditRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "cities": json.loads(row.cities_json),
        "summary": json.loads(row.summary_json),
        "blockers": json.loads(row.blockers_json),
        "timing": json.loads(row.timing_json),
        "bucket_audit": json.loads(row.bucket_audit_json),
        "stability": json.loads(row.stability_json),
        "segments": json.loads(row.segments_json),
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.json else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    row = asyncio.run(
        run(get_settings(), cities=parse_cities(args.cities), days=args.days)
    )
    if args.json:
        print(json.dumps(_run_to_jsonable(row), sort_keys=True))


if __name__ == "__main__":
    main()
