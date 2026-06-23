"""Feature candidate audit tests."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.feature_candidate_audit import generate_feature_candidate_audit_report
from analysis.strategy_repair import HistoricalCandidate
from app.config import Settings
from app.db.models import (
    FeatureCandidateAuditRun,
    FeatureDiscoveryRun,
    PaperFill,
    PaperOrder,
    Signal,
)


def _candidate(index: int, *, winner: bool = True) -> HistoricalCandidate:
    ts = datetime(2025, 1, 1, 10, tzinfo=UTC) + timedelta(days=index)
    return HistoricalCandidate(
        ts=ts,
        sampled_ts=ts,
        market_id=f"m-{index}",
        event_id=f"e-{index}",
        city_slug="dallas" if index % 2 == 0 else "seattle",
        target_date=date(2025, 1, 1) + timedelta(days=index),
        price=Decimal("0.20"),
        raw_prob=0.70,
        winner=winner,
        bucket_kind="below",
        bucket_label="25C or lower",
        hours_to_close=12.0,
        price_source="data_api_trades",
    )


async def test_feature_candidate_audit_data_review_without_candidate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    row = await generate_feature_candidate_audit_report(session_factory, Settings())

    assert row.status == "DATA_REVIEW"
    assert row.feature_discovery_run_id is None
    assert "no_feature_candidate" in row.summary_json


async def test_feature_candidate_audit_replays_latest_candidate_without_trading_artifacts(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    run_at = datetime(2026, 6, 22, tzinfo=UTC)
    folds = [
        {
            "index": 0,
            "valid": True,
            "fold_window": {"start": "2025-04-01", "end": "2025-05-30"},
            "selected_variant": "threshold_distance_specialist_yes_n30_edge0_000",
        },
        {
            "index": 1,
            "valid": True,
            "fold_window": {"start": "2025-05-31", "end": "2025-07-29"},
            "selected_variant": "threshold_distance_specialist_yes_n30_edge0_000",
        },
    ]
    async with session_factory() as session, session.begin():
        session.add(
            FeatureDiscoveryRun(
                run_at=run_at,
                status="FEATURE_CANDIDATE",
                window_start=date(2025, 1, 1),
                window_end=date(2025, 8, 1),
                cities_json='["dallas", "seattle"]',
                summary_json='{"best_family": "threshold_distance_specialist"}',
                families_json="{}",
                best_family_json="{}",
                folds_json=json.dumps(folds),
                gates_json="{}",
            )
        )

    async def fake_candidates(
        session: AsyncSession, settings: Settings
    ) -> tuple[list[HistoricalCandidate], int, dict[str, int], dict[str, int], dict[str, int]]:
        candidates = [_candidate(i, winner=i < 150 or i % 3 == 0) for i in range(220)]
        return (
            candidates,
            len(candidates),
            {"data_api_trades": len(candidates), "clob_prices_history": 0},
            {"data_api_trades": len(candidates), "clob_prices_history": 0},
            {"data_api_trades": len(candidates), "clob_prices_history": 0},
        )

    monkeypatch.setattr("analysis.feature_candidate_audit._historical_candidates", fake_candidates)

    row = await generate_feature_candidate_audit_report(
        session_factory,
        Settings(max_stake_per_order=Decimal("1"), max_exposure_per_market=Decimal("999")),
    )

    async with session_factory() as session:
        assert row.status in {"CANDIDATE_REVIEW", "READY_FOR_REPAIR_V5", "REJECTED_FEATURE_EDGE"}
        assert (await session.execute(select(func.count(Signal.id)))).scalar_one() == 0
        assert (await session.execute(select(func.count(PaperOrder.id)))).scalar_one() == 0
        assert (await session.execute(select(func.count(PaperFill.id)))).scalar_one() == 0
        assert (
            await session.execute(select(func.count(FeatureCandidateAuditRun.id)))
        ).scalar_one() == 1
