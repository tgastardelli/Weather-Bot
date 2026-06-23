"""Feature discovery tests."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis import feature_discovery
from analysis.feature_discovery import (
    FeatureVariant,
    _build_segments,
    _decision_price,
    _decision_winner,
    _enrich_candidates,
    _gates,
    _reason,
    _rolling_origin,
    generate_feature_discovery_report,
)
from analysis.strategy_repair import HistoricalCandidate
from app.config import Settings
from app.db.models import FeatureDiscoveryRun, PaperFill, PaperOrder, Signal


def _candidate(
    index: int,
    *,
    market_id: str = "m-1",
    price: Decimal = Decimal("0.20"),
    raw_prob: float = 0.70,
    winner: bool = True,
    city_slug: str = "dallas",
) -> HistoricalCandidate:
    ts = datetime(2025, 1, 1, 10, tzinfo=UTC) + timedelta(days=index)
    return HistoricalCandidate(
        ts=ts,
        sampled_ts=ts,
        market_id=market_id,
        event_id=f"e-{index}",
        city_slug=city_slug,
        target_date=date(2025, 1, 1) + timedelta(days=index),
        price=price,
        raw_prob=raw_prob,
        winner=winner,
        bucket_kind="below",
        bucket_label="25C or lower",
        hours_to_close=12.0,
        price_source="data_api_trades",
    )


def test_feature_enrichment_uses_only_prior_market_points() -> None:
    enriched = _enrich_candidates(
        [
            _candidate(0, price=Decimal("0.20"), raw_prob=0.40),
            _candidate(1, price=Decimal("0.30"), raw_prob=0.55),
            _candidate(2, price=Decimal("0.10"), raw_prob=0.45),
        ]
    )

    assert enriched[0].price_momentum_24h == Decimal("0.00")
    assert enriched[1].price_momentum_24h == Decimal("0.10")
    assert enriched[1].forecast_revision == pytest.approx(0.15)
    assert enriched[2].price_momentum_24h == Decimal("-0.20")


def test_buy_no_feature_value_uses_no_price_and_winner() -> None:
    enriched = _enrich_candidates(
        [_candidate(i, price=Decimal("0.80"), raw_prob=0.20, winner=False) for i in range(40)]
    )
    variant = FeatureVariant(
        name="buy_no_feature_value_no_n30_edge0_000",
        family="buy_no_feature_value",
        side="NO",
        min_samples=30,
        min_edge_net=Decimal("0.000"),
        probability_cap=0.80,
    )
    segments = _build_segments(enriched, Decimal("0.05"), variant)
    reason = _reason(enriched[-1], segments[next(iter(segments))], variant, Decimal("0.05"))

    assert _decision_price(enriched[-1], variant) == Decimal("0.20000")
    assert _decision_winner(enriched[-1], variant) is True
    assert reason is None


def test_feature_gates_do_not_release_live_directly() -> None:
    gates = _gates(
        {
            "profile": {
                "n_resolved_trades": 80,
                "total_pnl": "10",
                "brier_delta": 0.02,
                "top_5_abs_pnl_share": "0.10",
                "pnl_ci_high": "1",
                "traded_cities": ["dallas", "seattle"],
            }
        },
        valid_folds=3,
        selected_cities=["dallas", "seattle"],
        quarantined=[],
    )

    assert gates["feature_candidate"]["passed"] is True  # type: ignore[index]
    assert gates["live_release"]["passed"] is False  # type: ignore[index]


def test_feature_rolling_origin_selects_without_future_fold_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feature_discovery, "DEFAULT_MIN_TRAIN_CANDIDATES", 20)
    monkeypatch.setattr(feature_discovery, "DEFAULT_MIN_FOLD_CANDIDATES", 5)
    candidates = [
        _candidate(
            i,
            price=Decimal("0.20"),
            raw_prob=0.70,
            winner=i < 120 or i % 2 == 0,
            city_slug="dallas" if i % 2 == 0 else "seattle",
        )
        for i in range(170)
    ]
    best, folds, summary = _rolling_origin(
        _enrich_candidates(candidates),
        Settings(max_stake_per_order=Decimal("1"), max_exposure_per_market=Decimal("999")),
    )

    assert int(summary["valid_folds"]) >= 1
    assert all(
        fold.get("n_train", 0) < len(candidates)
        for fold in folds
        if fold.get("valid") is True
    )
    assert best is None or best["cannot_approve_live"] is True


async def test_feature_discovery_report_does_not_create_trading_artifacts(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_candidates(
        session: AsyncSession, settings: Settings
    ) -> tuple[list[HistoricalCandidate], int, dict[str, int], dict[str, int], dict[str, int]]:
        return (
            [_candidate(i) for i in range(20)],
            20,
            {"data_api_trades": 20, "clob_prices_history": 0},
            {"data_api_trades": 20, "clob_prices_history": 0},
            {"data_api_trades": 20, "clob_prices_history": 0},
        )

    monkeypatch.setattr(feature_discovery, "_historical_candidates", fake_candidates)

    row = await generate_feature_discovery_report(
        session_factory,
        Settings(cities=["dallas"], validation_history_days=730),
        cities=["dallas"],
        days=730,
    )

    async with session_factory() as session:
        assert row.status in {"NO_FEATURE_EDGE", "DATA_REVIEW"}
        assert (await session.execute(select(func.count(Signal.id)))).scalar_one() == 0
        assert (await session.execute(select(func.count(PaperOrder.id)))).scalar_one() == 0
        assert (await session.execute(select(func.count(PaperFill.id)))).scalar_one() == 0
        assert (
            await session.execute(select(func.count(FeatureDiscoveryRun.id)))
        ).scalar_one() == 1
