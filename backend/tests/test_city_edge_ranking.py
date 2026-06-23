"""City edge ranking tests."""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis import city_edge_ranking
from analysis.city_edge_ranking import generate_city_edge_ranking_report
from app.config import Settings
from app.db.models import (
    CalibrationMetric,
    City,
    CityEdgeRankingRun,
    PaperFill,
    PaperOrder,
    Signal,
)


def _city(slug: str, *, needs_review: bool) -> City:
    return City(
        slug=slug,
        name=slug.title(),
        series_slug=f"{slug}-daily-weather",
        station_code="KAAA",
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
        updated_at=datetime(2026, 6, 18, tzinfo=UTC),
    )


def _calibration(slug: str, *, samples: int = 120) -> CalibrationMetric:
    return CalibrationMetric(
        computed_at=datetime(2026, 6, 18, tzinfo=UTC),
        city_slug=slug,
        model="ensemble_pool",
        lead_days=1,
        bias_c=0,
        mae_c=1,
        residual_std_c=1,
        n_samples=samples,
    )


async def test_city_edge_ranking_keeps_needs_review_as_research_only(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
) -> None:
    async def fake_candidates(
        *args: object,
        **kwargs: object,
    ) -> tuple[list[object], int, dict[str, int], dict[str, int], dict[str, int]]:
        return (
            [],
            100,
            {"market_trade_history_points": 100},
            {"market_trade_history_points": 100},
            {"market_trade_history_points": 100},
        )

    def fake_rolling(
        *args: object,
        **kwargs: object,
    ) -> tuple[dict[str, object], list[dict[str, object]], dict[str, object]]:
        best = {
            "family": "market_anchor",
            "name": "discovery_oos_market_anchor",
            "profile": {
                "n_resolved_trades": 80,
                "total_pnl": "10",
                "brier_delta": 0.05,
                "top_5_abs_pnl_share": "0.10",
                "pnl_ci_high": "5",
            },
        }
        return best, [], {"valid_folds": 3, "fold_count": 3}

    async def fake_resolved(session: AsyncSession) -> dict[str, int]:
        return {"seoul": 80, "nyc": 80}

    async def fake_trades(session: AsyncSession) -> dict[str, int]:
        return {"seoul": 1000, "nyc": 1000}

    async def fake_prices(session: AsyncSession) -> dict[str, int]:
        return {}

    monkeypatch.setattr(city_edge_ranking, "_historical_candidates", fake_candidates)
    monkeypatch.setattr(city_edge_ranking, "_rolling_origin", fake_rolling)
    monkeypatch.setattr(city_edge_ranking, "_resolved_markets_by_city", fake_resolved)
    monkeypatch.setattr(city_edge_ranking, "_trade_history_by_city", fake_trades)
    monkeypatch.setattr(city_edge_ranking, "_price_history_by_city", fake_prices)

    async with session_factory() as session, session.begin():
        session.add_all([_city("seoul", needs_review=False), _city("nyc", needs_review=True)])
        session.add_all([_calibration("seoul"), _calibration("nyc")])

    row = await generate_city_edge_ranking_report(
        session_factory,
        Settings(validation_min_samples=120),
        days=730,
    )

    live = json.loads(row.cities_json)
    research = json.loads(row.research_json)
    assert [city["city_slug"] for city in live] == ["seoul"]
    assert [city["city_slug"] for city in research] == ["nyc"]
    assert live[0]["eligible_for_targeted_discovery"] is True
    assert research[0]["operational_quarantine"] is True
    assert "operational_quarantine" in research[0]["rejection_reasons"]
    assert "resolution_not_verified" in research[0]["rejection_reasons"]
    assert "needs_review_research_only" in research[0]["rejection_reasons"]
    assert json.loads(row.summary_json)["quarantined_diagnostic_cities"] == ["nyc"]
    assert json.loads(row.gates_json)["live_release"]["passed"] is False


async def test_city_edge_ranking_quarantines_nyc_even_if_needs_review_false(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
) -> None:
    async def fake_resolved(session: AsyncSession) -> dict[str, int]:
        return {"nyc": 80}

    async def fake_trades(session: AsyncSession) -> dict[str, int]:
        return {"nyc": 1000}

    async def fake_prices(session: AsyncSession) -> dict[str, int]:
        return {}

    monkeypatch.setattr(city_edge_ranking, "_resolved_markets_by_city", fake_resolved)
    monkeypatch.setattr(city_edge_ranking, "_trade_history_by_city", fake_trades)
    monkeypatch.setattr(city_edge_ranking, "_price_history_by_city", fake_prices)

    async with session_factory() as session, session.begin():
        session.add(_city("nyc", needs_review=False))
        session.add(_calibration("nyc"))

    row = await generate_city_edge_ranking_report(
        session_factory,
        Settings(validation_min_samples=120),
        days=730,
    )

    assert json.loads(row.cities_json) == []
    research = json.loads(row.research_json)
    assert [city["city_slug"] for city in research] == ["nyc"]
    assert research[0]["classification"] == "research_only"
    assert research[0]["eligible_for_targeted_discovery"] is False
    assert json.loads(row.gates_json)["operational_quarantine"]["passed"] is True


async def test_city_edge_ranking_does_not_create_trading_artifacts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    row = await generate_city_edge_ranking_report(
        session_factory,
        Settings(validation_history_days=30),
        days=30,
    )

    async with session_factory() as session:
        signals = (await session.execute(select(func.count(Signal.id)))).scalar_one()
        orders = (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        fills = (await session.execute(select(func.count(PaperFill.id)))).scalar_one()
        persisted = (await session.execute(select(CityEdgeRankingRun))).scalar_one()

    assert row.id == persisted.id
    assert signals == 0
    assert orders == 0
    assert fills == 0
    assert json.loads(row.summary_json)["cannot_approve_live"] is True
