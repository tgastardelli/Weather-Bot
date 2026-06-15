"""Event detail endpoints."""

from statistics import quantiles

from fastapi import APIRouter, HTTPException
from sqlalchemy import or_, select

from app.api.deps import SessionDep, SettingsDep
from app.api.markets import build_event_out
from app.api.schemas import EventDetailOut, ForecastPoint, ObservationPoint, PricePoint
from app.db.models import (
    EnsembleMember,
    Event,
    ForecastSnapshot,
    Market,
    MarketPriceSnapshot,
    Observation,
)

router = APIRouter(prefix="/api/events", tags=["events"])


def _percentiles(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0], ordered[0], ordered[0]
    cuts = quantiles(ordered, n=10, method="inclusive")
    return cuts[0], cuts[4], cuts[8]


@router.get("/{event_id_or_slug}")
async def get_event_detail(
    event_id_or_slug: str, session: SessionDep, settings: SettingsDep
) -> EventDetailOut:
    event = (
        await session.execute(
            select(Event).where(or_(Event.id == event_id_or_slug, Event.slug == event_id_or_slug))
        )
    ).scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="event_not_found")

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
    labels = {market.id: market.group_item_title for market in markets}
    prices = list(
        (
            await session.execute(
                select(MarketPriceSnapshot)
                .where(MarketPriceSnapshot.market_id.in_(labels))
                .order_by(MarketPriceSnapshot.ts)
            )
        )
        .scalars()
        .all()
    )
    price_points = [
        PricePoint(
            ts=price.ts,
            market_id=price.market_id,
            label=labels[price.market_id],
            mid=price.mid,
        )
        for price in prices
    ]

    forecasts = list(
        (
            await session.execute(
                select(ForecastSnapshot)
                .where(
                    ForecastSnapshot.city_slug == event.city_slug,
                    ForecastSnapshot.target_date == event.target_date,
                )
                .order_by(ForecastSnapshot.fetched_at, ForecastSnapshot.model)
            )
        )
        .scalars()
        .all()
    )
    forecast_points: list[ForecastPoint] = []
    for forecast in forecasts:
        p10: float | None = None
        p50: float | None = None
        p90: float | None = None
        if forecast.n_members:
            members = (
                await session.execute(
                    select(EnsembleMember.tmax_c).where(
                        EnsembleMember.snapshot_id == forecast.id
                    )
                )
            ).scalars().all()
            p10, p50, p90 = _percentiles(list(members))
        forecast_points.append(
            ForecastPoint(
                fetched_at=forecast.fetched_at,
                model=forecast.model,
                source=forecast.source,
                target_date=forecast.target_date,
                tmax_c=forecast.tmax_c,
                p10=p10,
                p50=p50,
                p90=p90,
            )
        )

    observations = list(
        (
            await session.execute(
                select(Observation)
                .where(Observation.city_slug == event.city_slug)
                .order_by(Observation.observed_at)
            )
        )
        .scalars()
        .all()
    )
    obs_points = [
        ObservationPoint(observed_at=obs.observed_at, temp_c=obs.temp_c)
        for obs in observations
    ]
    return EventDetailOut(
        event=await build_event_out(session, settings, event),
        prices=price_points,
        forecasts=forecast_points,
        observations=obs_points,
    )
