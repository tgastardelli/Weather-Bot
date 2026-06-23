"""Research-only onboarding report for new weather cities."""

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.historical_validation import parse_cities
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    CalibrationMetric,
    City,
    CityOnboardingRun,
    DailyObservedMax,
    Event,
    ForecastSnapshot,
    Market,
    MarketPriceHistoryPoint,
    MarketTradeHistoryPoint,
    PaperFill,
    PaperOrder,
    Signal,
)
from app.db.session import create_engine, create_session_factory
from app.polymarket.registry import city_default, station_info

logger = logging.getLogger(__name__)


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


async def _max_calibration_samples(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(CalibrationMetric.city_slug, func.max(CalibrationMetric.n_samples)).group_by(
                CalibrationMetric.city_slug
            )
        )
    ).all()
    return {str(city): int(samples or 0) for city, samples in rows}


async def _count_by_city(
    session: AsyncSession, city_column: object, row_id: object
) -> dict[str, int]:
    query = select(city_column, func.count(row_id)).group_by(city_column)
    rows = (await session.execute(query)).all()
    return {str(city): int(count or 0) for city, count in rows}


async def _market_counts(session: AsyncSession) -> tuple[dict[str, int], dict[str, int]]:
    rows = (
        await session.execute(
            select(Event.city_slug, func.count(Market.id))
            .join(Market, Market.event_id == Event.id)
            .group_by(Event.city_slug)
        )
    ).all()
    markets = {str(city): int(count or 0) for city, count in rows}
    resolved_rows = (
        await session.execute(
            select(Event.city_slug, func.count(Market.id))
            .join(Market, Market.event_id == Event.id)
            .where(Market.winner.is_not(None))
            .group_by(Event.city_slug)
        )
    ).all()
    resolved = {str(city): int(count or 0) for city, count in resolved_rows}
    return markets, resolved


async def _history_counts(session: AsyncSession) -> tuple[dict[str, int], dict[str, int]]:
    trade_rows = (
        await session.execute(
            select(Event.city_slug, func.count(MarketTradeHistoryPoint.id))
            .join(Market, Market.event_id == Event.id)
            .join(MarketTradeHistoryPoint, MarketTradeHistoryPoint.market_id == Market.id)
            .group_by(Event.city_slug)
        )
    ).all()
    price_rows = (
        await session.execute(
            select(Event.city_slug, func.count(MarketPriceHistoryPoint.id))
            .join(Market, Market.event_id == Event.id)
            .join(MarketPriceHistoryPoint, MarketPriceHistoryPoint.market_id == Market.id)
            .group_by(Event.city_slug)
        )
    ).all()
    return (
        {str(city): int(count or 0) for city, count in trade_rows},
        {str(city): int(count or 0) for city, count in price_rows},
    )


def _check_metadata(city: City | None) -> tuple[bool, list[str]]:
    if city is None:
        return False, ["city_not_found"]
    missing: list[str] = []
    if city.station_code is None:
        missing.append("missing_station")
    if city.latitude is None or city.longitude is None:
        missing.append("missing_lat_lon")
    if city.timezone is None:
        missing.append("missing_timezone")
    if city.unit not in {"C", "F"}:
        missing.append("unit_suspect")
    if city.rounding not in {"round", "floor"}:
        missing.append("rounding_suspect")
    if city.resolution_source is None:
        missing.append("missing_resolution_source")
    return not missing, missing


def _classification(
    *,
    city: City | None,
    metadata_ok: bool,
    climate_ok: bool,
    market_ok: bool,
    resolution_ok: bool,
) -> str:
    if city is None or not climate_ok or not market_ok:
        return "excluded"
    if city.needs_review or not metadata_ok or not resolution_ok:
        return "research_only"
    return "live_eligible"


def _apply_city_default(city: City) -> list[str]:
    default = city_default(city.slug)
    if default is None:
        return []
    changed: list[str] = []
    info = station_info(default.station_code)
    if city.station_code != default.station_code:
        city.station_code = default.station_code
        changed.append("station_code")
    if info is not None:
        if city.latitude != info[0]:
            city.latitude = info[0]
            changed.append("latitude")
        if city.longitude != info[1]:
            city.longitude = info[1]
            changed.append("longitude")
        if city.timezone != info[2]:
            city.timezone = info[2]
            changed.append("timezone")
    if city.unit != default.unit:
        city.unit = default.unit
        changed.append("unit")
    if city.rounding != default.rounding:
        city.rounding = default.rounding
        changed.append("rounding")
    if city.resolution_source != default.resolution_source:
        city.resolution_source = default.resolution_source
        changed.append("resolution_source")
    if city.resolution_url != default.resolution_url:
        city.resolution_url = default.resolution_url
        changed.append("resolution_url")
    if city.needs_review != default.needs_review:
        city.needs_review = default.needs_review
        changed.append("needs_review")
    if changed:
        city.updated_at = datetime.now(UTC)
    return changed


async def generate_city_onboarding_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str],
    days: int | None = None,
    repair_metadata: bool = False,
) -> CityOnboardingRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)

    async with session_factory() as session:
        artifact_counts_before = {
            "signals": (await session.execute(select(func.count(Signal.id)))).scalar_one(),
            "paper_orders": (
                await session.execute(select(func.count(PaperOrder.id)))
            ).scalar_one(),
            "paper_fills": (await session.execute(select(func.count(PaperFill.id)))).scalar_one(),
        }
        rows = (
            await session.execute(select(City).where(City.slug.in_(cities)).order_by(City.slug))
        ).scalars().all()
        repaired_metadata: dict[str, list[str]] = {}
        if repair_metadata:
            for city in rows:
                changed = _apply_city_default(city)
                if changed:
                    repaired_metadata[city.slug] = changed
            if repaired_metadata:
                await session.commit()
                rows = (
                    await session.execute(
                        select(City).where(City.slug.in_(cities)).order_by(City.slug)
                    )
                ).scalars().all()
        city_by_slug = {city.slug: city for city in rows}
        calibration = await _max_calibration_samples(session)
        forecasts = await _count_by_city(session, ForecastSnapshot.city_slug, ForecastSnapshot.id)
        observations = await _count_by_city(
            session, DailyObservedMax.city_slug, DailyObservedMax.id
        )
        events = await _count_by_city(session, Event.city_slug, Event.id)
        markets, resolved = await _market_counts(session)
        trades, prices = await _history_counts(session)
        artifact_counts_after = {
            "signals": (await session.execute(select(func.count(Signal.id)))).scalar_one(),
            "paper_orders": (
                await session.execute(select(func.count(PaperOrder.id)))
            ).scalar_one(),
            "paper_fills": (await session.execute(select(func.count(PaperFill.id)))).scalar_one(),
        }

    city_checks: list[dict[str, object]] = []
    status_counts: dict[str, int] = {"live_eligible": 0, "research_only": 0, "excluded": 0}
    for slug in cities:
        city = city_by_slug.get(slug)
        metadata_ok, metadata_reasons = _check_metadata(city)
        n_pairs = calibration.get(slug, 0)
        climate_ok = n_pairs >= settings.validation_min_samples
        n_markets = markets.get(slug, 0)
        n_resolved = resolved.get(slug, 0)
        market_history_points = trades.get(slug, 0) + prices.get(slug, 0)
        market_ok = n_resolved > 0 and market_history_points > 0
        resolution_ok = bool(city and city.resolution_source and n_resolved > 0)
        classification = _classification(
            city=city,
            metadata_ok=metadata_ok,
            climate_ok=climate_ok,
            market_ok=market_ok,
            resolution_ok=resolution_ok,
        )
        status_counts[classification] += 1
        checks = {
            "metadata": {
                "passed": metadata_ok,
                "reasons": metadata_reasons,
            },
            "climate": {
                "passed": climate_ok,
                "forecast_observed_pairs": n_pairs,
                "forecast_snapshots": forecasts.get(slug, 0),
                "observations": observations.get(slug, 0),
                "required_pairs": settings.validation_min_samples,
            },
            "market": {
                "passed": market_ok,
                "events": events.get(slug, 0),
                "markets": n_markets,
                "resolved_markets": n_resolved,
                "trade_history_points": trades.get(slug, 0),
                "price_history_points": prices.get(slug, 0),
            },
            "resolution": {
                "passed": resolution_ok,
                "source": city.resolution_source if city is not None else None,
                "needs_review": city.needs_review if city is not None else True,
            },
            "metadata_repair": {
                "applied": slug in repaired_metadata,
                "fields": repaired_metadata.get(slug, []),
            },
        }
        city_checks.append(
            {
                "city_slug": slug,
                "classification": classification,
                "needs_review": city.needs_review if city is not None else True,
                "checks": checks,
            }
        )

    gates = {
        "researchable_city": {
            "passed": status_counts["live_eligible"] + status_counts["research_only"] > 0,
            "value": status_counts,
            "required": {"researchable_cities_gt": 0},
        },
        "live_release": {
            "passed": False,
            "value": "diagnostic_only",
            "required": "city onboarding never approves live trading",
        },
        "trading_artifacts_unchanged": {
            "passed": artifact_counts_before == artifact_counts_after,
            "value": {"before": artifact_counts_before, "after": artifact_counts_after},
            "required": "onboarding must not create signals/orders/fills",
        },
    }
    status = "READY_FOR_RESEARCH" if gates["researchable_city"]["passed"] else "DATA_REVIEW"
    summary = {
        "source": "city_onboarding",
        "diagnostic_only": True,
        "cannot_approve_live": True,
        "requested_cities": cities,
        "repair_metadata": repair_metadata,
        "repaired_metadata": repaired_metadata,
        **status_counts,
    }

    async with session_factory() as session, session.begin():
        row = CityOnboardingRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            cities_json=_json(cities),
            summary_json=_json(summary),
            checks_json=_json(city_checks),
            gates_json=_json(gates),
        )
        session.add(row)
        await session.flush()
        logger.info("city onboarding: status=%s cities=%s", status, ",".join(cities))
        return row


async def run(
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
    repair_metadata: bool = False,
) -> CityOnboardingRun:
    selected = cities if cities is not None else settings.cities or []
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_city_onboarding_report(
            session_factory,
            settings,
            cities=selected,
            days=days,
            repair_metadata=repair_metadata,
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run research-only city onboarding.")
    parser.add_argument("--cities", help="Comma-separated city slugs.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument(
        "--repair-metadata",
        action="store_true",
        help="Apply known research-only metadata defaults before reporting.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def _run_to_jsonable(row: CityOnboardingRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "cities": json.loads(row.cities_json),
        "summary": json.loads(row.summary_json),
        "checks": json.loads(row.checks_json),
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
            repair_metadata=args.repair_metadata,
        )
    )
    if args.json:
        print(json.dumps(_run_to_jsonable(row), sort_keys=True))


if __name__ == "__main__":
    main()
