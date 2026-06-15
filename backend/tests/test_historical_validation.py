"""Historical validation report tests."""

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.historical_validation import generate_historical_validation_report
from app.config import Settings
from app.db.models import City, HistoricalValidationRun


async def test_historical_validation_fails_when_history_is_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 10, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            City(
                slug="seoul",
                name="Seoul",
                series_slug="seoul-daily-weather",
                station_code="RKSI",
                station_name=None,
                latitude=37.4602,
                longitude=126.4407,
                timezone="Asia/Seoul",
                unit="C",
                resolution_source="wunderground",
                resolution_url=None,
                rounding="round",
                needs_review=False,
                active=True,
                updated_at=now,
            )
        )

    row = await generate_historical_validation_report(
        session_factory,
        Settings(cities=["seoul"], validation_history_days=30),
        cities=["seoul"],
        days=30,
    )

    gates = json.loads(row.gates_json)
    assert row.status == "INSUFFICIENT_HISTORY"
    assert gates["historical_samples"]["passed"] is False
    assert gates["historical_trades"]["passed"] is False

    async with session_factory() as session:
        saved = (await session.execute(select(HistoricalValidationRun))).scalars().all()

    assert len(saved) == 1
