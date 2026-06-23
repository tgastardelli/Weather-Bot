"""City research audit tests."""

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.city_research_audit import generate_city_research_audit_report
from app.config import Settings
from app.db.models import CalibrationMetric, City, CityResearchAuditRun


def _city(slug: str, now: datetime, *, needs_review: bool = False) -> City:
    return City(
        slug=slug,
        name=slug.title(),
        series_slug=f"{slug}-daily-weather",
        station_code="RKSI",
        station_name=None,
        latitude=1.0,
        longitude=1.0,
        timezone="UTC",
        unit="C",
        resolution_source="wunderground",
        resolution_url=None,
        rounding="round",
        needs_review=needs_review,
        active=True,
        updated_at=now,
    )


async def test_city_research_audit_classifies_needs_review_as_research_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 18, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add_all([_city("seoul", now), _city("nyc", now, needs_review=True)])
        session.add_all(
            [
                CalibrationMetric(
                    computed_at=now,
                    city_slug="seoul",
                    model="ensemble_pool",
                    lead_days=1,
                    bias_c=0,
                    mae_c=1,
                    residual_std_c=1,
                    n_samples=120,
                ),
                CalibrationMetric(
                    computed_at=now,
                    city_slug="nyc",
                    model="ensemble_pool",
                    lead_days=1,
                    bias_c=0,
                    mae_c=1,
                    residual_std_c=1,
                    n_samples=120,
                ),
            ]
        )

    row = await generate_city_research_audit_report(
        session_factory,
        Settings(validation_min_samples=120),
        days=730,
    )
    cities = {item["city_slug"]: item for item in json.loads(row.cities_json)}
    summary = json.loads(row.summary_json)

    assert cities["seoul"]["classification"] == "live_eligible"
    assert cities["nyc"]["classification"] == "research_only"
    assert "needs_review" in cities["nyc"]["reasons"]
    assert summary["cannot_approve_live"] is True

    async with session_factory() as session:
        persisted = (await session.execute(select(CityResearchAuditRun))).scalar_one()
    assert persisted.id == row.id
