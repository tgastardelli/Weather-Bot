"""API tests using ASGITransport, no real server."""

from datetime import UTC, date, datetime
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    City,
    CityVolatilityMetric,
    Event,
    HistoricalValidationRun,
    HistoryBackfillRun,
    Market,
    MarketPriceSnapshot,
)
from app.main import app


async def test_markets_endpoint(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
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
        session.add(
            Event(
                id="123",
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
                id="m1",
                event_id="123",
                condition_id="0xcond",
                question="Will it be 23°C or below?",
                group_item_title="23°C or below",
                group_item_threshold=0,
                bucket_kind="below",
                bucket_low=None,
                bucket_high=Decimal("23"),
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
                market_id="m1",
                best_bid=Decimal("0.10"),
                best_ask=Decimal("0.12"),
                mid=Decimal("0.11"),
                bid_size=Decimal("100"),
                ask_size=Decimal("50"),
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/markets")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["city_slug"] == "seoul"
    assert body[0]["buckets"][0]["best_ask"] == "0.12"


async def test_city_volatility_endpoint_returns_empty_without_saved_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/city-volatility")

    assert response.status_code == 200
    assert response.json() == []


async def test_city_volatility_endpoint_returns_latest_saved_ranking(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_run = datetime(2026, 6, 10, tzinfo=UTC)
    latest_run = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                CityVolatilityMetric(
                    computed_at=first_run,
                    city_slug="old-city",
                    station_code="KOLD",
                    n_samples=10,
                    forecast_mae_c=1.0,
                    tail_miss_rate_2c=0.1,
                    tail_miss_rate_3c=0.0,
                    tail_miss_rate_5c=0.0,
                    upside_surprise_rate_3c=0.0,
                    downside_surprise_rate_3c=0.0,
                    avg_intraday_range_c=5.0,
                    p90_intraday_range_c=6.0,
                    max_3h_move_c=2.0,
                    max_6h_move_c=3.0,
                    reward_volatility_score=10.0,
                    data_quality="ok",
                    lead_mae_json="{}",
                    params_json="{}",
                ),
                CityVolatilityMetric(
                    computed_at=latest_run,
                    city_slug="wild",
                    station_code="KWLD",
                    n_samples=100,
                    forecast_mae_c=4.0,
                    tail_miss_rate_2c=0.6,
                    tail_miss_rate_3c=0.4,
                    tail_miss_rate_5c=0.1,
                    upside_surprise_rate_3c=0.3,
                    downside_surprise_rate_3c=0.1,
                    avg_intraday_range_c=12.0,
                    p90_intraday_range_c=18.0,
                    max_3h_move_c=7.0,
                    max_6h_move_c=9.0,
                    reward_volatility_score=85.0,
                    data_quality="ok",
                    lead_mae_json='{"1": 4.0}',
                    params_json='{"days": 730}',
                ),
                CityVolatilityMetric(
                    computed_at=latest_run,
                    city_slug="stable",
                    station_code="KSTB",
                    n_samples=100,
                    forecast_mae_c=1.0,
                    tail_miss_rate_2c=0.05,
                    tail_miss_rate_3c=0.01,
                    tail_miss_rate_5c=0.0,
                    upside_surprise_rate_3c=0.01,
                    downside_surprise_rate_3c=0.0,
                    avg_intraday_range_c=4.0,
                    p90_intraday_range_c=5.0,
                    max_3h_move_c=2.0,
                    max_6h_move_c=3.0,
                    reward_volatility_score=20.0,
                    data_quality="low_samples",
                    lead_mae_json='{"1": 1.0}',
                    params_json='{"days": 730}',
                ),
            ]
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/city-volatility")

    assert response.status_code == 200
    body = response.json()
    assert [row["city_slug"] for row in body] == ["wild", "stable"]
    assert body[0]["computed_at"] == "2026-06-11T00:00:00Z"
    assert body[0]["reward_volatility_score"] == 85.0
    assert body[0]["lead_mae_json"] == '{"1": 4.0}'


async def test_historical_validation_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            HistoricalValidationRun(
                run_at=run_at,
                status="FAILED",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 11),
                cities_json='["seoul"]',
                data_health_json='{"market_price_history_points": 10}',
                model_health_json='{"min_forecast_observed_pairs": 120}',
                trading_json=(
                    '{"execution_proxy": "polymarket_prices_history_last_price_no_book_depth", '
                    '"profiles": {"max_edge": {"total_pnl": "-1.23"}}}'
                ),
                gates_json='{"historical_pnl": {"passed": false, "value": "-1.23"}}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/historical-validation")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["run_at"] == "2026-06-11T00:00:00Z"
    assert body["latest"]["status"] == "FAILED"
    assert '"total_pnl": "-1.23"' in body["latest"]["trading_json"]
    assert body["history"][0]["data_health_json"] == '{"market_price_history_points": 10}'


async def test_history_backfill_endpoint_returns_latest_windows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            HistoryBackfillRun(
                run_at=run_at,
                completed_at=run_at,
                status="COMPLETED",
                window_start=date(2026, 6, 1),
                window_end=date(2026, 6, 7),
                cities_json='["seoul"]',
                interval="1d",
                probe_trades=False,
                events_seen=1,
                markets_upserted=11,
                history_points=0,
                trade_history_points=100,
                rejected_trade_sources=0,
                source_status_json='{"accepted": 11}',
                errors_json="[]",
                params_json='{"chunk_days": 7}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/history-backfill")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "COMPLETED"
    assert body["latest"]["trade_history_points"] == 100
    assert body["latest"]["completed_at"] == "2026-06-11T00:00:00Z"


async def test_live_readiness_endpoint_blocks_by_default(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/live-readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "BLOCKED"
    assert body["mode"] == "paper"
    assert "mode_live" in body["blockers"]
    assert body["ready_for_live_review"] is False
