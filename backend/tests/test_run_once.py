"""Manual collector entrypoint tests."""

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.run_once import build_parser, parse_cities, run_once
from app.config import Settings
from app.db.models import Event, Market, MarketPriceSnapshot


class FakePolymarketClient:
    async def list_weather_events(
        self, *, active: bool = True, closed: bool = False, page_size: int = 100
    ) -> list[dict[str, Any]]:
        return [
            self._event("nyc", "nyc-event", "nyc-market", "yes-nyc"),
            self._event("seoul", "seoul-event", "seoul-market", "yes-seoul"),
        ]

    async def get_book(self, token_id: str) -> dict[str, Any]:
        return {
            "asset_id": token_id,
            "bids": [{"price": "0.10", "size": "100"}],
            "asks": [{"price": "0.12", "size": "50"}],
        }

    async def get_event(self, event_id: str) -> dict[str, Any]:
        return {"id": event_id, "closed": False, "markets": []}

    @staticmethod
    def _event(city: str, event_id: str, market_id: str, yes_token: str) -> dict[str, Any]:
        return {
            "id": event_id,
            "slug": f"highest-temperature-in-{city}-on-june-10-2026",
            "title": f"Highest temperature in {city} on June 10, 2026?",
            "endDate": "2026-06-11T12:00:00Z",
            "active": True,
            "closed": False,
            "markets": [
                {
                    "id": market_id,
                    "conditionId": f"0x{market_id}",
                    "question": "Will it be 23°C or below?",
                    "groupItemTitle": "23°C or below",
                    "groupItemThreshold": "0",
                    "clobTokenIds": f'["{yes_token}","no-{yes_token}"]',
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


class FakeOpenMeteoClient:
    async def daily_tmax_forecast(
        self, lat: float, lon: float, models: list[str], days: int
    ) -> dict[str, list[tuple[date, float | None]]]:
        return {models[0]: [(date(2026, 6, 10), 24.0)]}

    async def ensemble_daily_tmax(
        self, lat: float, lon: float, model: str, days: int
    ) -> dict[date, list[float]]:
        return {date(2026, 6, 10): [23.0, 24.0, 25.0]}


class FakeMetarClient:
    async def recent(self, station: str, hours: int = 6) -> list[Any]:
        return []


def test_parser_accepts_markets_all_and_flags() -> None:
    parser = build_parser()

    markets = parser.parse_args(["markets", "--cities", "seoul,hong-kong,nyc", "--json"])
    all_job = parser.parse_args(["all", "--no-signals", "--high-reward-fast-lane"])

    assert markets.job == "markets"
    assert markets.json is True
    assert parse_cities(markets.cities) == ["seoul", "hong-kong", "nyc"]
    assert all_job.job == "all"
    assert all_job.no_signals is True
    assert all_job.high_reward_fast_lane is True
    assert parse_cities(None) is None


async def test_run_once_markets_writes_filtered_city(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    result = await run_once(
        "markets",
        settings=Settings(cities=["seoul"]),
        cities=["nyc"],
        include_signals=False,
        session_factory=session_factory,
        pm_client=FakePolymarketClient(),
    )

    async with session_factory() as session:
        events = (await session.execute(select(Event))).scalars().all()
        markets = (await session.execute(select(Market))).scalars().all()
        snapshots = (await session.execute(select(MarketPriceSnapshot))).scalars().all()

    assert result.to_jsonable() == {
        "job": "markets",
        "events_upserted": 1,
        "markets_upserted": 1,
        "price_snapshots": 1,
        "errors": [],
    }
    assert [event.city_slug for event in events] == ["nyc"]
    assert len(markets) == 1
    assert len(snapshots) == 1


async def test_run_once_all_reports_ensemble_member_count(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    result = await run_once(
        "all",
        settings=Settings(
            cities=["seoul"],
            deterministic_models=["gfs"],
            ensemble_models=["gfs"],
            forecast_days=1,
        ),
        include_signals=False,
        session_factory=session_factory,
        pm_client=FakePolymarketClient(),
        om_client=FakeOpenMeteoClient(),  # type: ignore[arg-type]
        metar_client=FakeMetarClient(),  # type: ignore[arg-type]
    )

    assert result.to_jsonable()["ensemble_members"] == 3
    assert result.forecast_snapshots == 2


async def test_run_once_high_reward_fast_lane_uses_operational_cities(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    result = await run_once(
        "markets",
        settings=Settings(cities=["seoul"], strategy_policy_mode="raw"),
        cities=["nyc"],
        include_signals=False,
        high_reward_fast_lane=True,
        session_factory=session_factory,
        pm_client=FakePolymarketClient(),
    )

    async with session_factory() as session:
        events = (await session.execute(select(Event))).scalars().all()

    assert result.events_upserted == 0
    assert events == []
