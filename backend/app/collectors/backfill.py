"""Backfill histórico: previsões passadas (Open-Meteo) x observações (ERA5).

Uso (com o registry já populado pelo discovery, ou após seed manual):
    uv run python -m app.collectors.backfill --days 365

Cria a base imediata de calibração previsão-vs-realizado sem esperar
meses de coleta. Caveat: a Historical Forecast API arquiva a previsão de
menor lead disponível por dia — os resíduos representam lead ~0-1d.
"""

import argparse
import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.models import Base, DailyObservedMax, ForecastSnapshot
from app.db.session import create_engine, create_session_factory
from app.weather.open_meteo import OpenMeteoClient

logger = logging.getLogger(__name__)


async def backfill_city(
    session_factory: async_sessionmaker[AsyncSession],
    client: OpenMeteoClient,
    city_slug: str,
    lat: float,
    lon: float,
    models: list[str],
    days: int,
) -> tuple[int, int]:
    """Backfill de uma cidade; retorna (n_forecasts, n_observed)."""
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
                        lead_days=1,  # aproximação do lead arquivado (ver docstring)
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
                .values(
                    city_slug=city_slug, target_date=target_date, tmax_c=tmax, source="era5"
                )
                .on_conflict_do_nothing(
                    index_elements=["city_slug", "target_date", "source"]
                )
            )
            result = await session.execute(stmt)
            n_observed += int(getattr(result, "rowcount", 0) or 0)
    return n_forecasts, n_observed


async def run_backfill(settings: Settings, days: int) -> None:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with httpx.AsyncClient(timeout=60.0) as http:
        client = OpenMeteoClient(http)
        from app.collectors.markets import active_cities

        async with session_factory() as session:
            cities = await active_cities(session, settings)
        if not cities:
            logger.warning(
                "registry vazio — rode o collector de mercados primeiro "
                "(ou inicie a API) para descobrir as cidades."
            )
        for city in cities:
            if city.latitude is None or city.longitude is None:
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
            logger.info("backfill %s: %d previsões, %d observações", city.slug, n_f, n_o)

    await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Backfill histórico de previsões/observações")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    asyncio.run(run_backfill(get_settings(), args.days))


if __name__ == "__main__":
    main()
