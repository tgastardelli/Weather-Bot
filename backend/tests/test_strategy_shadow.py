"""Strategy shadow diagnostics tests."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.strategy_shadow import generate_shadow_decisions
from app.config import Settings
from app.db.models import (
    City,
    Event,
    HighRewardCityHuntRun,
    Market,
    PaperFill,
    PaperOrder,
    Signal,
    StrategyDiscoveryRun,
    StrategyShadowDecision,
)


def _historical_candidate(
    city_slug: str,
    index: int,
    *,
    price: Decimal = Decimal("0.05000"),
    raw_prob: float = 0.30,
    winner: bool = True,
) -> object:
    from analysis.strategy_repair import HistoricalCandidate

    ts = datetime(2026, 5, 20, 10, tzinfo=UTC)
    ts = ts.replace(day=min(28, 1 + index))
    return HistoricalCandidate(
        ts=ts,
        sampled_ts=ts,
        market_id=f"{city_slug}-market-{index}",
        event_id=f"{city_slug}-event-{index}",
        city_slug=city_slug,
        target_date=date(2026, 5, min(28, 1 + index)),
        price=price,
        raw_prob=raw_prob,
        winner=winner,
        bucket_kind="above",
        bucket_label="tail",
        hours_to_close=12.0,
        price_source="data_api_trades",
    )


async def test_shadow_without_discovery_does_not_create_trading_artifacts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload = await generate_shadow_decisions(session_factory, Settings())

    async with session_factory() as session:
        signals = (await session.execute(select(func.count(Signal.id)))).scalar_one()
        orders = (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        fills = (await session.execute(select(func.count(PaperFill.id)))).scalar_one()
        decisions = (
            await session.execute(select(func.count(StrategyShadowDecision.id)))
        ).scalar_one()

    assert payload["status"] == "NO_DISCOVERY_RUN"
    assert signals == 0
    assert orders == 0
    assert fills == 0
    assert decisions == 0


async def test_shadow_with_no_candidates_keeps_trading_artifacts_unchanged(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 17, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            City(
                slug="dallas",
                name="Dallas",
                series_slug="dallas-daily-weather",
                station_code="KDAL",
                station_name=None,
                latitude=32.8,
                longitude=-96.8,
                timezone="America/Chicago",
                unit="F",
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
                slug="highest-temperature-in-dallas-on-june-17-2026",
                title="Highest temperature in Dallas on June 17?",
                city_slug="dallas",
                target_date=date(2026, 6, 17),
                end_date=now,
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
                question="Will it be 90°F?",
                group_item_title="90°F",
                group_item_threshold=1,
                bucket_kind="exact",
                bucket_low=Decimal("90"),
                bucket_high=Decimal("90"),
                yes_token_id="yes",
                no_token_id="no",
                tick_size=Decimal("0.001"),
                min_order_size=Decimal("5"),
                closed=True,
                winner=False,
                resolved_at=now,
                updated_at=now,
            )
        )
        session.add(
            StrategyDiscoveryRun(
                run_at=now,
                status="DISCOVERY_CANDIDATE",
                universe="ranked-live",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 17),
                cities_json=json.dumps(["dallas"]),
                summary_json=json.dumps({"discovery_version": "v4"}),
                families_json="{}",
                best_family_json=json.dumps(
                    {"last_fold_payload": {"name": "dallas_fast_lane_n20_edge0_000"}}
                ),
                folds_json="[]",
                gates_json="{}",
            )
        )
        session.add(
            StrategyShadowDecision(
                ts=now,
                policy_name="discovery_v4_shadow",
                market_id="market-1",
                event_id="event-1",
                city_slug="dallas",
                target_date=date(2026, 6, 17),
                raw_prob=0.1,
                calibrated_prob=0.1,
                market_price=Decimal("0.10"),
                edge_net=Decimal("0.01"),
                reason="old",
                would_trade=False,
                segment_key="old",
            )
        )

    payload = await generate_shadow_decisions(
        session_factory,
        Settings(cities=["dallas"], validation_history_days=30),
        cities=["dallas"],
        policy_name="discovery_v4_shadow",
    )

    async with session_factory() as session:
        signals = (await session.execute(select(func.count(Signal.id)))).scalar_one()
        orders = (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        fills = (await session.execute(select(func.count(PaperFill.id)))).scalar_one()
        decisions = (
            await session.execute(select(func.count(StrategyShadowDecision.id)))
        ).scalar_one()

    assert payload["counts_before"] == payload["counts_after"]
    assert signals == 0
    assert orders == 0
    assert fills == 0
    assert decisions == 1


async def test_high_reward_shadow_without_approved_hunt_does_not_create_artifacts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload = await generate_shadow_decisions(
        session_factory,
        Settings(),
        source="high-reward-city-hunt",
        policy_name="high_reward_shadow_v1",
    )

    async with session_factory() as session:
        assert (await session.execute(select(func.count(Signal.id)))).scalar_one() == 0
        assert (await session.execute(select(func.count(PaperOrder.id)))).scalar_one() == 0
        assert (await session.execute(select(func.count(PaperFill.id)))).scalar_one() == 0
        assert (
            await session.execute(select(func.count(StrategyShadowDecision.id)))
        ).scalar_one() == 0

    assert payload["status"] == "NO_APPROVED_HUNT"


async def test_high_reward_shadow_uses_approved_cities_and_preserves_other_policies(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    now = datetime(2026, 6, 22, tzinfo=UTC)
    cities = ["seattle", "seoul", "toronto"]
    approved = [
        {
            "city_slug": "seattle",
            "family": "seasonal_tail_city",
            "side": "YES",
            "variant": "seasonal_tail_city_yes_pxlte0_20_delta0_06_m12",
        },
        {
            "city_slug": "seoul",
            "family": "cheap_tail_no",
            "side": "NO",
            "variant": "cheap_tail_no_no_pxlte0_05_delta0_04",
        },
        {
            "city_slug": "toronto",
            "family": "cheap_tail_no",
            "side": "NO",
            "variant": "cheap_tail_no_no_pxlte0_05_delta0_04",
        },
    ]
    async with session_factory() as session, session.begin():
        for city_slug in cities:
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
            for index in range(20):
                event_id = f"{city_slug}-event-{index}"
                market_id = f"{city_slug}-market-{index}"
                session.add(
                    Event(
                        id=event_id,
                        slug=f"{event_id}-slug",
                        title=event_id,
                        city_slug=city_slug,
                        target_date=date(2026, 5, min(28, 1 + index)),
                        end_date=now,
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
                        winner=city_slug == "seattle",
                        resolved_at=now,
                        updated_at=now,
                    )
                )
        session.add(
            HighRewardCityHuntRun(
                run_at=now,
                status="READY_FOR_SHADOW_FAST_LANE",
                window_start=date(2024, 6, 22),
                window_end=date(2026, 6, 22),
                cities_json=json.dumps(cities),
                summary_json=json.dumps({"approved_city_count": 3}),
                rankings_json="{}",
                candidates_json=json.dumps(
                    {
                        "approved": approved,
                        "approved_all": [
                            *approved,
                            {
                                "city_slug": "seattle",
                                "family": "cheap_tail_yes",
                                "side": "YES",
                                "variant": "cheap_tail_yes_yes_pxlte0_05_delta0_04",
                                "n_trades": 53,
                                "total_pnl": "22897.39",
                                "roi": "43.2026",
                                "payoff_ratio": "166.3380",
                                "blockers": [],
                            },
                        ],
                    }
                ),
                gates_json="{}",
            )
        )
        session.add(
            StrategyShadowDecision(
                ts=now,
                policy_name="other_policy",
                market_id="seattle-market-0",
                event_id="seattle-event-0",
                city_slug="seattle",
                target_date=date(2026, 5, 1),
                raw_prob=0.1,
                calibrated_prob=0.1,
                market_price=Decimal("0.10"),
                edge_net=Decimal("0.01"),
                reason="old",
                would_trade=False,
                segment_key="old",
            )
        )

    async def fake_candidates(
        session: AsyncSession, settings: Settings
    ) -> tuple[list[object], int, dict[str, int], dict[str, int], dict[str, int]]:
        candidates = []
        for city_slug in cities:
            for index in range(20):
                candidates.append(
                    _historical_candidate(
                        city_slug,
                        index,
                        price=Decimal("0.95000")
                        if city_slug in {"seoul", "toronto"}
                        else Decimal("0.05000"),
                        winner=city_slug == "seattle",
                    )
                )
        return (
            candidates,
            len(candidates),
            {"data_api_trades": len(candidates)},
            {"data_api_trades": len(candidates)},
            {"data_api_trades": len(candidates)},
        )

    monkeypatch.setattr("analysis.strategy_shadow._historical_candidates", fake_candidates)

    payload = await generate_shadow_decisions(
        session_factory,
        Settings(max_stake_per_order=Decimal("10")),
        source="high-reward-city-hunt",
        policy_name="high_reward_shadow_v1",
        limit=1000,
    )

    async with session_factory() as session:
        other_policy = (
            await session.execute(
                select(func.count(StrategyShadowDecision.id)).where(
                    StrategyShadowDecision.policy_name == "other_policy"
                )
            )
        ).scalar_one()
        high_reward = (
            await session.execute(
                select(func.count(StrategyShadowDecision.id)).where(
                    StrategyShadowDecision.policy_name == "high_reward_shadow_v1"
                )
            )
        ).scalar_one()
        would_trade = (
            await session.execute(
                select(func.count(StrategyShadowDecision.id)).where(
                    StrategyShadowDecision.policy_name == "high_reward_shadow_v1",
                    StrategyShadowDecision.would_trade.is_(True),
                )
            )
        ).scalar_one()

    assert payload["status"] == "SHADOW_READY_FOR_REVIEW"
    assert payload["trading_artifacts_unchanged"] is True
    assert payload["covered_cities"] == ["seattle", "seoul", "toronto"]
    assert payload["active_variant_by_city"]["seattle"] == "cheap_tail_yes_yes_pxlte0_05_delta0_04"
    assert other_policy == 1
    assert high_reward == 60
    assert would_trade == 60


async def test_high_reward_shadow_uses_approved_all_city_as_active_fallback(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    now = datetime(2026, 6, 22, tzinfo=UTC)
    primary_cities = ["seattle", "seoul", "toronto"]
    fallback_city = "atlanta"
    all_cities = [*primary_cities, fallback_city]
    approved = [
        {
            "city_slug": "seattle",
            "family": "cheap_tail_yes",
            "side": "YES",
            "variant": "cheap_tail_yes_yes_pxlte0_05_delta0_04",
            "n_trades": 53,
            "total_pnl": "22897.39",
            "roi": "43.2026",
            "payoff_ratio": "166.3380",
            "blockers": [],
        },
        {
            "city_slug": "seoul",
            "family": "cheap_tail_no",
            "side": "NO",
            "variant": "cheap_tail_no_no_pxlte0_05_delta0_04",
            "n_trades": 667,
            "total_pnl": "5469.50",
            "roi": "0.8200",
            "payoff_ratio": "100.1620",
            "blockers": [],
        },
        {
            "city_slug": "toronto",
            "family": "cheap_tail_no",
            "side": "NO",
            "variant": "cheap_tail_no_no_pxlte0_05_delta0_04",
            "n_trades": 41,
            "total_pnl": "542.38",
            "roi": "1.3229",
            "payoff_ratio": "94.2380",
            "blockers": [],
        },
    ]
    atlanta = {
        "city_slug": fallback_city,
        "family": "cheap_tail_yes",
        "side": "YES",
        "variant": "cheap_tail_yes_yes_pxlte0_05_delta0_04",
        "n_trades": 89,
        "total_pnl": "14665.91",
        "roi": "16.4786",
        "payoff_ratio": "73.0760",
        "blockers": [],
    }
    async with session_factory() as session, session.begin():
        session.add(
            HighRewardCityHuntRun(
                run_at=now,
                status="READY_FOR_SHADOW_FAST_LANE",
                window_start=date(2024, 6, 22),
                window_end=date(2026, 6, 22),
                cities_json=json.dumps(all_cities),
                summary_json=json.dumps({"approved_city_count": 3}),
                rankings_json=json.dumps({"top_variants": [*approved, atlanta]}),
                candidates_json=json.dumps(
                    {"approved": approved, "approved_all": [*approved, atlanta]}
                ),
                gates_json="{}",
            )
        )

    async def fake_candidates(
        session: AsyncSession, settings: Settings
    ) -> tuple[list[object], int, dict[str, int], dict[str, int], dict[str, int]]:
        candidates = []
        for city_slug in all_cities:
            for index in range(20):
                price = (
                    Decimal("0.95000")
                    if city_slug in {"seoul", "toronto"}
                    else Decimal("0.05000")
                )
                raw_prob = 0.95 if city_slug == "toronto" else 0.30
                candidates.append(
                    _historical_candidate(
                        city_slug,
                        index,
                        price=price,
                        raw_prob=raw_prob,
                        winner=city_slug in {"seattle", "atlanta"},
                    )
                )
        return (
            candidates,
            len(candidates),
            {"data_api_trades": len(candidates)},
            {"data_api_trades": len(candidates)},
            {"data_api_trades": len(candidates)},
        )

    monkeypatch.setattr("analysis.strategy_shadow._historical_candidates", fake_candidates)

    payload = await generate_shadow_decisions(
        session_factory,
        Settings(max_stake_per_order=Decimal("10")),
        source="high-reward-city-hunt",
        policy_name="high_reward_shadow_v1",
        limit=1000,
    )

    assert payload["status"] == "SHADOW_READY_FOR_REVIEW"
    assert payload["approved_cities"] == ["seattle", "seoul", "toronto"]
    assert payload["fallback_active_trade_cities"] == ["atlanta"]
    assert payload["active_trade_cities"] == ["atlanta", "seattle", "seoul"]
