"""Detecção de resolução: marca buckets vencedores e fecha eventos."""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Event, Market, Resolution
from app.polymarket.client import PolymarketPublicClient

logger = logging.getLogger(__name__)


def _winner_from_outcome_prices(market_raw: dict[str, Any]) -> bool | None:
    """outcomePrices '1'/'0' (string JSON) após resolução; None se indefinido."""
    raw = market_raw.get("outcomePrices")
    try:
        prices = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return None
    if not isinstance(prices, list) or len(prices) != 2:
        return None
    yes = str(prices[0])
    if yes == "1":
        return True
    if yes == "0":
        return False
    return None


async def collect_resolutions(
    session_factory: async_sessionmaker[AsyncSession],
    client: PolymarketPublicClient,
) -> int:
    """Para eventos abertos já vencidos, busca o estado na Gamma e grava o vencedor."""
    now = datetime.now(UTC)
    resolved = 0

    async with session_factory() as session:
        stale_events = (
            (
                await session.execute(
                    select(Event).where(
                        Event.closed.is_(False),
                        Event.end_date.is_not(None),
                        Event.end_date < now + timedelta(hours=1),
                    )
                )
            )
            .scalars()
            .all()
        )
        event_ids = [e.id for e in stale_events]

    for event_id in event_ids:
        try:
            raw = await client.get_event(event_id)
        except Exception as exc:
            logger.warning("resolution fetch %s falhou: %s", event_id, exc)
            continue
        if not raw.get("closed", False):
            continue

        async with session_factory() as session, session.begin():
            event_row = await session.get(Event, event_id)
            if event_row is None:
                continue
            markets = (
                (await session.execute(select(Market).where(Market.event_id == event_id)))
                .scalars()
                .all()
            )
            by_id = {m.id: m for m in markets}
            winner_market: Market | None = None
            for market_raw in raw.get("markets") or []:
                market_row = by_id.get(str(market_raw.get("id")))
                if market_row is None:
                    continue
                winner = _winner_from_outcome_prices(market_raw)
                market_row.closed = True
                market_row.winner = winner
                market_row.resolved_at = now
                if winner:
                    winner_market = market_row
            event_row.closed = True
            event_row.active = False
            event_row.updated_at = now
            if await session.get(Resolution, event_id) is None:
                session.add(
                    Resolution(
                        event_id=event_id,
                        winner_market_id=winner_market.id if winner_market else None,
                        winner_bucket=(
                            winner_market.group_item_title if winner_market else None
                        ),
                        resolved_at=now,
                    )
                )
            resolved += 1
            logger.info(
                "evento resolvido: %s vencedor=%s",
                event_row.slug,
                winner_market.group_item_title if winner_market else "?",
            )
    return resolved
