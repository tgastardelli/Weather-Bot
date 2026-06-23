"""Strategy engine regression tests."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    City,
    EnsembleMember,
    Event,
    ForecastSnapshot,
    Market,
    MarketPriceSnapshot,
    SignalStrategyAudit,
    StrategyCalibrationSegment,
    StrategyRepairRun,
)
from app.strategy.engine import scan_and_store_signals


async def _add_signal_fixture(
    session: AsyncSession,
    *,
    ask: Decimal,
    with_ensemble: bool,
) -> datetime:
    now = datetime(2026, 6, 10, 10, tzinfo=UTC)
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
            id="event-1",
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
            id="market-1",
            event_id="event-1",
            condition_id="0xcond",
            question="Will it be 25C?",
            group_item_title="25C",
            group_item_threshold=0,
            bucket_kind="exact",
            bucket_low=Decimal("25"),
            bucket_high=Decimal("25"),
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
            market_id="market-1",
            best_bid=ask - Decimal("0.01"),
            best_ask=ask,
            mid=ask - Decimal("0.005"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
        )
    )
    if with_ensemble:
        snapshot = ForecastSnapshot(
            fetched_at=now,
            city_slug="seoul",
            source="open_meteo_ensemble",
            model="gfs",
            target_date=date(2026, 6, 10),
            lead_days=0,
            tmax_c=None,
            n_members=1,
        )
        session.add(snapshot)
        await session.flush()
        session.add(EnsembleMember(snapshot_id=snapshot.id, member=0, tmax_c=25.0))
    await session.flush()
    return now


async def _add_high_reward_fixture(
    session: AsyncSession,
    *,
    city_slug: str,
    ask: Decimal,
    bid: Decimal,
    member_tmax_c: float,
) -> datetime:
    now = datetime(2026, 6, 10, 10, tzinfo=UTC)
    session.add(
        City(
            slug=city_slug,
            name=city_slug.replace("-", " ").title(),
            series_slug=f"{city_slug}-daily-weather",
            station_code="TEST",
            station_name=None,
            latitude=1.0,
            longitude=1.0,
            timezone="UTC",
            unit="C",
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
            id=f"{city_slug}-event",
            slug=f"highest-temperature-in-{city_slug}-on-june-10-2026",
            title=f"Highest temperature in {city_slug} on June 10, 2026?",
            city_slug=city_slug,
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
            id=f"{city_slug}-market",
            event_id=f"{city_slug}-event",
            condition_id=f"0x{city_slug}",
            question="Will it be 25C?",
            group_item_title="25C",
            group_item_threshold=0,
            bucket_kind="exact",
            bucket_low=Decimal("25"),
            bucket_high=Decimal("25"),
            yes_token_id=f"yes-{city_slug}",
            no_token_id=f"no-{city_slug}",
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
            market_id=f"{city_slug}-market",
            best_bid=bid,
            best_ask=ask,
            mid=(bid + ask) / Decimal("2"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
        )
    )
    snapshot = ForecastSnapshot(
        fetched_at=now,
        city_slug=city_slug,
        source="open_meteo_ensemble",
        model="gfs",
        target_date=date(2026, 6, 10),
        lead_days=0,
        tmax_c=None,
        n_members=1,
    )
    session.add(snapshot)
    await session.flush()
    session.add(EnsembleMember(snapshot_id=snapshot.id, member=0, tmax_c=member_tmax_c))
    await session.flush()
    return now


async def _add_high_reward_repair(session: AsyncSession, now: datetime) -> None:
    session.add(
        StrategyRepairRun(
            run_at=now,
            status="PROMISING",
            window_start=date(2026, 1, 1),
            window_end=date(2026, 6, 10),
            cities_json='["atlanta","seattle","toronto"]',
            summary_json="{}",
            baseline_json="{}",
            variants_json="[]",
            best_variant_json=(
                '{"policy_name":"repair_v5_high_reward_v1",'
                '"policy_version":"repair_v5_high_reward",'
                '"active_cities":["atlanta","seattle","toronto"],'
                '"side_by_city":{"atlanta":"YES","seattle":"YES","toronto":"NO"},'
                '"variant_by_city":{'
                '"atlanta":"cheap_tail_yes_yes_pxlte0_05_delta0_04",'
                '"seattle":"cheap_tail_yes_yes_pxlte0_10_delta0_04",'
                '"toronto":"cheap_tail_no_no_pxlte0_05_delta0_04"},'
                '"family_by_city":{"atlanta":"cheap_tail_yes",'
                '"seattle":"cheap_tail_yes","toronto":"cheap_tail_no"}}'
            ),
            gates_json="{}",
        )
    )
    await session.flush()


async def test_scan_does_not_emit_without_ensemble(session: AsyncSession) -> None:
    now = await _add_signal_fixture(session, ask=Decimal("0.20"), with_ensemble=False)

    signals = await scan_and_store_signals(
        session,
        Settings(ensemble_models=["gfs"], min_edge_net=Decimal("0.01")),
        now=now,
    )

    assert signals == []


@pytest.mark.parametrize(
    ("ask", "expected_profiles"),
    [
        (Decimal("0.20"), ["longshot", "max_edge"]),
        (Decimal("0.21"), ["max_edge"]),
    ],
)
async def test_longshot_profile_requires_configured_max_price(
    session: AsyncSession,
    ask: Decimal,
    expected_profiles: list[str],
) -> None:
    now = await _add_signal_fixture(session, ask=ask, with_ensemble=True)

    signals = await scan_and_store_signals(
        session,
        Settings(
            ensemble_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
        ),
        now=now,
    )

    assert sorted(signal.profile for signal in signals) == expected_profiles


async def test_repair_v2_policy_calibrates_signal_and_writes_audit(
    session: AsyncSession,
) -> None:
    now = await _add_signal_fixture(session, ask=Decimal("0.20"), with_ensemble=True)
    repair = StrategyRepairRun(
        run_at=now,
        status="PROMISING",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 6, 10),
        cities_json='["seoul"]',
        summary_json="{}",
        baseline_json="{}",
        variants_json="[]",
        best_variant_json=(
            '{"policy_name":"repair_v2_test","alpha":0.5,'
            '"probability_cap":0.8,"min_calibration_samples":50}'
        ),
        gates_json="{}",
    )
    session.add(repair)
    await session.flush()
    session.add_all(
        [
            StrategyCalibrationSegment(
                run_id=repair.id,
                policy_name="repair_v2_test",
                segment_key="specific|seoul|exact|0.9-1.0|0.20-0.40|24-48h",
                n=100,
                wins=80,
                observed_rate=0.8,
                brier_delta=0.1,
                pnl=Decimal("10"),
                eligible=True,
                alpha=0.5,
                cap=0.8,
                min_samples=50,
            ),
            StrategyCalibrationSegment(
                run_id=repair.id,
                policy_name="repair_v2_test",
                segment_key="global",
                n=100,
                wins=80,
                observed_rate=0.8,
                brier_delta=0.1,
                pnl=Decimal("10"),
                eligible=True,
                alpha=0.5,
                cap=0.8,
                min_samples=50,
            ),
        ]
    )

    signals = await scan_and_store_signals(
        session,
        Settings(
            ensemble_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
            strategy_policy_mode="repair_v2",
        ),
        now=now,
    )

    audits = (await session.execute(select(SignalStrategyAudit))).scalars().all()
    assert signals
    assert audits
    assert {audit.policy_name for audit in audits} == {"repair_v2_test"}
    assert all(audit.raw_model_prob == 1.0 for audit in audits)
    assert all(audit.calibrated_model_prob == 0.5 for audit in audits)


async def test_repair_v3_policy_uses_specific_segment_and_writes_audit(
    session: AsyncSession,
) -> None:
    now = await _add_signal_fixture(session, ask=Decimal("0.50"), with_ensemble=True)
    repair = StrategyRepairRun(
        run_at=now,
        status="PROMISING",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 6, 10),
        cities_json='["seoul"]',
        summary_json="{}",
        baseline_json="{}",
        variants_json="[]",
        best_variant_json=(
            '{"policy_name":"repair_v3_test","alpha":1.0,'
            '"probability_cap":0.9,"min_calibration_samples":50,'
            '"segment_scope":"specific_only"}'
        ),
        gates_json="{}",
    )
    session.add(repair)
    await session.flush()
    session.add_all(
        [
            StrategyCalibrationSegment(
                run_id=repair.id,
                policy_name="repair_v3_test",
                segment_key="specific|seoul|exact|0.9-1.0|0.40-0.60|24-48h",
                n=100,
                wins=80,
                observed_rate=0.8,
                brier_delta=0.1,
                pnl=Decimal("10"),
                eligible=True,
                alpha=1.0,
                cap=0.9,
                min_samples=50,
            ),
            StrategyCalibrationSegment(
                run_id=repair.id,
                policy_name="repair_v3_test",
                segment_key="global",
                n=100,
                wins=80,
                observed_rate=0.8,
                brier_delta=0.1,
                pnl=Decimal("10"),
                eligible=False,
                alpha=1.0,
                cap=0.9,
                min_samples=50,
            ),
        ]
    )

    signals = await scan_and_store_signals(
        session,
        Settings(
            ensemble_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
            strategy_policy_mode="repair_v3",
        ),
        now=now,
    )

    audits = (await session.execute(select(SignalStrategyAudit))).scalars().all()
    assert signals
    assert audits
    assert {audit.policy_name for audit in audits} == {"repair_v3_test"}
    assert all(audit.segment_key is not None for audit in audits)
    assert all(audit.segment_key.startswith("specific|") for audit in audits if audit.segment_key)
    assert all(audit.calibrated_model_prob == 0.8 for audit in audits)


async def test_repair_v4_policy_uses_same_specific_segment_and_writes_audit(
    session: AsyncSession,
) -> None:
    now = await _add_signal_fixture(session, ask=Decimal("0.50"), with_ensemble=True)
    repair = StrategyRepairRun(
        run_at=now,
        status="PROMISING",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 6, 10),
        cities_json='["seoul"]',
        summary_json="{}",
        baseline_json="{}",
        variants_json="[]",
        best_variant_json=(
            '{"policy_name":"repair_v4_test","alpha":1.0,'
            '"probability_cap":0.9,"min_calibration_samples":50,'
            '"segment_scope":"specific_only","min_edge_net":"0.01",'
            '"price_floor":"0.05"}'
        ),
        gates_json="{}",
    )
    session.add(repair)
    await session.flush()
    session.add_all(
        [
            StrategyCalibrationSegment(
                run_id=repair.id,
                policy_name="repair_v4_test",
                segment_key="specific|seoul|exact|0.9-1.0|0.40-0.60|24-48h",
                n=100,
                wins=80,
                observed_rate=0.8,
                brier_delta=0.1,
                pnl=Decimal("10"),
                eligible=True,
                alpha=1.0,
                cap=0.9,
                min_samples=50,
            ),
            StrategyCalibrationSegment(
                run_id=repair.id,
                policy_name="repair_v4_test",
                segment_key="global",
                n=100,
                wins=80,
                observed_rate=0.8,
                brier_delta=0.1,
                pnl=Decimal("10"),
                eligible=False,
                alpha=1.0,
                cap=0.9,
                min_samples=50,
            ),
        ]
    )

    signals = await scan_and_store_signals(
        session,
        Settings(
            ensemble_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
            strategy_policy_mode="repair_v4",
        ),
        now=now,
    )

    audits = (await session.execute(select(SignalStrategyAudit))).scalars().all()
    assert signals
    assert audits
    assert {audit.policy_name for audit in audits} == {"repair_v4_test"}
    assert all(audit.segment_key is not None for audit in audits)
    assert all(audit.segment_key.startswith("specific|") for audit in audits if audit.segment_key)


async def test_repair_v5_high_reward_runtime_creates_yes_signal_and_audit(
    session: AsyncSession,
) -> None:
    now = await _add_high_reward_fixture(
        session,
        city_slug="atlanta",
        ask=Decimal("0.04"),
        bid=Decimal("0.03"),
        member_tmax_c=25.0,
    )
    await _add_high_reward_repair(session, now)

    signals = await scan_and_store_signals(
        session,
        Settings(
            ensemble_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
            strategy_policy_mode="repair_v5",
        ),
        now=now,
    )

    audits = (await session.execute(select(SignalStrategyAudit))).scalars().all()
    assert len(signals) == 1
    assert signals[0].token_id == "yes-atlanta"
    assert signals[0].profile == "max_edge"
    assert signals[0].market_price == Decimal("0.04")
    assert audits[0].policy_name == "repair_v5_high_reward_v1"
    assert audits[0].segment_key is not None
    assert "atlanta|cheap_tail_yes|YES" in audits[0].segment_key


async def test_repair_v5_high_reward_runtime_creates_no_signal_from_yes_bid(
    session: AsyncSession,
) -> None:
    now = await _add_high_reward_fixture(
        session,
        city_slug="toronto",
        ask=Decimal("0.97"),
        bid=Decimal("0.96"),
        member_tmax_c=10.0,
    )
    await _add_high_reward_repair(session, now)

    signals = await scan_and_store_signals(
        session,
        Settings(
            ensemble_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
            strategy_policy_mode="repair_v5",
        ),
        now=now,
    )

    audits = (await session.execute(select(SignalStrategyAudit))).scalars().all()
    assert len(signals) == 1
    assert signals[0].token_id == "no-toronto"
    assert signals[0].market_price == Decimal("0.04000")
    assert signals[0].model_prob == 1.0
    assert audits[0].policy_name == "repair_v5_high_reward_v1"
    assert audits[0].raw_model_prob == 0.0
    assert audits[0].calibrated_model_prob == 1.0
    assert audits[0].segment_key is not None
    assert "toronto|cheap_tail_no|NO" in audits[0].segment_key


async def test_repair_v5_high_reward_runtime_rejects_city_outside_policy(
    session: AsyncSession,
) -> None:
    now = await _add_high_reward_fixture(
        session,
        city_slug="dallas",
        ask=Decimal("0.04"),
        bid=Decimal("0.03"),
        member_tmax_c=25.0,
    )
    await _add_high_reward_repair(session, now)

    signals = await scan_and_store_signals(
        session,
        Settings(
            ensemble_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
            strategy_policy_mode="repair_v5",
        ),
        now=now,
    )

    audits = (await session.execute(select(SignalStrategyAudit))).scalars().all()
    assert signals == []
    assert audits == []
