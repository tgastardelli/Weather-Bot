"""Strategy repair tests."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.strategy_repair import (
    HistoricalCandidate,
    RepairVariant,
    RollingOriginConfig,
    _city_quality_gate,
    _filter_reason,
    _fold_validity,
    _fold_windows,
    _repair_v4_variants,
    _rolling_origin_evaluation,
    _rolling_origin_gates,
    _rolling_origin_status,
    _simulate_variant,
    generate_strategy_repair_report,
)
from app.config import Settings
from app.db.models import (
    City,
    Event,
    ForecastSnapshot,
    Market,
    MarketTradeHistoryPoint,
    StrategyCalibrationSegment,
    StrategyRepairRun,
)
from app.strategy.probability_calibration import (
    ProbabilityContext,
    WalkForwardMarketAwareCalibrator,
    WalkForwardProbabilityCalibrator,
)
from app.strategy.repair_decision import (
    RepairPolicyParams,
    RepairSegmentStats,
    evaluate_repair_policy,
)


def _context(target_date: date, *, prob: float = 0.95) -> ProbabilityContext:
    return ProbabilityContext(
        city_slug="seoul",
        bucket_kind="exact",
        model_prob=prob,
        market_price=Decimal("0.20"),
        hours_to_close=24.0,
        target_date=target_date,
    )


def test_walk_forward_calibrator_ignores_future_and_caps_probability() -> None:
    calibrator = WalkForwardProbabilityCalibrator(min_samples=2, probability_cap=0.80)
    future = _context(date(2026, 6, 3))
    past = _context(date(2026, 6, 1))
    current = _context(date(2026, 6, 2))

    calibrator.observe(future, 1.0)
    assert calibrator.calibrate(current).source == "raw_capped"

    calibrator.observe(past, 1.0)
    calibrator.observe(past, 1.0)
    capped = calibrator.calibrate(current)

    assert capped.source == "specific"
    assert capped.n_samples == 2
    assert capped.calibrated_prob == 0.80
    assert capped.capped is True


async def test_strategy_repair_city_quality_blocks_quarantined_city(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 20, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            City(
                slug="nyc",
                name="NYC",
                series_slug="nyc-daily-weather",
                station_code="KNYC",
                station_name=None,
                latitude=40.7,
                longitude=-73.9,
                timezone="America/New_York",
                unit="F",
                resolution_source="wunderground",
                resolution_url=None,
                rounding="round",
                needs_review=False,
                active=True,
                updated_at=now,
            )
        )

    async with session_factory() as session:
        passed, value = await _city_quality_gate(session, Settings(cities=["nyc"]))

    assert passed is False
    assert value["needs_review"] == []
    assert value["operational_quarantine"][0]["city_slug"] == "nyc"


def test_market_aware_calibrator_uses_past_segments_and_market_anchor() -> None:
    calibrator = WalkForwardMarketAwareCalibrator(
        min_samples=2,
        probability_cap=0.60,
        alpha=0.50,
        fee_rate=Decimal("0.05"),
    )
    future = _context(date(2026, 6, 3), prob=0.95)
    past = _context(date(2026, 6, 1), prob=0.95)
    current = _context(date(2026, 6, 2), prob=0.95)

    calibrator.observe(future, 1.0, 0.60)
    assert calibrator.calibrate(current).eligible is False

    calibrator.observe(past, 1.0, 0.60)
    calibrator.observe(past, 1.0, 0.60)
    result = calibrator.calibrate(current)

    assert result.eligible is True
    assert result.source == "specific"
    assert result.n_samples == 2
    assert result.calibrated_prob == 0.60
    assert result.capped is True
    assert result.brier_delta is not None and result.brier_delta > 0


def test_market_aware_specific_only_does_not_fallback_to_city_bucket() -> None:
    fallback = WalkForwardMarketAwareCalibrator(
        min_samples=2,
        probability_cap=0.80,
        alpha=1.0,
        fee_rate=Decimal("0.05"),
    )
    specific_only = WalkForwardMarketAwareCalibrator(
        min_samples=2,
        probability_cap=0.80,
        alpha=1.0,
        fee_rate=Decimal("0.05"),
        segment_scope="specific_only",
    )
    past = _context(date(2026, 6, 1), prob=0.95)
    current = ProbabilityContext(
        city_slug="seoul",
        bucket_kind="above",
        model_prob=0.95,
        market_price=Decimal("0.20"),
        hours_to_close=24.0,
        target_date=date(2026, 6, 2),
    )

    for calibrator in (fallback, specific_only):
        calibrator.observe(past, 1.0, 0.60)
        calibrator.observe(past, 1.0, 0.60)

    assert fallback.calibrate(current).eligible is True
    specific_result = specific_only.calibrate(current)
    assert specific_result.eligible is False
    assert specific_result.reason == "min_samples"


def test_repair_v3_price_extreme_requires_segment_edge_over_cost() -> None:
    candidate = HistoricalCandidate(
        ts=datetime(2026, 6, 1, 10, tzinfo=UTC),
        sampled_ts=datetime(2026, 6, 1, 10, tzinfo=UTC),
        market_id="market-1",
        event_id="event-1",
        city_slug="seoul",
        target_date=date(2026, 6, 1),
        price=Decimal("0.04"),
        raw_prob=0.20,
        winner=True,
        bucket_kind="exact",
        bucket_label="25C",
        hours_to_close=24.0,
        price_source="data_api_trades",
    )
    variant = RepairVariant(
        name="repair_v3_test",
        calibrate=True,
        apply_segment_filters=True,
        repair_v3=True,
        alpha=0.25,
        min_samples=50,
        probability_cap=0.40,
        min_edge_net=Decimal("0.00"),
        segment_scope="specific_only",
    )

    blocked = _filter_reason(
        candidate,
        variant=variant,
        fee_rate=Decimal("0.05"),
        calibrated_prob=0.05,
        raw_edge=Decimal("0.10"),
        calibrated_edge=Decimal("0.01"),
        calibration_samples=50,
        calibration_segment_key="specific|seoul|exact|0.2-0.3|0.00-0.05|24-48h",
        calibration_observed_rate=0.03,
        calibration_brier_delta=0.01,
    )
    allowed = _filter_reason(
        candidate,
        variant=variant,
        fee_rate=Decimal("0.05"),
        calibrated_prob=0.08,
        raw_edge=Decimal("0.10"),
        calibrated_edge=Decimal("0.03"),
        calibration_samples=50,
        calibration_segment_key="specific|seoul|exact|0.2-0.3|0.00-0.05|24-48h",
        calibration_observed_rate=0.08,
        calibration_brier_delta=0.01,
    )

    assert blocked == "price_bucket_0_00_0_05"
    assert allowed is None


def test_repair_v4_blocks_low_price_and_segment_cost() -> None:
    context = ProbabilityContext(
        city_slug="seoul",
        bucket_kind="exact",
        model_prob=0.20,
        market_price=Decimal("0.04"),
        hours_to_close=24.0,
        target_date=date(2026, 6, 1),
    )
    params = RepairPolicyParams(
        policy_name="repair_v4_test",
        policy_version="repair_v4",
        alpha=0.10,
        probability_cap=0.20,
        min_samples=50,
        min_edge_net=Decimal("0.000"),
        segment_scope="specific_only",
        price_floor=Decimal("0.05"),
    )
    segment = RepairSegmentStats(
        segment_key="specific|seoul|exact|0.2-0.3|0.00-0.05|24-48h",
        n=100,
        wins=10,
        observed_rate=0.10,
        brier_delta=0.01,
        pnl=Decimal("2.00"),
    )

    decision = evaluate_repair_policy(
        params=params,
        context=context,
        fee_rate=Decimal("0.05"),
        segment=segment,
        global_rate=0.10,
    )

    assert decision.eligible is False
    assert decision.reason == "low_price_diagnostic_only"


def test_repair_v4_holdout_observes_train_without_trading_it() -> None:
    variant = next(
        item
        for item in _repair_v4_variants()
        if item.alpha == 0.10
        and item.probability_cap == 0.20
        and item.min_samples == 50
        and item.min_edge_net == Decimal("0.000")
        and item.price_floor == Decimal("0.05")
    )
    settings = Settings(
        min_edge_net=Decimal("0.000"),
        max_stake_per_order=Decimal("10"),
        max_exposure_per_market=Decimal("1000"),
    )
    candidates: list[HistoricalCandidate] = []
    for index in range(60):
        target = date(2026, 1, 1) + timedelta(days=index)
        candidates.append(
            HistoricalCandidate(
                ts=datetime.combine(target, datetime.min.time(), tzinfo=UTC)
                + timedelta(hours=10),
                sampled_ts=datetime.combine(target, datetime.min.time(), tzinfo=UTC)
                + timedelta(hours=10),
                market_id=f"train-{index}",
                event_id=f"event-train-{index}",
                city_slug="seoul",
                target_date=target,
                price=Decimal("0.10"),
                raw_prob=0.30,
                winner=True,
                bucket_kind="exact",
                bucket_label="25C",
                hours_to_close=24.0,
                price_source="data_api_trades",
            )
        )
    holdout_date = date(2026, 3, 15)
    candidates.append(
        HistoricalCandidate(
            ts=datetime(2026, 3, 15, 10, tzinfo=UTC),
            sampled_ts=datetime(2026, 3, 15, 10, tzinfo=UTC),
            market_id="holdout-1",
            event_id="event-holdout-1",
            city_slug="seoul",
            target_date=holdout_date,
            price=Decimal("0.10"),
            raw_prob=0.30,
            winner=True,
            bucket_kind="exact",
            bucket_label="25C",
            hours_to_close=24.0,
            price_source="data_api_trades",
        )
    )

    trades, metadata = _simulate_variant(
        candidates,
        settings,
        variant,
        evaluation_start=holdout_date,
    )

    assert len(trades["max_edge"]) == 1
    assert metadata["walk_forward_traded_segments"] == 1


def _linear_candidates(
    start: date, days: int, *, winner_step: int = 2
) -> list[HistoricalCandidate]:
    candidates: list[HistoricalCandidate] = []
    for index in range(days):
        target = start + timedelta(days=index)
        candidates.append(
            HistoricalCandidate(
                ts=datetime.combine(target, datetime.min.time(), tzinfo=UTC)
                + timedelta(hours=10),
                sampled_ts=datetime.combine(target, datetime.min.time(), tzinfo=UTC)
                + timedelta(hours=10),
                market_id=f"market-{index}",
                event_id=f"event-{index}",
                city_slug="seoul",
                target_date=target,
                price=Decimal("0.10"),
                raw_prob=0.30,
                winner=index % winner_step == 0,
                bucket_kind="exact",
                bucket_label="25C",
                hours_to_close=24.0,
                price_source="data_api_trades",
            )
        )
    return candidates


def _fake_best_variant(
    *, n_trades: int, pnl: str, brier_delta: float, concentration: str, pnl_ci_high: str
) -> dict[str, object]:
    return {
        "name": "repair_v4_oos",
        "profiles": {
            "longshot": {},
            "max_edge": {
                "n_resolved_trades": n_trades,
                "total_pnl": pnl,
                "brier_delta": brier_delta,
                "top_5_abs_pnl_share": concentration,
                "pnl_ci_high": pnl_ci_high,
            },
        },
    }


def test_fold_windows_expanding_train_then_fold() -> None:
    windows = _fold_windows(
        date(2026, 1, 1),
        date(2026, 1, 20),
        RollingOriginConfig(fold_days=5, min_train_days=5),
    )

    assert [window.fold_start for window in windows] == [
        date(2026, 1, 6),
        date(2026, 1, 11),
        date(2026, 1, 16),
    ]
    assert windows[0].fold_end == date(2026, 1, 11)


def test_fold_validity_flags_insufficient_train_and_fold() -> None:
    config = RollingOriginConfig(min_train_days=5, min_train_candidates=3, min_fold_candidates=2)
    one = _linear_candidates(date(2026, 1, 1), 1)

    short_train = _fold_validity(train_part=one * 2, n_fold=10, train_days=10, config=config)
    short_fold = _fold_validity(train_part=one * 5, n_fold=1, train_days=10, config=config)
    valid = _fold_validity(train_part=one * 5, n_fold=5, train_days=10, config=config)

    assert short_train == (False, "insufficient_train")
    assert short_fold == (False, "insufficient_fold")
    assert valid == (True, None)


def test_rolling_origin_fixes_policy_and_aggregates_oos_without_future() -> None:
    candidates = _linear_candidates(date(2026, 1, 1), 30)
    settings = Settings(
        min_edge_net=Decimal("0.000"),
        max_stake_per_order=Decimal("10"),
        max_exposure_per_market=Decimal("1000"),
    )
    config = RollingOriginConfig(
        fold_days=5,
        min_train_days=5,
        min_train_candidates=3,
        min_fold_candidates=1,
        min_folds=2,
    )
    variants = [RepairVariant("calibrated_cap", calibrate=True, apply_segment_filters=False)]

    result = _rolling_origin_evaluation(
        candidates, settings, variants, blocked_city_slugs=set(), config=config
    )

    assert result.selected_variant is not None
    assert result.selected_variant.name == "calibrated_cap"
    assert result.fold_count >= 2
    assert result.market_history_span == {"start": "2026-01-01", "end": "2026-01-30"}

    valid_folds = [fold for fold in result.folds if fold["valid"] is True]
    assert len(valid_folds) == result.fold_count
    # Each fold trains strictly before it trades: expanding train ends where the fold starts.
    for fold in valid_folds:
        assert fold["train_window"]["end"] == fold["fold_window"]["start"]  # type: ignore[index]
    # OOS aggregate equals the sum of the per-fold out-of-sample trades (no double counting).
    assert sum(int(fold["n_oos_trades"]) for fold in valid_folds) == len(
        result.oos_trades["max_edge"]
    )


def test_rolling_origin_does_not_select_zero_trade_variant() -> None:
    candidates = _linear_candidates(date(2026, 1, 1), 30)
    settings = Settings(
        min_edge_net=Decimal("2.000"),
        max_stake_per_order=Decimal("10"),
        max_exposure_per_market=Decimal("1000"),
    )
    config = RollingOriginConfig(
        fold_days=5,
        min_train_days=5,
        min_train_candidates=3,
        min_fold_candidates=1,
        min_folds=2,
    )
    variants = [RepairVariant("no_trades", calibrate=True, apply_segment_filters=False)]

    result = _rolling_origin_evaluation(
        candidates, settings, variants, blocked_city_slugs=set(), config=config
    )

    assert result.selected_variant is None
    assert result.selection_reason == "no_selectable_train_variant"
    assert all(
        int(payload["profiles"]["max_edge"]["n_resolved_trades"]) == 0  # type: ignore[index]
        for payload in result.train_variant_payloads
    )


def test_rolling_origin_status_transitions() -> None:
    city_quality = (True, {"missing_cities": [], "needs_review": []})
    passing = _fake_best_variant(
        n_trades=60, pnl="5", brier_delta=0.02, concentration="0.20", pnl_ci_high="1"
    )
    gates = _rolling_origin_gates(passing, city_quality, fold_count=3, min_folds=3)
    assert _rolling_origin_status(gates, 3, min_folds=3) == ("PROMISING", None)

    few = _rolling_origin_gates(passing, city_quality, fold_count=1, min_folds=3)
    assert _rolling_origin_status(few, 1, min_folds=3) == (
        "INSUFFICIENT_HISTORY",
        "few_valid_folds",
    )

    thin = _fake_best_variant(
        n_trades=10, pnl="5", brier_delta=0.02, concentration="0.20", pnl_ci_high="1"
    )
    thin_gates = _rolling_origin_gates(thin, city_quality, fold_count=3, min_folds=3)
    assert _rolling_origin_status(thin_gates, 3, min_folds=3) == (
        "NO_HISTORICAL_EDGE",
        "insufficient_oos_trades",
    )

    no_edge = _fake_best_variant(
        n_trades=60, pnl="-5", brier_delta=-0.01, concentration="0.20", pnl_ci_high="-1"
    )
    no_edge_gates = _rolling_origin_gates(no_edge, city_quality, fold_count=3, min_folds=3)
    assert _rolling_origin_status(no_edge_gates, 3, min_folds=3) == (
        "NO_HISTORICAL_EDGE",
        "no_oos_edge",
    )


async def test_rolling_origin_report_insufficient_history_when_no_valid_folds(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    row = await generate_strategy_repair_report(
        session_factory,
        Settings(
            cities=["seoul"],
            deterministic_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
            validation_history_days=120,
        ),
        policy_version="repair_v4",
        validation_scheme="rolling-origin",
    )

    summary = json.loads(row.summary_json)
    assert row.status == "INSUFFICIENT_HISTORY"
    assert summary["validation_scheme"] == "rolling-origin"
    assert summary["fold_count"] == 0
    assert summary["insufficient_reason"] == "few_valid_folds"
    assert summary["selected_policy_name"] is not None


async def test_strategy_repair_report_compares_baseline_and_filtered_variant(
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
        for index in range(55):
            target = date(2026, 4, 1) + timedelta(days=index)
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
                    price=Decimal("0.02"),
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

    row = await generate_strategy_repair_report(
        session_factory,
        Settings(
            cities=["seoul"],
            deterministic_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
            validation_history_days=120,
        ),
        policy_version="legacy",
        validation_scheme="fixed-holdout",
    )

    baseline = json.loads(row.baseline_json)
    variants = json.loads(row.variants_json)
    filtered = next(variant for variant in variants if variant["name"] == "calibrated_filtered")

    assert row.status in {"INSUFFICIENT_HISTORY", "NEEDS_MODEL_REPAIR"}
    assert baseline["profiles"]["max_edge"]["n_resolved_trades"] == 55
    assert filtered["blocked_counts"]["price_bucket_0_00_0_05"] == 55
    assert json.loads(row.summary_json)["probability_cap"] == 0.8

    async with session_factory() as session:
        persisted = (await session.execute(select(StrategyRepairRun))).scalar_one()
    assert persisted.id == row.id


async def test_strategy_repair_v3_persists_best_policy_segments(
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
        for index in range(120):
            target = date(2026, 1, 1) + timedelta(days=index)
            winner = index % 4 == 0
            event_id = f"repair-v2-event-{index}"
            market_id = f"repair-v2-market-{index}"
            token_id = f"repair-v2-yes-token-{index}"
            session.add(
                Event(
                    id=event_id,
                    slug=f"highest-temperature-in-seoul-repair-v2-{index}",
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
                    condition_id=f"0xrepair{index}",
                    question="Will it be 25C?",
                    group_item_title="25C",
                    group_item_threshold=index,
                    bucket_kind="exact",
                    bucket_low=Decimal("25"),
                    bucket_high=Decimal("25"),
                    yes_token_id=token_id,
                    no_token_id=f"repair-v2-no-token-{index}",
                    tick_size=Decimal("0.001"),
                    min_order_size=Decimal("5"),
                    closed=True,
                    winner=winner,
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
                    condition_id=f"0xrepair{index}",
                    price=Decimal("0.20"),
                    size=Decimal("5"),
                    side="BUY",
                    transaction_hash=f"0xrepairtx{index}",
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

    row = await generate_strategy_repair_report(
        session_factory,
        Settings(
            cities=["seoul"],
            deterministic_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
            validation_history_days=200,
        ),
        policy_version="repair_v3",
        validation_scheme="fixed-holdout",
    )

    best = json.loads(row.best_variant_json)
    summary = json.loads(row.summary_json)
    assert best["policy_name"].startswith("repair_v3")
    assert summary["policy_version"] == "repair_v3"
    assert "traded_segments" in summary

    async with session_factory() as session:
        segments = (
            await session.execute(
                select(StrategyCalibrationSegment).where(
                    StrategyCalibrationSegment.run_id == row.id
                )
            )
        ).scalars().all()

    assert segments
    assert any(segment.eligible for segment in segments)
    assert all(
        segment.segment_key.startswith("specific|") or not segment.eligible
        for segment in segments
    )
