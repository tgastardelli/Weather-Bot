"""High-risk/high-reward city hunt for asymmetric weather-market payoffs.

This report is diagnostic-only. It searches for volatile, operational cities
where low win rate can be acceptable because average wins are much larger than
average losses. It never creates signals, paper orders, paper fills, or live
readiness approvals.
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

from analysis.backtest import TradeResult, _max_drawdown, _trade_result
from analysis.historical_validation import parse_cities
from analysis.operational_quarantine import quarantine_payloads, split_operational_cities
from analysis.strategy_repair import HistoricalCandidate, _historical_candidates
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    City,
    CityVolatilityMetric,
    DailyObservedMax,
    ForecastSnapshot,
    HighRewardCityHuntRun,
    PaperFill,
    PaperOrder,
    Signal,
)
from app.db.session import create_engine, create_session_factory
from app.strategy.edge import net_edge

logger = logging.getLogger(__name__)

HUNT_SOURCE = "high_reward_city_hunt_historical_price_points"
MIN_CITY_TRADES = 15
MIN_APPROVED_CITIES = 3
MIN_PAYOFF_RATIO = Decimal("3.00")
MIN_RECENT_MAE_C = 1.0
MIN_TAIL_MISS_3C = 0.02
MIN_REWARD_VOLATILITY_SCORE = 23.0
CHEAP_PRICE_MAX = Decimal("0.20")
OVERCONFIDENT_YES_MIN = Decimal("0.80")
CENT = Decimal("0.01")

HuntSide = Literal["YES", "NO"]
HuntFamily = Literal[
    "cheap_tail_yes",
    "cheap_tail_no",
    "forecast_failure_fade",
    "volatility_breakout",
    "market_overconfidence_no",
    "seasonal_tail_city",
]


@dataclass(frozen=True)
class HuntVariant:
    family: HuntFamily
    side: HuntSide
    max_price: Decimal | None = None
    min_yes_price: Decimal | None = None
    min_prob_delta: Decimal = Decimal("0.08")
    required_bucket_kind: str | None = None
    required_month: int | None = None


@dataclass(frozen=True)
class HuntDecision:
    candidate: HistoricalCandidate
    variant: HuntVariant
    decision_price: Decimal
    model_prob: float
    edge_net: Decimal
    winner: bool


def _json(value: object) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _decision_price(candidate: HistoricalCandidate, side: HuntSide) -> Decimal:
    if side == "NO":
        return (Decimal("1") - candidate.price).quantize(Decimal("0.00001"))
    return candidate.price


def _decision_probability(candidate: HistoricalCandidate, side: HuntSide) -> float:
    return 1.0 - candidate.raw_prob if side == "NO" else candidate.raw_prob


def _decision_winner(candidate: HistoricalCandidate, side: HuntSide) -> bool:
    return not candidate.winner if side == "NO" else candidate.winner


def _variant_name(variant: HuntVariant) -> str:
    parts = [variant.family, variant.side.lower()]
    if variant.max_price is not None:
        parts.append(f"pxlte{variant.max_price}")
    if variant.min_yes_price is not None:
        parts.append(f"yesgte{variant.min_yes_price}")
    parts.append(f"delta{variant.min_prob_delta}")
    if variant.required_bucket_kind is not None:
        parts.append(variant.required_bucket_kind)
    if variant.required_month is not None:
        parts.append(f"m{variant.required_month:02d}")
    return "_".join(parts).replace(".", "_")


def _variants() -> list[HuntVariant]:
    variants: list[HuntVariant] = []
    for delta in (Decimal("0.04"), Decimal("0.08"), Decimal("0.12")):
        for price in (Decimal("0.05"), Decimal("0.10"), CHEAP_PRICE_MAX):
            variants.extend(
                [
                    HuntVariant("cheap_tail_yes", "YES", max_price=price, min_prob_delta=delta),
                    HuntVariant("cheap_tail_no", "NO", max_price=price, min_prob_delta=delta),
                    HuntVariant(
                        "volatility_breakout",
                        "YES",
                        max_price=price,
                        min_prob_delta=delta,
                    ),
                ]
            )
        for yes_price in (Decimal("0.75"), OVERCONFIDENT_YES_MIN, Decimal("0.90")):
            variants.append(
                HuntVariant(
                    "market_overconfidence_no",
                    "NO",
                    min_yes_price=yes_price,
                    min_prob_delta=delta,
                )
            )
        for bucket_kind in ("above", "below", "range"):
            variants.append(
                HuntVariant(
                    "forecast_failure_fade",
                    "NO",
                    max_price=Decimal("0.35"),
                    min_prob_delta=delta,
                    required_bucket_kind=bucket_kind,
                )
            )
    for month in range(1, 13):
        variants.append(
            HuntVariant(
                "seasonal_tail_city",
                "YES",
                max_price=CHEAP_PRICE_MAX,
                min_prob_delta=Decimal("0.06"),
                required_month=month,
            )
        )
    return variants


def _passes_variant(candidate: HistoricalCandidate, variant: HuntVariant) -> bool:
    decision_price = _decision_price(candidate, variant.side)
    decision_prob = Decimal(str(_decision_probability(candidate, variant.side)))
    if variant.max_price is not None and decision_price > variant.max_price:
        return False
    if variant.min_yes_price is not None and candidate.price < variant.min_yes_price:
        return False
    if (
        variant.required_bucket_kind is not None
        and candidate.bucket_kind != variant.required_bucket_kind
    ):
        return False
    if variant.required_month is not None and candidate.target_date.month != variant.required_month:
        return False
    if decision_prob - decision_price < variant.min_prob_delta:
        return False
    if variant.family == "cheap_tail_yes" and candidate.raw_prob < 0.10:
        return False
    if variant.family == "cheap_tail_no" and candidate.raw_prob > 0.90:
        return False
    if variant.family == "market_overconfidence_no" and candidate.raw_prob > float(candidate.price):
        return False
    return True


def _decision(
    candidate: HistoricalCandidate, variant: HuntVariant, settings: Settings
) -> HuntDecision | None:
    if not _passes_variant(candidate, variant):
        return None
    decision_price = _decision_price(candidate, variant.side)
    probability = _decision_probability(candidate, variant.side)
    edge = net_edge(probability, decision_price, settings.taker_fee_rate)
    if edge <= Decimal("0"):
        return None
    return HuntDecision(
        candidate=candidate,
        variant=variant,
        decision_price=decision_price,
        model_prob=probability,
        edge_net=edge,
        winner=_decision_winner(candidate, variant.side),
    )


def _trade_from_decision(decision: HuntDecision, settings: Settings) -> TradeResult | None:
    base = decision.candidate
    return _trade_result(
        ts=base.ts,
        stake=settings.max_stake_per_order,
        market_price=decision.decision_price,
        model_prob=decision.model_prob,
        winner=decision.winner,
        fee_rate=settings.taker_fee_rate,
        market_id=base.market_id,
        event_id=base.event_id,
        city_slug=base.city_slug,
        target_date=base.target_date,
        bucket_kind=base.bucket_kind,
        bucket_label=base.bucket_label,
        edge_net=decision.edge_net,
        hours_to_close=base.hours_to_close,
        price_source=base.price_source,
    )


def _max_loss_streak(trades: list[TradeResult]) -> int:
    current = 0
    longest = 0
    for trade in trades:
        if trade.pnl > 0:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def _city_payoff_metrics(trades: list[TradeResult]) -> dict[str, object]:
    pnls = [trade.pnl for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl <= 0]
    total_staked = sum((trade.stake for trade in trades), Decimal("0")).quantize(CENT)
    total_pnl = sum(pnls, Decimal("0")).quantize(CENT)
    average_win = (sum(wins, Decimal("0")) / Decimal(len(wins))).quantize(CENT) if wins else None
    average_loss_abs = (
        (abs(sum(losses, Decimal("0"))) / Decimal(len(losses))).quantize(CENT)
        if losses
        else None
    )
    payoff_ratio = (
        (average_win / average_loss_abs).quantize(Decimal("0.0001"))
        if average_win is not None and average_loss_abs not in (None, Decimal("0"))
        else None
    )
    profit_factor = (
        (sum(wins, Decimal("0")) / abs(sum(losses, Decimal("0")))).quantize(Decimal("0.0001"))
        if losses and sum(losses, Decimal("0")) < 0
        else None
    )
    return {
        "n_trades": len(trades),
        "n_wins": len(wins),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "average_win": str(average_win) if average_win is not None else None,
        "average_loss": str(average_loss_abs) if average_loss_abs is not None else None,
        "payoff_ratio": str(payoff_ratio) if payoff_ratio is not None else None,
        "total_staked": str(total_staked),
        "total_pnl": str(total_pnl),
        "roi": (
            str((total_pnl / total_staked).quantize(Decimal("0.0001")))
            if total_staked > 0
            else None
        ),
        "max_loss_streak": _max_loss_streak(trades),
        "max_drawdown": str(_max_drawdown(pnls)),
        "profit_factor": str(profit_factor) if profit_factor is not None else None,
    }


def _is_city_candidate(
    metrics: dict[str, object],
    *,
    volatility: dict[str, object] | None,
    recent_error: dict[str, object] | None,
    needs_review: bool,
) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if needs_review:
        blockers.append("needs_review")
    if int(metrics.get("n_trades") or 0) < MIN_CITY_TRADES:
        blockers.append("low_oos_trades")
    if Decimal(str(metrics.get("total_pnl") or "0")) <= 0:
        blockers.append("non_positive_pnl")
    if metrics.get("roi") is None or Decimal(str(metrics["roi"])) <= 0:
        blockers.append("non_positive_roi")
    payoff = metrics.get("payoff_ratio")
    if payoff is None or Decimal(str(payoff)) < MIN_PAYOFF_RATIO:
        blockers.append("payoff_ratio_below_3x")

    tail_3c = Decimal(str((volatility or {}).get("tail_miss_rate_3c") or "0"))
    volatility_score = Decimal(
        str((volatility or {}).get("reward_volatility_score") or "0")
    )
    recent_30 = Decimal(str((recent_error or {}).get("mae_30d") or "0"))
    recent_60 = Decimal(str((recent_error or {}).get("mae_60d") or "0"))
    has_tail_evidence = tail_3c >= Decimal(str(MIN_TAIL_MISS_3C))
    has_recent_error = max(recent_30, recent_60) >= Decimal(str(MIN_RECENT_MAE_C))
    has_volatility_score = volatility_score >= Decimal(str(MIN_REWARD_VOLATILITY_SCORE))
    if not (has_tail_evidence or has_recent_error or has_volatility_score):
        blockers.append("insufficient_tail_or_recent_error")
    return not blockers, blockers


async def _latest_volatility(session: AsyncSession) -> dict[str, CityVolatilityMetric]:
    latest = (
        await session.execute(select(func.max(CityVolatilityMetric.computed_at)))
    ).scalar_one_or_none()
    if latest is None:
        return {}
    rows = (
        (
            await session.execute(
                select(CityVolatilityMetric).where(CityVolatilityMetric.computed_at == latest)
            )
        )
        .scalars()
        .all()
    )
    return {row.city_slug: row for row in rows}


async def _city_flags(session: AsyncSession) -> dict[str, bool]:
    rows = (await session.execute(select(City.slug, City.needs_review))).all()
    return {slug: needs_review for slug, needs_review in rows}


async def _recent_error_metrics(
    session: AsyncSession, cities: list[str], *, as_of: date
) -> dict[str, dict[str, object]]:
    rows = (
        await session.execute(
            select(
                ForecastSnapshot.city_slug,
                ForecastSnapshot.target_date,
                ForecastSnapshot.tmax_c,
                DailyObservedMax.tmax_c,
            )
            .join(
                DailyObservedMax,
                (DailyObservedMax.city_slug == ForecastSnapshot.city_slug)
                & (DailyObservedMax.target_date == ForecastSnapshot.target_date),
            )
            .where(ForecastSnapshot.city_slug.in_(cities))
            .where(ForecastSnapshot.tmax_c.is_not(None))
            .where(ForecastSnapshot.target_date >= as_of - timedelta(days=90))
            .where(ForecastSnapshot.target_date <= as_of)
        )
    ).all()
    errors: dict[str, dict[int, list[float]]] = {
        city: {30: [], 60: [], 90: []} for city in cities
    }
    for city_slug, target_date, forecast_tmax, observed_tmax in rows:
        age = (as_of - target_date).days
        error = abs(float(observed_tmax) - float(forecast_tmax))
        for window in (30, 60, 90):
            if 0 <= age <= window:
                errors[str(city_slug)][window].append(error)
    return {
        city: {
            "mae_30d": round(sum(by_window[30]) / len(by_window[30]), 4)
            if by_window[30]
            else None,
            "mae_60d": round(sum(by_window[60]) / len(by_window[60]), 4)
            if by_window[60]
            else None,
            "mae_90d": round(sum(by_window[90]) / len(by_window[90]), 4)
            if by_window[90]
            else None,
            "n_30d": len(by_window[30]),
            "n_60d": len(by_window[60]),
            "n_90d": len(by_window[90]),
        }
        for city, by_window in errors.items()
    }


def _volatility_payload(row: CityVolatilityMetric | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "reward_volatility_score": row.reward_volatility_score,
        "forecast_mae_c": row.forecast_mae_c,
        "tail_miss_rate_3c": row.tail_miss_rate_3c,
        "tail_miss_rate_5c": row.tail_miss_rate_5c,
        "upside_surprise_rate_3c": row.upside_surprise_rate_3c,
        "downside_surprise_rate_3c": row.downside_surprise_rate_3c,
        "p90_intraday_range_c": row.p90_intraday_range_c,
        "data_quality": row.data_quality,
    }


def _rank_city_variants(
    candidates: list[HistoricalCandidate],
    settings: Settings,
) -> tuple[list[dict[str, object]], dict[str, Counter[str]]]:
    variants = _variants()
    rows: list[dict[str, object]] = []
    blocked: dict[str, Counter[str]] = defaultdict(Counter)
    by_city: dict[str, list[HistoricalCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_city[candidate.city_slug].append(candidate)

    for city_slug, city_candidates in by_city.items():
        for variant in variants:
            trades: list[TradeResult] = []
            buckets: Counter[str] = Counter()
            for candidate in city_candidates:
                decision = _decision(candidate, variant, settings)
                if decision is None:
                    blocked[city_slug]["variant_filter"] += 1
                    continue
                trade = _trade_from_decision(decision, settings)
                if trade is None:
                    blocked[city_slug]["invalid_trade"] += 1
                    continue
                trades.append(trade)
                buckets[candidate.bucket_kind] += 1
            if not trades:
                continue
            metrics = _city_payoff_metrics(trades)
            rows.append(
                {
                    "city_slug": city_slug,
                    "family": variant.family,
                    "side": variant.side,
                    "variant": _variant_name(variant),
                    "bucket_kinds": dict(buckets),
                    **metrics,
                }
            )

    def score(row: dict[str, object]) -> tuple[int, Decimal, Decimal, int]:
        payoff = Decimal(str(row.get("payoff_ratio") or "0"))
        pnl = Decimal(str(row.get("total_pnl") or "0"))
        roi = Decimal(str(row.get("roi") or "0"))
        trades = int(row.get("n_trades") or 0)
        gates = 0
        gates += 1 if trades >= MIN_CITY_TRADES else 0
        gates += 1 if pnl > 0 else 0
        gates += 1 if payoff >= MIN_PAYOFF_RATIO else 0
        gates += 1 if roi > 0 else 0
        return gates, payoff, pnl, trades

    return sorted(rows, key=score, reverse=True), blocked


def _best_per_city(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    best: dict[str, dict[str, object]] = {}
    for row in rows:
        city = str(row["city_slug"])
        if city not in best:
            best[city] = row
    return list(best.values())


def _gates(
    approved: list[dict[str, object]], selected_cities: list[str], excluded: list[str]
) -> dict[str, object]:
    return {
        "three_operational_cities": {
            "passed": len(approved) >= MIN_APPROVED_CITIES,
            "value": {"approved_cities": [row["city_slug"] for row in approved]},
            "required": {"min_cities": MIN_APPROVED_CITIES},
        },
        "city_trade_count": {
            "passed": all(int(row.get("n_trades") or 0) >= MIN_CITY_TRADES for row in approved),
            "value": {str(row["city_slug"]): row.get("n_trades") for row in approved},
            "required": {"min_oos_trades_per_city": MIN_CITY_TRADES},
        },
        "payoff_asymmetry": {
            "passed": all(
                Decimal(str(row.get("payoff_ratio") or "0")) >= MIN_PAYOFF_RATIO
                for row in approved
            ),
            "value": {str(row["city_slug"]): row.get("payoff_ratio") for row in approved},
            "required": {"avg_win_to_avg_loss_gte": str(MIN_PAYOFF_RATIO)},
        },
        "positive_pnl": {
            "passed": all(Decimal(str(row.get("total_pnl") or "0")) > 0 for row in approved),
            "value": {str(row["city_slug"]): row.get("total_pnl") for row in approved},
            "required": {"total_pnl_gt": "0"},
        },
        "positive_roi": {
            "passed": all(Decimal(str(row.get("roi") or "0")) > 0 for row in approved),
            "value": {str(row["city_slug"]): row.get("roi") for row in approved},
            "required": {"roi_gt": "0"},
        },
        "operational_quarantine": {
            "passed": not excluded,
            "value": {
                "excluded_quarantined": excluded,
                "quarantine": quarantine_payloads(excluded),
            },
            "required": "quarantined cities cannot count toward shadow/live",
        },
        "universe_health": {
            "passed": bool(selected_cities),
            "value": {"selected_cities": selected_cities},
            "required": {"selected_cities_gt": 0},
        },
        "live_release": {
            "passed": False,
            "value": "diagnostic_only",
            "required": "repair_v5_high_reward PROMISING plus measurement READY_FOR_LIVE_REVIEW",
        },
    }


def _status(gates: dict[str, object], approved: list[dict[str, object]]) -> str:
    universe = gates.get("universe_health")
    if isinstance(universe, dict) and universe.get("passed") is not True:
        return "DATA_REVIEW"
    three_cities = gates.get("three_operational_cities")
    if isinstance(three_cities, dict) and three_cities.get("passed") is True:
        return "READY_FOR_SHADOW_FAST_LANE"
    if approved:
        return "HIGH_REWARD_CANDIDATE"
    return "NO_ASYMMETRIC_EDGE"


async def _artifact_counts(session: AsyncSession) -> dict[str, int]:
    return {
        "signals": int((await session.execute(select(func.count(Signal.id)))).scalar_one()),
        "paper_orders": int(
            (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        ),
        "paper_fills": int((await session.execute(select(func.count(PaperFill.id)))).scalar_one()),
    }


async def generate_high_reward_city_hunt_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
) -> HighRewardCityHuntRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)
    requested_cities = cities or settings.cities or []
    selected_cities, excluded_quarantined = split_operational_cities(requested_cities)
    run_settings = settings.model_copy(
        update={"cities": selected_cities, "validation_history_days": history_days}
    )

    async with session_factory() as session:
        artifacts_before = await _artifact_counts(session)
        raw_candidates, n_candidates, source_counts, raw_counts, sampled_counts = (
            await _historical_candidates(session, run_settings)
        )
        volatility = await _latest_volatility(session)
        city_needs_review = await _city_flags(session)
        recent_errors = await _recent_error_metrics(session, selected_cities, as_of=window_end)
        artifacts_after = await _artifact_counts(session)

    candidates = [
        candidate
        for candidate in raw_candidates
        if candidate.city_slug in selected_cities
        and window_start <= candidate.target_date <= window_end
    ]
    ranking_rows, blocked = _rank_city_variants(candidates, run_settings)
    best_rows = _best_per_city(ranking_rows)

    enriched_rows: list[dict[str, object]] = []
    approved: list[dict[str, object]] = []
    for row in best_rows:
        city = str(row["city_slug"])
        vol_payload = _volatility_payload(volatility.get(city))
        recent_payload = recent_errors.get(city, {})
        passed, blockers = _is_city_candidate(
            row,
            volatility=vol_payload,
            recent_error=recent_payload,
            needs_review=city_needs_review.get(city, True),
        )
        enriched = {
            **row,
            "passed": passed,
            "blockers": blockers,
            "volatility": vol_payload,
            "recent_error": recent_payload,
            "why_low_winrate_can_work": (
                "average winning payoff is at least 3x average loss"
                if passed
                else None
            ),
        }
        enriched_rows.append(enriched)
        if passed:
            approved.append(enriched)

    approved = sorted(
        approved,
        key=lambda row: (
            Decimal(str(row.get("payoff_ratio") or "0")),
            Decimal(str(row.get("total_pnl") or "0")),
            int(row.get("n_trades") or 0),
        ),
        reverse=True,
    )
    top_three = approved[:MIN_APPROVED_CITIES]
    gates = _gates(top_three, selected_cities, excluded_quarantined)
    status = _status(gates, top_three)
    summary = {
        "source": HUNT_SOURCE,
        "diagnostic_only": True,
        "cannot_approve_live": True,
        "requested_cities": requested_cities,
        "selected_cities": selected_cities,
        "excluded_quarantined": excluded_quarantined,
        "n_candidate_price_points": n_candidates,
        "n_filtered_candidates": len(candidates),
        "price_source_counts": source_counts,
        "price_source_raw_counts": raw_counts,
        "price_source_sampled_counts": sampled_counts,
        "approved_city_count": len(top_three),
        "approved_cities": [row["city_slug"] for row in top_three],
        "strategy_goal": "high_risk_high_reward_asymmetric_payoff",
        "winrate_goal": "low_winrate_allowed_if_payoff_ratio_gte_3x",
        "next_action": (
            "activate_shadow_fast_lane"
            if status == "READY_FOR_SHADOW_FAST_LANE"
            else "review_high_reward_city_candidates"
            if status == "HIGH_REWARD_CANDIDATE"
            else "expand_features_or_weather_universe"
        ),
        "artifact_counts_before": artifacts_before,
        "artifact_counts_after": artifacts_after,
    }
    rankings = {
        "best_per_city": enriched_rows,
        "top_variants": ranking_rows[:30],
        "blocked_counts": {city: dict(counter) for city, counter in blocked.items()},
    }
    candidates_payload = {
        "approved": top_three,
        "approved_all": approved,
        "rejected": [row for row in enriched_rows if not row.get("passed")],
    }

    async with session_factory() as session, session.begin():
        row = HighRewardCityHuntRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            cities_json=_json(selected_cities),
            summary_json=_json(summary),
            rankings_json=_json(rankings),
            candidates_json=_json(candidates_payload),
            gates_json=_json(gates),
        )
        session.add(row)
        await session.flush()
        logger.info("high reward city hunt: status=%s approved=%d", status, len(top_three))
        return row


async def run(
    settings: Settings, *, cities: list[str] | None = None, days: int | None = None
) -> HighRewardCityHuntRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_high_reward_city_hunt_report(
            session_factory, settings, cities=cities, days=days
        )
    finally:
        await engine.dispose()


def _row_payload(row: HighRewardCityHuntRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "cities": json.loads(row.cities_json),
        "summary": json.loads(row.summary_json),
        "rankings": json.loads(row.rankings_json),
        "candidates": json.loads(row.candidates_json),
        "gates": json.loads(row.gates_json),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search for high-risk/high-reward asymmetric city candidates."
    )
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
        print(f"high reward city hunt status={row.status} run_id={row.id}")


if __name__ == "__main__":
    main()
