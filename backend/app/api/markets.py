"""Market list endpoints for the dashboard."""

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, SettingsDep
from app.api.schemas import BucketOut, EventOut
from app.config import Settings
from app.db.models import City, Event, Market, MarketPriceSnapshot
from app.strategy.edge import net_edge
from app.strategy.engine import event_model_probs

router = APIRouter(prefix="/api/markets", tags=["markets"])


async def _latest_prices(
    session: AsyncSession, market_ids: list[str]
) -> dict[str, MarketPriceSnapshot]:
    if not market_ids:
        return {}
    snapshots = (
        (
            await session.execute(
                select(MarketPriceSnapshot)
                .where(MarketPriceSnapshot.market_id.in_(market_ids))
                .order_by(MarketPriceSnapshot.market_id, MarketPriceSnapshot.ts.desc())
            )
        )
        .scalars()
        .all()
    )
    latest: dict[str, MarketPriceSnapshot] = {}
    for snapshot in snapshots:
        latest.setdefault(snapshot.market_id, snapshot)
    return latest


async def build_event_out(session: AsyncSession, settings: Settings, event: Event) -> EventOut:
    markets = list(
        (
            await session.execute(
                select(Market)
                .where(Market.event_id == event.id)
                .order_by(Market.group_item_threshold)
            )
        )
        .scalars()
        .all()
    )
    latest = await _latest_prices(session, [market.id for market in markets])
    city = await session.get(City, event.city_slug)
    probs = (
        await event_model_probs(session, settings, event, city, markets)
        if city is not None and markets
        else None
    )
    buckets: list[BucketOut] = []
    for index, market in enumerate(markets):
        price = latest.get(market.id)
        model_prob = probs[index] if probs is not None and index < len(probs) else None
        edge = (
            net_edge(model_prob, price.best_ask, settings.taker_fee_rate)
            if model_prob is not None and price is not None and price.best_ask is not None
            else None
        )
        buckets.append(
            BucketOut(
                market_id=market.id,
                label=market.group_item_title,
                kind=market.bucket_kind,
                low=market.bucket_low,
                high=market.bucket_high,
                yes_token_id=market.yes_token_id,
                best_bid=price.best_bid if price is not None else None,
                best_ask=price.best_ask if price is not None else None,
                mid=price.mid if price is not None else None,
                model_prob=model_prob,
                edge_net=edge,
                winner=market.winner,
            )
        )
    return EventOut(
        id=event.id,
        slug=event.slug,
        title=event.title,
        city_slug=event.city_slug,
        target_date=event.target_date,
        end_date=event.end_date,
        closed=event.closed,
        volume=event.volume,
        liquidity=event.liquidity,
        buckets=buckets,
    )


@router.get("")
async def list_markets(
    session: SessionDep,
    settings: SettingsDep,
    city: str | None = None,
    include_closed: bool = False,
) -> list[EventOut]:
    query = select(Event)
    if city is not None:
        query = query.where(Event.city_slug == city)
    if not include_closed:
        query = query.where(Event.closed.is_(False), Event.active.is_(True))
    query = query.order_by(Event.target_date, Event.city_slug)
    events = (await session.execute(query)).scalars().all()
    return [await build_event_out(session, settings, event) for event in events]
