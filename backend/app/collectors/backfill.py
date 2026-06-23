"""Historical weather backfill: archived forecasts vs ERA5 observations.

Usage:
    uv run python -m app.collectors.backfill --days 365
    uv run python -m app.collectors.backfill --cities nyc,shanghai --days 730

The backfill only uses cities already present in city_registry. It never creates
signals, orders, fills, or live-trading artifacts.
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.models import Base, City, DailyObservedMax, ForecastSnapshot
from app.db.session import create_engine, create_session_factory
from app.weather.open_meteo import OpenMeteoClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CityBackfillResult:
    city_slug: str
    status: str
    forecasts: int = 0
    observations: int = 0
    reason: str | None = None


def parse_cities(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    cities = [part.strip() for part in raw.split(",") if part.strip()]
    return cities or None


async def backfill_city(
    session_factory: async_sessionmaker[AsyncSession],
    client: OpenMeteoClient,
    city_slug: str,
    lat: float,
    lon: float,
    models: list[str],
    days: int,
) -> tuple[int, int]:
    """Backfill one city; returns (n_forecasts, n_observed)."""
    now = datetime.now(UTC)
    end = now.date() - timedelta(days=1)
    start = end - timedelta(days=days)

    forecasts = await client.historical_daily_tmax(lat, lon, models, start, end)
    observed = await client.era5_daily_tmax(lat, lon, start, end)

    n_forecasts = 0
    n_observed = 0
    async with session_factory() as session, session.begin():
        for model, series in forecasts.items():
            for target_date, tmax in series:
                if tmax is None:
                    continue
                session.add(
                    ForecastSnapshot(
                        fetched_at=now,
                        city_slug=city_slug,
                        source="historical",
                        model=model,
                        target_date=target_date,
                        lead_days=1,
                        tmax_c=tmax,
                        n_members=0,
                    )
                )
                n_forecasts += 1
        for target_date, tmax in observed:
            if tmax is None:
                continue
            stmt = (
                sqlite_insert(DailyObservedMax)
                .values(city_slug=city_slug, target_date=target_date, tmax_c=tmax, source="era5")
                .on_conflict_do_nothing(index_elements=["city_slug", "target_date", "source"])
            )
            result = await session.execute(stmt)
            n_observed += int(getattr(result, "rowcount", 0) or 0)
    return n_forecasts, n_observed


async def _selected_cities(
    session: AsyncSession, settings: Settings, cities: list[str] | None
) -> tuple[list[City], list[str]]:
    selected = cities if cities is not None else settings.cities
    if selected is not None:
        rows = (
            await session.execute(select(City).where(City.slug.in_(selected)).order_by(City.slug))
        ).scalars().all()
        found = {city.slug for city in rows}
        return list(rows), sorted(set(selected) - found)

    from app.collectors.markets import active_cities

    return await active_cities(session, settings), []


async def run_backfill(
    settings: Settings, days: int, *, cities: list[str] | None = None
) -> list[CityBackfillResult]:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = OpenMeteoClient(http)
            async with session_factory() as session:
                city_rows, missing = await _selected_cities(session, settings, cities)

            results = [
                CityBackfillResult(
                    city_slug=city_slug,
                    status="skipped",
                    reason="city_not_found",
                )
                for city_slug in missing
            ]
            if not city_rows:
                logger.warning("registry vazio; rode o collector de mercados primeiro.")
            for city in city_rows:
                if city.latitude is None or city.longitude is None:
                    results.append(
                        CityBackfillResult(
                            city_slug=city.slug,
                            status="skipped",
                            reason="missing_lat_lon",
                        )
                    )
                    logger.warning("backfill %s skipped: missing latitude/longitude", city.slug)
                    continue
                n_f, n_o = await backfill_city(
                    session_factory,
                    client,
                    city.slug,
                    city.latitude,
                    city.longitude,
                    settings.deterministic_models,
                    days,
                )
                results.append(
                    CityBackfillResult(
                        city_slug=city.slug,
                        status="ok",
                        forecasts=n_f,
                        observations=n_o,
                    )
                )
                logger.info("backfill %s: %d forecasts, %d observations", city.slug, n_f, n_o)
            return results
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Backfill historical forecasts/observations.")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--cities", help="Comma-separated city slugs, e.g. nyc,shanghai.")
    args = parser.parse_args()
    results = asyncio.run(run_backfill(get_settings(), args.days, cities=parse_cities(args.cities)))
    for result in results:
        if result.status == "ok":
            logger.info(
                "backfill result %s: forecasts=%d observations=%d",
                result.city_slug,
                result.forecasts,
                result.observations,
            )
        else:
            logger.warning(
                "backfill result %s: status=%s reason=%s",
                result.city_slug,
                result.status,
                result.reason,
            )


if __name__ == "__main__":
    main()
