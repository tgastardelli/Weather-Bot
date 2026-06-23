"""Scheduler configuration tests."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.scheduler import build_scheduler, scheduler_effective_settings
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


def test_scheduler_effective_settings_preserves_raw_policy_cities() -> None:
    settings = Settings(cities=["seoul"], strategy_policy_mode="raw", mode="paper")

    effective = scheduler_effective_settings(settings)

    assert effective is settings
    assert effective.cities == ["seoul"]
    assert effective.strategy_policy_mode == "raw"
    assert effective.mode == "paper"


def test_scheduler_effective_settings_forces_high_reward_fast_lane() -> None:
    settings = Settings(
        cities=["seoul", "tokyo", "hong-kong"],
        strategy_policy_mode="repair_v5",
        mode="live",
        live_trading_enabled=True,
    )

    effective = scheduler_effective_settings(settings)

    assert effective is not settings
    assert effective.cities == ["atlanta", "seattle", "toronto"]
    assert effective.strategy_policy_mode == "repair_v5"
    assert effective.mode == "paper"
    assert effective.live_trading_enabled is False


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
