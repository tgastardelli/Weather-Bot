"""Measurement report tests."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.measurement import build_measurement_report
from app.config import Settings
from app.db.models import (
    BacktestResult,
    BookSnapshot,
    City,
    Event,
    EvidenceRun,
    ForecastSnapshot,
    Market,
    MeasurementRun,
    PaperEquitySnapshot,
    PaperFill,
    PaperOrder,
    Signal,
)
from app.execution.paper import taker_fee
from app.main import app


def _passing_gates() -> str:
    return json.dumps(
        {
            "ensemble_members": {"passed": True, "value": 100},
            "city_quality": {"passed": True, "value": []},
        }
    )


async def test_measurement_report_fails_without_paper_fills(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    report = await build_measurement_report(
        session_factory,
        Settings(),
        now=datetime(2026, 6, 14, tzinfo=UTC),
    )
    checks = json.loads(report.checks_json)

    assert report.status == "MEASURING"
    assert checks["fee_reconciliation"]["passed"] is False
    assert checks["fill_audit_links"]["passed"] is False


async def test_measurement_report_passes_when_paper_ledger_reconciles(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    price = Decimal("0.20")
    size = Decimal("5")
    fee = taker_fee(price, size, Decimal("0.05"))
    entry_cash_delta = -((price * size) + fee).quantize(Decimal("0.00001"))
    settlement_cash_delta = size
    n = 50
    final_cash = Decimal("1000") + (entry_cash_delta + settlement_cash_delta) * n
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
        for index in range(n):
            target_date = date(2026, 6, 1) + timedelta(days=index)
            event_id = f"event-{index}"
            market_id = f"market-{index}"
            token_id = f"yes-{index}"
            signal_ts = datetime(2026, 6, 1, 10, tzinfo=UTC) + timedelta(days=index)
            session.add(
                Event(
                    id=event_id,
                    slug=f"highest-temperature-in-seoul-{index}",
                    title="Highest temperature in Seoul?",
                    city_slug="seoul",
                    target_date=target_date,
                    end_date=signal_ts + timedelta(days=1),
                    neg_risk_market_id=None,
                    active=False,
                    closed=True,
                    volume=None,
                    liquidity=None,
                    first_seen_at=signal_ts,
                    updated_at=signal_ts,
                )
            )
            session.add(
                Market(
                    id=market_id,
                    event_id=event_id,
                    condition_id=f"0x{index}",
                    question="Will it be 25C?",
                    group_item_title="25C",
                    group_item_threshold=0,
                    bucket_kind="exact",
                    bucket_low=Decimal("25"),
                    bucket_high=Decimal("25"),
                    yes_token_id=token_id,
                    no_token_id=f"no-{index}",
                    tick_size=Decimal("0.001"),
                    min_order_size=Decimal("5"),
                    closed=True,
                    winner=True,
                    resolved_at=signal_ts + timedelta(days=1),
                    updated_at=signal_ts,
                )
            )
            session.add(
                ForecastSnapshot(
                    fetched_at=signal_ts - timedelta(hours=1),
                    city_slug="seoul",
                    source="open_meteo_ensemble",
                    model="gfs",
                    target_date=target_date,
                    lead_days=0,
                    tmax_c=None,
                    n_members=1,
                )
            )
            book = BookSnapshot(
                ts=signal_ts,
                token_id=token_id,
                bids_json='[["0.19","100"]]',
                asks_json='[["0.20","100"]]',
            )
            session.add(book)
            signal = Signal(
                ts=signal_ts,
                market_id=market_id,
                token_id=token_id,
                side="BUY",
                profile="max_edge",
                model_prob=0.70,
                market_price=price,
                edge_gross=Decimal("0.50000"),
                edge_net=Decimal("0.49200"),
                stake=Decimal("10"),
                status="PROPOSED",
                reason=None,
            )
            session.add(signal)
            await session.flush()
            order = PaperOrder(
                ts=signal_ts,
                signal_id=signal.id,
                market_id=market_id,
                condition_id=f"0x{index}",
                token_id=token_id,
                side="BUY",
                order_type="FAK",
                expected_price=price,
                max_spend=Decimal("10"),
                requested_size=size,
                filled_size=size,
                avg_fill_price=price,
                fee_paid=fee,
                slippage=Decimal("0.00000"),
                status="FILLED",
                reject_reason=None,
                book_snapshot_id=book.id,
            )
            session.add(order)
            await session.flush()
            session.add_all(
                [
                    PaperFill(
                        order_id=order.id,
                        signal_id=signal.id,
                        market_id=market_id,
                        token_id=token_id,
                        book_snapshot_id=book.id,
                        ts=signal_ts,
                        price=price,
                        size=size,
                        fee_paid=fee,
                        cash_delta=entry_cash_delta,
                        liquidity="TAKER",
                    ),
                    PaperFill(
                        order_id=order.id,
                        signal_id=signal.id,
                        market_id=market_id,
                        token_id=token_id,
                        book_snapshot_id=None,
                        ts=signal_ts + timedelta(days=1),
                        price=Decimal("1"),
                        size=size,
                        fee_paid=Decimal("0"),
                        cash_delta=settlement_cash_delta,
                        liquidity="SETTLEMENT",
                    ),
                ]
            )
        session.add(
            PaperEquitySnapshot(
                ts=now,
                cash=final_cash,
                equity=final_cash,
                realized_pnl=final_cash - Decimal("1000"),
                unrealized_pnl=Decimal("0"),
            )
        )
        session.add(
            BacktestResult(
                run_at=now,
                profile="max_edge",
                n_trades=50,
                n_wins=50,
                total_staked=Decimal("100.00"),
                total_pnl=Decimal("10.00"),
                win_rate=1.0,
                profit_factor=None,
                max_drawdown=Decimal("0.00"),
                params_json=json.dumps(
                    {
                        "source": "replay_price_snapshots",
                        "brier_model": 0.10,
                        "brier_market": 0.20,
                        "brier_delta": 0.10,
                    }
                ),
            )
        )
        session.add(
            EvidenceRun(
                run_at=now,
                status="PROMISING",
                window_start=date(2026, 6, 1),
                window_end=date(2026, 6, 30),
                cities_json='["seoul","tokyo","hong-kong"]',
                data_health_json=json.dumps({"forward_days": 30}),
                model_health_json="{}",
                trading_json="{}",
                gates_json=_passing_gates(),
            )
        )

    report = await build_measurement_report(session_factory, Settings(), now=now)
    checks = json.loads(report.checks_json)
    summary = json.loads(report.summary_json)

    assert report.status == "READY_FOR_LIVE_REVIEW"
    assert all(check["passed"] for check in checks.values())
    assert summary["entry_fills"] == 50
    assert Decimal(summary["paper_pnl"]) > Decimal("0")


async def test_measurement_endpoint_returns_latest_history(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            MeasurementRun(
                run_at=now,
                status="MEASURING",
                window_start=None,
                window_end=None,
                summary_json='{"paper_pnl":"0"}',
                metrics_json='{"total_fee_paid":"0"}',
                checks_json='{"fee_reconciliation":{"passed":false}}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/measurement")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "MEASURING"
    assert body["latest"]["summary_json"] == '{"paper_pnl":"0"}'
    assert len(body["history"]) == 1
