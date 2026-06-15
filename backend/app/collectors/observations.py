"""Coleta de observações METAR intradiárias nas estações de resolução."""

import logging
from datetime import UTC, datetime

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Observation
from app.weather.metar import MetarClient

logger = logging.getLogger(__name__)


async def collect_observations(
    session_factory: async_sessionmaker[AsyncSession],
    client: MetarClient,
    settings: Settings,
    hours: int = 6,
) -> int:
    """Insere METARs recentes (dedupe por estação+timestamp, INSERT OR IGNORE)."""
    from app.collectors.markets import active_cities

    inserted = 0
    async with session_factory() as session:
        cities = await active_cities(session, settings)

    for city in cities:
        station = city.station_code
        if station is None or station == "HKO":
            # HKO não é estação METAR de aeroporto; intradiário de HK fica
            # para fase futura (fonte oficial do observatório).
            continue
        try:
            observations = await client.recent(station, hours=hours)
        except Exception as exc:
            logger.warning("metar %s falhou: %s", station, exc)
            continue
        if not observations:
            continue
        async with session_factory() as session, session.begin():
            for obs in observations:
                stmt = (
                    sqlite_insert(Observation)
                    .values(
                        city_slug=city.slug,
                        station_code=obs.station,
                        observed_at=obs.observed_at.astimezone(UTC),
                        temp_c=obs.temp_c,
                        source="metar",
                    )
                    .on_conflict_do_nothing(
                        index_elements=["station_code", "observed_at", "source"]
                    )
                )
                result = await session.execute(stmt)
                inserted += int(getattr(result, "rowcount", 0) or 0)

    logger.info("observations collect: %d novas METARs (%s)", inserted, datetime.now(UTC))
    return inserted
