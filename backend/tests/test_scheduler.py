"""Scheduler configuration tests."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.scheduler import build_scheduler
from app.config import Settings


class FakePolymarketClient:
    pass


class FakeOpenMeteoClient:
    pass


class FakeMetarClient:
    pass


def test_default_settings_enable_automated_paper_validation() -> None:
    settings = Settings()

    assert settings.mode == "paper"
    assert settings.collectors_enabled is True
    assert settings.weekly_validation_enabled is True
    assert settings.weekly_validation_hour_utc == 18
    assert settings.cities == ["seoul", "tokyo", "hong-kong"]
    assert settings.validation_history_days == 730
    assert settings.validation_min_samples == 120


async def test_scheduler_registers_weekly_validation_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scheduler = build_scheduler(
        session_factory,
        FakePolymarketClient(),  # type: ignore[arg-type]
        FakeOpenMeteoClient(),  # type: ignore[arg-type]
        FakeMetarClient(),  # type: ignore[arg-type]
        Settings(weekly_validation_enabled=True),
    )
    scheduler.start(paused=True)
    try:
        job_ids = {job.id for job in scheduler.get_jobs()}
    finally:
        scheduler.shutdown(wait=False)

    assert job_ids == {
        "markets",
        "forecasts",
        "observations",
        "resolutions",
        "weekly-validation",
    }


async def test_scheduler_can_disable_weekly_validation_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scheduler = build_scheduler(
        session_factory,
        FakePolymarketClient(),  # type: ignore[arg-type]
        FakeOpenMeteoClient(),  # type: ignore[arg-type]
        FakeMetarClient(),  # type: ignore[arg-type]
        Settings(weekly_validation_enabled=False),
    )
    scheduler.start(paused=True)
    try:
        job_ids = {job.id for job in scheduler.get_jobs()}
    finally:
        scheduler.shutdown(wait=False)

    assert job_ids == {"markets", "forecasts", "observations", "resolutions"}
