"""Compute forecast calibration metrics from historical forecasts and observations."""

import asyncio
import logging
from datetime import UTC, datetime
from statistics import fmean, pstdev

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.models import Base, CalibrationMetric, DailyObservedMax, ForecastSnapshot
from app.db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)


async def compute_calibration(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Persist bias/MAE/std by city, model and lead_days."""
    computed_at = datetime.now(UTC)
    async with session_factory() as session:
        observed_rows = (
            await session.execute(
                select(DailyObservedMax).where(DailyObservedMax.source.in_(["era5", "resolution"]))
            )
        ).scalars().all()
        observed = {
            (row.city_slug, row.target_date): row.tmax_c
            for row in observed_rows
        }
        forecasts = (
            await session.execute(
                select(ForecastSnapshot).where(ForecastSnapshot.tmax_c.is_not(None))
            )
        ).scalars().all()

    groups: dict[tuple[str, str, int], list[float]] = {}
    for forecast in forecasts:
        actual = observed.get((forecast.city_slug, forecast.target_date))
        if actual is None or forecast.tmax_c is None:
            continue
        key = (forecast.city_slug, forecast.model, forecast.lead_days)
        groups.setdefault(key, []).append(actual - forecast.tmax_c)

    written = 0
    async with session_factory() as session, session.begin():
        for (city_slug, model, lead_days), residuals in groups.items():
            if not residuals:
                continue
            stmt = (
                sqlite_insert(CalibrationMetric)
                .values(
                    computed_at=computed_at,
                    city_slug=city_slug,
                    model=model,
                    lead_days=lead_days,
                    bias_c=fmean(residuals),
                    mae_c=fmean(abs(value) for value in residuals),
                    residual_std_c=pstdev(residuals) if len(residuals) > 1 else 0.0,
                    n_samples=len(residuals),
                )
                .on_conflict_do_update(
                    index_elements=["city_slug", "model", "lead_days"],
                    set_={
                        "computed_at": computed_at,
                        "bias_c": fmean(residuals),
                        "mae_c": fmean(abs(value) for value in residuals),
                        "residual_std_c": pstdev(residuals) if len(residuals) > 1 else 0.0,
                        "n_samples": len(residuals),
                    },
                )
            )
            await session.execute(stmt)
            written += 1
    return written


async def run(settings: Settings) -> int:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await compute_calibration(session_factory)
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    written = asyncio.run(run(get_settings()))
    logger.info("calibration metrics written: %d", written)


if __name__ == "__main__":
    main()
