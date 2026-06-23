"""High-reward paper fast-lane status tests."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from analysis.high_reward_paper_status import build_high_reward_paper_status
from app.config import Settings
from app.db.models import (
    City,
    EnsembleMember,
    Event,
    ForecastSnapshot,
    Market,
    MarketPriceSnapshot,
    PaperFill,
    PaperOrder,
    Signal,
    SignalStrategyAudit,
    StrategyRepairRun,
)
from app.execution.paper import taker_fee


async def _seed_repair(session: AsyncSession, now: datetime) -> None:
    session.add(
        StrategyRepairRun(
            run_at=now,
            status="PROMISING",
            window_start=date(2026, 1, 1),
            window_end=date(2026, 6, 14),
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


async def _seed_city_fill(
    session: AsyncSession,
    *,
    now: datetime,
    city_slug: str,
    side: str,
    yes_winner: bool,
    pnl_win: bool,
    settled: bool = True,
) -> None:
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
    event_id = f"{city_slug}-event"
    market_id = f"{city_slug}-market"
    session.add(
        Event(
            id=event_id,
            slug=f"highest-temperature-in-{city_slug}-on-june-14-2026",
            title=city_slug,
            city_slug=city_slug,
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
    yes_token = f"yes-{city_slug}"
    no_token = f"no-{city_slug}"
    token_id = no_token if side == "NO" else yes_token
    session.add(
        Market(
            id=market_id,
            event_id=event_id,
            condition_id=f"0x{city_slug}",
            question="Tail?",
            group_item_title="Tail",
            group_item_threshold=1,
            bucket_kind="above",
            bucket_low=Decimal("90"),
            bucket_high=None,
            yes_token_id=yes_token,
            no_token_id=no_token,
            tick_size=Decimal("0.001"),
            min_order_size=Decimal("5"),
            closed=True,
            winner=yes_winner,
            resolved_at=now,
            updated_at=now,
        )
    )
    price = Decimal("0.05")
    size = Decimal("5")
    fee = taker_fee(price, size, Decimal("0.05"))
    signal = Signal(
        ts=now,
        market_id=market_id,
        token_id=token_id,
        side="BUY",
        profile="max_edge",
        model_prob=0.80,
        market_price=price,
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
            segment_key=f"repair_v5_high_reward|{city_slug}|family|{side}|variant|above|month-06",
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
        market_id=market_id,
        condition_id=f"0x{city_slug}",
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
        book_snapshot_id=1,
    )
    session.add(order)
    await session.flush()
    entry_cash = -((price * size) + fee).quantize(Decimal("0.00001"))
    settlement_cash = size if pnl_win else Decimal("0")
    settlement_price = Decimal("1") if pnl_win else Decimal("0")
    fills = [
        PaperFill(
            order_id=order.id,
            signal_id=signal.id,
            market_id=market_id,
            token_id=token_id,
            book_snapshot_id=1,
            ts=now,
            price=price,
            size=size,
            fee_paid=fee,
            cash_delta=entry_cash,
            liquidity="TAKER",
        )
    ]
    if settled:
        fills.append(
            PaperFill(
                order_id=order.id,
                signal_id=signal.id,
                market_id=market_id,
                token_id=token_id,
                book_snapshot_id=None,
                ts=now + timedelta(hours=12),
                price=settlement_price,
                size=size,
                fee_paid=Decimal("0"),
                cash_delta=settlement_cash,
                liquidity="SETTLEMENT",
            ),
        )
    session.add_all(fills)


async def _seed_current_toronto_candidate(
    session: AsyncSession,
    *,
    now: datetime,
    market_id: str = "toronto-active-market",
    bucket_low: Decimal = Decimal("25"),
    yes_bid: Decimal = Decimal("0.80"),
    member_tmax_c: float = 25.0,
) -> None:
    target_date = (now + timedelta(days=1)).date()
    if await session.get(City, "toronto") is None:
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
                unit="C",
                resolution_source="resolution",
                resolution_url=None,
                rounding="round",
                needs_review=False,
                active=True,
                updated_at=now,
            )
        )

    if await session.get(Event, "toronto-active-event") is None:
        session.add(
            Event(
                id="toronto-active-event",
                slug="highest-temperature-in-toronto-on-current-test-date",
                title="Toronto active",
                city_slug="toronto",
                target_date=target_date,
                end_date=now + timedelta(hours=24),
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
            id=market_id,
            event_id="toronto-active-event",
            condition_id="0xtoronto-active",
            question="Will it be 25C?",
            group_item_title="25C",
            group_item_threshold=0,
            bucket_kind="exact",
            bucket_low=bucket_low,
            bucket_high=bucket_low,
            yes_token_id=f"yes-{market_id}",
            no_token_id=f"no-{market_id}",
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
            market_id=market_id,
            best_bid=yes_bid,
            best_ask=yes_bid + Decimal("0.01"),
            mid=yes_bid + Decimal("0.005"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
        )
    )
    snapshot = ForecastSnapshot(
        fetched_at=now,
        city_slug="toronto",
        source="open_meteo_ensemble",
        model="gfs",
        target_date=target_date,
        lead_days=1,
        tmax_c=None,
        n_members=1,
    )
    session.add(snapshot)
    await session.flush()
    session.add(EnsembleMember(snapshot_id=snapshot.id, member=0, tmax_c=member_tmax_c))


async def test_high_reward_paper_status_reports_three_city_fast_lane(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC) - timedelta(days=31)
    await _seed_repair(session, now)
    await _seed_city_fill(
        session, now=now, city_slug="atlanta", side="YES", yes_winner=True, pnl_win=True
    )
    await _seed_city_fill(
        session, now=now, city_slug="seattle", side="YES", yes_winner=True, pnl_win=True
    )
    await _seed_city_fill(
        session, now=now, city_slug="toronto", side="NO", yes_winner=False, pnl_win=True
    )

    payload = await build_high_reward_paper_status(session, Settings())

    assert payload["status"] == "PAPER_READY_FOR_MEASUREMENT"
    assert payload["policy_name"] == "repair_v5_high_reward_v1"
    assert payload["blockers"] == []
    summary = payload["summary"]
    assert isinstance(summary, dict)
    assert summary["entry_fills"] == 3
    assert summary["settlement_fills"] == 3
    assert summary["wrong_token_signals"] == 0
    gate_progress = summary["gate_progress"]
    assert isinstance(gate_progress, dict)
    assert gate_progress["sample_gate_passed"] is True
    assert gate_progress["coverage_gate_passed"] is True
    assert gate_progress["remaining_forward_days"] == 0.0
    assert gate_progress["remaining_resolved_fills"] == 47
    assert gate_progress["missing_coverage"] == []
    assert summary["pending_targets"] == []
    assert summary["paper_pnl"] == "14.21436"
    assert summary["resolved_pnl"] == "14.21436"
    next_action = summary["next_action"]
    assert isinstance(next_action, dict)
    assert next_action["code"] == "run_measurement_review"
    cities = {str(row["city_slug"]): row for row in payload["cities"]}  # type: ignore[index]
    assert cities["toronto"]["side"] == "NO"
    assert cities["toronto"]["entry_fills"] == 1


async def test_high_reward_paper_status_stays_running_before_sample_gate(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    await _seed_repair(session, now)
    await _seed_city_fill(
        session, now=now, city_slug="atlanta", side="YES", yes_winner=True, pnl_win=True
    )
    await _seed_city_fill(
        session, now=now, city_slug="seattle", side="YES", yes_winner=True, pnl_win=True
    )
    await _seed_city_fill(
        session, now=now, city_slug="toronto", side="NO", yes_winner=False, pnl_win=True
    )

    payload = await build_high_reward_paper_status(session, Settings())

    assert payload["status"] == "PAPER_RUNNING"
    summary = payload["summary"]
    assert isinstance(summary, dict)
    gate_progress = summary["gate_progress"]
    assert isinstance(gate_progress, dict)
    assert gate_progress["sample_gate_passed"] is False
    assert gate_progress["coverage_gate_passed"] is True
    assert gate_progress["resolved_fills"] == 3
    assert gate_progress["remaining_resolved_fills"] == 47
    assert gate_progress["missing_coverage"] == []
    next_action = summary["next_action"]
    assert isinstance(next_action, dict)
    assert next_action["code"] == "continue_until_sample_gate"


async def test_high_reward_paper_status_reports_pending_targets(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    await _seed_repair(session, now)
    await _seed_city_fill(
        session,
        now=now,
        city_slug="atlanta",
        side="YES",
        yes_winner=False,
        pnl_win=False,
        settled=False,
    )

    payload = await build_high_reward_paper_status(session, Settings())

    summary = payload["summary"]
    assert isinstance(summary, dict)
    pending_targets = summary["pending_targets"]
    assert isinstance(pending_targets, list)
    assert summary["paper_pnl"] == "-0.26188"
    assert summary["resolved_pnl"] == "0.00000"
    assert pending_targets == [
        {
            "city_slug": "atlanta",
            "side": "YES",
            "target_date": "2026-06-14",
            "closed": True,
            "winner": False,
            "signals": 1,
            "entry_signals": 1,
            "settled_signals": 0,
            "pending_signals": 1,
            "entry_fills": 1,
            "settlement_fills": 0,
        }
    ]
    next_action = summary["next_action"]
    assert isinstance(next_action, dict)
    assert next_action["code"] == "continue_scheduler_until_coverage"
    assert next_action["missing_coverage"] == ["seattle", "toronto"]
    assert next_action["pending_targets"] == 1


async def test_high_reward_paper_status_fails_policy_side_mismatch(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    await _seed_repair(session, now)
    await _seed_city_fill(
        session, now=now, city_slug="toronto", side="YES", yes_winner=True, pnl_win=True
    )

    payload = await build_high_reward_paper_status(session, Settings())

    assert payload["status"] == "PAPER_FAILED"
    assert "side_token_mismatch" in payload["blockers"]


async def test_high_reward_paper_status_explains_current_candidate_reasons(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    await _seed_repair(session, now)
    await _seed_current_toronto_candidate(session, now=now)

    payload = await build_high_reward_paper_status(session, Settings(ensemble_models=["gfs"]))

    summary = payload["summary"]
    assert isinstance(summary, dict)
    diagnostics = summary["current_candidate_diagnostics"]
    assert isinstance(diagnostics, dict)
    assert diagnostics["eligible"] == 0
    assert diagnostics["actionable"] == 0
    assert diagnostics["reason_counts"] == {"variant_price_filter": 1}
    assert diagnostics["actionability_reason_counts"] == {}
    samples = diagnostics["samples"]
    assert isinstance(samples, list)
    assert samples[0]["market_price"] == "0.20000"
    assert samples[0]["variant_max_price"] == "0.05"
    assert samples[0]["price_to_variant_max"] == "-0.15000"
    assert samples[0]["min_probability_delta"] == "0.04"
    assert samples[0]["probability_delta"] == "-0.19500"
    assert samples[0]["probability_delta_to_min"] == "-0.23500"
    cities = {str(row["city_slug"]): row for row in payload["cities"]}  # type: ignore[index]
    city_diagnostics = cities["toronto"]["current_candidate_diagnostics"]
    assert isinstance(city_diagnostics, dict)
    assert city_diagnostics["reason_counts"] == {"variant_price_filter": 1}


async def test_high_reward_paper_status_marks_current_candidate_ready_to_signal(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    await _seed_repair(session, now)
    await _seed_current_toronto_candidate(
        session,
        now=now,
        market_id="toronto-ready-market",
        bucket_low=Decimal("30"),
        yes_bid=Decimal("0.997"),
        member_tmax_c=25.0,
    )

    payload = await build_high_reward_paper_status(session, Settings(ensemble_models=["gfs"]))

    summary = payload["summary"]
    assert isinstance(summary, dict)
    diagnostics = summary["current_candidate_diagnostics"]
    assert isinstance(diagnostics, dict)
    assert diagnostics["eligible"] == 1
    assert diagnostics["actionable"] == 1
    assert diagnostics["reason_counts"] == {"eligible": 1}
    assert diagnostics["actionability_reason_counts"] == {"ready_to_signal": 1}


async def test_high_reward_paper_status_prefers_priced_missing_coverage_sample(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    await _seed_repair(session, now)
    await _seed_current_toronto_candidate(
        session,
        now=now,
        market_id="toronto-missing-price",
        bucket_low=Decimal("30"),
        yes_bid=Decimal("1.000"),
        member_tmax_c=25.0,
    )
    await _seed_current_toronto_candidate(
        session,
        now=now,
        market_id="toronto-priced",
        bucket_low=Decimal("24"),
        yes_bid=Decimal("0.43"),
        member_tmax_c=25.0,
    )

    payload = await build_high_reward_paper_status(session, Settings(ensemble_models=["gfs"]))

    cities = {str(row["city_slug"]): row for row in payload["cities"]}  # type: ignore[index]
    city_diagnostics = cities["toronto"]["current_candidate_diagnostics"]
    assert isinstance(city_diagnostics, dict)
    samples = city_diagnostics["samples"]
    assert isinstance(samples, list)
    assert samples[0]["market_id"] == "toronto-priced"
    assert samples[0]["reason"] == "variant_price_filter"
