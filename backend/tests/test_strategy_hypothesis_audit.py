"""Strategy hypothesis audit tests."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.strategy_hypothesis_audit import (
    _blockers,
    _decision_trace,
    _status,
    generate_strategy_hypothesis_audit_report,
)
from analysis.strategy_repair import HistoricalCandidate
from app.config import Settings
from app.db.models import (
    City,
    Event,
    HistoricalDiagnosticsRun,
    Market,
    MarketTradeHistoryPoint,
    Resolution,
    StrategyCalibrationSegment,
    StrategyRepairRun,
)


async def test_hypothesis_audit_flags_after_close_trade_and_bucket_mismatch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 10, 10, tzinfo=UTC)
    close = datetime(2026, 6, 10, 12, tzinfo=UTC)
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
                active=True,
                needs_review=False,
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
                end_date=close,
                neg_risk_market_id=None,
                active=False,
                closed=True,
                volume=None,
                liquidity=None,
                first_seen_at=now,
                updated_at=now,
            )
        )
        session.add_all(
            [
                Market(
                    id="m-yes",
                    event_id="event-1",
                    condition_id="cond-1",
                    question="25C?",
                    group_item_title="25°C",
                    group_item_threshold=25,
                    bucket_kind="exact",
                    bucket_low=Decimal("25"),
                    bucket_high=Decimal("25"),
                    yes_token_id="yes-1",
                    no_token_id="no-1",
                    tick_size=Decimal("0.001"),
                    min_order_size=Decimal("5"),
                    closed=True,
                    winner=True,
                    resolved_at=close,
                    updated_at=now,
                ),
                Market(
                    id="m-no",
                    event_id="event-1",
                    condition_id="cond-2",
                    question="26C?",
                    group_item_title="26°C",
                    group_item_threshold=26,
                    bucket_kind="exact",
                    bucket_low=Decimal("26"),
                    bucket_high=Decimal("26"),
                    yes_token_id="yes-2",
                    no_token_id="no-2",
                    tick_size=Decimal("0.001"),
                    min_order_size=Decimal("5"),
                    closed=True,
                    winner=False,
                    resolved_at=close,
                    updated_at=now,
                ),
            ]
        )
        session.add(
            Resolution(
                event_id="event-1",
                winner_market_id="m-no",
                winner_bucket="26°C",
                resolved_at=close,
            )
        )
        session.add(
            MarketTradeHistoryPoint(
                ts=datetime(2026, 6, 10, 13, tzinfo=UTC),
                market_id="m-yes",
                token_id="yes-1",
                condition_id="cond-1",
                price=Decimal("0.20"),
                size=Decimal("5"),
                side="BUY",
                transaction_hash="tx-1",
                source="data_api_trades",
            )
        )
        session.add(
            HistoricalDiagnosticsRun(
                run_at=now,
                status="NEEDS_MODEL_REPAIR",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 10),
                cities_json='["seoul"]',
                summary_json="{}",
                segments_json="{}",
                calibration_json='{"max_edge": []}',
                recommendations_json='{"worst_segments": [], "top_losing_trades": []}',
            )
        )
        session.add(
            StrategyRepairRun(
                run_at=now,
                status="NO_HISTORICAL_EDGE",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 10),
                cities_json='["seoul"]',
                summary_json='{"policy_name": "repair_v4_test", "folds": []}',
                baseline_json="{}",
                variants_json="[]",
                best_variant_json="{}",
                gates_json="{}",
            )
        )

    row = await generate_strategy_hypothesis_audit_report(
        session_factory,
        Settings(cities=["seoul"], validation_history_days=730),
        cities=["seoul"],
        days=730,
    )

    timing = json.loads(row.timing_json)
    bucket = json.loads(row.bucket_audit_json)
    blockers = json.loads(row.blockers_json)
    assert row.status == "DATA_REVIEW"
    assert timing["data_api_trades"]["after_market_close"] == 1
    assert timing["raw_discardable_after_market_close"] == 1
    assert timing["candidate_after_market_close"] == 0
    assert bucket["issue_count"] == 1
    assert "timing_invalid" not in blockers
    assert "bucket_mapping_suspect" in blockers


def _candidate(*, price: Decimal = Decimal("0.20"), raw_prob: float = 0.80) -> HistoricalCandidate:
    ts = datetime(2026, 3, 7, 10, tzinfo=UTC)
    return HistoricalCandidate(
        ts=ts,
        sampled_ts=ts,
        market_id="m-1",
        event_id="e-1",
        city_slug="seoul",
        target_date=date(2026, 3, 7),
        price=price,
        raw_prob=raw_prob,
        winner=True,
        bucket_kind="above",
        bucket_label="25°C or higher",
        hours_to_close=10.0,
        price_source="data_api_trades",
    )


def _segment(
    candidate: HistoricalCandidate, *, pnl: Decimal = Decimal("10")
) -> StrategyCalibrationSegment:
    from analysis.strategy_hypothesis_audit import _candidate_segment_key

    return StrategyCalibrationSegment(
        run_id=1,
        policy_name="repair_v4_test",
        segment_key=_candidate_segment_key(candidate),
        n=100,
        wins=80,
        observed_rate=0.80,
        brier_delta=0.05,
        pnl=pnl,
        eligible=True,
        alpha=0.10,
        cap=0.80,
        min_samples=50,
    )


def test_decision_trace_reports_no_actionable_oos_edge() -> None:
    candidate = _candidate(price=Decimal("0.70"), raw_prob=0.80)
    segment = _segment(candidate)
    trace = _decision_trace(
        [candidate],
        repair_summary={
            "policy_name": "repair_v4_test",
            "policy_version": "repair_v4",
            "alpha": 0.10,
            "probability_cap": 0.80,
            "min_calibration_samples": 50,
            "min_edge_net": "0.50",
            "price_floor": "0.05",
            "folds": [{"valid": True, "fold_window": {"start": "2026-03-06"}}],
        },
        segment_rows={segment.segment_key: segment},
        settings=Settings(),
        blocked_city_slugs=set(),
    )
    blockers = _blockers(
        timing={"valid": True},
        bucket_audit={"valid": True},
        stability={"no_oos_segment_recurrence": False},
        decision_trace=trace,
        diagnostics={},
        repair=None,
    )

    assert trace["oos_candidates"] == 1
    assert trace["actionable_candidates"] == 0
    assert trace["blocked_counts"] == {"min_edge_net": 1}
    assert "no_actionable_oos_edge" in blockers
    assert _status(blockers) == "NO_ACTIONABLE_OOS_EDGE"


def test_decision_trace_reports_ready_for_repair_v5_candidate() -> None:
    candidate = _candidate(price=Decimal("0.20"), raw_prob=0.80)
    segment = _segment(candidate)
    trace = _decision_trace(
        [candidate],
        repair_summary={
            "policy_name": "repair_v4_test",
            "policy_version": "repair_v4",
            "alpha": 0.50,
            "probability_cap": 0.80,
            "min_calibration_samples": 50,
            "min_edge_net": "0.01",
            "price_floor": "0.05",
            "folds": [{"valid": True, "fold_window": {"start": "2026-03-06"}}],
        },
        segment_rows={segment.segment_key: segment},
        settings=Settings(),
        blocked_city_slugs=set(),
    )
    blockers = _blockers(
        timing={"valid": True},
        bucket_audit={"valid": True},
        stability={"no_oos_segment_recurrence": False},
        decision_trace=trace,
        diagnostics={},
        repair=StrategyRepairRun(
            run_at=datetime(2026, 3, 8, tzinfo=UTC),
            status="PROMISING",
            window_start=date(2026, 1, 1),
            window_end=date(2026, 3, 8),
            cities_json="[]",
            summary_json="{}",
            baseline_json="{}",
            variants_json="[]",
            best_variant_json="{}",
            gates_json="{}",
        ),
    )

    assert trace["actionable_candidates"] == 1
    assert trace["blocked_counts"] == {}
    assert _status(blockers) == "READY_FOR_REPAIR_V5"
