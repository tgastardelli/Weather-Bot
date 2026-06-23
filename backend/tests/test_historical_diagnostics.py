"""Historical diagnostics tests."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.historical_diagnostics import generate_historical_diagnostics_report
from app.config import Settings
from app.db.models import (
    City,
    Event,
    ForecastSnapshot,
    HistoricalDiagnosticsRun,
    Market,
    MarketTradeHistoryPoint,
)


async def test_historical_diagnostics_segments_overconfident_losses(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 10, 10, tzinfo=UTC)
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
        for index in range(20):
            target = date(2026, 5, 20) + timedelta(days=index)
            event_id = f"event-{index}"
            market_id = f"market-{index}"
            token_id = f"yes-token-{index}"
            session.add(
                Event(
                    id=event_id,
                    slug=f"highest-temperature-in-seoul-on-{index}",
                    title="Highest temperature in Seoul?",
                    city_slug="seoul",
                    target_date=target,
                    end_date=datetime.combine(
                        target + timedelta(days=1),
                        datetime.min.time(),
                        tzinfo=UTC,
                    )
                    + timedelta(hours=12),
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
                    condition_id=f"0xcond{index}",
                    question="Will it be 25C?",
                    group_item_title="25C",
                    group_item_threshold=index,
                    bucket_kind="exact",
                    bucket_low=Decimal("25"),
                    bucket_high=Decimal("25"),
                    yes_token_id=token_id,
                    no_token_id=f"no-token-{index}",
                    tick_size=Decimal("0.001"),
                    min_order_size=Decimal("5"),
                    closed=True,
                    winner=False,
                    resolved_at=now,
                    updated_at=now,
                )
            )
            session.add(
                MarketTradeHistoryPoint(
                    ts=datetime.combine(target, datetime.min.time(), tzinfo=UTC)
                    + timedelta(hours=10),
                    market_id=market_id,
                    token_id=token_id,
                    condition_id=f"0xcond{index}",
                    price=Decimal("0.20"),
                    size=Decimal("5"),
                    side="BUY",
                    transaction_hash=f"0xtx{index}",
                    source="data_api_trades",
                )
            )
            session.add(
                ForecastSnapshot(
                    fetched_at=now,
                    city_slug="seoul",
                    source="historical",
                    model="gfs",
                    target_date=target,
                    lead_days=1,
                    tmax_c=25.0,
                    n_members=0,
                )
            )

    row = await generate_historical_diagnostics_report(
        session_factory,
        Settings(
            cities=["seoul"],
            deterministic_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
            validation_history_days=60,
        ),
    )

    summary = json.loads(row.summary_json)
    calibration = json.loads(row.calibration_json)
    recommendations = json.loads(row.recommendations_json)

    assert row.status == "INSUFFICIENT_HISTORY"
    assert summary["profiles"]["max_edge"]["n_trades"] == 20
    assert summary["profiles"]["max_edge"]["total_pnl"] == "-200.00"
    assert calibration["max_edge"][-1]["model_overconfidence"] == 1.0
    assert recommendations["checks"]["overconfidence_detected"] is True
    assert recommendations["worst_segments"][0]["segment"] == "seoul"

    async with session_factory() as session:
        persisted = (
            await session.execute(select(HistoricalDiagnosticsRun))
        ).scalar_one()
    assert persisted.id == row.id
