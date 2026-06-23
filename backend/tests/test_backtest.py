"""Backtest tests."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.backtest import build_parser, run_backtest
from app.config import Settings
from app.db.models import (
    City,
    DailyObservedMax,
    EnsembleMember,
    Event,
    ForecastSnapshot,
    Market,
    MarketPriceHistoryPoint,
    MarketPriceSnapshot,
    MarketTradeHistoryPoint,
    Signal,
)


def _city(now: datetime) -> City:
    return City(
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


def _event(now: datetime, end_date: datetime) -> Event:
    return Event(
        id="event-1",
        slug="highest-temperature-in-seoul-on-june-10-2026",
        title="Highest temperature in Seoul on June 10, 2026?",
        city_slug="seoul",
        target_date=date(2026, 6, 10),
        end_date=end_date,
        neg_risk_market_id=None,
        active=False,
        closed=True,
        volume=None,
        liquidity=None,
        first_seen_at=now,
        updated_at=now,
    )


def _market(now: datetime, *, winner: bool = True) -> Market:
    return Market(
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
        closed=True,
        winner=winner,
        resolved_at=now,
        updated_at=now,
    )


def test_backtest_parser_accepts_modes() -> None:
    parser = build_parser()

    assert parser.parse_args([]).mode == "stored-signals"
    assert parser.parse_args(["--mode", "replay"]).mode == "replay"
    assert parser.parse_args(["--mode", "historical-price"]).mode == "historical-price"
    assert parser.parse_args(["--mode", "both"]).mode == "both"


async def test_stored_signal_backtest_still_uses_persisted_signals(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 10, 10, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(_city(now))
        session.add(_event(now, datetime(2026, 6, 11, 12, tzinfo=UTC)))
        session.add(_market(now, winner=True))
        session.add(
            Signal(
                ts=now,
                market_id="market-1",
                token_id="yes-token",
                side="BUY",
                profile="max_edge",
                model_prob=0.31,
                market_price=Decimal("0.20"),
                edge_gross=Decimal("0.11000"),
                edge_net=Decimal("0.10200"),
                stake=Decimal("10"),
                status="PROPOSED",
                reason=None,
            )
        )

    results = await run_backtest(session_factory, Settings(), mode="stored-signals")
    by_profile = {result.profile: result for result in results}
    params = json.loads(by_profile["max_edge"].params_json)

    assert by_profile["longshot"].n_trades == 0
    assert by_profile["max_edge"].n_trades == 1
    assert by_profile["max_edge"].total_pnl == Decimal("38.08")
    assert params["source"] == "stored_signals_resolved_markets"
    assert params["fee_formula"] == "fee_rate * price * (1 - price)"


async def test_replay_ignores_future_ensembles_and_calculates_brier(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    price_ts = datetime(2026, 6, 10, 10, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(_city(price_ts))
        session.add(_event(price_ts, datetime(2026, 6, 11, 12, tzinfo=UTC)))
        session.add(_market(price_ts, winner=True))
        session.add(
            MarketPriceSnapshot(
                ts=price_ts,
                market_id="market-1",
                best_bid=Decimal("0.19"),
                best_ask=Decimal("0.20"),
                mid=Decimal("0.195"),
                bid_size=Decimal("100"),
                ask_size=Decimal("100"),
            )
        )
        past = ForecastSnapshot(
            fetched_at=datetime(2026, 6, 10, 9, tzinfo=UTC),
            city_slug="seoul",
            source="open_meteo_ensemble",
            model="gfs",
            target_date=date(2026, 6, 10),
            lead_days=0,
            tmax_c=None,
            n_members=1,
        )
        future = ForecastSnapshot(
            fetched_at=datetime(2026, 6, 10, 11, tzinfo=UTC),
            city_slug="seoul",
            source="open_meteo_ensemble",
            model="gfs",
            target_date=date(2026, 6, 10),
            lead_days=0,
            tmax_c=None,
            n_members=1,
        )
        session.add_all([past, future])
        await session.flush()
        session.add_all(
            [
                EnsembleMember(snapshot_id=past.id, member=0, tmax_c=25.0),
                EnsembleMember(snapshot_id=future.id, member=0, tmax_c=30.0),
            ]
        )

    results = await run_backtest(
        session_factory,
        Settings(
            ensemble_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
        ),
        mode="replay",
    )
    by_profile = {result.profile: result for result in results}
    params = json.loads(by_profile["max_edge"].params_json)

    assert by_profile["max_edge"].n_trades == 1
    assert by_profile["longshot"].n_trades == 1
    assert by_profile["max_edge"].total_pnl == Decimal("38.08")
    assert params["source"] == "replay_price_snapshots"
    assert params["execution_proxy"] == "best_ask_taker_no_depth_slippage"
    assert params["n_candidate_snapshots"] == 1
    assert params["n_resolved_trades"] == 1
    assert params["roi"] == "3.8080"
    assert params["max_loss_streak"] == 0
    assert params["avg_edge_net"] == "0.79200"
    assert params["avg_market_price"] == "0.20000"
    assert params["brier_model"] == 0.0
    assert round(params["brier_delta"], 4) == 0.64
    assert round(params["brier_market"], 4) == 0.64
    assert params["by_city"]["seoul"]["n_resolved_trades"] == 1
    assert params["by_lead_days"]["0"]["n_resolved_trades"] == 1
    assert params["by_bucket_kind"]["exact"]["n_resolved_trades"] == 1


async def test_historical_price_backtest_uses_walk_forward_bias_without_lookahead(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 10, 10, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(_city(now))
        session.add(_event(now, datetime(2026, 6, 11, 12, tzinfo=UTC)))
        session.add(_market(now, winner=True))
        session.add_all(
            [
                MarketPriceHistoryPoint(
                    ts=datetime(2026, 6, 8, 12, tzinfo=UTC),
                    market_id="market-1",
                    token_id="yes-token",
                    price=Decimal("0.20"),
                    interval="1d",
                    source="clob_prices_history",
                ),
                MarketPriceHistoryPoint(
                    ts=now,
                    market_id="market-1",
                    token_id="yes-token",
                    price=Decimal("0.20"),
                    interval="1d",
                    source="clob_prices_history",
                ),
            ]
        )
        session.add_all(
            [
                ForecastSnapshot(
                    fetched_at=now,
                    city_slug="seoul",
                    source="historical",
                    model="gfs",
                    target_date=date(2026, 6, 8),
                    lead_days=1,
                    tmax_c=24.0,
                    n_members=0,
                ),
                ForecastSnapshot(
                    fetched_at=now,
                    city_slug="seoul",
                    source="historical",
                    model="gfs",
                    target_date=date(2026, 6, 10),
                    lead_days=1,
                    tmax_c=24.0,
                    n_members=0,
                ),
                ForecastSnapshot(
                    fetched_at=now,
                    city_slug="seoul",
                    source="historical",
                    model="gfs",
                    target_date=date(2026, 6, 12),
                    lead_days=1,
                    tmax_c=40.0,
                    n_members=0,
                ),
            ]
        )
        session.add_all(
            [
                DailyObservedMax(
                    city_slug="seoul",
                    target_date=date(2026, 6, 8),
                    tmax_c=25.0,
                    source="era5",
                ),
                DailyObservedMax(
                    city_slug="seoul",
                    target_date=date(2026, 6, 12),
                    tmax_c=20.0,
                    source="era5",
                ),
            ]
        )

    results = await run_backtest(
        session_factory,
        Settings(
            cities=["seoul"],
            deterministic_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
            validation_history_days=30,
        ),
        mode="historical-price",
    )
    by_profile = {result.profile: result for result in results}
    params = json.loads(by_profile["max_edge"].params_json)

    assert by_profile["max_edge"].n_trades == 1
    assert by_profile["longshot"].n_trades == 1
    assert by_profile["max_edge"].total_pnl == Decimal("38.08")
    assert params["source"] == "historical_price_points"
    assert params["execution_proxy"] == "polymarket_prices_history_last_price_no_book_depth"
    assert params["model_input_source"] == "historical_deterministic_forecasts_as_members"
    assert params["walk_forward_calibration"] is True
    assert params["n_candidate_price_points"] == 1
    assert params["n_resolved_trades"] == 1
    assert params["brier_model"] == 0.0


async def test_historical_price_backtest_prefers_valid_trade_history_points(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 10, 10, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(_city(now))
        session.add(_event(now, datetime(2026, 6, 11, 12, tzinfo=UTC)))
        session.add(_market(now, winner=True))
        session.add(
            MarketPriceHistoryPoint(
                ts=now,
                market_id="market-1",
                token_id="yes-token",
                price=Decimal("0.90"),
                interval="1d",
                source="clob_prices_history",
            )
        )
        session.add(
            MarketTradeHistoryPoint(
                ts=now,
                market_id="market-1",
                token_id="yes-token",
                condition_id="0xcond",
                price=Decimal("0.20"),
                size=Decimal("5"),
                side="BUY",
                transaction_hash="0xtx",
                source="data_api_trades",
            )
        )
        session.add(
            ForecastSnapshot(
                fetched_at=now,
                city_slug="seoul",
                source="historical",
                model="gfs",
                target_date=date(2026, 6, 10),
                lead_days=1,
                tmax_c=25.0,
                n_members=0,
            )
        )

    results = await run_backtest(
        session_factory,
        Settings(
            cities=["seoul"],
            deterministic_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
            validation_history_days=30,
        ),
        mode="historical-price",
    )
    by_profile = {result.profile: result for result in results}
    params = json.loads(by_profile["max_edge"].params_json)

    assert by_profile["max_edge"].n_trades == 1
    assert by_profile["max_edge"].total_pnl == Decimal("38.08")
    assert params["execution_proxy"] == "historical_last_trade_no_book_depth"
    assert params["price_source_counts"] == {
        "clob_prices_history": 0,
        "data_api_trades": 1,
    }


async def test_historical_price_backtest_samples_last_trade_per_hour(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 10, 10, 45, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(_city(now))
        session.add(_event(now, datetime(2026, 6, 11, 12, tzinfo=UTC)))
        session.add(_market(now, winner=True))
        session.add_all(
            [
                MarketTradeHistoryPoint(
                    ts=datetime(2026, 6, 10, 10, 5, tzinfo=UTC),
                    market_id="market-1",
                    token_id="yes-token",
                    condition_id="0xcond",
                    price=Decimal("0.90"),
                    size=Decimal("5"),
                    side="BUY",
                    transaction_hash="0xold",
                    source="data_api_trades",
                ),
                MarketTradeHistoryPoint(
                    ts=now,
                    market_id="market-1",
                    token_id="yes-token",
                    condition_id="0xcond",
                    price=Decimal("0.20"),
                    size=Decimal("5"),
                    side="BUY",
                    transaction_hash="0xnew",
                    source="data_api_trades",
                ),
            ]
        )
        session.add(
            ForecastSnapshot(
                fetched_at=now,
                city_slug="seoul",
                source="historical",
                model="gfs",
                target_date=date(2026, 6, 10),
                lead_days=1,
                tmax_c=25.0,
                n_members=0,
            )
        )

    results = await run_backtest(
        session_factory,
        Settings(
            cities=["seoul"],
            deterministic_models=["gfs"],
            min_edge_net=Decimal("0.01"),
            prob_clamp_epsilon=0.0,
            validation_history_days=30,
        ),
        mode="historical-price",
    )
    by_profile = {result.profile: result for result in results}
    params = json.loads(by_profile["max_edge"].params_json)

    assert by_profile["max_edge"].n_trades == 1
    assert by_profile["max_edge"].total_pnl == Decimal("38.08")
    assert params["price_sampling"] == "last_trade_per_market_per_60m_bucket"
    assert params["n_raw_price_points"] == 2
    assert params["n_sampled_price_points"] == 1
    assert params["n_candidate_price_points"] == 1
    assert params["price_source_raw_counts"] == {
        "clob_prices_history": 0,
        "data_api_trades": 2,
    }
    assert params["price_source_sampled_counts"] == {
        "clob_prices_history": 0,
        "data_api_trades": 1,
    }
