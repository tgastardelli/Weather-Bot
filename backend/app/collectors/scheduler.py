"""Agendamento dos collectors (APScheduler async) — iniciado no lifespan."""

import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.forecasts import collect_forecasts
from app.collectors.markets import collect_markets
from app.collectors.observations import collect_observations
from app.collectors.resolutions import collect_resolutions
from app.config import Settings
from app.execution.paper import settle_resolved_positions, submit_proposed_signals
from app.polymarket.client import PolymarketPublicClient
from app.strategy.engine import scan_and_store_signals
from app.weather.metar import MetarClient
from app.weather.open_meteo import OpenMeteoClient

logger = logging.getLogger(__name__)


async def _run_signal_scan(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    async with session_factory() as session, session.begin():
        signals = await scan_and_store_signals(session, settings)
        stats = await submit_proposed_signals(session, settings, signals=signals)
        logger.info(
            "paper execution: orders=%d fills=%d rejected=%d",
            stats.orders,
            stats.fills,
            stats.rejected,
        )


async def _run_paper_settlement(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    async with session_factory() as session, session.begin():
        stats = await settle_resolved_positions(session, settings)
        if stats.settled:
            logger.info("paper settlement: settled=%d fills=%d", stats.settled, stats.fills)


async def _run_evidence_report(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    from analysis.evidence import generate_evidence_report

    await generate_evidence_report(session_factory, settings, cities=settings.cities)


async def _run_measurement_report(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    from analysis.measurement import build_measurement_report

    await build_measurement_report(session_factory, settings)


async def _run_weekly_validation(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    from analysis.backtest import run_backtest
    from analysis.calibration import compute_calibration
    from analysis.city_volatility import run_city_volatility
    from analysis.evidence import generate_evidence_report
    from analysis.historical_validation import generate_historical_validation_report
    from analysis.measurement import build_measurement_report

    logger.info("weekly validation started")
    calibration_rows = await compute_calibration(session_factory)
    volatility_rows = await run_city_volatility(
        settings,
        days=settings.validation_history_days,
        cities=settings.cities,
        min_samples=settings.validation_min_samples,
    )
    backtest_results = await run_backtest(session_factory, settings, mode="both")
    historical = await generate_historical_validation_report(
        session_factory,
        settings,
        cities=settings.cities,
        days=settings.validation_history_days,
    )
    await generate_evidence_report(session_factory, settings, cities=settings.cities)
    measurement = await build_measurement_report(session_factory, settings)
    evidence = await generate_evidence_report(session_factory, settings, cities=settings.cities)
    logger.info(
        (
            "weekly validation finished: calibration=%d volatility=%d backtests=%d "
            "measurement=%s historical=%s evidence=%s"
        ),
        calibration_rows,
        len(volatility_rows),
        len(backtest_results),
        measurement.status,
        historical.status,
        evidence.status,
    )


def build_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    pm_client: PolymarketPublicClient,
    om_client: OpenMeteoClient,
    metar_client: MetarClient,
    settings: Settings,
) -> AsyncIOScheduler:
    """Monta o scheduler com jobs idempotentes (não inicia)."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    common: dict[str, Any] = {
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 120,
    }
    weekly_common: dict[str, Any] = {
        **common,
        "misfire_grace_time": 3600,
    }

    async def markets_job() -> None:
        await collect_markets(session_factory, pm_client, settings)

    async def forecasts_job() -> None:
        await collect_forecasts(session_factory, om_client, settings)
        await _run_signal_scan(session_factory, settings)

    async def observations_job() -> None:
        await collect_observations(session_factory, metar_client, settings)

    async def resolutions_job() -> None:
        await collect_resolutions(session_factory, pm_client)
        await _run_paper_settlement(session_factory, settings)
        await _run_evidence_report(session_factory, settings)
        await _run_measurement_report(session_factory, settings)

    async def weekly_validation_job() -> None:
        await _run_weekly_validation(session_factory, settings)

    scheduler.add_job(
        markets_job, "interval", minutes=settings.markets_interval_minutes,
        id="markets", **common,
    )
    scheduler.add_job(
        forecasts_job, "interval", minutes=settings.forecasts_interval_minutes,
        id="forecasts", **common,
    )
    scheduler.add_job(
        observations_job, "interval", minutes=settings.observations_interval_minutes,
        id="observations", **common,
    )
    scheduler.add_job(
        resolutions_job, "interval", minutes=settings.resolutions_interval_minutes,
        id="resolutions", **common,
    )
    if settings.weekly_validation_enabled:
        scheduler.add_job(
            weekly_validation_job,
            "cron",
            day_of_week=settings.weekly_validation_day_of_week,
            hour=settings.weekly_validation_hour_utc,
            minute=settings.weekly_validation_minute_utc,
            id="weekly-validation",
            **weekly_common,
        )
    logger.info(
        (
            "scheduler montado: markets=%dmin forecasts=%dmin obs=%dmin "
            "resolutions=%dmin weekly_validation=%s"
        ),
        settings.markets_interval_minutes,
        settings.forecasts_interval_minutes,
        settings.observations_interval_minutes,
        settings.resolutions_interval_minutes,
        settings.weekly_validation_enabled,
    )
    return scheduler
