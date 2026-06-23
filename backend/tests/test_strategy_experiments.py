"""Diagnostic strategy experiment tests."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.strategy_experiments import (
    EXPERIMENT_SET,
    ExperimentVariant,
    _evaluate_variant,
    _gates,
    _status,
    generate_strategy_experiment_report,
)
from analysis.strategy_repair import HistoricalCandidate
from app.config import Settings
from app.db.models import PaperFill, PaperOrder, Signal, StrategyExperimentRun


def _candidate(
    index: int,
    *,
    price: Decimal = Decimal("0.20"),
    raw_prob: float = 0.60,
    winner: bool = True,
    bucket_kind: str = "below",
) -> HistoricalCandidate:
    ts = datetime(2026, 1, 1, 10, tzinfo=UTC) + timedelta(days=index)
    return HistoricalCandidate(
        ts=ts,
        sampled_ts=ts,
        market_id=f"m-{index}",
        event_id=f"e-{index}",
        city_slug="seoul",
        target_date=date(2026, 1, 1) + timedelta(days=index),
        price=price,
        raw_prob=raw_prob,
        winner=winner,
        bucket_kind=bucket_kind,
        bucket_label="25C or lower",
        hours_to_close=12.0,
        price_source="data_api_trades",
    )


def test_strategy_experiment_blocks_extreme_above_high_price() -> None:
    payload = _evaluate_variant(
        [
            _candidate(
                1,
                price=Decimal("0.97"),
                raw_prob=0.95,
                bucket_kind="above",
            )
        ],
        Settings(),
        ExperimentVariant(
            name="flex_v1_test",
            alpha=0.10,
            probability_cap=0.40,
            min_samples=30,
            min_edge_net=Decimal("0.000"),
        ),
        blocked_city_slugs=set(),
    )

    assert payload["blocked_counts"] == {"blocked_extreme_above_high_price": 1}
    assert payload["profiles"]["max_edge"]["n_resolved_trades"] == 0  # type: ignore[index]
    assert payload["cannot_approve_live"] is True


def test_strategy_experiment_can_reach_shadow_paper_status() -> None:
    candidates = [_candidate(i, winner=i % 5 != 0) for i in range(90)]
    best = _evaluate_variant(
        candidates,
        Settings(),
        ExperimentVariant(
            name="flex_v1_test",
            alpha=0.15,
            probability_cap=0.40,
            min_samples=30,
            min_edge_net=Decimal("0.000"),
        ),
        blocked_city_slugs=set(),
    )
    gates = _gates(best, (True, {"missing_cities": [], "needs_review": []}))

    assert gates["diagnostic_brier"]["passed"] is True  # type: ignore[index]
    assert gates["proxy_pnl"]["passed"] is True  # type: ignore[index]
    assert gates["oos_or_historical_sample"]["passed"] is True  # type: ignore[index]
    assert _status(gates) == "READY_FOR_SHADOW_PAPER"
    assert gates["live_release"]["passed"] is False  # type: ignore[index]


async def test_strategy_experiment_report_does_not_create_trading_artifacts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    row = await generate_strategy_experiment_report(
        session_factory,
        Settings(cities=["seoul"], validation_history_days=30),
        cities=["seoul"],
        days=30,
        experiment_set=EXPERIMENT_SET,
    )

    async with session_factory() as session:
        signals = (await session.execute(select(func.count(Signal.id)))).scalar_one()
        orders = (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        fills = (await session.execute(select(func.count(PaperFill.id)))).scalar_one()
        persisted = (await session.execute(select(StrategyExperimentRun))).scalar_one()

    assert row.id == persisted.id
    assert signals == 0
    assert orders == 0
    assert fills == 0
    assert row.experiment_set == EXPERIMENT_SET
    assert json.loads(row.shadow_json)["forward_shadow_enabled"] is False
