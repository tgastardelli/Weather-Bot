"""Resolution backfill tests."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.resolution_backfill import (
    generate_resolution_backfill,
    parse_wunderground_tmax_f,
)
from app.config import Settings
from app.db.models import City, DailyObservedMax


def test_wunderground_parser_rejects_no_data_recorded_page() -> None:
    html = """
    <div class="summary-table"> No data recorded </div>
    <script>{"v3-wx-observations-current":{"temperatureMax24Hour":84}}</script>
    """

    assert parse_wunderground_tmax_f(html) is None


async def test_resolution_backfill_csv_converts_fahrenheit_and_celsius(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 20, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            City(
                slug="nyc",
                name="NYC",
                series_slug="nyc-daily-weather",
                station_code="KNYC",
                station_name=None,
                latitude=40.7789,
                longitude=-73.9692,
                timezone="America/New_York",
                unit="F",
                resolution_source="wunderground",
                resolution_url="https://www.wunderground.com/history/daily/us/ny/new-york-city/KNYC",
                rounding="round",
                needs_review=True,
                active=True,
                updated_at=now,
            )
        )

    csv_path = Path(".test-resolution-backfill-valid.csv")
    try:
        await asyncio.to_thread(
            csv_path.write_text,
            "\n".join(
                [
                    "city_slug,target_date,station_code,tmax,unit,source_url",
                    "nyc,2026-06-18,KNYC,68,F,https://example.test/f",
                    "nyc,2026-06-19,KNYC,20,C,https://example.test/c",
                ]
            ),
            encoding="utf-8",
        )

        row = await generate_resolution_backfill(
            session_factory,
            Settings(cities=["nyc"]),
            cities=["nyc"],
            days=10,
            source="csv",
            csv_path=csv_path,
        )
    finally:
        await asyncio.to_thread(csv_path.unlink, missing_ok=True)

    async with session_factory() as session:
        observations = (
            await session.execute(
                select(DailyObservedMax).order_by(DailyObservedMax.target_date)
            )
        ).scalars().all()

    assert row.status == "OK"
    assert len(observations) == 2
    assert observations[0].source == "resolution"
    assert round(observations[0].tmax_c, 4) == 20.0
    assert round(observations[1].tmax_c, 4) == 20.0
    gates = json.loads(row.gates_json)
    assert gates["trading_artifacts_unchanged"]["passed"] is True


async def test_resolution_backfill_invalid_csv_writes_no_partial_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 20, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            City(
                slug="nyc",
                name="NYC",
                series_slug="nyc-daily-weather",
                station_code="KNYC",
                station_name=None,
                latitude=40.7789,
                longitude=-73.9692,
                timezone="America/New_York",
                unit="F",
                resolution_source="wunderground",
                resolution_url=None,
                rounding="round",
                needs_review=True,
                active=True,
                updated_at=now,
            )
        )

    csv_path = Path(".test-resolution-backfill-invalid.csv")
    try:
        await asyncio.to_thread(
            csv_path.write_text,
            "\n".join(
                [
                    "city_slug,target_date,station_code,tmax,unit,source_url",
                    "nyc,2026-06-18,KNYC,68,F,https://example.test/f",
                    "nyc,2026-06-19,KNYC,20,X,https://example.test/bad",
                ]
            ),
            encoding="utf-8",
        )

        row = await generate_resolution_backfill(
            session_factory,
            Settings(cities=["nyc"]),
            cities=["nyc"],
            days=10,
            source="csv",
            csv_path=csv_path,
        )
    finally:
        await asyncio.to_thread(csv_path.unlink, missing_ok=True)

    async with session_factory() as session:
        count = len((await session.execute(select(DailyObservedMax))).scalars().all())

    assert row.status == "DATA_REVIEW"
    assert count == 0
    assert json.loads(row.gates_json)["source_valid"]["passed"] is False
