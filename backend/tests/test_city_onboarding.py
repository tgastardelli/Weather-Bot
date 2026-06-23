"""City onboarding tests."""

import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.city_onboarding import generate_city_onboarding_report
from app.config import Settings
from app.db.models import (
    CalibrationMetric,
    City,
    CityOnboardingRun,
    PaperFill,
    PaperOrder,
    Signal,
)


def _city(
    slug: str,
    now: datetime,
    *,
    needs_review: bool = False,
    station_code: str | None = "RKSI",
    latitude: float | None = 1.0,
    longitude: float | None = 1.0,
) -> City:
    return City(
        slug=slug,
        name=slug.title(),
        series_slug=f"{slug}-daily-weather",
        station_code=station_code,
        station_name=None,
        latitude=latitude,
        longitude=longitude,
        timezone="UTC",
        unit="C",
        resolution_source="wunderground",
        resolution_url=None,
        rounding="round",
        needs_review=needs_review,
        active=True,
        updated_at=now,
    )


async def test_city_onboarding_classifies_metadata_and_climate_failures(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 18, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                _city("nyc", now, needs_review=True, station_code=None),
                _city("shanghai", now, latitude=None, longitude=None),
            ]
        )
        session.add(
            CalibrationMetric(
                computed_at=now,
                city_slug="nyc",
                model="ensemble_pool",
                lead_days=1,
                bias_c=0,
                mae_c=1,
                residual_std_c=1,
                n_samples=130,
            )
        )

    row = await generate_city_onboarding_report(
        session_factory,
        Settings(validation_min_samples=120),
        cities=["nyc", "shanghai"],
        days=730,
    )
    checks = {item["city_slug"]: item for item in json.loads(row.checks_json)}

    assert row.status == "DATA_REVIEW"
    assert checks["nyc"]["classification"] == "excluded"
    assert checks["nyc"]["checks"]["metadata"]["passed"] is False
    assert checks["nyc"]["checks"]["climate"]["passed"] is True
    assert checks["shanghai"]["checks"]["metadata"]["passed"] is False
    assert checks["shanghai"]["checks"]["climate"]["passed"] is False

    async with session_factory() as session:
        persisted = (await session.execute(select(CityOnboardingRun))).scalar_one()
        signals = (await session.execute(select(func.count(Signal.id)))).scalar_one()
        orders = (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        fills = (await session.execute(select(func.count(PaperFill.id)))).scalar_one()

    assert persisted.id == row.id
    assert signals == 0
    assert orders == 0
    assert fills == 0


async def test_city_onboarding_repair_metadata_keeps_city_research_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 18, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(_city("nyc", now, needs_review=True, station_code=None))
        session.add(
            CalibrationMetric(
                computed_at=now,
                city_slug="nyc",
                model="ensemble_pool",
                lead_days=1,
                bias_c=0,
                mae_c=1,
                residual_std_c=1,
                n_samples=130,
            )
        )

    row = await generate_city_onboarding_report(
        session_factory,
        Settings(validation_min_samples=120),
        cities=["nyc"],
        days=730,
        repair_metadata=True,
    )
    checks = json.loads(row.checks_json)
    summary = json.loads(row.summary_json)

    assert summary["repair_metadata"] is True
    assert "nyc" in summary["repaired_metadata"]
    assert checks[0]["checks"]["metadata"]["passed"] is True
    assert checks[0]["classification"] == "excluded"

    async with session_factory() as session:
        city = await session.get(City, "nyc")

    assert city is not None
    assert city.station_code == "KNYC"
    assert city.latitude is not None
    assert city.longitude is not None
    assert city.timezone == "America/New_York"
    assert city.needs_review is True
