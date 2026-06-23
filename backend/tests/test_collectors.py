"""Collector integration tests with mocked clients."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.backfill import _selected_cities, parse_cities
from app.collectors.forecasts import collect_forecasts
from app.collectors.market_history_backfill import (
    collect_market_history,
    collect_market_history_chunked,
    parse_price_history_points,
    parse_trade_history_points,
)
from app.collectors.markets import collect_markets
from app.config import Settings
from app.db.models import (
    City,
    EnsembleMember,
    Event,
    ForecastSnapshot,
    HistoryBackfillRun,
    Market,
    MarketPriceHistoryPoint,
    MarketPriceSnapshot,
    MarketTradeHistoryPoint,
)
from app.weather.open_meteo import OpenMeteoClient


class FakePolymarketClient:
    async def list_weather_events(
        self, *, active: bool = True, closed: bool = False, page_size: int = 100
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": "123",
                "slug": "highest-temperature-in-seoul-on-june-10-2026",
                "title": "Highest temperature in Seoul on June 10, 2026?",
                "endDate": "2026-06-11T12:00:00Z",
                "active": active,
                "closed": closed,
                "markets": [
                    {
                        "id": "m1",
                        "conditionId": "0xcond",
                        "question": "Will it be 23°C or below?",
                        "groupItemTitle": "23°C or below",
                        "groupItemThreshold": "0",
                        "clobTokenIds": '["yes-token","no-token"]',
                        "outcomePrices": '["0.12","0.88"]',
                        "orderPriceMinTickSize": "0.001",
                        "orderMinSize": "5",
                        "closed": False,
                        "gameStartTime": "2026-06-10 00:00:00+00",
                        "description": (
                            "Resolves using "
                            "wunderground.com/history/daily/kr/incheon/RKSI"
                        ),
                    }
                ],
            }
        ]

    async def get_book(self, token_id: str) -> dict[str, Any]:
        return {
            "asset_id": token_id,
            "bids": [{"price": "0.10", "size": "100"}],
            "asks": [{"price": "0.12", "size": "50"}],
        }

    async def get_prices_history(
        self, token_id: str, interval: str = "1d"
    ) -> list[dict[str, Any]]:
        return [
            {"t": 1_781_078_400, "p": "0.12"},
            {"timestamp": 1_781_164_800_000, "price": "0.20"},
            {"t": "not-a-date", "p": "0.30"},
            {"t": 1_781_251_200, "p": "1.00"},
        ]

    async def get_public_trades(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class FakeTradeHistoryClient(FakePolymarketClient):
    def __init__(self, trades: list[dict[str, Any]]) -> None:
        self.trades = trades

    async def get_prices_history(
        self, token_id: str, interval: str = "1d"
    ) -> list[dict[str, Any]]:
        return []

    async def get_public_trades(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self.trades


class FakeWeatherResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeWeatherHttp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def get(self, url: str, params: dict[str, Any]) -> FakeWeatherResponse:
        return FakeWeatherResponse(self.payload)


class FakeOpenMeteoClient:
    async def daily_tmax_forecast(
        self, lat: float, lon: float, models: list[str], days: int
    ) -> dict[str, list[tuple[date, float | None]]]:
        return {models[0]: [(date(2026, 6, 10), 24.0)]}

    async def ensemble_daily_tmax(
        self, lat: float, lon: float, model: str, days: int
    ) -> dict[date, list[float]]:
        return {date(2026, 6, 10): [23.0, 24.0, 25.0]}


async def test_collect_markets_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(cities=["seoul"])
    fake_client = FakePolymarketClient()

    first = await collect_markets(session_factory, fake_client, settings)  # type: ignore[arg-type]
    second = await collect_markets(session_factory, fake_client, settings)  # type: ignore[arg-type]

    async with session_factory() as session:
        events = (await session.execute(select(Event))).scalars().all()
        markets = (await session.execute(select(Market))).scalars().all()
        snapshots = (await session.execute(select(MarketPriceSnapshot))).scalars().all()

    assert first.events_upserted == 1
    assert second.events_upserted == 1
    assert len(events) == 1
    assert len(markets) == 1
    assert len(snapshots) == 2


def test_backfill_parse_cities() -> None:
    assert parse_cities("nyc, shanghai,,") == ["nyc", "shanghai"]
    assert parse_cities(None) is None


async def test_backfill_selected_cities_limits_to_requested_registry_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 18, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add_all(
            [
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
                City(
                    slug="seoul",
                    name="Seoul",
                    series_slug="seoul-daily-weather",
                    station_code="RKSI",
                    station_name=None,
                    latitude=37.4,
                    longitude=126.4,
                    timezone="Asia/Seoul",
                    unit="F",
                    resolution_source="wunderground",
                    resolution_url=None,
                    rounding="round",
                    needs_review=False,
                    active=True,
                    updated_at=now,
                ),
            ]
        )

    async with session_factory() as session:
        cities, missing = await _selected_cities(
            session,
            Settings(cities=["seoul"]),
            ["nyc", "missing-city"],
        )

    assert [city.slug for city in cities] == ["nyc"]
    assert missing == ["missing-city"]


def test_prices_history_parser_handles_seconds_millis_and_invalid_rows() -> None:
    points = parse_price_history_points(
        [
            {"t": 1_781_078_400, "p": "0.12"},
            {"timestamp": 1_781_164_800_000, "price": "0.20"},
            {"time": "bad", "value": "0.30"},
            {"ts": 1_781_251_200, "p": "1.00"},
        ]
    )

    assert [point.price for point in points] == [Decimal("0.12"), Decimal("0.20")]
    assert all(point.ts.tzinfo is UTC for point in points)


def test_trade_history_parser_accepts_valid_filtered_payload() -> None:
    result = parse_trade_history_points(
        [
            {
                "timestamp": 1_781_164_800,
                "price": "0.20",
                "size": "5",
                "side": "BUY",
                "asset": "yes-token",
                "conditionId": "0xcond",
                "transactionHash": "0xtx",
                "eventSlug": "highest-temperature-in-seoul-on-june-10-2026",
                "proxyWallet": "not-persisted",
                "name": "not-persisted",
            },
            {
                "timestamp": 1_781_164_801,
                "price": "0.80",
                "size": "7",
                "side": "SELL",
                "asset": "no-token",
                "conditionId": "0xcond",
                "transactionHash": "0xno",
                "eventSlug": "highest-temperature-in-seoul-on-june-10-2026",
                "outcome": "No",
            }
        ],
        token_id="yes-token",
        condition_id="0xcond",
        event_slug="highest-temperature-in-seoul-on-june-10-2026",
    )

    assert result.status == "accepted"
    assert len(result.points) == 1
    assert result.points[0].price == Decimal("0.20")
    assert result.points[0].size == Decimal("5")
    assert result.points[0].transaction_hash == "0xtx"


def test_trade_history_parser_rejects_global_or_invalid_payloads() -> None:
    rejected = parse_trade_history_points(
        [
            {
                "timestamp": 1_781_164_800,
                "price": "0.20",
                "size": "5",
                "asset": "other-token",
                "conditionId": "0xother",
            }
        ],
        token_id="yes-token",
        condition_id="0xcond",
        event_slug="highest-temperature-in-seoul-on-june-10-2026",
    )
    invalid = parse_trade_history_points(
        [
            {
                "timestamp": 1_781_164_800,
                "price": "1.00",
                "size": "5",
                "asset": "yes-token",
                "conditionId": "0xcond",
            }
        ],
        token_id="yes-token",
        condition_id="0xcond",
        event_slug="highest-temperature-in-seoul-on-june-10-2026",
    )

    assert rejected.status == "rejected_unfiltered_response"
    assert invalid.status == "invalid_payload"


async def test_market_history_backfill_is_idempotent_and_separate_from_snapshots(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(cities=["seoul"])
    fake_client = FakePolymarketClient()

    first = await collect_market_history(
        session_factory,
        fake_client,  # type: ignore[arg-type]
        settings,
        days=30,
    )
    second = await collect_market_history(
        session_factory,
        fake_client,  # type: ignore[arg-type]
        settings,
        days=30,
    )

    async with session_factory() as session:
        points = (await session.execute(select(MarketPriceHistoryPoint))).scalars().all()
        snapshots = (await session.execute(select(MarketPriceSnapshot))).scalars().all()

    assert first.events_seen == 1
    assert first.history_points == 2
    assert second.history_points == 0
    assert len(points) == 2
    assert len(snapshots) == 0


async def test_market_history_backfill_uses_valid_trades_when_prices_history_is_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(cities=["seoul"])
    fake_client = FakeTradeHistoryClient(
        [
            {
                "timestamp": 1_781_164_800,
                "price": "0.20",
                "size": "5",
                "side": "BUY",
                "asset": "yes-token",
                "conditionId": "0xcond",
                "transactionHash": "0xtx",
                "eventSlug": "highest-temperature-in-seoul-on-june-10-2026",
                "proxyWallet": "not-persisted",
                "name": "not-persisted",
            }
        ]
    )

    first = await collect_market_history(
        session_factory,
        fake_client,  # type: ignore[arg-type]
        settings,
        days=30,
    )
    second = await collect_market_history(
        session_factory,
        fake_client,  # type: ignore[arg-type]
        settings,
        days=30,
    )

    async with session_factory() as session:
        points = (await session.execute(select(MarketTradeHistoryPoint))).scalars().all()

    assert first.history_points == 0
    assert first.trade_history_points == 1
    assert second.trade_history_points == 0
    assert first.trade_source_status == {"accepted": 1}
    assert len(points) == 1
    assert points[0].token_id == "yes-token"
    assert not hasattr(points[0], "proxyWallet")


async def test_market_history_backfill_rejects_unfiltered_trade_response(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(cities=["seoul"])
    fake_client = FakeTradeHistoryClient(
        [
            {
                "timestamp": 1_781_164_800,
                "price": "0.20",
                "size": "5",
                "asset": "global-token",
                "conditionId": "0xglobal",
            }
        ]
    )

    stats = await collect_market_history(
        session_factory,
        fake_client,  # type: ignore[arg-type]
        settings,
        days=30,
    )

    async with session_factory() as session:
        points = (await session.execute(select(MarketTradeHistoryPoint))).scalars().all()

    assert stats.trade_history_points == 0
    assert stats.rejected_trade_sources == 1
    assert stats.trade_source_status == {"rejected_unfiltered_response": 1}
    assert points == []


async def test_market_history_backfill_chunked_resume_skips_completed_windows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(cities=["seoul"])
    fake_client = FakePolymarketClient()

    first = await collect_market_history_chunked(
        session_factory,
        fake_client,  # type: ignore[arg-type]
        settings,
        days=30,
        from_date=date(2026, 6, 1),
        to_date=date(2026, 6, 14),
        chunk_days=7,
        resume=True,
    )
    second = await collect_market_history_chunked(
        session_factory,
        fake_client,  # type: ignore[arg-type]
        settings,
        days=30,
        from_date=date(2026, 6, 1),
        to_date=date(2026, 6, 14),
        chunk_days=7,
        resume=True,
    )

    async with session_factory() as session:
        runs = (await session.execute(select(HistoryBackfillRun))).scalars().all()
        price_points = (await session.execute(select(MarketPriceHistoryPoint))).scalars().all()

    assert first.windows_total == 2
    assert first.windows_completed == 2
    assert first.windows_skipped == 0
    assert second.windows_skipped == 2
    assert len(runs) == 2
    assert all(run.status == "COMPLETED" for run in runs)
    assert len(price_points) == 2


async def test_open_meteo_ensemble_parser_extracts_member_daily_maxima() -> None:
    client = OpenMeteoClient(
        FakeWeatherHttp(
            {
                "hourly": {
                    "time": [
                        "2026-06-10T00:00",
                        "2026-06-10T01:00",
                        "2026-06-10T02:00",
                    ],
                    "temperature_2m": [20.0, 25.0, 24.0],
                    "temperature_2m_member01": [21.0, 26.0, 22.0],
                    "temperature_2m_member02": [None, None, None],
                }
            }
        )
    )

    result = await client.ensemble_daily_tmax(1.0, 2.0, "gfs", 1)

    assert result == {date(2026, 6, 10): [25.0, 26.0]}


async def test_collect_forecasts_writes_ensemble_snapshots_and_members(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 9, tzinfo=UTC)
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

    snapshots = await collect_forecasts(
        session_factory,
        FakeOpenMeteoClient(),  # type: ignore[arg-type]
        Settings(
            cities=["seoul"],
            deterministic_models=["gfs"],
            ensemble_models=["gfs"],
            forecast_days=1,
        ),
    )

    async with session_factory() as session:
        forecast_rows = (await session.execute(select(ForecastSnapshot))).scalars().all()
        member_rows = (await session.execute(select(EnsembleMember))).scalars().all()

    ensemble_rows = [row for row in forecast_rows if row.source == "open_meteo_ensemble"]
    assert snapshots == 2
    assert len(ensemble_rows) == 1
    assert ensemble_rows[0].n_members == 3
    assert [row.tmax_c for row in member_rows] == [23.0, 24.0, 25.0]
