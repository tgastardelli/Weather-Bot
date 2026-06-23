"""Weather city discovery tests."""

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.weather_city_discovery import generate_weather_city_discovery_report
from app.config import Settings
from app.db.models import City, PaperFill, PaperOrder, Signal


class FakeWeatherClient:
    async def list_weather_events(
        self,
        *,
        active: bool = True,
        closed: bool = False,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        _ = page_size
        if active or not closed:
            return []
        return [
            {
                "id": "event-1",
                "slug": "highest-temperature-in-chicago-on-june-1-2026",
                "title": "Highest temperature in Chicago on June 1, 2026?",
                "endDate": "2026-06-02T12:00:00Z",
                "active": False,
                "closed": True,
                "markets": [
                    {
                        "id": "market-1",
                        "conditionId": "0x1",
                        "question": "Will it be 80°F?",
                        "groupItemTitle": "80°F",
                        "groupItemThreshold": 1,
                        "clobTokenIds": '["yes", "no"]',
                        "outcomePrices": '["0.50", "0.50"]',
                        "orderPriceMinTickSize": "0.001",
                        "orderMinSize": "5",
                        "closed": True,
                        "gameStartTime": "2026-06-01 15:00:00+00",
                        "description": (
                            "Source: https://www.wunderground.com/history/daily/"
                            "us/il/chicago/KMDW"
                        ),
                    }
                ],
            }
        ]


async def test_weather_city_discovery_registers_new_city_as_needs_review(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    row = await generate_weather_city_discovery_report(
        session_factory,
        Settings(),
        FakeWeatherClient(),  # type: ignore[arg-type]
        days=730,
    )

    async with session_factory() as session:
        city = await session.get(City, "chicago")
        signals = (await session.execute(select(func.count(Signal.id)))).scalar_one()
        orders = (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        fills = (await session.execute(select(func.count(PaperFill.id)))).scalar_one()

    assert city is not None
    assert city.needs_review is True
    assert city.station_code == "KMDW"
    assert signals == 0
    assert orders == 0
    assert fills == 0
    assert row.status == "DISCOVERED_NEW_CITIES"
    assert json.loads(row.summary_json)["new_cities_registered"] == 1
