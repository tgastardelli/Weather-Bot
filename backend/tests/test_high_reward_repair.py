"""High-reward repair v5 tests."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.high_reward_repair import generate_high_reward_repair_report
from app.config import Settings
from app.db.models import (
    City,
    Event,
    Market,
    PaperFill,
    PaperOrder,
    Signal,
    StrategyRepairRun,
    StrategyShadowDecision,
)


async def _seed_shadow_city(
    session: AsyncSession,
    city_slug: str,
    *,
    side: str,
    start_index: int,
    n: int = 20,
) -> None:
    now = datetime(2026, 6, 22, tzinfo=UTC)
    session.add(
        City(
            slug=city_slug,
            name=city_slug.title(),
            series_slug=f"{city_slug}-daily-weather",
            station_code="TEST",
            station_name=None,
            latitude=1.0,
            longitude=1.0,
            timezone="UTC",
            unit="F",
            resolution_source="resolution",
            resolution_url=None,
            rounding="round",
            needs_review=False,
            active=True,
            updated_at=now,
        )
    )
    for index in range(n):
        row_index = start_index + index
        target_date = date(2026, 5, 1) + timedelta(days=row_index)
        ts = datetime(2026, 5, 1, 10, tzinfo=UTC) + timedelta(days=row_index)
        event_id = f"{city_slug}-event-{index}"
        market_id = f"{city_slug}-market-{index}"
        yes_winner = index % 5 == 0 if side == "YES" else index % 5 != 0
        session.add(
            Event(
                id=event_id,
                slug=f"{event_id}-slug",
                title=event_id,
                city_slug=city_slug,
                target_date=target_date,
                end_date=ts + timedelta(hours=12),
                neg_risk_market_id=None,
                active=False,
                closed=True,
                volume=None,
                liquidity=None,
                first_seen_at=ts,
                updated_at=ts,
            )
        )
        session.add(
            Market(
                id=market_id,
                event_id=event_id,
                condition_id=f"0x{city_slug}{index}",
                question="Tail?",
                group_item_title="tail",
                group_item_threshold=1,
                bucket_kind="above",
                bucket_low=Decimal("90"),
                bucket_high=None,
                yes_token_id=f"yes-{city_slug}-{index}",
                no_token_id=f"no-{city_slug}-{index}",
                tick_size=Decimal("0.001"),
                min_order_size=Decimal("5"),
                closed=True,
                winner=yes_winner,
                resolved_at=ts + timedelta(hours=12),
                updated_at=ts,
            )
        )
        session.add(
            StrategyShadowDecision(
                ts=ts,
                policy_name="high_reward_shadow_v1",
                market_id=market_id,
                event_id=event_id,
                city_slug=city_slug,
                target_date=target_date,
                raw_prob=0.30 if side == "YES" else 0.70,
                calibrated_prob=0.30,
                market_price=Decimal("0.05000"),
                edge_net=Decimal("0.24750"),
                reason=None,
                would_trade=True,
                segment_key=(
                    f"high_reward|{city_slug}|cheap_tail_{side.lower()}|"
                    f"{side}|cheap_tail_{side.lower()}_pxlte0_05|above|month-05"
                ),
            )
        )


async def test_high_reward_repair_promotes_shadow_ready_policy_without_artifacts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        await _seed_shadow_city(session, "seattle", side="YES", start_index=0)
        await _seed_shadow_city(session, "seoul", side="NO", start_index=30)
        await _seed_shadow_city(session, "toronto", side="NO", start_index=60)

    row = await generate_high_reward_repair_report(
        session_factory,
        Settings(max_stake_per_order=Decimal("10")),
    )

    async with session_factory() as session:
        signals = (await session.execute(select(func.count(Signal.id)))).scalar_one()
        orders = (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        fills = (await session.execute(select(func.count(PaperFill.id)))).scalar_one()
        repairs = (await session.execute(select(func.count(StrategyRepairRun.id)))).scalar_one()

    summary = json.loads(row.summary_json)
    best = json.loads(row.best_variant_json)
    gates = json.loads(row.gates_json)

    assert row.status == "PROMISING"
    assert summary["policy_name"] == "repair_v5_high_reward_v1"
    assert best["policy_version"] == "repair_v5_high_reward"
    assert gates["three_active_cities"]["passed"] is True
    assert gates["payoff_asymmetry"]["passed"] is True
    assert signals == 0
    assert orders == 0
    assert fills == 0
    assert repairs == 1


async def test_high_reward_repair_blocks_when_less_than_three_cities(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        await _seed_shadow_city(session, "seattle", side="YES", start_index=0, n=30)
        await _seed_shadow_city(session, "seoul", side="NO", start_index=40, n=30)

    row = await generate_high_reward_repair_report(
        session_factory,
        Settings(max_stake_per_order=Decimal("10")),
    )

    gates = json.loads(row.gates_json)

    assert row.status == "SHADOW_REVIEW"
    assert gates["three_active_cities"]["passed"] is False
