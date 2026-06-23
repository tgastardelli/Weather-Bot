"""Live-readiness guardrail tests."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import HistoricalValidationRun, MeasurementRun, Signal, StrategyRepairRun
from app.execution.live import (
    GeoblockStatus,
    LiveEngine,
    LiveTradingBlocked,
    build_live_readiness_report,
)


async def test_live_readiness_blocks_on_geoblock_failure(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            HistoricalValidationRun(
                run_at=now,
                status="PROMISING",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 14),
                cities_json='["seoul","tokyo","hong-kong"]',
                data_health_json="{}",
                model_health_json="{}",
                trading_json="{}",
                gates_json="{}",
            )
        )
        session.add(
            MeasurementRun(
                run_at=now,
                status="READY_FOR_LIVE_REVIEW",
                window_start=date(2026, 6, 1),
                window_end=date(2026, 6, 14),
                summary_json="{}",
                metrics_json="{}",
                checks_json="{}",
            )
        )

    settings = Settings(
        mode="live",
        live_trading_enabled=True,
        live_kill_switch_engaged=False,
        max_stake_per_order=Decimal("5"),
        max_exposure_per_market=Decimal("15"),
        max_daily_loss=Decimal("10"),
    )
    async with session_factory() as session:
        report = await build_live_readiness_report(
            session,
            settings,
            geoblock=GeoblockStatus("BLOCKED", False, {"blocked": True}),
        )

    assert report.status == "BLOCKED"
    assert report.checks["geoblock"]["passed"] is False
    assert "geoblock" in report.blockers


async def test_live_engine_refuses_to_submit_orders(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    signal = Signal(
        id=1,
        ts=datetime(2026, 6, 14, tzinfo=UTC),
        market_id="market-1",
        token_id="token-1",
        side="BUY",
        profile="max_edge",
        model_prob=0.70,
        market_price=Decimal("0.20"),
        edge_gross=Decimal("0.50"),
        edge_net=Decimal("0.49"),
        stake=Decimal("5"),
        status="PROPOSED",
        reason=None,
    )

    async with session_factory() as session:
        with pytest.raises(LiveTradingBlocked):
            await LiveEngine(Settings()).submit_signal(session, signal)


async def test_live_readiness_requires_promising_strategy_repair_policy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            StrategyRepairRun(
                run_at=now,
                status="NEEDS_MODEL_REPAIR",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 14),
                cities_json='["seoul"]',
                summary_json="{}",
                baseline_json="{}",
                variants_json="[]",
                best_variant_json='{"policy_name":"repair_v2_test"}',
                gates_json="{}",
            )
        )
        session.add(
            MeasurementRun(
                run_at=now,
                status="READY_FOR_LIVE_REVIEW",
                window_start=date(2026, 6, 1),
                window_end=date(2026, 6, 14),
                summary_json='{"policy_name":"repair_v2_test"}',
                metrics_json="{}",
                checks_json="{}",
            )
        )

    settings = Settings(
        mode="live",
        live_trading_enabled=True,
        live_kill_switch_engaged=False,
        max_stake_per_order=Decimal("5"),
        max_exposure_per_market=Decimal("15"),
        max_daily_loss=Decimal("10"),
    )
    async with session_factory() as session:
        report = await build_live_readiness_report(
            session,
            settings,
            geoblock=GeoblockStatus("ALLOWED", True, {"blocked": False}),
        )

    assert report.status == "BLOCKED"
    assert report.checks["strategy_repair"]["passed"] is False
    assert "strategy_repair" in report.blockers


async def test_live_readiness_accepts_same_promising_repair_policy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
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
                best_variant_json='{"policy_name":"repair_v2_test"}',
                gates_json="{}",
            )
        )
        session.add(
            MeasurementRun(
                run_at=now,
                status="READY_FOR_LIVE_REVIEW",
                window_start=date(2026, 6, 1),
                window_end=date(2026, 6, 14),
                summary_json='{"policy_name":"repair_v2_test"}',
                metrics_json="{}",
                checks_json="{}",
            )
        )

    settings = Settings(
        mode="live",
        live_trading_enabled=True,
        live_kill_switch_engaged=False,
        max_stake_per_order=Decimal("5"),
        max_exposure_per_market=Decimal("15"),
        max_daily_loss=Decimal("10"),
    )
    async with session_factory() as session:
        report = await build_live_readiness_report(
            session,
            settings,
            geoblock=GeoblockStatus("ALLOWED", True, {"blocked": False}),
        )

    assert report.status == "READY_FOR_MICRO_CAPITAL"
    assert report.checks["strategy_repair"]["passed"] is True
    assert report.checks["measurement"]["passed"] is True


async def test_live_readiness_accepts_same_promising_repair_v3_policy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
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
                best_variant_json='{"policy_name":"repair_v3_test"}',
                gates_json="{}",
            )
        )
        session.add(
            MeasurementRun(
                run_at=now,
                status="READY_FOR_LIVE_REVIEW",
                window_start=date(2026, 6, 1),
                window_end=date(2026, 6, 14),
                summary_json='{"policy_name":"repair_v3_test"}',
                metrics_json="{}",
                checks_json="{}",
            )
        )

    settings = Settings(
        mode="live",
        live_trading_enabled=True,
        live_kill_switch_engaged=False,
        max_stake_per_order=Decimal("5"),
        max_exposure_per_market=Decimal("15"),
        max_daily_loss=Decimal("10"),
    )
    async with session_factory() as session:
        report = await build_live_readiness_report(
            session,
            settings,
            geoblock=GeoblockStatus("ALLOWED", True, {"blocked": False}),
        )

    assert report.status == "READY_FOR_MICRO_CAPITAL"
    assert report.checks["strategy_repair"]["passed"] is True
    assert report.checks["measurement"]["passed"] is True


async def test_live_readiness_accepts_same_promising_repair_v4_policy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
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
        session.add(
            MeasurementRun(
                run_at=now,
                status="READY_FOR_LIVE_REVIEW",
                window_start=date(2026, 6, 1),
                window_end=date(2026, 6, 14),
                summary_json='{"policy_name":"repair_v4_test"}',
                metrics_json="{}",
                checks_json="{}",
            )
        )

    settings = Settings(
        mode="live",
        live_trading_enabled=True,
        live_kill_switch_engaged=False,
        max_stake_per_order=Decimal("5"),
        max_exposure_per_market=Decimal("15"),
        max_daily_loss=Decimal("10"),
    )
    async with session_factory() as session:
        report = await build_live_readiness_report(
            session,
            settings,
            geoblock=GeoblockStatus("ALLOWED", True, {"blocked": False}),
        )

    assert report.status == "READY_FOR_MICRO_CAPITAL"
    assert report.checks["strategy_repair"]["passed"] is True
    assert report.checks["measurement"]["passed"] is True


async def test_live_readiness_accepts_same_promising_repair_v5_policy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
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
                best_variant_json='{"policy_name":"repair_v5_test"}',
                gates_json="{}",
            )
        )
        session.add(
            MeasurementRun(
                run_at=now,
                status="READY_FOR_LIVE_REVIEW",
                window_start=date(2026, 6, 1),
                window_end=date(2026, 6, 14),
                summary_json='{"policy_name":"repair_v5_test"}',
                metrics_json="{}",
                checks_json="{}",
            )
        )

    settings = Settings(
        mode="live",
        live_trading_enabled=True,
        live_kill_switch_engaged=False,
        max_stake_per_order=Decimal("5"),
        max_exposure_per_market=Decimal("15"),
        max_daily_loss=Decimal("10"),
    )
    async with session_factory() as session:
        report = await build_live_readiness_report(
            session,
            settings,
            geoblock=GeoblockStatus("ALLOWED", True, {"blocked": False}),
        )

    assert report.status == "READY_FOR_MICRO_CAPITAL"
    assert report.checks["strategy_repair"]["passed"] is True
    assert report.checks["measurement"]["passed"] is True


async def test_live_readiness_blocks_repair_measurement_policy_mismatch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
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
                best_variant_json='{"policy_name":"repair_v3_test"}',
                gates_json="{}",
            )
        )
        session.add(
            MeasurementRun(
                run_at=now,
                status="READY_FOR_LIVE_REVIEW",
                window_start=date(2026, 6, 1),
                window_end=date(2026, 6, 14),
                summary_json='{"policy_name":"repair_v2_test"}',
                metrics_json="{}",
                checks_json="{}",
            )
        )

    settings = Settings(
        mode="live",
        live_trading_enabled=True,
        live_kill_switch_engaged=False,
        max_stake_per_order=Decimal("5"),
        max_exposure_per_market=Decimal("15"),
        max_daily_loss=Decimal("10"),
    )
    async with session_factory() as session:
        report = await build_live_readiness_report(
            session,
            settings,
            geoblock=GeoblockStatus("ALLOWED", True, {"blocked": False}),
        )

    assert report.status == "BLOCKED"
    assert report.checks["measurement"]["passed"] is False
    assert "measurement" in report.blockers


async def test_live_readiness_blocks_quarantined_repair_city(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            StrategyRepairRun(
                run_at=now,
                status="PROMISING",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 14),
                cities_json='["nyc"]',
                summary_json="{}",
                baseline_json="{}",
                variants_json="[]",
                best_variant_json='{"policy_name":"repair_v5_test"}',
                gates_json="{}",
            )
        )
        session.add(
            MeasurementRun(
                run_at=now,
                status="READY_FOR_LIVE_REVIEW",
                window_start=date(2026, 6, 1),
                window_end=date(2026, 6, 14),
                summary_json='{"policy_name":"repair_v5_test"}',
                metrics_json="{}",
                checks_json="{}",
            )
        )

    settings = Settings(
        mode="live",
        live_trading_enabled=True,
        live_kill_switch_engaged=False,
        max_stake_per_order=Decimal("5"),
        max_exposure_per_market=Decimal("15"),
        max_daily_loss=Decimal("10"),
    )
    async with session_factory() as session:
        report = await build_live_readiness_report(
            session,
            settings,
            geoblock=GeoblockStatus("ALLOWED", True, {"blocked": False}),
        )

    assert report.status == "BLOCKED"
    assert report.checks["operational_cities"]["passed"] is False
    assert "operational_cities" in report.blockers
