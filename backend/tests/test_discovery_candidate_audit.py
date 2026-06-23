"""Discovery candidate audit tests."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.discovery_candidate_audit import (
    _city_resolution_audit,
    generate_discovery_candidate_audit_report,
)
from app.config import Settings
from app.db.models import (
    City,
    DailyObservedMax,
    DiscoveryCandidateAuditRun,
    Event,
    Market,
    PaperFill,
    PaperOrder,
    Signal,
)


def _city(now: datetime, *, needs_review: bool = True) -> City:
    return City(
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
        needs_review=needs_review,
        active=True,
        updated_at=now,
    )


def _event(now: datetime) -> Event:
    return Event(
        id="event-nyc",
        slug="highest-temperature-in-nyc-on-june-10-2026",
        title="Highest temperature in NYC on June 10, 2026?",
        city_slug="nyc",
        target_date=date(2026, 6, 10),
        end_date=datetime(2026, 6, 11, 12, tzinfo=UTC),
        neg_risk_market_id=None,
        active=False,
        closed=True,
        volume=None,
        liquidity=None,
        first_seen_at=now,
        updated_at=now,
    )


def _market(*, winner: bool) -> Market:
    return Market(
        id="market-nyc",
        event_id="event-nyc",
        condition_id="0xcond",
        question="Will it be 86F?",
        group_item_title="86F",
        group_item_threshold=0,
        bucket_kind="exact",
        bucket_low=Decimal("86"),
        bucket_high=Decimal("86"),
        yes_token_id="yes-token",
        no_token_id="no-token",
        tick_size=Decimal("0.001"),
        min_order_size=Decimal("5"),
        closed=True,
        winner=winner,
        resolved_at=datetime(2026, 6, 11, 12, tzinfo=UTC),
        updated_at=datetime(2026, 6, 11, 12, tzinfo=UTC),
    )


async def test_city_resolution_audit_detects_winner_mismatch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 12, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(_city(now))
        session.add(_event(now))
        session.add(_market(winner=False))
        session.add(
            DailyObservedMax(
                city_slug="nyc",
                target_date=date(2026, 6, 10),
                tmax_c=30.0,
                source="era5",
            )
        )

    async with session_factory() as session:
        audit = await _city_resolution_audit(
            session,
            traded_cities={"nyc"},
            research_only={"nyc"},
            window_start=date(2026, 6, 1),
            window_end=date(2026, 6, 30),
        )

    assert audit["valid"] is False
    assert audit["issues"] == ["nyc:winner_mismatch"]
    city = audit["cities"][0]  # type: ignore[index]
    assert city["mismatches"] == 1


async def test_discovery_candidate_audit_without_candidate_is_data_review(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    row = await generate_discovery_candidate_audit_report(
        session_factory,
        Settings(validation_history_days=30),
        days=30,
    )

    async with session_factory() as session:
        signals = (await session.execute(select(func.count(Signal.id)))).scalar_one()
        orders = (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        fills = (await session.execute(select(func.count(PaperFill.id)))).scalar_one()
        persisted = (await session.execute(select(DiscoveryCandidateAuditRun))).scalar_one()

    assert row.id == persisted.id
    assert row.status == "DATA_REVIEW"
    assert signals == 0
    assert orders == 0
    assert fills == 0
    assert json.loads(row.summary_json)["cannot_approve_live"] is True
