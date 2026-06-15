"""Signal endpoints."""

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import SessionDep
from app.api.schemas import SignalRowOut
from app.db.models import Event, Market, Signal

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("")
async def list_signals(session: SessionDep, limit: int = 200) -> list[SignalRowOut]:
    rows = (
        await session.execute(
            select(Signal, Market, Event)
            .join(Market, Signal.market_id == Market.id)
            .join(Event, Market.event_id == Event.id)
            .order_by(Signal.ts.desc())
            .limit(limit)
        )
    ).all()
    return [
        SignalRowOut(
            id=signal.id,
            ts=signal.ts,
            market_id=signal.market_id,
            side=signal.side,
            profile=signal.profile,
            model_prob=signal.model_prob,
            market_price=signal.market_price,
            edge_gross=signal.edge_gross,
            edge_net=signal.edge_net,
            stake=signal.stake,
            status=signal.status,
            reason=signal.reason,
            bucket_label=market.group_item_title,
            event_slug=event.slug,
            city_slug=event.city_slug,
        )
        for signal, market, event in rows
    ]
