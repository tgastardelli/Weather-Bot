"""Research-only city audit before broad strategy discovery.

This report classifies cities for diagnostics without mutating the canonical
city registry. A city marked needs_review can be used as research_only in a POC,
but never becomes live-eligible from this report alone.
"""

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.models import (
    Base,
    CalibrationMetric,
    City,
    CityResearchAuditRun,
    DailyObservedMax,
    Event,
    ForecastSnapshot,
    Market,
    MarketPriceHistoryPoint,
    MarketTradeHistoryPoint,
)
from app.db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)

Classification = Literal["live_eligible", "research_only", "excluded"]


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


async def _count_by_city(
    session: AsyncSession,
    column: object,
    city_column: object,
) -> dict[str, int]:
    query = select(city_column, func.count(column)).group_by(city_column)
    rows = (await session.execute(query)).all()
    return {str(city): int(count or 0) for city, count in rows}


async def _max_calibration_samples(session: AsyncSession) -> dict[str, int]:
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


def _classify_city(
    city: City,
    *,
    n_calibration_samples: int,
    min_samples: int,
) -> tuple[Classification, list[str]]:
    reasons: list[str] = []
    if not city.active:
        reasons.append("inactive")
    if n_calibration_samples < min_samples:
        reasons.append("low_forecast_observed_pairs")
    if city.unit not in {"C", "F"}:
        reasons.append("unit_suspect")
    if city.rounding not in {"round", "floor"}:
        reasons.append("rounding_suspect")
    if city.station_code is None:
        reasons.append("missing_station")
    if city.resolution_source is None:
        reasons.append("missing_resolution_source")

    if "inactive" in reasons or "low_forecast_observed_pairs" in reasons:
        return "excluded", reasons
    if city.needs_review:
        return "research_only", [*reasons, "needs_review"]
    if reasons:
        return "research_only", reasons
    return "live_eligible", []


def _failure_categories(
    *,
    reasons: list[str],
    forecast_observed_pairs: int,
    resolved_markets: int,
    market_history_points: int,
) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {
        "metadata": [],
        "climate": [],
        "market": [],
        "resolution": [],
    }
    for reason in reasons:
        if reason in {
            "missing_station",
            "missing_resolution_source",
            "missing_lat_lon",
            "missing_timezone",
            "unit_suspect",
            "rounding_suspect",
        }:
            categories["metadata"].append(reason)
        elif reason == "low_forecast_observed_pairs":
            categories["climate"].append(reason)
        elif reason == "needs_review":
            categories["resolution"].append(reason)
        else:
            categories["metadata"].append(reason)
    if forecast_observed_pairs <= 0:
        categories["climate"].append("missing_forecast_observed_history")
    if resolved_markets <= 0:
        categories["resolution"].append("missing_resolved_markets")
    if market_history_points <= 0:
        categories["market"].append("missing_market_history")
    return {key: sorted(set(value)) for key, value in categories.items()}


async def generate_city_research_audit_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    days: int | None = None,
) -> CityResearchAuditRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)

    async with session_factory() as session:
        cities = (
            await session.execute(select(City).order_by(City.active.desc(), City.slug))
        ).scalars().all()
        calibration_samples = await _max_calibration_samples(session)
        forecast_counts = await _count_by_city(
            session, ForecastSnapshot.id, ForecastSnapshot.city_slug
        )
        observed_counts = await _count_by_city(
            session, DailyObservedMax.id, DailyObservedMax.city_slug
        )
        event_counts = await _count_by_city(session, Event.id, Event.city_slug)
        resolved_counts = await _resolved_markets_by_city(session)
        trade_counts = await _trade_history_by_city(session)
        price_counts = await _price_history_by_city(session)

    city_rows: list[dict[str, object]] = []
    counts: dict[str, int] = {"live_eligible": 0, "research_only": 0, "excluded": 0}
    for city in cities:
        n_samples = calibration_samples.get(city.slug, 0)
        classification, reasons = _classify_city(
            city,
            n_calibration_samples=n_samples,
            min_samples=settings.validation_min_samples,
        )
        counts[classification] += 1
        market_history_points = trade_counts.get(city.slug, 0) + price_counts.get(city.slug, 0)
        resolved_markets = resolved_counts.get(city.slug, 0)
        label_issues = 0
        if event_counts.get(city.slug, 0) > 0 and resolved_markets <= 0:
            label_issues += 1
        if city.unit not in {"C", "F"} or city.rounding not in {"round", "floor"}:
            label_issues += 1
        city_rows.append(
            {
                "city_slug": city.slug,
                "name": city.name,
                "classification": classification,
                "needs_review": city.needs_review,
                "active": city.active,
                "station_code": city.station_code,
                "resolution_source": city.resolution_source,
                "unit": city.unit,
                "rounding": city.rounding,
                "forecast_observed_pairs": n_samples,
                "forecast_snapshots": forecast_counts.get(city.slug, 0),
                "observations": observed_counts.get(city.slug, 0),
                "events": event_counts.get(city.slug, 0),
                "resolved_markets": resolved_markets,
                "trade_history_points": trade_counts.get(city.slug, 0),
                "price_history_points": price_counts.get(city.slug, 0),
                "market_history_points": market_history_points,
                "bucket_label_issues": label_issues,
                "reasons": reasons,
                "failure_categories": _failure_categories(
                    reasons=reasons,
                    forecast_observed_pairs=n_samples,
                    resolved_markets=resolved_markets,
                    market_history_points=market_history_points,
                ),
            }
        )

    gates = {
        "live_eligible_city": {
            "passed": counts["live_eligible"] > 0,
            "value": {"live_eligible": counts["live_eligible"]},
            "required": {"live_eligible_gt": 0},
        },
        "research_universe": {
            "passed": counts["live_eligible"] + counts["research_only"] > 0,
            "value": counts,
            "required": {"researchable_cities_gt": 0},
        },
        "live_release": {
            "passed": False,
            "value": "diagnostic_only",
            "required": "city audit does not approve live trading",
        },
    }
    status = "READY_FOR_RESEARCH" if gates["research_universe"]["passed"] else "DATA_REVIEW"
    summary = {
        "source": "city_research_audit",
        "diagnostic_only": True,
        "cannot_approve_live": True,
        "min_forecast_observed_pairs": settings.validation_min_samples,
        **counts,
    }

    async with session_factory() as session, session.begin():
        row = CityResearchAuditRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            summary_json=_json(summary),
            cities_json=_json(city_rows),
            gates_json=_json(gates),
        )
        session.add(row)
        await session.flush()
        logger.info("city research audit: status=%s counts=%s", status, counts)
        return row


async def run(settings: Settings, *, days: int | None = None) -> CityResearchAuditRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_city_research_audit_report(session_factory, settings, days=days)
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run research-only city audit.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def _run_to_jsonable(row: CityResearchAuditRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "summary": json.loads(row.summary_json),
        "cities": json.loads(row.cities_json),
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
