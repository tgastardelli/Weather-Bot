"""Coleta de mercados Polymarket: discovery + snapshots de preço/book.

Idempotente: eventos/mercados são upsertados por id; snapshots são append-only.
Sessões curtas — as chamadas de rede acontecem FORA da transação (SQLite tem
1 writer; skill python-backend §4).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import BookSnapshot, City, Event, Market, MarketPriceSnapshot
from app.polymarket.client import PolymarketPublicClient, best_levels, book_top_json
from app.polymarket.normalize import NormalizedEvent, normalize_event
from app.polymarket.registry import extract_resolution_meta, station_info

logger = logging.getLogger(__name__)

BOOK_CONCURRENCY = 8


@dataclass
class CollectStats:
    events_upserted: int = 0
    markets_upserted: int = 0
    price_snapshots: int = 0
    errors: list[str] = field(default_factory=list)


async def _ensure_city(session: AsyncSession, event: NormalizedEvent, now: datetime) -> City:
    city = await session.get(City, event.city_slug)
    if city is not None:
        return city
    description = event.markets[0].description if event.markets else ""
    meta = extract_resolution_meta(description)
    info = station_info(meta.station_code)
    city = City(
        slug=event.city_slug,
        name=event.city_slug.replace("-", " ").title(),
        series_slug=f"{event.city_slug}-daily-weather",
        station_code=meta.station_code,
        station_name=None,
        latitude=info[0] if info else None,
        longitude=info[1] if info else None,
        timezone=info[2] if info else None,
        unit=meta.unit,
        resolution_source=meta.source,
        resolution_url=meta.url,
        rounding=meta.rounding,
        needs_review=meta.source == "unknown" or info is None,
        active=True,
        updated_at=now,
    )
    session.add(city)
    await session.flush()
    logger.info("nova cidade registrada: %s (station=%s, needs_review=%s)",
                city.slug, city.station_code, city.needs_review)
    return city


def _apply_event(row: Event, event: NormalizedEvent, now: datetime) -> None:
    row.slug = event.slug
    row.title = event.title
    row.city_slug = event.city_slug
    row.target_date = event.target_date
    row.end_date = event.end_date
    row.neg_risk_market_id = event.neg_risk_market_id
    row.active = event.active
    row.closed = event.closed
    row.volume = event.volume
    row.liquidity = event.liquidity
    row.updated_at = now


async def _upsert_event(
    session: AsyncSession, event: NormalizedEvent, now: datetime
) -> tuple[int, int]:
    await _ensure_city(session, event, now)
    row = await session.get(Event, event.id)
    if row is None:
        row = Event(id=event.id, first_seen_at=now)
        _apply_event(row, event, now)
        session.add(row)
    else:
        _apply_event(row, event, now)
    markets = 0
    for normalized in event.markets:
        market_row = await session.get(Market, normalized.id)
        if market_row is None:
            market_row = Market(id=normalized.id, event_id=event.id, updated_at=now)
            session.add(market_row)
        market_row.event_id = event.id
        market_row.condition_id = normalized.condition_id
        market_row.question = normalized.question
        market_row.group_item_title = normalized.group_item_title
        market_row.group_item_threshold = normalized.group_item_threshold
        market_row.bucket_kind = normalized.bucket.kind
        market_row.bucket_low = normalized.bucket.low
        market_row.bucket_high = normalized.bucket.high
        market_row.yes_token_id = normalized.yes_token_id
        market_row.no_token_id = normalized.no_token_id
        market_row.tick_size = normalized.tick_size
        market_row.min_order_size = normalized.min_order_size
        market_row.closed = normalized.closed
        market_row.updated_at = now
        markets += 1
    return 1, markets


async def collect_markets(
    session_factory: async_sessionmaker[AsyncSession],
    client: PolymarketPublicClient,
    settings: Settings,
) -> CollectStats:
    """Discovery de eventos 'highest temperature' + snapshot de preços/books."""
    stats = CollectStats()
    now = datetime.now(UTC)

    raw_events = await client.list_weather_events(active=True, closed=False)
    events = [e for e in (normalize_event(r) for r in raw_events) if e is not None]
    if settings.cities is not None:
        events = [e for e in events if e.city_slug in settings.cities]

    async with session_factory() as session, session.begin():
        for event in events:
            upserted_events, upserted_markets = await _upsert_event(session, event, now)
            stats.events_upserted += upserted_events
            stats.markets_upserted += upserted_markets

    # Snapshots de book fora da transação de upsert (rede fora de transação).
    semaphore = asyncio.Semaphore(BOOK_CONCURRENCY)

    async def fetch_book(market_id: str, token_id: str) -> tuple[str, str, dict[str, Any]] | None:
        async with semaphore:
            try:
                book = await client.get_book(token_id)
                return market_id, token_id, book
            except Exception as exc:
                stats.errors.append(f"book {token_id[:16]}…: {exc}")
                return None

    pairs = [(m.id, m.yes_token_id) for e in events for m in e.markets]
    books = await asyncio.gather(*(fetch_book(mid, tid) for mid, tid in pairs))

    snapshot_ts = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        for result in books:
            if result is None:
                continue
            market_id, token_id, book = result
            bid, ask = best_levels(book)
            mid = (bid[0] + ask[0]) / 2 if bid and ask else None
            session.add(
                MarketPriceSnapshot(
                    ts=snapshot_ts,
                    market_id=market_id,
                    best_bid=bid[0] if bid else None,
                    best_ask=ask[0] if ask else None,
                    mid=mid,
                    bid_size=bid[1] if bid else None,
                    ask_size=ask[1] if ask else None,
                )
            )
            bids_json, asks_json = book_top_json(book, settings.book_depth_levels)
            session.add(
                BookSnapshot(ts=snapshot_ts, token_id=token_id,
                             bids_json=bids_json, asks_json=asks_json)
            )
            stats.price_snapshots += 1

    logger.info(
        "markets collect: %d eventos, %d mercados, %d snapshots, %d erros",
        stats.events_upserted, stats.markets_upserted,
        stats.price_snapshots, len(stats.errors),
    )
    return stats


async def active_cities(session: AsyncSession, settings: Settings) -> list[City]:
    """Cidades ativas com coordenadas (prontas para coleta de previsão)."""
    query = select(City).where(City.active.is_(True), City.latitude.is_not(None))
    cities = (await session.execute(query)).scalars().all()
    if settings.cities is not None:
        cities = [c for c in cities if c.slug in settings.cities]
    return list(cities)
