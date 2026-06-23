"""Rank live-eligible cities before targeted discovery or repair_v5.

This module is diagnostic-only. It never creates signals, paper orders, fills,
or live-readiness approvals.
"""

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.operational_quarantine import (
    is_operationally_quarantined,
    quarantine_payloads,
    quarantine_reasons,
)
from analysis.strategy_discovery import (
    MAX_TOP_5_ABS_PNL_SHARE,
    _profile_payload,
    _rolling_origin,
)
from analysis.strategy_repair import _historical_candidates
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    CalibrationMetric,
    City,
    CityEdgeRankingRun,
    Event,
    Market,
    MarketPriceHistoryPoint,
    MarketTradeHistoryPoint,
    PaperFill,
    PaperOrder,
    Signal,
)
from app.db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)

RANKING_SOURCE = "city_edge_ranking"
MIN_OOS_TRADES = 50
MIN_VALID_FOLDS = 3
SAMPLE_LIMIT = 12


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


async def _calibration_samples(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(CalibrationMetric.city_slug, func.max(CalibrationMetric.n_samples)).group_by(
                CalibrationMetric.city_slug
            )
        )
    ).all()
    return {str(city): int(samples or 0) for city, samples in rows}


async def _resolved_markets_by_city(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Event.city_slug, func.count(Market.id))
            .join(Market, Market.event_id == Event.id)
            .where(Market.winner.is_not(None))
            .group_by(Event.city_slug)
        )
    ).all()
    return {str(city): int(count or 0) for city, count in rows}


async def _trade_history_by_city(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Event.city_slug, func.count(MarketTradeHistoryPoint.id))
            .join(Market, Market.event_id == Event.id)
            .join(MarketTradeHistoryPoint, MarketTradeHistoryPoint.market_id == Market.id)
            .group_by(Event.city_slug)
        )
    ).all()
    return {str(city): int(count or 0) for city, count in rows}


async def _price_history_by_city(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Event.city_slug, func.count(MarketPriceHistoryPoint.id))
            .join(Market, Market.event_id == Event.id)
            .join(MarketPriceHistoryPoint, MarketPriceHistoryPoint.market_id == Market.id)
            .group_by(Event.city_slug)
        )
    ).all()
    return {str(city): int(count or 0) for city, count in rows}


def _profile_value(profile: dict[str, object], key: str, default: object = None) -> object:
    value = profile.get(key)
    return default if value is None else value


def _int_value(value: object, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _ranking_score(row: dict[str, object]) -> tuple[int, Decimal, float, int]:
    profile = row.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    n = _int_value(_profile_value(profile, "n_resolved_trades", 0))
    pnl = Decimal(str(_profile_value(profile, "total_pnl", "0") or "0"))
    brier_raw = _profile_value(profile, "brier_delta")
    brier = float(brier_raw) if isinstance(brier_raw, int | float) else -999999.0
    concentration = Decimal(str(_profile_value(profile, "top_5_abs_pnl_share", "999") or "999"))
    valid_folds = int(row.get("valid_folds") or 0)
    gates = 0
    gates += 1 if n >= MIN_OOS_TRADES else 0
    gates += 1 if valid_folds >= MIN_VALID_FOLDS else 0
    gates += 1 if brier > 0 else 0
    gates += 1 if pnl > 0 else 0
    gates += 1 if concentration <= MAX_TOP_5_ABS_PNL_SHARE else 0
    return (gates, pnl, brier, n)


def _status_from_rows(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "DATA_REVIEW"
    if any(row.get("classification") == "live_candidate" for row in rows):
        return "READY_FOR_TARGETED_DISCOVERY"
    return "DATA_REVIEW"


def _rejection_reasons(
    *,
    city: City,
    n_samples: int,
    market_history_points: int,
    resolved_markets: int,
    profile: dict[str, object],
    valid_folds: int,
    min_samples: int,
) -> list[str]:
    reasons: list[str] = []
    if is_operationally_quarantined(city.slug):
        reasons.extend(quarantine_reasons(city.slug))
        reasons.append("operational_quarantine")
    if city.needs_review:
        reasons.append("needs_review_research_only")
    if n_samples < min_samples:
        reasons.append("low_forecast_observed_pairs")
    if market_history_points <= 0:
        reasons.append("missing_market_history")
    if resolved_markets <= 0:
        reasons.append("missing_resolved_markets")
    if valid_folds < MIN_VALID_FOLDS:
        reasons.append("low_valid_folds")
    if _int_value(_profile_value(profile, "n_resolved_trades", 0)) < MIN_OOS_TRADES:
        reasons.append("low_oos_trades")
    brier_raw = _profile_value(profile, "brier_delta")
    if not isinstance(brier_raw, int | float) or float(brier_raw) <= 0:
        reasons.append("non_positive_brier_delta")
    if Decimal(str(_profile_value(profile, "total_pnl", "0") or "0")) <= 0:
        reasons.append("non_positive_pnl")
    if Decimal(str(_profile_value(profile, "top_5_abs_pnl_share", "999") or "999")) > (
        MAX_TOP_5_ABS_PNL_SHARE
    ):
        reasons.append("concentrated_pnl")
    return sorted(set(reasons))


async def _rank_city(
    session: AsyncSession,
    settings: Settings,
    city: City,
    *,
    days: int,
    sample_count: int,
    resolved_markets: int,
    trade_history_points: int,
    price_history_points: int,
) -> dict[str, object]:
    run_settings = settings.model_copy(
        update={"cities": [city.slug], "validation_history_days": days}
    )
    candidates, n_candidates, source_counts, raw_counts, sampled_counts = (
        await _historical_candidates(session, run_settings)
    )
    best_family, folds, rolling_summary = _rolling_origin(
        candidates,
        run_settings,
        discovery_version="v2",
    )
    profile_raw = best_family.get("profile") if isinstance(best_family, dict) else None
    profile = profile_raw if isinstance(profile_raw, dict) else _profile_payload([])
    valid_folds = _int_value(rolling_summary.get("valid_folds", 0))
    market_history_points = trade_history_points + price_history_points
    reasons = _rejection_reasons(
        city=city,
        n_samples=sample_count,
        market_history_points=market_history_points,
        resolved_markets=resolved_markets,
        profile=profile,
        valid_folds=valid_folds,
        min_samples=settings.validation_min_samples,
    )
    live_data_ok = (
        not city.needs_review
        and not is_operationally_quarantined(city.slug)
        and sample_count >= settings.validation_min_samples
        and market_history_points > 0
        and resolved_markets > 0
    )
    edge_ok = not any(
        reason
        in {
            "low_valid_folds",
            "low_oos_trades",
            "non_positive_brier_delta",
            "non_positive_pnl",
            "concentrated_pnl",
        }
        for reason in reasons
    )
    classification = (
        "live_candidate"
        if live_data_ok
        else "research_only"
        if city.needs_review or is_operationally_quarantined(city.slug)
        else "excluded"
    )
    row: dict[str, object] = {
        "city_slug": city.slug,
        "name": city.name,
        "classification": classification,
        "operational_quarantine": is_operationally_quarantined(city.slug),
        "quarantine_reasons": quarantine_reasons(city.slug),
        "needs_review": city.needs_review,
        "forecast_observed_pairs": sample_count,
        "resolved_markets": resolved_markets,
        "trade_history_points": trade_history_points,
        "price_history_points": price_history_points,
        "market_history_points": market_history_points,
        "n_candidate_price_points": n_candidates,
        "price_source_counts": source_counts,
        "price_source_raw_counts": raw_counts,
        "price_source_sampled_counts": sampled_counts,
        "valid_folds": valid_folds,
        "fold_count": _int_value(rolling_summary.get("fold_count", len(folds))),
        "best_family": best_family.get("family") if isinstance(best_family, dict) else None,
        "best_variant": best_family.get("name") if isinstance(best_family, dict) else None,
        "profile": profile,
        "rejection_reasons": reasons,
        "eligible_for_targeted_discovery": classification == "live_candidate" and edge_ok,
        "diagnostic_only": True,
        "cannot_approve_live": True,
    }
    return row


def _diagnostic_city_row(
    settings: Settings,
    city: City,
    *,
    sample_count: int,
    resolved_markets: int,
    trade_history_points: int,
    price_history_points: int,
) -> dict[str, object]:
    profile = _profile_payload([])
    market_history_points = trade_history_points + price_history_points
    reasons = _rejection_reasons(
        city=city,
        n_samples=sample_count,
        market_history_points=market_history_points,
        resolved_markets=resolved_markets,
        profile=profile,
        valid_folds=0,
        min_samples=settings.validation_min_samples,
    )
    return {
        "city_slug": city.slug,
        "name": city.name,
        "classification": (
            "research_only"
            if city.needs_review or is_operationally_quarantined(city.slug)
            else "excluded"
        ),
        "operational_quarantine": is_operationally_quarantined(city.slug),
        "quarantine_reasons": quarantine_reasons(city.slug),
        "needs_review": city.needs_review,
        "forecast_observed_pairs": sample_count,
        "resolved_markets": resolved_markets,
        "trade_history_points": trade_history_points,
        "price_history_points": price_history_points,
        "market_history_points": market_history_points,
        "n_candidate_price_points": 0,
        "price_source_counts": {},
        "price_source_raw_counts": {},
        "price_source_sampled_counts": {},
        "valid_folds": 0,
        "fold_count": 0,
        "best_family": None,
        "best_variant": None,
        "profile": profile,
        "rejection_reasons": reasons,
        "eligible_for_targeted_discovery": False,
        "diagnostic_only": True,
        "cannot_approve_live": True,
    }


async def generate_city_edge_ranking_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    days: int | None = None,
) -> CityEdgeRankingRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)

    async with session_factory() as session:
        cities = (
            await session.execute(select(City).where(City.active.is_(True)).order_by(City.slug))
        ).scalars().all()
        samples = await _calibration_samples(session)
        resolved = await _resolved_markets_by_city(session)
        trades = await _trade_history_by_city(session)
        prices = await _price_history_by_city(session)

        rows: list[dict[str, object]] = []
        for city in cities:
            sample_count = samples.get(city.slug, 0)
            resolved_markets = resolved.get(city.slug, 0)
            trade_history_points = trades.get(city.slug, 0)
            price_history_points = prices.get(city.slug, 0)
            market_history_points = trade_history_points + price_history_points
            should_simulate = (
                not city.needs_review
                and not is_operationally_quarantined(city.slug)
                and sample_count >= settings.validation_min_samples
                and resolved_markets > 0
                and market_history_points > 0
            )
            if should_simulate:
                rows.append(
                    await _rank_city(
                        session,
                        settings,
                        city,
                        days=history_days,
                        sample_count=sample_count,
                        resolved_markets=resolved_markets,
                        trade_history_points=trade_history_points,
                        price_history_points=price_history_points,
                    )
                )
            else:
                rows.append(
                    _diagnostic_city_row(
                        settings,
                        city,
                        sample_count=sample_count,
                        resolved_markets=resolved_markets,
                        trade_history_points=trade_history_points,
                        price_history_points=price_history_points,
                    )
                )

    live_rows = [row for row in rows if row.get("classification") == "live_candidate"]
    research_rows = [row for row in rows if row.get("classification") == "research_only"]
    ranked_live = sorted(live_rows, key=_ranking_score, reverse=True)
    ranked_research = sorted(research_rows, key=_ranking_score, reverse=True)
    top_live = [str(row["city_slug"]) for row in ranked_live[:3]]
    quarantined_diagnostics = [
        str(row["city_slug"]) for row in ranked_research if row.get("operational_quarantine")
    ]
    status = _status_from_rows(ranked_live)
    next_commands = [
        "uv run python -m analysis.city_edge_ranking --days 730 --json",
    ]
    if top_live:
        discovery_city_sets: list[list[str]] = []
        for size in range(1, min(3, len(top_live)) + 1):
            discovery_city_sets.append(top_live[:size])
        all_live = [str(row["city_slug"]) for row in ranked_live]
        if all_live not in discovery_city_sets:
            discovery_city_sets.append(all_live)
        for city_set in discovery_city_sets:
            next_commands.append(
                "uv run python -m analysis.strategy_discovery "
                f"--days 730 --universe ranked-live --discovery-version v3 "
                f"--cities {','.join(city_set)} --json"
            )
        next_commands.append(
            "uv run python -m analysis.discovery_candidate_audit --days 730 --json"
        )

    signals_count = orders_count = fills_count = 0
    async with session_factory() as session:
        signals_count = int((await session.execute(select(func.count(Signal.id)))).scalar_one())
        orders_count = int((await session.execute(select(func.count(PaperOrder.id)))).scalar_one())
        fills_count = int((await session.execute(select(func.count(PaperFill.id)))).scalar_one())

    gates: dict[str, Any] = {
        "live_candidates_available": {
            "passed": bool(ranked_live),
            "value": {"live_candidates": [row["city_slug"] for row in ranked_live]},
            "required": {"live_candidate_count_gt": 0},
        },
        "targeted_discovery_candidate": {
            "passed": bool(ranked_live),
            "value": {"top_live_cities": top_live},
            "required": {
                "live_eligible_ranked_cities_gt": 0,
                "next_step": "run targeted Discovery V2 combinations",
            },
        },
        "research_only_block": {
            "passed": all(row.get("needs_review") is not True for row in ranked_live),
            "value": {
                "research_only_diagnostic_cities": [
                    row["city_slug"] for row in ranked_research
                ]
            },
            "required": "needs_review cities cannot enter live-targeted ranking",
        },
        "operational_quarantine": {
            "passed": all(row.get("operational_quarantine") is not True for row in ranked_live),
            "value": {
                "quarantined_diagnostic_cities": quarantined_diagnostics,
                "quarantine": quarantine_payloads(set(quarantined_diagnostics)),
            },
            "required": "quarantined cities cannot enter operational ranking",
        },
        "trading_artifacts_unchanged": {
            "passed": True,
            "value": {
                "signals": signals_count,
                "paper_orders": orders_count,
                "paper_fills": fills_count,
            },
            "required": "city edge ranking does not create trading artifacts",
        },
        "live_release": {
            "passed": False,
            "value": "diagnostic_only",
            "required": "strategy_repair PROMISING plus measurement READY_FOR_LIVE_REVIEW",
        },
    }
    summary = {
        "source": RANKING_SOURCE,
        "diagnostic_only": True,
        "cannot_approve_live": True,
        "history_days": history_days,
        "live_candidate_count": len(ranked_live),
        "research_only_count": len(ranked_research),
        "operational_candidates": top_live,
        "research_only_diagnostic": [str(row["city_slug"]) for row in ranked_research],
        "quarantined_diagnostic_cities": quarantined_diagnostics,
        "operational_quarantine": quarantine_payloads(set(quarantined_diagnostics)),
        "top_live_cities": top_live,
        "best_live_city": top_live[0] if top_live else None,
        "next_action": (
            "run_ranked_live_discovery"
            if top_live
            else "fix_city_data_or_review_weather_hypothesis"
        ),
        "next_commands": next_commands,
        "min_forecast_observed_pairs": settings.validation_min_samples,
        "min_oos_trades": MIN_OOS_TRADES,
        "min_valid_folds": MIN_VALID_FOLDS,
    }

    async with session_factory() as session, session.begin():
        run = CityEdgeRankingRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            summary_json=_json(summary),
            cities_json=_json(ranked_live),
            research_json=_json(ranked_research),
            gates_json=_json(gates),
        )
        session.add(run)
        await session.flush()
        logger.info("city edge ranking: status=%s top_live=%s", status, top_live)
        return run


async def run_report(*, days: int | None = None) -> CityEdgeRankingRun:
    settings = get_settings()
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_city_edge_ranking_report(session_factory, settings, days=days)
    finally:
        await engine.dispose()


def _row_payload(row: CityEdgeRankingRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "summary": json.loads(row.summary_json),
        "cities": json.loads(row.cities_json),
        "research": json.loads(row.research_json),
        "gates": json.loads(row.gates_json),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank live-eligible cities by historical edge.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    row = asyncio.run(run_report(days=args.days))
    if args.json:
        print(json.dumps(_row_payload(row), indent=2, sort_keys=True))
    else:
        print(f"city edge ranking status={row.status} run_id={row.id}")


if __name__ == "__main__":
    main()
