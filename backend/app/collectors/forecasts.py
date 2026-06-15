"""Coleta de previsões (determinísticas + ensemble) por cidade ativa."""

import logging
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import EnsembleMember, ForecastSnapshot
from app.weather.open_meteo import OpenMeteoClient

logger = logging.getLogger(__name__)


async def collect_forecasts(
    session_factory: async_sessionmaker[AsyncSession],
    client: OpenMeteoClient,
    settings: Settings,
) -> int:
    """Snapshot determinístico (por modelo) + ensemble (por membro) para cada cidade."""
    from app.collectors.markets import active_cities

    now = datetime.now(UTC)
    snapshots = 0

    async with session_factory() as session:
        cities = await active_cities(session, settings)

    for city in cities:
        lat, lon = city.latitude, city.longitude
        if lat is None or lon is None:
            continue
        try:
            deterministic = await client.daily_tmax_forecast(
                lat, lon, settings.deterministic_models, settings.forecast_days
            )
            ensembles: dict[str, dict[date, list[float]]] = {}
            for model in settings.ensemble_models:
                by_day = await client.ensemble_daily_tmax(
                    lat, lon, model, settings.forecast_days
                )
                if sum(len(members) for members in by_day.values()) == 0:
                    logger.warning("ensemble vazio: city=%s model=%s", city.slug, model)
                ensembles[model] = by_day
        except Exception as exc:
            logger.warning("forecast %s falhou: %s", city.slug, exc)
            continue

        async with session_factory() as session, session.begin():
            for model, series in deterministic.items():
                for target_date, tmax in series:
                    if tmax is None:
                        continue
                    session.add(
                        ForecastSnapshot(
                            fetched_at=now,
                            city_slug=city.slug,
                            source="open_meteo",
                            model=model,
                            target_date=target_date,
                            lead_days=max((target_date - now.date()).days, 0),
                            tmax_c=tmax,
                            n_members=0,
                        )
                    )
                    snapshots += 1
            for model, by_day in ensembles.items():
                for target_date, members in by_day.items():
                    snapshot = ForecastSnapshot(
                        fetched_at=now,
                        city_slug=city.slug,
                        source="open_meteo_ensemble",
                        model=model,
                        target_date=target_date,
                        lead_days=max((target_date - now.date()).days, 0),
                        tmax_c=None,
                        n_members=len(members),
                    )
                    session.add(snapshot)
                    await session.flush()
                    session.add_all(
                        EnsembleMember(snapshot_id=snapshot.id, member=i, tmax_c=value)
                        for i, value in enumerate(members)
                    )
                    snapshots += 1

    logger.info("forecasts collect: %d snapshots para %d cidades", snapshots, len(cities))
    return snapshots
