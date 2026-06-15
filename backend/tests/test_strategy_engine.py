"""Strategy engine regression tests."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    City,
    EnsembleMember,
    Event,
    ForecastSnapshot,
    Market,
    MarketPriceSnapshot,
)
from app.strategy.engine import scan_and_store_signals


async def _add_signal_fixture(
    session: AsyncSession,
    *,
    ask: Decimal,
    with_ensemble: bool,
) -> datetime:
    now = datetime(2026, 6, 10, 10, tzinfo=UTC)
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
    session.add(
        Event(
            id="event-1",
            slug="highest-temperature-in-seoul-on-june-10-2026",
            title="Highest temperature in Seoul on June 10, 2026?",
            city_slug="seoul",
            target_date=date(2026, 6, 10),
            end_date=datetime(2026, 6, 11, 12, tzinfo=UTC),
            neg_risk_market_id=None,
            active=True,
            closed=False,
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
            condition_id="0xcond",
            question="Will it be 25C?",
            group_item_title="25C",
            group_item_threshold=0,
            bucket_kind="exact",
            bucket_low=Decimal("25"),
            bucket_high=Decimal("25"),
            yes_token_id="yes-token",
            no_token_id="no-token",
            tick_size=Decimal("0.001"),
            min_order_size=Decimal("5"),
            closed=False,
            winner=None,
            resolved_at=None,
            updated_at=now,
        )
    )
    session.add(
        MarketPriceSnapshot(
            ts=now,
            market_id="market-1",
            best_bid=ask - Decimal("0.01"),
            best_ask=ask,
            mid=ask - Decimal("0.005"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
        )
    )
    if with_ensemble:
        snapshot = ForecastSnapshot(
            fetched_at=now,
            city_slug="seoul",
            source="open_meteo_ensemble",
            model="gfs",
            target_date=date(2026, 6, 10),
            lead_days=0,
            tmax_c=None,
            n_members=1,
        )
        session.add(snapshot)
        await session.flush()
        session.add(EnsembleMember(snapshot_id=snapshot.id, member=0, tmax_c=25.0))
    await session.flush()
    return now


async def test_scan_does_not_emit_without_ensemble(session: AsyncSession) -> None:
    now = await _add_signal_fixture(session, ask=Decimal("0.20"), with_ensemble=False)

    signals = await scan_and_store_signals(
        session,
        Settings(ensemble_models=["gfs"], min_edge_net=Decimal("0.01")),
        now=now,
    )

    assert signals == []


@pytest.mark.parametrize(
    ("ask", "expected_profiles"),
    [
        (Decimal("0.20"), ["longshot", "max_edge"]),
        (Decimal("0.21"), ["max_edge"]),
    ],
)
async def test_longshot_profile_requires_configured_max_price(
    session: AsyncSession,
    ask: Decimal,
    expected_profiles: list[str],
) -> None:
    now = await _add_signal_fixture(session, ask=ask, with_ensemble=True)

    signals = await scan_and_store_signals(
        session,
        Settings(
            ensemble_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
        ),
        now=now,
    )

    assert sorted(signal.profile for signal in signals) == expected_profiles
