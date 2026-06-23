"""High-reward city hunt tests."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.high_reward_city_hunt import generate_high_reward_city_hunt_report
from analysis.strategy_repair import HistoricalCandidate
from app.config import Settings
from app.db.models import (
    City,
    CityVolatilityMetric,
    HighRewardCityHuntRun,
    PaperFill,
    PaperOrder,
    Signal,
)


def _candidate(city_slug: str, index: int, *, winner: bool) -> HistoricalCandidate:
    ts = datetime(2025, 1, 1, 10, tzinfo=UTC) + timedelta(days=index)
    return HistoricalCandidate(
        ts=ts,
        sampled_ts=ts,
        market_id=f"{city_slug}-m-{index}",
        event_id=f"{city_slug}-e-{index}",
        city_slug=city_slug,
        target_date=date(2025, 1, 1) + timedelta(days=index),
        price=Decimal("0.05000"),
        raw_prob=0.30,
        winner=winner,
        bucket_kind="above",
        bucket_label="hot tail",
        hours_to_close=12.0,
        price_source="data_api_trades",
    )


async def _seed_city(session: AsyncSession, city_slug: str) -> None:
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
    session.add(
        CityVolatilityMetric(
            computed_at=now,
            city_slug=city_slug,
            station_code="TEST",
            n_samples=120,
            forecast_mae_c=3.0,
            tail_miss_rate_2c=0.40,
            tail_miss_rate_3c=0.30,
            tail_miss_rate_5c=0.10,
            upside_surprise_rate_3c=0.20,
            downside_surprise_rate_3c=0.10,
            avg_intraday_range_c=8.0,
            p90_intraday_range_c=14.0,
            max_3h_move_c=6.0,
            max_6h_move_c=8.0,
            reward_volatility_score=80.0,
            data_quality="ok",
            lead_mae_json="{}",
            params_json="{}",
        )
    )


async def test_high_reward_city_hunt_finds_three_asymmetric_cities_without_artifacts(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    cities = ["dallas", "seattle", "tokyo"]
    async with session_factory() as session, session.begin():
        for city in cities:
            await _seed_city(session, city)

    async def fake_candidates(
        session: AsyncSession, settings: Settings
    ) -> tuple[list[HistoricalCandidate], int, dict[str, int], dict[str, int], dict[str, int]]:
        candidates: list[HistoricalCandidate] = []
        for city in cities:
            for index in range(20):
                candidates.append(_candidate(city, index, winner=index % 5 == 0))
        return (
            candidates,
            len(candidates),
            {"data_api_trades": len(candidates), "clob_prices_history": 0},
            {"data_api_trades": len(candidates), "clob_prices_history": 0},
            {"data_api_trades": len(candidates), "clob_prices_history": 0},
        )

    monkeypatch.setattr("analysis.high_reward_city_hunt._historical_candidates", fake_candidates)

    row = await generate_high_reward_city_hunt_report(
        session_factory,
        Settings(max_stake_per_order=Decimal("10")),
        cities=cities,
        days=730,
    )

    async with session_factory() as session:
        assert row.status == "READY_FOR_SHADOW_FAST_LANE"
        assert "low_winrate_allowed" in row.summary_json
        assert (await session.execute(select(func.count(Signal.id)))).scalar_one() == 0
        assert (await session.execute(select(func.count(PaperOrder.id)))).scalar_one() == 0
        assert (await session.execute(select(func.count(PaperFill.id)))).scalar_one() == 0
        assert (
            await session.execute(select(func.count(HighRewardCityHuntRun.id)))
        ).scalar_one() == 1


async def test_high_reward_city_hunt_rejects_bad_payoff_ratio(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    async with session_factory() as session, session.begin():
        await _seed_city(session, "dallas")

    async def fake_candidates(
        session: AsyncSession, settings: Settings
    ) -> tuple[list[HistoricalCandidate], int, dict[str, int], dict[str, int], dict[str, int]]:
        candidates = [_candidate("dallas", index, winner=False) for index in range(20)]
        return (candidates, len(candidates), {"data_api_trades": len(candidates)}, {}, {})

    monkeypatch.setattr("analysis.high_reward_city_hunt._historical_candidates", fake_candidates)

    row = await generate_high_reward_city_hunt_report(
        session_factory,
        Settings(max_stake_per_order=Decimal("10")),
        cities=["dallas"],
        days=730,
    )

    assert row.status == "NO_ASYMMETRIC_EDGE"
    assert "payoff_ratio_below_3x" in row.candidates_json
