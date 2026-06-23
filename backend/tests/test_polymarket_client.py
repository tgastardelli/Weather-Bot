import httpx
import pytest

from app.polymarket.client import PolymarketPublicClient


class FakeGammaHttp:
    def __init__(self, status_by_offset: dict[int, int] | None = None) -> None:
        self.status_by_offset = status_by_offset or {}
        self.offsets: list[int] = []

    async def get(
        self, url: str, params: dict[str, object] | None = None
    ) -> httpx.Response:
        offset = int((params or {}).get("offset", 0))
        limit = int((params or {}).get("limit", 100))
        self.offsets.append(offset)
        request = httpx.Request("GET", url)
        status = self.status_by_offset.get(offset, 200)
        if status != 200:
            return httpx.Response(status, request=request, json={"error": "offset rejected"})
        return httpx.Response(
            200,
            request=request,
            json=[{"id": f"event-{offset}-{idx}"} for idx in range(limit)],
        )


@pytest.mark.asyncio
async def test_list_weather_events_treats_high_offset_422_as_end() -> None:
    http = FakeGammaHttp(status_by_offset={200: 422})
    client = PolymarketPublicClient(http, use_sdk=False)  # type: ignore[arg-type]

    events = await client.list_weather_events(active=False, closed=True, page_size=100)

    assert len(events) == 200
    assert http.offsets == [0, 100, 200]


@pytest.mark.asyncio
async def test_list_weather_events_raises_first_page_422() -> None:
    http = FakeGammaHttp(status_by_offset={0: 422})
    client = PolymarketPublicClient(http, use_sdk=False)  # type: ignore[arg-type]

    with pytest.raises(httpx.HTTPStatusError):
        await client.list_weather_events(active=False, closed=True, page_size=100)

    assert http.offsets == [0]
