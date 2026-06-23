"""Measurement report tests."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.measurement import (
    _is_repaired_policy,
    build_measurement_report,
    measurement_run_payload,
)
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
    SignalStrategyAudit,
    StrategyRepairRun,
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


def test_measurement_recognizes_high_reward_repair_v5_policy() -> None:
    assert _is_repaired_policy("repair_v5_high_reward_v1") is True


def test_measurement_run_payload_serializes_json_fields() -> None:
    row = MeasurementRun(
        id=7,
        run_at=datetime(2026, 6, 23, 3, 41, tzinfo=UTC),
        status="MEASURING",
        window_start=date(2026, 6, 22),
        window_end=date(2026, 6, 23),
        summary_json='{"paper_pnl":"0.00000"}',
        metrics_json='{"total_fee_paid":"1.23000"}',
        checks_json='{"fee_reconciliation":{"passed":true}}',
    )

    payload = measurement_run_payload(row)

    assert payload["run_at"] == "2026-06-23T03:41:00+00:00"
    assert payload["window_start"] == "2026-06-22"
    assert payload["window_end"] == "2026-06-23"
    assert payload["summary"] == {"paper_pnl": "0.00000"}
    assert payload["metrics"] == {"total_fee_paid": "1.23000"}
    assert payload["checks"] == {"fee_reconciliation": {"passed": True}}


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


async def test_measurement_sample_size_passes_with_forward_days_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            EvidenceRun(
                run_at=now,
                status="MEASURING",
                window_start=date(2026, 5, 15),
                window_end=date(2026, 6, 14),
                cities_json='["atlanta","seattle","toronto"]',
                data_health_json=json.dumps({"forward_days": 30}),
                model_health_json="{}",
                trading_json="{}",
                gates_json=_passing_gates(),
            )
        )

    report = await build_measurement_report(session_factory, Settings(), now=now)
    checks = json.loads(report.checks_json)

    assert checks["sample_size"]["passed"] is True


async def test_measurement_sample_size_passes_with_resolved_fills_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            City(
                slug="atlanta",
                name="Atlanta",
                series_slug="atlanta-daily-weather",
                station_code="KATL",
                station_name=None,
                latitude=33.6407,
                longitude=-84.4277,
                timezone="America/New_York",
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
            Event(
                id="sample-event",
                slug="highest-temperature-in-atlanta-on-june-14-2026",
                title="Atlanta",
                city_slug="atlanta",
                target_date=date(2026, 6, 14),
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
                id="sample-market",
                event_id="sample-event",
                condition_id="0xsample",
                question="Tail?",
                group_item_title="Tail",
                group_item_threshold=1,
                bucket_kind="above",
                bucket_low=Decimal("90"),
                bucket_high=None,
                yes_token_id="sample-yes",
                no_token_id="sample-no",
                tick_size=Decimal("0.001"),
                min_order_size=Decimal("5"),
                closed=True,
                winner=True,
                resolved_at=now,
                updated_at=now,
            )
        )
        signal = Signal(
            ts=now,
            market_id="sample-market",
            token_id="sample-yes",
            side="BUY",
            profile="max_edge",
            model_prob=0.80,
            market_price=Decimal("0.05"),
            edge_gross=Decimal("0.75000"),
            edge_net=Decimal("0.74762"),
            stake=Decimal("10"),
            status="PROPOSED",
            reason=None,
        )
        session.add(signal)
        await session.flush()
        session.add(
            SignalStrategyAudit(
                signal_id=signal.id,
                ts=now,
                policy_name="repair_v5_high_reward_v1",
                segment_key="repair_v5_high_reward|atlanta|cheap_tail_yes|YES|v|above|month-06",
                raw_model_prob=0.80,
                calibrated_model_prob=0.80,
                n_samples=0,
                eligible=True,
                reason=None,
            )
        )
        order = PaperOrder(
            ts=now,
            signal_id=signal.id,
            market_id="sample-market",
            condition_id="0xsample",
            token_id="sample-yes",
            side="BUY",
            order_type="FAK",
            expected_price=Decimal("0.05"),
            max_spend=Decimal("10"),
            requested_size=Decimal("5"),
            filled_size=Decimal("5"),
            avg_fill_price=Decimal("0.05"),
            fee_paid=Decimal("0"),
            slippage=Decimal("0.00000"),
            status="FILLED",
            reject_reason=None,
            book_snapshot_id=None,
        )
        session.add(order)
        await session.flush()
        session.add_all(
            PaperFill(
                order_id=order.id,
                signal_id=signal.id,
                market_id="sample-market",
                token_id="sample-yes",
                book_snapshot_id=None,
                ts=now + timedelta(minutes=index),
                price=Decimal("1"),
                size=Decimal("1"),
                fee_paid=Decimal("0"),
                cash_delta=Decimal("1"),
                liquidity="SETTLEMENT",
            )
            for index in range(50)
        )
        session.add(
            EvidenceRun(
                run_at=now,
                status="MEASURING",
                window_start=date(2026, 6, 14),
                window_end=date(2026, 6, 14),
                cities_json='["atlanta","seattle","toronto"]',
                data_health_json=json.dumps({"forward_days": 1}),
                model_health_json="{}",
                trading_json="{}",
                gates_json=_passing_gates(),
            )
        )

    report = await build_measurement_report(session_factory, Settings(), now=now)
    checks = json.loads(report.checks_json)

    assert checks["sample_size"]["passed"] is True


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
    legacy_cash_delta = Decimal("-10.00000")
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
            session.add(
                SignalStrategyAudit(
                    signal_id=signal.id,
                    ts=signal_ts,
                    policy_name="repair_v4_test",
                    segment_key="specific|seoul|exact|0.6-0.7|0.20-0.40|24-48h",
                    raw_model_prob=0.95,
                    calibrated_model_prob=0.70,
                    n_samples=100,
                    eligible=True,
                    reason=None,
                )
            )
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
            Event(
                id="legacy-event",
                slug="legacy-paper-event",
                title="Legacy paper event",
                city_slug="seoul",
                target_date=date(2026, 5, 31),
                end_date=now,
                neg_risk_market_id=None,
                active=False,
                closed=False,
                volume=None,
                liquidity=None,
                first_seen_at=now,
                updated_at=now,
            )
        )
        session.add(
            Market(
                id="legacy-market",
                event_id="legacy-event",
                condition_id="0xlegacy",
                question="Legacy?",
                group_item_title="Legacy",
                group_item_threshold=0,
                bucket_kind="exact",
                bucket_low=Decimal("25"),
                bucket_high=Decimal("25"),
                yes_token_id="legacy-yes",
                no_token_id="legacy-no",
                tick_size=Decimal("0.001"),
                min_order_size=Decimal("5"),
                closed=False,
                winner=None,
                resolved_at=None,
                updated_at=now,
            )
        )
        legacy_book = BookSnapshot(
            ts=now,
            token_id="legacy-yes",
            bids_json='[["0.19","100"]]',
            asks_json='[["0.20","100"]]',
        )
        session.add(legacy_book)
        legacy_signal = Signal(
            ts=now,
            market_id="legacy-market",
            token_id="legacy-yes",
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
        session.add(legacy_signal)
        await session.flush()
        legacy_order = PaperOrder(
            ts=now,
            signal_id=legacy_signal.id,
            market_id="legacy-market",
            condition_id="0xlegacy",
            token_id="legacy-yes",
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
            book_snapshot_id=legacy_book.id,
        )
        session.add(legacy_order)
        await session.flush()
        session.add(
            PaperFill(
                order_id=legacy_order.id,
                signal_id=legacy_signal.id,
                market_id="legacy-market",
                token_id="legacy-yes",
                book_snapshot_id=legacy_book.id,
                ts=now,
                price=price,
                size=size,
                fee_paid=fee,
                cash_delta=legacy_cash_delta,
                liquidity="TAKER",
            )
        )
        final_cash = final_cash + legacy_cash_delta
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
        session.add(
            StrategyRepairRun(
                run_at=now,
                status="PROMISING",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 14),
                cities_json='["seoul"]',
                summary_json="{}",
                baseline_json="{}",
                variants_json="[]",
                best_variant_json='{"policy_name":"repair_v4_test"}',
                gates_json="{}",
            )
        )

    report = await build_measurement_report(session_factory, Settings(), now=now)
    checks = json.loads(report.checks_json)
    summary = json.loads(report.summary_json)
    metrics = json.loads(report.metrics_json)

    assert report.status == "READY_FOR_LIVE_REVIEW"
    assert all(check["passed"] for check in checks.values())
    assert summary["entry_fills"] == 50
    assert summary["all_entry_fills"] == 51
    assert summary["paper_fill_policy_counts"] == {"repair_v4_test": 50}
    assert summary["all_paper_fill_policy_counts"] == {
        "missing_audit": 1,
        "repair_v4_test": 50,
    }
    assert summary["policy_name"] == "repair_v4_test"
    assert Decimal(summary["paper_pnl"]) > Decimal("0")
    assert metrics["paper_brier_n"] == 50
    assert metrics["paper_brier_delta"] > 0


async def test_measurement_fails_when_filled_order_missing_slippage(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    price = Decimal("0.20")
    size = Decimal("5")
    fee = taker_fee(price, size, Decimal("0.05"))
    async with session_factory() as session, session.begin():
        session.add(
            City(
                slug="seattle",
                name="Seattle",
                series_slug="seattle-daily-weather",
                station_code="KSEA",
                station_name=None,
                latitude=47.4502,
                longitude=-122.3088,
                timezone="America/Los_Angeles",
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
            Event(
                id="slippage-event",
                slug="highest-temperature-in-seattle-on-june-14-2026",
                title="Seattle",
                city_slug="seattle",
                target_date=date(2026, 6, 14),
                end_date=now,
                neg_risk_market_id=None,
                active=False,
                closed=False,
                volume=None,
                liquidity=None,
                first_seen_at=now,
                updated_at=now,
            )
        )
        session.add(
            Market(
                id="slippage-market",
                event_id="slippage-event",
                condition_id="0xslippage",
                question="Tail?",
                group_item_title="Tail",
                group_item_threshold=1,
                bucket_kind="above",
                bucket_low=Decimal("90"),
                bucket_high=None,
                yes_token_id="slippage-yes",
                no_token_id="slippage-no",
                tick_size=Decimal("0.001"),
                min_order_size=Decimal("5"),
                closed=False,
                winner=None,
                resolved_at=None,
                updated_at=now,
            )
        )
        signal = Signal(
            ts=now,
            market_id="slippage-market",
            token_id="slippage-yes",
            side="BUY",
            profile="max_edge",
            model_prob=0.80,
            market_price=price,
            edge_gross=Decimal("0.60000"),
            edge_net=Decimal("0.59200"),
            stake=Decimal("10"),
            status="PROPOSED",
            reason=None,
        )
        session.add(signal)
        await session.flush()
        session.add(
            SignalStrategyAudit(
                signal_id=signal.id,
                ts=now,
                policy_name="repair_v5_high_reward_v1",
                segment_key="repair_v5_high_reward|seattle|cheap_tail_yes|YES|v|above|month-06",
                raw_model_prob=0.80,
                calibrated_model_prob=0.80,
                n_samples=0,
                eligible=True,
                reason=None,
            )
        )
        order = PaperOrder(
            ts=now,
            signal_id=signal.id,
            market_id="slippage-market",
            condition_id="0xslippage",
            token_id="slippage-yes",
            side="BUY",
            order_type="FAK",
            expected_price=price,
            max_spend=Decimal("10"),
            requested_size=size,
            filled_size=size,
            avg_fill_price=price,
            fee_paid=fee,
            slippage=None,
            status="FILLED",
            reject_reason=None,
            book_snapshot_id=1,
        )
        session.add(order)
        await session.flush()
        session.add(
            PaperFill(
                order_id=order.id,
                signal_id=signal.id,
                market_id="slippage-market",
                token_id="slippage-yes",
                book_snapshot_id=1,
                ts=now,
                price=price,
                size=size,
                fee_paid=fee,
                cash_delta=-((price * size) + fee).quantize(Decimal("0.00001")),
                liquidity="TAKER",
            )
        )

    report = await build_measurement_report(session_factory, Settings(), now=now)
    checks = json.loads(report.checks_json)

    assert checks["slippage_reconciliation"]["passed"] is False
    assert checks["slippage_reconciliation"]["value"]["failures"] == 1


async def test_measurement_reconciles_no_settlement_by_token(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            City(
                slug="toronto",
                name="Toronto",
                series_slug="toronto-daily-weather",
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
            Event(
                id="event-no",
                slug="highest-temperature-in-toronto-on-june-14-2026",
                title="Toronto",
                city_slug="toronto",
                target_date=date(2026, 6, 14),
                end_date=now + timedelta(hours=12),
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
                id="market-no",
                event_id="event-no",
                condition_id="0xno",
                question="Tail?",
                group_item_title="Tail",
                group_item_threshold=1,
                bucket_kind="above",
                bucket_low=Decimal("90"),
                bucket_high=None,
                yes_token_id="yes-no",
                no_token_id="no-no",
                tick_size=Decimal("0.001"),
                min_order_size=Decimal("5"),
                closed=True,
                winner=False,
                resolved_at=now,
                updated_at=now,
            )
        )
        signal = Signal(
            ts=now,
            market_id="market-no",
            token_id="no-no",
            side="BUY",
            profile="max_edge",
            model_prob=0.80,
            market_price=Decimal("0.05"),
            edge_gross=Decimal("0.75000"),
            edge_net=Decimal("0.74762"),
            stake=Decimal("10"),
            status="PROPOSED",
            reason=None,
        )
        session.add(signal)
        await session.flush()
        session.add(
            SignalStrategyAudit(
                signal_id=signal.id,
                ts=now,
                policy_name="repair_v5_high_reward_v1",
                segment_key="repair_v5_high_reward|toronto|cheap_tail_no|NO|v|above|month-06",
                raw_model_prob=0.20,
                calibrated_model_prob=0.80,
                n_samples=0,
                eligible=True,
                reason=None,
            )
        )
        order = PaperOrder(
            ts=now,
            signal_id=signal.id,
            market_id="market-no",
            condition_id="0xno",
            token_id="no-no",
            side="BUY",
            order_type="FAK",
            expected_price=Decimal("0.05"),
            max_spend=Decimal("10"),
            requested_size=Decimal("5"),
            filled_size=Decimal("5"),
            avg_fill_price=Decimal("0.05"),
            fee_paid=taker_fee(Decimal("0.05"), Decimal("5"), Decimal("0.05")),
            slippage=Decimal("0.00000"),
            status="FILLED",
            reject_reason=None,
            book_snapshot_id=None,
        )
        session.add(order)
        await session.flush()
        session.add(
            PaperFill(
                order_id=order.id,
                signal_id=signal.id,
                market_id="market-no",
                token_id="no-no",
                book_snapshot_id=None,
                ts=now,
                price=Decimal("1"),
                size=Decimal("5"),
                fee_paid=Decimal("0"),
                cash_delta=Decimal("5"),
                liquidity="SETTLEMENT",
            )
        )

    report = await build_measurement_report(session_factory, Settings(), now=now)
    checks = json.loads(report.checks_json)

    assert checks["settlement_reconciliation"]["passed"] is True
    assert checks["settlement_reconciliation"]["value"]["price_failures"] == 0


async def test_measurement_rejects_inverted_no_settlement(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            City(
                slug="toronto",
                name="Toronto",
                series_slug="toronto-daily-weather",
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
            Event(
                id="event-no-bad",
                slug="highest-temperature-in-toronto-on-june-14-2026-bad",
                title="Toronto",
                city_slug="toronto",
                target_date=date(2026, 6, 14),
                end_date=now + timedelta(hours=12),
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
                id="market-no-bad",
                event_id="event-no-bad",
                condition_id="0xnobad",
                question="Tail?",
                group_item_title="Tail",
                group_item_threshold=1,
                bucket_kind="above",
                bucket_low=Decimal("90"),
                bucket_high=None,
                yes_token_id="yes-no-bad",
                no_token_id="no-no-bad",
                tick_size=Decimal("0.001"),
                min_order_size=Decimal("5"),
                closed=True,
                winner=False,
                resolved_at=now,
                updated_at=now,
            )
        )
        signal = Signal(
            ts=now,
            market_id="market-no-bad",
            token_id="no-no-bad",
            side="BUY",
            profile="max_edge",
            model_prob=0.80,
            market_price=Decimal("0.05"),
            edge_gross=Decimal("0.75000"),
            edge_net=Decimal("0.74762"),
            stake=Decimal("10"),
            status="PROPOSED",
            reason=None,
        )
        session.add(signal)
        await session.flush()
        order = PaperOrder(
            ts=now,
            signal_id=signal.id,
            market_id="market-no-bad",
            condition_id="0xnobad",
            token_id="no-no-bad",
            side="BUY",
            order_type="FAK",
            expected_price=Decimal("0.05"),
            max_spend=Decimal("10"),
            requested_size=Decimal("5"),
            filled_size=Decimal("5"),
            avg_fill_price=Decimal("0.05"),
            fee_paid=Decimal("0"),
            slippage=Decimal("0.00000"),
            status="FILLED",
            reject_reason=None,
            book_snapshot_id=None,
        )
        session.add(order)
        await session.flush()
        session.add(
            PaperFill(
                order_id=order.id,
                signal_id=signal.id,
                market_id="market-no-bad",
                token_id="no-no-bad",
                book_snapshot_id=None,
                ts=now,
                price=Decimal("0"),
                size=Decimal("5"),
                fee_paid=Decimal("0"),
                cash_delta=Decimal("0"),
                liquidity="SETTLEMENT",
            )
        )

    report = await build_measurement_report(session_factory, Settings(), now=now)
    checks = json.loads(report.checks_json)

    assert checks["settlement_reconciliation"]["passed"] is False
    assert checks["settlement_reconciliation"]["value"]["price_failures"] == 1


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
