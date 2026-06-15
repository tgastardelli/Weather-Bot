"""Cliente publico Gamma + CLOB (somente leitura, sem credenciais).

O projeto padroniza o SDK oficial `polymarket-client`. Como ele ainda e beta,
este adapter preserva a interface usada pelos collectors e cai para REST
publico quando o SDK nao estiver instalado ou nao expuser um metodo necessario.
"""

import asyncio
import importlib
import inspect
import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx

try:  # pragma: no cover - exercitado apenas quando o SDK esta instalado
    _polymarket = importlib.import_module("polymarket")
except ImportError:  # pragma: no cover - fallback REST usado em testes
    _AsyncPublicClient: Any | None = None
else:
    _AsyncPublicClient = getattr(_polymarket, "AsyncPublicClient", None)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
DATA_BASE = "https://data-api.polymarket.com"
WEATHER_TAG_ID = 84

_RETRY_STATUS = {429, 500, 502, 503, 504}


def _object_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        dumped = to_dict()
        return dumped if isinstance(dumped, dict) else {}
    return {
        key: getattr(value, key)
        for key in ("bids", "asks", "mid", "history")
        if hasattr(value, key)
    }


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class PolymarketPublicClient:
    """GETs publicos com retry/backoff e SDK oficial quando disponivel."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        max_retries: int = 3,
        *,
        sdk_client: Any | None = None,
        use_sdk: bool = True,
    ) -> None:
        self._http = http
        self._max_retries = max_retries
        self._sdk = sdk_client
        if self._sdk is None and use_sdk and _AsyncPublicClient is not None:
            try:
                self._sdk = _AsyncPublicClient()
            except Exception:
                self._sdk = None

    async def aclose(self) -> None:
        if self._sdk is None:
            return
        close = getattr(self._sdk, "aclose", None) or getattr(self._sdk, "close", None)
        if callable(close):
            await _maybe_await(close())

    async def _sdk_call(self, method_name: str, *args: Any, **kwargs: Any) -> Any | None:
        if self._sdk is None:
            return None
        method: Callable[..., Any] | None = getattr(self._sdk, method_name, None)
        if method is None:
            return None
        try:
            return await _maybe_await(method(*args, **kwargs))
        except Exception:
            return None

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        delay = 1.0
        last_exc: Exception | None = None
        for _ in range(self._max_retries + 1):
            try:
                resp = await self._http.get(url, params=params)
                if resp.status_code in _RETRY_STATUS:
                    raise httpx.HTTPStatusError(
                        f"status {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status not in _RETRY_STATUS:
                    raise
                last_exc = exc
                await asyncio.sleep(delay)
                delay *= 2
        raise RuntimeError(f"GET {url} falhou apos retries") from last_exc

    async def list_weather_events(
        self, *, active: bool = True, closed: bool = False, page_size: int = 100
    ) -> list[dict[str, Any]]:
        """Eventos com a tag weather; pagina por offset ate esgotar."""
        events: list[dict[str, Any]] = []
        offset = 0
        while True:
            batch: list[dict[str, Any]] = await self._get_json(
                f"{GAMMA_BASE}/events",
                params={
                    "tag_id": WEATHER_TAG_ID,
                    "active": str(active).lower(),
                    "closed": str(closed).lower(),
                    "limit": page_size,
                    "offset": offset,
                },
            )
            events.extend(batch)
            if len(batch) < page_size:
                return events
            offset += page_size

    async def get_event(self, event_id: str) -> dict[str, Any]:
        return await self._get_json(f"{GAMMA_BASE}/events/{event_id}")  # type: ignore[no-any-return]

    async def get_book(self, token_id: str) -> dict[str, Any]:
        sdk_book = await self._sdk_call("get_order_book", token_id=token_id)
        if sdk_book is not None:
            return _object_to_dict(sdk_book)
        return await self._get_json(f"{CLOB_BASE}/book", params={"token_id": token_id})  # type: ignore[no-any-return]

    async def get_midpoint(self, token_id: str) -> Decimal | None:
        sdk_mid = await self._sdk_call("get_midpoint", token_id=token_id)
        if sdk_mid is not None:
            if isinstance(sdk_mid, Decimal):
                return sdk_mid
            if isinstance(sdk_mid, str | int | float):
                return Decimal(str(sdk_mid))
            data = _object_to_dict(sdk_mid)
            mid = data.get("mid")
            return Decimal(str(mid)) if mid is not None else None
        data = await self._get_json(f"{CLOB_BASE}/midpoint", params={"token_id": token_id})
        mid = data.get("mid") if isinstance(data, dict) else None
        return Decimal(str(mid)) if mid is not None else None

    async def get_prices_history(
        self, token_id: str, interval: str = "1d"
    ) -> list[dict[str, Any]]:
        sdk_history = await self._sdk_call(
            "get_price_history", token_id=token_id, interval=interval
        )
        if sdk_history is not None:
            if isinstance(sdk_history, list):
                return [
                    item if isinstance(item, dict) else _object_to_dict(item)
                    for item in sdk_history
                ]
            data = _object_to_dict(sdk_history)
            history = data.get("history", [])
            return list(history) if isinstance(history, list) else []
        data = await self._get_json(
            f"{CLOB_BASE}/prices-history", params={"market": token_id, "interval": interval}
        )
        history = data.get("history", []) if isinstance(data, dict) else []
        return list(history)

    async def get_public_trades(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        data = await self._get_json(f"{DATA_BASE}/trades", params=params)
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            for key in ("trades", "data", "results"):
                rows = data.get(key)
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
        return []


def best_levels(book: dict[str, Any]) -> tuple[
    tuple[Decimal, Decimal] | None, tuple[Decimal, Decimal] | None
]:
    """Extrai (best_bid, best_ask) como (price, size) a partir do payload /book."""

    def parse(levels: list[dict[str, Any]]) -> list[tuple[Decimal, Decimal]]:
        return [(Decimal(str(level["price"])), Decimal(str(level["size"]))) for level in levels]

    bids = parse(book.get("bids", []) or [])
    asks = parse(book.get("asks", []) or [])
    best_bid = max(bids, key=lambda level: level[0], default=None)
    best_ask = min(asks, key=lambda level: level[0], default=None)
    return best_bid, best_ask


def book_top_json(book: dict[str, Any], depth: int) -> tuple[str, str]:
    """Serializa o topo do book ([[price, size], ...]) para auditoria/replay."""

    def top(levels: list[dict[str, Any]], reverse: bool) -> list[list[str]]:
        parsed = sorted(
            ((str(level["price"]), str(level["size"])) for level in levels),
            key=lambda level: Decimal(level[0]),
            reverse=reverse,
        )
        return [list(level) for level in parsed[:depth]]

    bids = top(book.get("bids", []) or [], reverse=True)
    asks = top(book.get("asks", []) or [], reverse=False)
    return json.dumps(bids), json.dumps(asks)
