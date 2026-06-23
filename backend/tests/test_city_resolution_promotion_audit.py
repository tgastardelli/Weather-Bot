"""City resolution promotion audit tests."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.city_resolution_promotion_audit import (
    generate_city_resolution_promotion_audit_report,
)
from app.config import Settings
from app.db.models import (
    City,
    DailyObservedMax,
    Event,
    Market,
    MarketTradeHistoryPoint,
)


async def test_city_resolution_promotion_requires_matching_winner(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 19, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            City(
                slug="chicago",
                name="Chicago",
                series_slug="chicago-daily-weather",
                station_code="KMDW",
                station_name=None,
                latitude=41.7868,
                longitude=-87.7522,
                timezone="America/Chicago",
                unit="F",
                resolution_source="wunderground",
                resolution_url=None,
                rounding="round",
                needs_review=True,
                active=True,
                updated_at=now,
            )
        )
        session.add(
            Event(
                id="event-1",
                slug="highest-temperature-in-chicago-on-june-1-2026",
                title="Highest temperature in Chicago on June 1, 2026?",
                city_slug="chicago",
                target_date=date(2026, 6, 1),
                end_date=datetime(2026, 6, 2, 12, tzinfo=UTC),
                neg_risk_market_id=None,
                active=False,
                closed=True,
                volume=None,
                liquidity=None,
                first_seen_at=now,
                updated_at=now,
            )
        )
        session.add(
            Market(
                id="market-1",
                event_id="event-1",
                condition_id="0x1",
                question="Will it be 80°F?",
                group_item_title="80°F",
                group_item_threshold=1,
                bucket_kind="exact",
                bucket_low=Decimal("80"),
                bucket_high=Decimal("80"),
                yes_token_id="yes",
                no_token_id="no",
                tick_size=Decimal("0.001"),
                min_order_size=Decimal("5"),
                closed=True,
                winner=True,
                resolved_at=now,
                updated_at=now,
            )
        )
        session.add(
            Market(
                id="market-2",
                event_id="event-1",
                condition_id="0x2",
                question="Will it be 27°C?",
                group_item_title="27°C",
                group_item_threshold=2,
                bucket_kind="exact",
                bucket_low=Decimal("27"),
                bucket_high=Decimal("27"),
                yes_token_id="yes-c",
                no_token_id="no-c",
                tick_size=Decimal("0.001"),
                min_order_size=Decimal("5"),
                closed=True,
                winner=True,
                resolved_at=now,
                updated_at=now,
            )
        )
        session.add(
            DailyObservedMax(
                city_slug="chicago",
                target_date=date(2026, 6, 1),
                tmax_c=26.6667,
                source="resolution",
            )
        )
        session.add(
            DailyObservedMax(
                city_slug="chicago",
                target_date=date(2026, 6, 1),
                tmax_c=0,
                source="era5",
            )
        )
        session.add(
            MarketTradeHistoryPoint(
                ts=now,
                market_id="market-1",
                token_id="yes",
                condition_id="0x1",
                price=Decimal("0.50"),
                size=Decimal("10"),
                side="BUY",
                transaction_hash="0xabc",
                source="data_api_trades",
            )
        )
        session.add(
            MarketTradeHistoryPoint(
                ts=now,
                market_id="market-2",
                token_id="yes-c",
                condition_id="0x2",
                price=Decimal("0.50"),
                size=Decimal("10"),
                side="BUY",
                transaction_hash="0xdef",
                source="data_api_trades",
            )
        )

    row = await generate_city_resolution_promotion_audit_report(
        session_factory,
        Settings(),
        cities=["chicago"],
        days=730,
    )
    resolution = json.loads(row.resolution_json)

    assert row.status == "READY_FOR_EXPANDED_DISCOVERY"
    assert resolution["cities"][0]["promotion_status"] == "LIVE_ELIGIBLE_CANDIDATE"
    assert resolution["cities"][0]["resolution_points"] == 1
    assert resolution["cities"][0]["resolution_source_used"] == "resolution"
    assert json.loads(row.gates_json)["live_release"]["passed"] is False
