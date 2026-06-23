"""Strategy discovery tests."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.strategy_discovery import (
    _build_segments,
    _decision_price,
    _decision_winner,
    _gates,
    _reason,
    _research_cities,
    _rolling_origin,
    _status,
    _variants,
    generate_strategy_discovery_report,
)
from analysis.strategy_repair import HistoricalCandidate
from app.config import Settings
from app.db.models import (
    CalibrationMetric,
    City,
    PaperFill,
    PaperOrder,
    Signal,
    StrategyDiscoveryRun,
)


def _candidate(
    index: int,
    *,
    price: Decimal = Decimal("0.20"),
    raw_prob: float = 0.60,
    winner: bool = True,
    city_slug: str = "seoul",
) -> HistoricalCandidate:
    ts = datetime(2025, 1, 1, 10, tzinfo=UTC) + timedelta(days=index)
    return HistoricalCandidate(
        ts=ts,
        sampled_ts=ts,
        market_id=f"m-{index}",
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


def test_discovery_can_find_shadow_candidate_from_oos_folds() -> None:
    candidates = [
        _candidate(i, winner=i % 5 != 0, city_slug="seoul" if i % 2 == 0 else "tokyo")
        for i in range(220)
    ]
    best, folds, summary = _rolling_origin(
        candidates,
        Settings(max_stake_per_order=Decimal("1"), max_exposure_per_market=Decimal("999")),
    )
    gates = _gates(
        best,
        valid_folds=int(summary["valid_folds"]),
        universe_health={"eligible": ["seoul", "tokyo"]},
    )

    assert len([fold for fold in folds if fold["valid"] is True]) >= 3
    assert gates["oos_trades"]["passed"] is True  # type: ignore[index]
    assert gates["oos_pnl"]["passed"] is True  # type: ignore[index]
    assert gates["live_release"]["passed"] is False  # type: ignore[index]
    assert _status(gates) in {"READY_FOR_SHADOW_PAPER", "DISCOVERY_CANDIDATE"}


def test_discovery_rejects_pnl_positive_when_brier_is_negative() -> None:
    best = {
        "profile": {
            "n_resolved_trades": 80,
            "total_pnl": "10",
            "brier_delta": -0.10,
            "top_5_abs_pnl_share": "0.10",
            "pnl_ci_high": "1",
            "city_pnl_share": {"top_city_abs_pnl_share": "0.50"},
        }
    }
    gates = _gates(best, valid_folds=3, universe_health={"eligible": ["seoul", "tokyo"]})

    assert gates["oos_pnl"]["passed"] is True  # type: ignore[index]
    assert gates["oos_brier"]["passed"] is False  # type: ignore[index]
    assert _status(gates) == "NO_EDGE_FOUND"


async def test_discovery_research_universe_excludes_needs_review_and_low_samples(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 17, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                City(
                    slug="seoul",
                    name="Seoul",
                    series_slug="seoul-daily-weather",
                    station_code="RKSI",
                    station_name=None,
                    latitude=37.46,
                    longitude=126.44,
                    timezone="Asia/Seoul",
                    unit="C",
                    resolution_source="wunderground",
                    resolution_url=None,
                    rounding="round",
                    needs_review=False,
                    active=True,
                    updated_at=now,
                ),
                City(
                    slug="review-city",
                    name="Review",
                    series_slug=None,
                    station_code=None,
                    station_name=None,
                    latitude=None,
                    longitude=None,
                    timezone=None,
                    unit="C",
                    resolution_source=None,
                    resolution_url=None,
                    rounding="round",
                    needs_review=True,
                    active=True,
                    updated_at=now,
                ),
                City(
                    slug="thin-city",
                    name="Thin",
                    series_slug=None,
                    station_code=None,
                    station_name=None,
                    latitude=None,
                    longitude=None,
                    timezone=None,
                    unit="C",
                    resolution_source=None,
                    resolution_url=None,
                    rounding="round",
                    needs_review=False,
                    active=True,
                    updated_at=now,
                ),
            ]
        )
        session.add_all(
            [
                CalibrationMetric(
                    computed_at=now,
                    city_slug="seoul",
                    model="ensemble_pool",
                    lead_days=1,
                    bias_c=0,
                    mae_c=1,
                    residual_std_c=1,
                    n_samples=120,
                ),
                CalibrationMetric(
                    computed_at=now,
                    city_slug="thin-city",
                    model="ensemble_pool",
                    lead_days=1,
                    bias_c=0,
                    mae_c=1,
                    residual_std_c=1,
                    n_samples=20,
                ),
            ]
        )

    async with session_factory() as session:
        eligible, health = await _research_cities(
            session, Settings(validation_min_samples=120), universe="research"
        )

    assert eligible == ["seoul"]
    assert health["excluded_needs_review"] == ["review-city"]
    assert health["excluded_low_samples"] == ["thin-city"]


async def test_discovery_poc_universe_can_include_research_only_city(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 17, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                City(
                    slug="seoul",
                    name="Seoul",
                    series_slug="seoul-daily-weather",
                    station_code="RKSI",
                    station_name=None,
                    latitude=37.46,
                    longitude=126.44,
                    timezone="Asia/Seoul",
                    unit="C",
                    resolution_source="wunderground",
                    resolution_url=None,
                    rounding="round",
                    needs_review=False,
                    active=True,
                    updated_at=now,
                ),
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
                    needs_review=True,
                    active=True,
                    updated_at=now,
                ),
            ]
        )
        for city_slug in ("seoul", "nyc"):
            session.add(
                CalibrationMetric(
                    computed_at=now,
                    city_slug=city_slug,
                    model="ensemble_pool",
                    lead_days=1,
                    bias_c=0,
                    mae_c=1,
                    residual_std_c=1,
                    n_samples=120,
                )
            )

    async with session_factory() as session:
        selected, health = await _research_cities(
            session,
            Settings(validation_min_samples=120),
            universe="poc",
            include_research_only=True,
        )

    assert selected == ["seoul", "nyc"]
    assert health["live_eligible"] == ["seoul"]
    assert health["research_only"] == ["nyc"]
    assert health["selected_cities"] == ["seoul", "nyc"]
    assert health["operational_quarantine"][0]["city_slug"] == "nyc"


async def test_ranked_live_discovery_filters_quarantined_requested_city(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 17, tzinfo=UTC)
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
        session.add(
            CalibrationMetric(
                computed_at=now,
                city_slug="nyc",
                model="ensemble_pool",
                lead_days=1,
                bias_c=0,
                mae_c=1,
                residual_std_c=1,
                n_samples=120,
            )
        )

    row = await generate_strategy_discovery_report(
        session_factory,
        Settings(validation_min_samples=120),
        cities=["nyc"],
        days=30,
        universe="ranked-live",
        discovery_version="v3",
    )

    summary = json.loads(row.summary_json)
    gates = json.loads(row.gates_json)
    assert json.loads(row.cities_json) == []
    assert summary["requested_cities"] == ["nyc"]
    assert summary["excluded_quarantined"] == ["nyc"]
    assert gates["universe_health"]["passed"] is False
    assert row.status == "DATA_REVIEW"


async def test_ranked_live_discovery_reports_requested_operational_live_cities(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 17, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            City(
                slug="dallas",
                name="Dallas",
                series_slug="dallas-daily-weather",
                station_code="KDAL",
                station_name=None,
                latitude=32.8,
                longitude=-96.8,
                timezone="America/Chicago",
                unit="F",
                resolution_source="wunderground",
                resolution_url=None,
                rounding="round",
                needs_review=False,
                active=True,
                updated_at=now,
            )
        )
        session.add(
            CalibrationMetric(
                computed_at=now,
                city_slug="dallas",
                model="ensemble_pool",
                lead_days=1,
                bias_c=0,
                mae_c=1,
                residual_std_c=1,
                n_samples=120,
            )
        )

    row = await generate_strategy_discovery_report(
        session_factory,
        Settings(validation_min_samples=120),
        cities=["dallas"],
        days=30,
        universe="ranked-live",
        discovery_version="v3",
    )

    summary = json.loads(row.summary_json)
    assert summary["live_eligible_cities"] == ["dallas"]
    assert summary["operational_candidates"] == ["dallas"]


def test_discovery_v2_caps_shadow_when_edge_only_uses_research_only_city() -> None:
    best = {
        "profile": {
            "n_resolved_trades": 80,
            "total_pnl": "10",
            "brier_delta": 0.10,
            "top_5_abs_pnl_share": "0.10",
            "pnl_ci_high": "1",
            "city_pnl_share": {"top_city_abs_pnl_share": "0.50"},
            "traded_cities": ["nyc"],
        }
    }
    gates = _gates(
        best,
        valid_folds=3,
        universe_health={
            "selected_cities": ["nyc"],
            "live_eligible": [],
            "research_only": ["nyc"],
        },
        discovery_version="v2",
    )

    assert gates["diagnostic_candidate"]["passed"] is True  # type: ignore[index]
    assert gates["research_only_cap"]["passed"] is False  # type: ignore[index]
    assert _status(gates) == "DISCOVERY_CANDIDATE"


def test_discovery_v3_includes_buy_no_as_diagnostic_side() -> None:
    variants = _variants("v3")
    buy_no = next(variant for variant in variants if variant.family == "buy_no_value")
    candidate = _candidate(1, price=Decimal("0.80"), winner=True)

    assert buy_no.side == "NO"
    assert buy_no.require_brier_positive is False
    assert _decision_price(candidate, buy_no) == Decimal("0.20000")
    assert _decision_winner(candidate, buy_no) is False


def test_discovery_v4_includes_new_research_families() -> None:
    families = {variant.family for variant in _variants("v4")}

    assert "market_extreme_fade" in families
    assert "city_season_specialist" in families
    assert "time_to_close_specialist" in families
    assert "forecast_error_regime" in families
    assert "dallas_fast_lane" in families
    assert any(variant.side == "NO" for variant in _variants("v4"))


def test_buy_no_uses_no_side_segment_cost_and_outcome() -> None:
    variant = next(variant for variant in _variants("v4") if variant.family == "buy_no_value")
    train = [
        _candidate(i, price=Decimal("0.80"), raw_prob=0.80, winner=False)
        for i in range(40)
    ]
    candidate = _candidate(50, price=Decimal("0.80"), raw_prob=0.80, winner=False)
    segments = _build_segments(train, Decimal("0.05"), variant)

    assert _reason(candidate, segments[next(iter(segments))], variant, Decimal("0.05")) is None


def test_dallas_fast_lane_diversification_exception_requires_volume() -> None:
    base_profile = {
        "total_pnl": "10",
        "brier_delta": 0.10,
        "top_5_abs_pnl_share": "0.10",
        "pnl_ci_high": "1",
        "city_pnl_share": {"top_city_abs_pnl_share": "1.0000"},
        "traded_cities": ["dallas"],
    }
    low_volume = {
        "family": "dallas_fast_lane",
        "profile": {**base_profile, "n_resolved_trades": 99},
    }
    high_volume = {
        "family": "dallas_fast_lane",
        "profile": {**base_profile, "n_resolved_trades": 100},
    }

    assert (
        _gates(
            low_volume,
            valid_folds=3,
            universe_health={"live_eligible": ["dallas"], "selected_cities": ["dallas"]},
            discovery_version="v4",
        )["city_diversification"]["passed"]  # type: ignore[index]
        is False
    )
    assert (
        _gates(
            high_volume,
            valid_folds=3,
            universe_health={"live_eligible": ["dallas"], "selected_cities": ["dallas"]},
            discovery_version="v4",
        )["city_diversification"]["passed"]  # type: ignore[index]
        is True
    )


async def test_discovery_report_does_not_create_trading_artifacts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    row = await generate_strategy_discovery_report(
        session_factory,
        Settings(cities=["seoul"], validation_history_days=30),
        cities=["seoul"],
        days=30,
        universe="focus",
    )

    async with session_factory() as session:
        signals = (await session.execute(select(func.count(Signal.id)))).scalar_one()
        orders = (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        fills = (await session.execute(select(func.count(PaperFill.id)))).scalar_one()
        persisted = (await session.execute(select(StrategyDiscoveryRun))).scalar_one()

    assert row.id == persisted.id
    assert signals == 0
    assert orders == 0
    assert fills == 0
    assert json.loads(row.summary_json)["cannot_approve_live"] is True
