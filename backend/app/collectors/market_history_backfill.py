"""Backfill de eventos fechados e historico de precos da Polymarket.

O historico de precos vem de CLOB prices-history e representa last/traded price
agregado pelo intervalo solicitado. Ele nao e book, best ask nem preco
executavel com profundidade.
"""

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.markets import _upsert_event
from app.collectors.resolutions import _winner_from_outcome_prices
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    Event,
    HistoryBackfillRun,
    Market,
    MarketPriceHistoryPoint,
    MarketTradeHistoryPoint,
    Resolution,
)
from app.db.session import create_engine, create_session_factory
from app.polymarket.client import PolymarketPublicClient
from app.polymarket.normalize import NormalizedEvent, normalize_event

logger = logging.getLogger(__name__)

HISTORY_CONCURRENCY = 8
DEFAULT_INTERVAL = "1d"
HISTORY_SOURCE = "clob_prices_history"
TRADE_HISTORY_SOURCE = "data_api_trades"
TradeProbeStatus = Literal[
    "accepted",
    "empty",
    "rejected_unfiltered_response",
    "invalid_payload",
]


@dataclass
class MarketHistoryBackfillStats:
    window_start: date | None = None
    window_end: date | None = None
    windows_total: int = 0
    windows_completed: int = 0
    windows_skipped: int = 0
    events_seen: int = 0
    events_upserted: int = 0
    markets_upserted: int = 0
    resolved_events: int = 0
    history_points: int = 0
    trade_history_points: int = 0
    rejected_trade_sources: int = 0
    trade_source_status: dict[str, int] | None = None
    errors: list[str] | None = None
    window_runs: list[dict[str, object]] | None = None

    def as_jsonable(self) -> dict[str, object]:
        data = asdict(self)
        data["window_start"] = self.window_start.isoformat() if self.window_start else None
        data["window_end"] = self.window_end.isoformat() if self.window_end else None
        data["errors"] = self.errors or []
        data["trade_source_status"] = self.trade_source_status or {}
        data["window_runs"] = self.window_runs or []
        data["price_source_counts"] = {
            HISTORY_SOURCE: self.history_points,
            TRADE_HISTORY_SOURCE: self.trade_history_points,
            "rejected": self.rejected_trade_sources,
        }
        return data


@dataclass(frozen=True)
class ParsedHistoryPoint:
    ts: datetime
    price: Decimal


@dataclass(frozen=True)
class ParsedTradePoint:
    ts: datetime
    price: Decimal
    size: Decimal
    side: str | None
    token_id: str
    condition_id: str
    transaction_hash: str | None


@dataclass(frozen=True)
class TradeProbeResult:
    status: TradeProbeStatus
    param_key: str | None
    raw_count: int
    points: list[ParsedTradePoint]
    reason: str | None = None

    def as_jsonable(self) -> dict[str, object]:
        return {
            "param_key": self.param_key,
            "points": len(self.points),
            "raw_count": self.raw_count,
            "reason": self.reason,
            "status": self.status,
        }


def parse_cities(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    values = [part.strip() for part in raw.split(",") if part.strip()]
    return values or None


def parse_date_arg(raw: str | None) -> date | None:
    if raw is None:
        return None
    return date.fromisoformat(raw)


def _parse_timestamp(raw: object) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, int | float):
        value = float(raw)
    elif isinstance(raw, str) and raw.strip().isdigit():
        value = float(raw.strip())
    elif isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    else:
        return None

    if value > 10_000_000_000:
        value /= 1000
    return datetime.fromtimestamp(value, UTC)


def _first_value(row: dict[str, Any], keys: tuple[str, ...]) -> object:
    for key in keys:
        if key in row:
            return row[key]
    return None


def parse_price_history_points(raw_history: list[dict[str, Any]]) -> list[ParsedHistoryPoint]:
    """Parse robusto do payload CLOB prices-history para pontos UTC + Decimal."""
    points: list[ParsedHistoryPoint] = []
    for row in raw_history:
        ts = _parse_timestamp(_first_value(row, ("t", "timestamp", "time", "ts")))
        price_raw = _first_value(row, ("p", "price", "value"))
        if ts is None or price_raw is None:
            continue
        try:
            price = Decimal(str(price_raw))
        except InvalidOperation:
            continue
        if Decimal(0) < price < Decimal(1):
            points.append(ParsedHistoryPoint(ts=ts, price=price))
    return sorted(points, key=lambda point: point.ts)


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_trade_side(raw: object) -> str | None:
    text = _as_text(raw)
    if text is None:
        return None
    side = text.upper()
    return side if side in {"BUY", "SELL"} else side[:8]


def _trade_matches_expected(
    row: dict[str, Any],
    *,
    token_id: str,
    condition_id: str,
    event_slug: str,
) -> bool:
    asset = _as_text(
        _first_value(row, ("asset", "asset_id", "token_id", "tokenId", "clobTokenId"))
    )
    row_condition = _as_text(_first_value(row, ("conditionId", "condition_id")))
    row_event_slug = _as_text(_first_value(row, ("eventSlug", "event_slug")))

    has_market_field = asset is not None or row_condition is not None
    if not has_market_field:
        return False
    if asset is not None and asset != token_id:
        return row_condition is not None and row_condition.lower() == condition_id.lower()
    if row_condition is not None and row_condition.lower() != condition_id.lower():
        return False
    return not (row_event_slug is not None and row_event_slug != event_slug)


def parse_trade_history_points(
    raw_trades: list[dict[str, Any]],
    *,
    token_id: str,
    condition_id: str,
    event_slug: str,
) -> TradeProbeResult:
    if not raw_trades:
        return TradeProbeResult("empty", None, 0, [])

    if not all(
        _trade_matches_expected(
            row,
            token_id=token_id,
            condition_id=condition_id,
            event_slug=event_slug,
        )
        for row in raw_trades
    ):
        return TradeProbeResult(
            "rejected_unfiltered_response",
            None,
            len(raw_trades),
            [],
            "response_contains_trade_outside_requested_market",
        )

    points: list[ParsedTradePoint] = []
    for row in raw_trades:
        asset = _as_text(
            _first_value(row, ("asset", "asset_id", "token_id", "tokenId", "clobTokenId"))
        )
        if asset != token_id:
            continue
        ts = _parse_timestamp(_first_value(row, ("timestamp", "t", "time", "ts")))
        price_raw = _first_value(row, ("price", "p"))
        size_raw = _first_value(row, ("size", "amount", "shares"))
        if ts is None or price_raw is None or size_raw is None:
            continue
        try:
            price = Decimal(str(price_raw))
            size = Decimal(str(size_raw))
        except InvalidOperation:
            continue
        if not (Decimal(0) < price < Decimal(1)) or size <= 0:
            continue
        points.append(
            ParsedTradePoint(
                ts=ts,
                price=price,
                size=size,
                side=_parse_trade_side(row.get("side")),
                token_id=token_id,
                condition_id=condition_id,
                transaction_hash=_as_text(
                    _first_value(row, ("transactionHash", "transaction_hash", "txHash"))
                ),
            )
        )

    if not points:
        return TradeProbeResult("invalid_payload", None, len(raw_trades), [])
    return TradeProbeResult("accepted", None, len(raw_trades), sorted(points, key=lambda p: p.ts))


async def _apply_resolutions(
    session: AsyncSession,
    raw_event: dict[str, Any],
    event: NormalizedEvent,
    now: datetime,
) -> int:
    event_row = await session.get(Event, event.id)
    if event_row is None:
        return 0

    markets = (
        (await session.execute(select(Market).where(Market.event_id == event.id))).scalars().all()
    )
    by_id = {market.id: market for market in markets}
    winner_market: Market | None = None
    for market_raw in raw_event.get("markets") or []:
        market = by_id.get(str(market_raw.get("id")))
        if market is None:
            continue
        winner = _winner_from_outcome_prices(market_raw)
        market.closed = True
        market.winner = winner
        market.resolved_at = now
        if winner:
            winner_market = market

    event_row.closed = True
    event_row.active = False
    event_row.updated_at = now
    if await session.get(Resolution, event.id) is None:
        session.add(
            Resolution(
                event_id=event.id,
                winner_market_id=winner_market.id if winner_market else None,
                winner_bucket=winner_market.group_item_title if winner_market else None,
                resolved_at=now,
            )
        )
        return 1
    return 0


async def _insert_history_points(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    market_id: str,
    token_id: str,
    interval: str,
    points: list[ParsedHistoryPoint],
    start_dt: datetime,
    end_dt: datetime | None = None,
) -> int:
    written = 0
    async with session_factory() as session, session.begin():
        for point in points:
            if point.ts < start_dt:
                continue
            if end_dt is not None and point.ts >= end_dt:
                continue
            stmt = (
                sqlite_insert(MarketPriceHistoryPoint)
                .values(
                    ts=point.ts,
                    market_id=market_id,
                    token_id=token_id,
                    price=point.price,
                    interval=interval,
                    source=HISTORY_SOURCE,
                )
                .on_conflict_do_nothing(index_elements=["token_id", "interval", "ts"])
            )
            result = await session.execute(stmt)
            written += int(getattr(result, "rowcount", 0) or 0)
    return written


async def _insert_trade_points(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    market_id: str,
    points: list[ParsedTradePoint],
    start_dt: datetime,
    end_dt: datetime | None = None,
) -> int:
    written = 0
    async with session_factory() as session, session.begin():
        for point in points:
            if point.ts < start_dt:
                continue
            if end_dt is not None and point.ts >= end_dt:
                continue
            stmt = (
                sqlite_insert(MarketTradeHistoryPoint)
                .values(
                    ts=point.ts,
                    market_id=market_id,
                    token_id=point.token_id,
                    condition_id=point.condition_id,
                    price=point.price,
                    size=point.size,
                    side=point.side,
                    transaction_hash=point.transaction_hash,
                    source=TRADE_HISTORY_SOURCE,
                )
                .on_conflict_do_nothing(
                    index_elements=["token_id", "ts", "price", "size", "side"]
                )
            )
            result = await session.execute(stmt)
            written += int(getattr(result, "rowcount", 0) or 0)
    return written


def _trade_query_candidates(
    *,
    token_id: str,
    condition_id: str,
    market_id: str,
    event_slug: str,
    limit: int,
) -> list[tuple[str, dict[str, object]]]:
    return [
        ("market_condition", {"market": condition_id, "limit": limit}),
        ("asset", {"asset": token_id, "limit": limit}),
        ("asset_id", {"asset_id": token_id, "limit": limit}),
        ("token_id", {"token_id": token_id, "limit": limit}),
        ("conditionId", {"conditionId": condition_id, "limit": limit}),
        ("condition_id", {"condition_id": condition_id, "limit": limit}),
        ("market_id", {"market": market_id, "limit": limit}),
        ("eventSlug", {"eventSlug": event_slug, "limit": limit}),
    ]


async def _probe_trade_history(
    client: PolymarketPublicClient,
    *,
    market_id: str,
    token_id: str,
    condition_id: str,
    event_slug: str,
    limit: int = 500,
) -> TradeProbeResult:
    empty_count = 0
    rejected_count = 0
    invalid_count = 0
    for param_key, params in _trade_query_candidates(
        token_id=token_id,
        condition_id=condition_id,
        market_id=market_id,
        event_slug=event_slug,
        limit=limit,
    ):
        raw_trades = await client.get_public_trades(params)
        result = parse_trade_history_points(
            raw_trades,
            token_id=token_id,
            condition_id=condition_id,
            event_slug=event_slug,
        )
        result = TradeProbeResult(
            status=result.status,
            param_key=param_key,
            raw_count=result.raw_count,
            points=result.points,
            reason=result.reason,
        )
        if result.status == "accepted":
            return result
        if param_key == "market_condition" and result.status == "empty":
            return result
        if result.status == "empty":
            empty_count += 1
        elif result.status == "rejected_unfiltered_response":
            rejected_count += 1
        else:
            invalid_count += 1

    if rejected_count:
        return TradeProbeResult(
            "rejected_unfiltered_response",
            None,
            rejected_count,
            [],
            "all_trade_filters_rejected_or_unfiltered",
        )
    if invalid_count:
        return TradeProbeResult("invalid_payload", None, invalid_count, [])
    return TradeProbeResult("empty", None, empty_count, [])


async def collect_market_history(
    session_factory: async_sessionmaker[AsyncSession],
    client: PolymarketPublicClient,
    settings: Settings,
    *,
    days: int,
    start_date: date | None = None,
    end_date: date | None = None,
    interval: str = DEFAULT_INTERVAL,
    probe_trades: bool = False,
    concurrency: int = HISTORY_CONCURRENCY,
    trade_limit: int = 500,
) -> MarketHistoryBackfillStats:
    stats = MarketHistoryBackfillStats(errors=[], trade_source_status={})
    now = datetime.now(UTC)
    start = start_date or (now.date() - timedelta(days=days))
    end = end_date or now.date()
    stats.window_start = start
    stats.window_end = end
    start_dt = datetime.combine(start, time.min, tzinfo=UTC)
    end_dt = datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
    selected = set(settings.cities or [])

    raw_events = await client.list_weather_events(active=False, closed=True)
    normalized: list[tuple[dict[str, Any], NormalizedEvent]] = []
    for raw in raw_events:
        try:
            event = normalize_event(raw)
        except (KeyError, ValueError) as exc:
            assert stats.errors is not None
            stats.errors.append(f"event {raw.get('id', '?')}: {exc}")
            continue
        if event is None:
            continue
        if selected and event.city_slug not in selected:
            continue
        if event.target_date < start or event.target_date > end:
            continue
        normalized.append((raw, event))

    stats.events_seen = len(normalized)

    async with session_factory() as session, session.begin():
        for raw, event in normalized:
            event_count, market_count = await _upsert_event(session, event, now)
            stats.events_upserted += event_count
            stats.markets_upserted += market_count
            stats.resolved_events += await _apply_resolutions(session, raw, event, now)

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def fetch_history(
        market_id: str,
        token_id: str,
        condition_id: str,
        event_slug: str,
    ) -> tuple[str, str, TradeProbeResult | None, list[dict[str, Any]]]:
        async with semaphore:
            try:
                history = await client.get_prices_history(token_id, interval=interval)
            except Exception as exc:  # pragma: no cover - network failure path
                assert stats.errors is not None
                stats.errors.append(f"prices-history {token_id[:16]}...: {exc}")
                history = []
            trade_probe: TradeProbeResult | None = None
            if probe_trades or not parse_price_history_points(history):
                try:
                    trade_probe = await _probe_trade_history(
                        client,
                        market_id=market_id,
                        token_id=token_id,
                        condition_id=condition_id,
                        event_slug=event_slug,
                        limit=trade_limit,
                    )
                except Exception as exc:  # pragma: no cover - network failure path
                    assert stats.errors is not None
                    stats.errors.append(f"trades {token_id[:16]}...: {exc}")
            return market_id, token_id, trade_probe, history

    market_pairs = [
        (market.id, market.yes_token_id, market.condition_id, event.slug)
        for _, event in normalized
        for market in event.markets
    ]
    histories = await asyncio.gather(
        *(
            fetch_history(market_id, token_id, condition_id, event_slug)
            for market_id, token_id, condition_id, event_slug in market_pairs
        )
    )
    for market_id, token_id, trade_probe, raw_history in histories:
        points = parse_price_history_points(raw_history)
        if not probe_trades:
            stats.history_points += await _insert_history_points(
                session_factory,
                market_id=market_id,
                token_id=token_id,
                interval=interval,
                points=points,
                start_dt=start_dt,
                end_dt=end_dt,
            )
        if trade_probe is None:
            continue
        assert stats.trade_source_status is not None
        stats.trade_source_status[trade_probe.status] = (
            stats.trade_source_status.get(trade_probe.status, 0) + 1
        )
        if trade_probe.status == "accepted":
            if not probe_trades:
                stats.trade_history_points += await _insert_trade_points(
                    session_factory,
                    market_id=market_id,
                    points=trade_probe.points,
                    start_dt=start_dt,
                    end_dt=end_dt,
                )
            else:
                stats.trade_history_points += len(trade_probe.points)
        elif trade_probe.status == "rejected_unfiltered_response":
            stats.rejected_trade_sources += 1

    logger.info(
        "market history backfill: events=%d markets=%d prices=%d trades=%d errors=%d",
        stats.events_seen,
        stats.markets_upserted,
        stats.history_points,
        stats.trade_history_points,
        len(stats.errors or []),
    )
    return stats


def _date_chunks(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive")
    chunks: list[tuple[date, date]] = []
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def _cities_key(cities: list[str] | None) -> str:
    return json.dumps(sorted(cities or []), sort_keys=True)


async def _completed_window_exists(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    window_start: date,
    window_end: date,
    cities_json: str,
    interval: str,
    probe_trades: bool,
) -> bool:
    async with session_factory() as session:
        found = (
            await session.execute(
                select(HistoryBackfillRun.id)
                .where(
                    HistoryBackfillRun.window_start == window_start,
                    HistoryBackfillRun.window_end == window_end,
                    HistoryBackfillRun.cities_json == cities_json,
                    HistoryBackfillRun.interval == interval,
                    HistoryBackfillRun.probe_trades == probe_trades,
                    HistoryBackfillRun.status == "COMPLETED",
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    return found is not None


async def _start_history_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    window_start: date,
    window_end: date,
    cities_json: str,
    interval: str,
    probe_trades: bool,
    params: dict[str, object],
) -> int:
    async with session_factory() as session, session.begin():
        row = HistoryBackfillRun(
            run_at=datetime.now(UTC),
            completed_at=None,
            status="RUNNING",
            window_start=window_start,
            window_end=window_end,
            cities_json=cities_json,
            interval=interval,
            probe_trades=probe_trades,
            params_json=json.dumps(params, sort_keys=True),
        )
        session.add(row)
        await session.flush()
        return row.id


async def _finish_history_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: int,
    *,
    status: str,
    stats: MarketHistoryBackfillStats,
) -> dict[str, object]:
    async with session_factory() as session, session.begin():
        row = await session.get(HistoryBackfillRun, run_id)
        if row is None:
            return {}
        row.completed_at = datetime.now(UTC)
        row.status = status
        row.events_seen = stats.events_seen
        row.markets_upserted = stats.markets_upserted
        row.history_points = stats.history_points
        row.trade_history_points = stats.trade_history_points
        row.rejected_trade_sources = stats.rejected_trade_sources
        row.source_status_json = json.dumps(stats.trade_source_status or {}, sort_keys=True)
        row.errors_json = json.dumps(stats.errors or [], sort_keys=True)
        return {
            "id": row.id,
            "status": row.status,
            "window_start": row.window_start.isoformat(),
            "window_end": row.window_end.isoformat(),
            "events_seen": row.events_seen,
            "markets_upserted": row.markets_upserted,
            "history_points": row.history_points,
            "trade_history_points": row.trade_history_points,
            "rejected_trade_sources": row.rejected_trade_sources,
        }


def _merge_stats(total: MarketHistoryBackfillStats, chunk: MarketHistoryBackfillStats) -> None:
    total.events_seen += chunk.events_seen
    total.events_upserted += chunk.events_upserted
    total.markets_upserted += chunk.markets_upserted
    total.resolved_events += chunk.resolved_events
    total.history_points += chunk.history_points
    total.trade_history_points += chunk.trade_history_points
    total.rejected_trade_sources += chunk.rejected_trade_sources
    if total.errors is None:
        total.errors = []
    total.errors.extend(chunk.errors or [])
    if total.trade_source_status is None:
        total.trade_source_status = {}
    for key, value in (chunk.trade_source_status or {}).items():
        total.trade_source_status[key] = total.trade_source_status.get(key, 0) + value


async def collect_market_history_chunked(
    session_factory: async_sessionmaker[AsyncSession],
    client: PolymarketPublicClient,
    settings: Settings,
    *,
    days: int,
    from_date: date | None = None,
    to_date: date | None = None,
    chunk_days: int = 30,
    resume: bool = False,
    interval: str = DEFAULT_INTERVAL,
    probe_trades: bool = False,
    concurrency: int = HISTORY_CONCURRENCY,
    trade_limit: int = 500,
) -> MarketHistoryBackfillStats:
    now = datetime.now(UTC).date()
    window_end = to_date or now
    window_start = from_date or (window_end - timedelta(days=days))
    chunks = _date_chunks(window_start, window_end, chunk_days)
    total = MarketHistoryBackfillStats(
        window_start=window_start,
        window_end=window_end,
        windows_total=len(chunks),
        errors=[],
        trade_source_status={},
        window_runs=[],
    )
    cities_json = _cities_key(settings.cities)
    for chunk_start, chunk_end in chunks:
        if resume and await _completed_window_exists(
            session_factory,
            window_start=chunk_start,
            window_end=chunk_end,
            cities_json=cities_json,
            interval=interval,
            probe_trades=probe_trades,
        ):
            total.windows_skipped += 1
            assert total.window_runs is not None
            total.window_runs.append(
                {
                    "status": "SKIPPED",
                    "window_start": chunk_start.isoformat(),
                    "window_end": chunk_end.isoformat(),
                }
            )
            continue

        run_id = await _start_history_run(
            session_factory,
            window_start=chunk_start,
            window_end=chunk_end,
            cities_json=cities_json,
            interval=interval,
            probe_trades=probe_trades,
            params={
                "chunk_days": chunk_days,
                "concurrency": concurrency,
                "days": days,
                "resume": resume,
                "trade_limit": trade_limit,
            },
        )
        try:
            chunk_stats = await collect_market_history(
                session_factory,
                client,
                settings,
                days=days,
                start_date=chunk_start,
                end_date=chunk_end,
                interval=interval,
                probe_trades=probe_trades,
                concurrency=concurrency,
                trade_limit=trade_limit,
            )
        except Exception as exc:
            failed = MarketHistoryBackfillStats(
                window_start=chunk_start,
                window_end=chunk_end,
                errors=[str(exc)],
                trade_source_status={},
            )
            run_payload = await _finish_history_run(
                session_factory, run_id, status="FAILED", stats=failed
            )
            assert total.window_runs is not None
            total.window_runs.append(run_payload)
            assert total.errors is not None
            total.errors.append(f"{chunk_start.isoformat()}..{chunk_end.isoformat()}: {exc}")
            continue

        _merge_stats(total, chunk_stats)
        total.windows_completed += 1
        run_payload = await _finish_history_run(
            session_factory, run_id, status="COMPLETED", stats=chunk_stats
        )
        assert total.window_runs is not None
        total.window_runs.append(run_payload)

    return total


async def run(
    settings: Settings,
    *,
    days: int,
    from_date: date | None = None,
    to_date: date | None = None,
    chunk_days: int | None = None,
    resume: bool = False,
    interval: str,
    probe_trades: bool,
    concurrency: int = HISTORY_CONCURRENCY,
    trade_limit: int = 500,
) -> MarketHistoryBackfillStats:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = PolymarketPublicClient(http)
            try:
                if chunk_days is None:
                    return await collect_market_history(
                        session_factory,
                        client,
                        settings,
                        days=days,
                        start_date=from_date,
                        end_date=to_date,
                        interval=interval,
                        probe_trades=probe_trades,
                        concurrency=concurrency,
                        trade_limit=trade_limit,
                    )
                return await collect_market_history_chunked(
                    session_factory,
                    client,
                    settings,
                    days=days,
                    from_date=from_date,
                    to_date=to_date,
                    chunk_days=chunk_days,
                    resume=resume,
                    interval=interval,
                    probe_trades=probe_trades,
                    concurrency=concurrency,
                    trade_limit=trade_limit,
                )
            finally:
                await client.aclose()
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill historical Polymarket price points for resolved weather markets."
    )
    parser.add_argument("--cities", help="Comma-separated city slugs.")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--from-date", help="Inclusive YYYY-MM-DD window start.")
    parser.add_argument("--to-date", help="Inclusive YYYY-MM-DD window end.")
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=None,
        help="Process history in persisted date windows. Recommended: 30.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip completed persisted windows for the same cities/interval/probe mode.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=HISTORY_CONCURRENCY,
        help="Concurrent market-history requests per window.",
    )
    parser.add_argument(
        "--trade-limit",
        type=int,
        default=500,
        help="Data API trade limit per market probe.",
    )
    parser.add_argument("--interval", default=DEFAULT_INTERVAL)
    parser.add_argument(
        "--probe-trades",
        action="store_true",
        help="Probe Data API trade filters without persisting trade points.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.json else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = get_settings()
    cities = parse_cities(args.cities)
    if cities is not None:
        settings = settings.model_copy(update={"cities": cities})
    stats = asyncio.run(
        run(
            settings,
            days=args.days,
            from_date=parse_date_arg(args.from_date),
            to_date=parse_date_arg(args.to_date),
            chunk_days=args.chunk_days,
            resume=args.resume,
            interval=args.interval,
            probe_trades=args.probe_trades,
            concurrency=args.concurrency,
            trade_limit=args.trade_limit,
        )
    )
    if args.json:
        print(json.dumps(stats.as_jsonable(), sort_keys=True))


if __name__ == "__main__":
    main()
