"""Strategy engine v0: varre mercados ativos e registra sinais (sem ordens).

Pipeline: probabilidade (ensemble) -> edge liquido (-fee) -> filtros
configuraveis -> Kelly fracionario -> checagens de risco -> Signal.
Perfis: `longshot` e `max_edge`.
"""

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    CalibrationMetric,
    City,
    EnsembleMember,
    Event,
    ForecastSnapshot,
    Market,
    MarketPriceSnapshot,
    Signal,
)
from app.polymarket.normalize import Bucket
from app.strategy.edge import cost_per_share, gross_edge, net_edge
from app.strategy.probability import Rounding, bucket_probabilities
from app.strategy.sizing import kelly_stake

logger = logging.getLogger(__name__)

SIGNAL_DEDUPE_WINDOW = timedelta(hours=1)
SIGNAL_EDGE_DELTA = Decimal("0.02")


def market_bucket(market: Market, unit: Literal["C", "F"]) -> Bucket:
    return Bucket(
        kind=market.bucket_kind,  # type: ignore[arg-type]
        unit=unit,
        low=market.bucket_low,
        high=market.bucket_high,
    )


async def _latest_ensemble_members(
    session: AsyncSession, city_slug: str, target_date: date, models: list[str]
) -> tuple[list[float], int]:
    """Membros do pool de ensembles (snapshot mais recente por modelo) + lead_days."""
    members: list[float] = []
    lead_days = 0
    for model in models:
        snapshot = (
            await session.execute(
                select(ForecastSnapshot)
                .where(
                    ForecastSnapshot.city_slug == city_slug,
                    ForecastSnapshot.target_date == target_date,
                    ForecastSnapshot.source == "open_meteo_ensemble",
                    ForecastSnapshot.model == model,
                )
                .order_by(ForecastSnapshot.fetched_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if snapshot is None:
            continue
        lead_days = max(lead_days, snapshot.lead_days)
        rows = (
            await session.execute(
                select(EnsembleMember.tmax_c).where(EnsembleMember.snapshot_id == snapshot.id)
            )
        ).scalars()
        members.extend(rows)
    return members, lead_days


async def _city_bias(
    session: AsyncSession, city_slug: str, models: list[str], lead_days: int
) -> float:
    rows = (
        await session.execute(
            select(CalibrationMetric.bias_c).where(
                CalibrationMetric.city_slug == city_slug,
                CalibrationMetric.lead_days == lead_days,
                CalibrationMetric.model.in_([*models, "ensemble_pool"]),
            )
        )
    ).scalars().all()
    return sum(rows) / len(rows) if rows else 0.0


async def _latest_price(
    session: AsyncSession, market_id: str
) -> MarketPriceSnapshot | None:
    return (
        await session.execute(
            select(MarketPriceSnapshot)
            .where(MarketPriceSnapshot.market_id == market_id)
            .order_by(MarketPriceSnapshot.ts.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _recent_duplicate(
    session: AsyncSession, market_id: str, profile: str, now: datetime, new_edge: Decimal
) -> bool:
    last = (
        await session.execute(
            select(Signal)
            .where(
                Signal.market_id == market_id,
                Signal.profile == profile,
                Signal.ts >= now - SIGNAL_DEDUPE_WINDOW,
            )
            .order_by(Signal.ts.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is None:
        return False
    return abs(last.edge_net - new_edge) < SIGNAL_EDGE_DELTA


async def _market_exposure_today(
    session: AsyncSession, market_id: str, now: datetime
) -> Decimal:
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    stakes = (
        await session.execute(
            select(Signal.stake).where(
                Signal.market_id == market_id,
                Signal.status == "PROPOSED",
                Signal.ts >= start_of_day,
            )
        )
    ).scalars().all()
    return sum(stakes, Decimal(0))


async def event_model_probs(
    session: AsyncSession,
    settings: Settings,
    event_row: Event,
    city: City,
    markets: list[Market],
) -> list[float] | None:
    """P(bucket) do modelo para os mercados de um evento (None sem ensemble)."""
    members, lead_days = await _latest_ensemble_members(
        session, city.slug, event_row.target_date, settings.ensemble_models
    )
    if not members:
        return None
    bias = await _city_bias(session, city.slug, settings.ensemble_models, lead_days)
    unit: Literal["C", "F"] = "F" if city.unit == "F" else "C"
    rounding: Rounding = "floor" if city.rounding == "floor" else "round"
    buckets = [market_bucket(m, unit) for m in markets]
    return bucket_probabilities(
        members,
        buckets,
        unit=unit,
        rounding=rounding,
        bias_c=bias,
        spread_inflation=settings.spread_inflation,
        clamp_epsilon=settings.prob_clamp_epsilon,
    )


async def scan_and_store_signals(
    session: AsyncSession, settings: Settings, now: datetime | None = None
) -> list[Signal]:
    now = now or datetime.now(UTC)
    created: list[Signal] = []

    events = (
        (
            await session.execute(
                select(Event).where(Event.active.is_(True), Event.closed.is_(False))
            )
        )
        .scalars()
        .all()
    )
    for event_row in events:
        if event_row.end_date is None:
            continue
        hours_to_close = (event_row.end_date - now).total_seconds() / 3600.0
        if not (settings.min_hours_to_close <= hours_to_close <= settings.max_hours_to_close):
            continue

        city = await session.get(City, event_row.city_slug)
        if city is None:
            continue

        markets = list(
            (
                await session.execute(
                    select(Market)
                    .where(Market.event_id == event_row.id, Market.closed.is_(False))
                    .order_by(Market.group_item_threshold)
                )
            )
            .scalars()
            .all()
        )
        if not markets:
            continue

        probs = await event_model_probs(session, settings, event_row, city, markets)
        if probs is None:
            continue

        for market, prob in zip(markets, probs, strict=True):
            price_row = await _latest_price(session, market.id)
            if price_row is None or price_row.best_ask is None:
                continue
            ask = price_row.best_ask
            if not (Decimal(0) < ask < Decimal(1)):
                continue
            e_net = net_edge(prob, ask, settings.taker_fee_rate)
            if e_net < settings.min_edge_net:
                continue
            e_gross = gross_edge(prob, ask)
            cost = cost_per_share(ask, settings.taker_fee_rate)
            stake = kelly_stake(
                prob,
                cost,
                bankroll=settings.bankroll,
                kelly_multiplier=settings.kelly_fraction,
                max_stake_per_order=settings.max_stake_per_order,
            )

            profiles = ["max_edge"]
            if ask <= settings.longshot_max_price:
                profiles.append("longshot")

            for profile in profiles:
                if await _recent_duplicate(session, market.id, profile, now, e_net):
                    continue
                status, reason = "PROPOSED", None
                if stake <= 0:
                    status, reason = "SKIPPED", "kelly_stake_zero"
                else:
                    exposure = await _market_exposure_today(session, market.id, now)
                    if exposure + stake > settings.max_exposure_per_market:
                        status, reason = "SKIPPED", "max_exposure_per_market"
                signal = Signal(
                    ts=now,
                    market_id=market.id,
                    token_id=market.yes_token_id,
                    side="BUY",
                    profile=profile,
                    model_prob=prob,
                    market_price=ask,
                    edge_gross=e_gross,
                    edge_net=e_net,
                    stake=stake if status == "PROPOSED" else Decimal(0),
                    status=status,
                    reason=reason,
                )
                session.add(signal)
                created.append(signal)
                logger.info(
                    "signal %s %s %s ask=%s p=%.3f edge_net=%s stake=%s",
                    profile,
                    market.group_item_title,
                    status,
                    ask,
                    prob,
                    e_net,
                    signal.stake,
                )
    await session.flush()
    return created
