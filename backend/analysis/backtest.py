"""Backtests over stored signals and replayed historical price snapshots."""

import argparse
import asyncio
import json
import logging
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal, cast

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.models import (
    BacktestResult,
    Base,
    CalibrationMetric,
    City,
    DailyObservedMax,
    EnsembleMember,
    Event,
    ForecastSnapshot,
    Market,
    MarketPriceHistoryPoint,
    MarketPriceSnapshot,
    MarketTradeHistoryPoint,
    Signal,
)
from app.db.session import create_engine, create_session_factory
from app.strategy.edge import cost_per_share, gross_edge, net_edge
from app.strategy.engine import SIGNAL_DEDUPE_WINDOW, SIGNAL_EDGE_DELTA, market_bucket
from app.strategy.probability import Rounding, bucket_probabilities
from app.strategy.sizing import kelly_stake

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
FEE_FORMULA = "fee_rate * price * (1 - price)"
REPLAY_EXECUTION_PROXY = "best_ask_taker_no_depth_slippage"
HISTORICAL_PRICE_EXECUTION_PROXY = "polymarket_prices_history_last_price_no_book_depth"
HISTORICAL_TRADE_EXECUTION_PROXY = "historical_last_trade_no_book_depth"
HISTORICAL_MIXED_EXECUTION_PROXY = "historical_last_trade_or_prices_history_no_book_depth"
HISTORICAL_MODEL_INPUT_SOURCE = "historical_deterministic_forecasts_as_members"
HISTORICAL_TRADE_PRICE_SAMPLING = "last_trade_per_market_per_60m_bucket"
HISTORICAL_PRICE_HISTORY_SAMPLING = "prices_history_points"
BOOTSTRAP_ITERATIONS = 500
BacktestMode = Literal["stored-signals", "replay", "historical-price", "both"]
Profile = Literal["longshot", "max_edge"]
PROFILES: tuple[Profile, ...] = ("longshot", "max_edge")
OBSERVED_SOURCE_PRIORITY = {"resolution": 3, "era5": 2, "metar": 1}


@dataclass(frozen=True)
class TradeResult:
    ts: datetime | None
    pnl: Decimal
    stake: Decimal
    won: bool
    outcome: float
    model_prob: float | None = None
    market_price: Decimal | None = None
    market_id: str | None = None
    event_id: str | None = None
    city_slug: str | None = None
    target_date: date | None = None
    lead_days: int | None = None
    bucket_kind: str | None = None
    bucket_label: str | None = None
    edge_net: Decimal | None = None
    hours_to_close: float | None = None
    price_source: str | None = None


@dataclass(frozen=True)
class EventReplayProbs:
    probs_by_market: dict[str, float]
    lead_days: int


@dataclass(frozen=True)
class HistoricalMarketPoint:
    ts: datetime
    sampled_ts: datetime
    market_id: str
    price: Decimal
    source: Literal["data_api_trades", "clob_prices_history"]


def _max_drawdown(pnls: list[Decimal]) -> Decimal:
    peak = Decimal(0)
    equity = Decimal(0)
    drawdown = Decimal(0)
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return abs(drawdown).quantize(CENT)


def _brier_score(trades: list[TradeResult], source: Literal["model", "market"]) -> float | None:
    values: list[float] = []
    for trade in trades:
        if source == "model":
            if trade.model_prob is None:
                continue
            probability = trade.model_prob
        else:
            if trade.market_price is None:
                continue
            probability = float(trade.market_price)
        values.append((probability - trade.outcome) ** 2)
    return sum(values) / len(values) if values else None


def _brier_delta(trades: list[TradeResult]) -> float | None:
    model = _brier_score(trades, "model")
    market = _brier_score(trades, "market")
    if model is None or market is None:
        return None
    return market - model


def _max_loss_streak(trades: list[TradeResult]) -> int:
    longest = 0
    current = 0
    for trade in trades:
        if trade.won:
            current = 0
            continue
        current += 1
        longest = max(longest, current)
    return longest


def _avg_decimal(values: list[Decimal]) -> str | None:
    if not values:
        return None
    average = sum(values, Decimal(0)) / Decimal(len(values))
    return str(average.quantize(Decimal("0.00001")))


def _roi(total_pnl: Decimal, total_staked: Decimal) -> str | None:
    if total_staked <= 0:
        return None
    return str((total_pnl / total_staked).quantize(Decimal("0.0001")))


def _trade_metrics(trades: list[TradeResult]) -> dict[str, object]:
    pnls = [trade.pnl for trade in trades]
    total_staked = sum((trade.stake for trade in trades), Decimal(0)).quantize(CENT)
    total_pnl = sum(pnls, Decimal(0)).quantize(CENT)
    edge_values = [trade.edge_net for trade in trades if trade.edge_net is not None]
    price_values = [trade.market_price for trade in trades if trade.market_price is not None]
    return {
        "n_resolved_trades": len(trades),
        "total_staked": str(total_staked),
        "total_pnl": str(total_pnl),
        "roi": _roi(total_pnl, total_staked),
        "brier_model": _brier_score(trades, "model"),
        "brier_market": _brier_score(trades, "market"),
        "brier_delta": _brier_delta(trades),
        "max_loss_streak": _max_loss_streak(trades),
        "avg_edge_net": _avg_decimal(edge_values),
        "avg_market_price": _avg_decimal(price_values),
    }


def _group_trade_metrics(
    trades: list[TradeResult], group: Literal["city", "lead_days", "bucket_kind"]
) -> dict[str, dict[str, object]]:
    groups: defaultdict[str, list[TradeResult]] = defaultdict(list)
    for trade in trades:
        if group == "city":
            key = trade.city_slug
        elif group == "lead_days":
            key = str(trade.lead_days) if trade.lead_days is not None else None
        else:
            key = trade.bucket_kind
        if key is not None:
            groups[key].append(trade)
    return {key: _trade_metrics(group_trades) for key, group_trades in groups.items()}


def _profile_metrics(
    trades: list[TradeResult],
    *,
    n_candidate_snapshots: int | None = None,
) -> dict[str, object]:
    metrics = _trade_metrics(trades)
    if n_candidate_snapshots is not None:
        metrics["n_candidate_snapshots"] = n_candidate_snapshots
    metrics["by_city"] = _group_trade_metrics(trades, "city")
    metrics["by_lead_days"] = _group_trade_metrics(trades, "lead_days")
    metrics["by_bucket_kind"] = _group_trade_metrics(trades, "bucket_kind")
    return metrics


def _percentile_decimal(values: list[Decimal], quantile: float) -> Decimal:
    if not values:
        return Decimal(0)
    ordered = sorted(values)
    position = round((len(ordered) - 1) * quantile)
    return ordered[position]


def _bootstrap_metrics(trades: list[TradeResult]) -> dict[str, object]:
    if len(trades) < 2:
        return {
            "bootstrap_iterations": 0,
            "pnl_ci_low": None,
            "pnl_ci_high": None,
            "roi_ci_low": None,
            "roi_ci_high": None,
        }
    rng = random.Random(1729)
    pnl_samples: list[Decimal] = []
    roi_samples: list[Decimal] = []
    for _ in range(BOOTSTRAP_ITERATIONS):
        sample = [trades[rng.randrange(len(trades))] for _ in trades]
        pnl = sum((trade.pnl for trade in sample), Decimal(0)).quantize(CENT)
        staked = sum((trade.stake for trade in sample), Decimal(0))
        pnl_samples.append(pnl)
        if staked > 0:
            roi_samples.append((pnl / staked).quantize(Decimal("0.0001")))

    return {
        "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
        "pnl_ci_low": str(_percentile_decimal(pnl_samples, 0.025).quantize(CENT)),
        "pnl_ci_high": str(_percentile_decimal(pnl_samples, 0.975).quantize(CENT)),
        "roi_ci_low": (
            str(_percentile_decimal(roi_samples, 0.025).quantize(Decimal("0.0001")))
            if roi_samples
            else None
        ),
        "roi_ci_high": (
            str(_percentile_decimal(roi_samples, 0.975).quantize(Decimal("0.0001")))
            if roi_samples
            else None
        ),
    }


def _concentration_metrics(trades: list[TradeResult]) -> dict[str, object]:
    abs_pnls = sorted((abs(trade.pnl) for trade in trades), reverse=True)
    total_abs = sum(abs_pnls, Decimal(0))
    if total_abs <= 0:
        return {"top_5_abs_pnl_share": None}
    top_share = sum(abs_pnls[:5], Decimal(0)) / total_abs
    return {"top_5_abs_pnl_share": str(top_share.quantize(Decimal("0.0001")))}


def _trade_result(
    *,
    ts: datetime | None = None,
    stake: Decimal,
    market_price: Decimal,
    model_prob: float,
    winner: bool,
    fee_rate: Decimal,
    market_id: str | None = None,
    event_id: str | None = None,
    city_slug: str | None = None,
    target_date: date | None = None,
    lead_days: int | None = None,
    bucket_kind: str | None = None,
    bucket_label: str | None = None,
    edge_net: Decimal | None = None,
    hours_to_close: float | None = None,
    price_source: str | None = None,
) -> TradeResult | None:
    cost = cost_per_share(market_price, fee_rate)
    if cost <= 0:
        return None
    shares = stake / cost
    settlement = Decimal(1) if winner else Decimal(0)
    pnl = (shares * (settlement - cost)).quantize(CENT)
    return TradeResult(
        ts=ts,
        pnl=pnl,
        stake=stake,
        won=pnl > 0,
        outcome=1.0 if winner else 0.0,
        model_prob=model_prob,
        market_price=market_price,
        market_id=market_id,
        event_id=event_id,
        city_slug=city_slug,
        target_date=target_date,
        lead_days=lead_days,
        bucket_kind=bucket_kind,
        bucket_label=bucket_label,
        edge_net=edge_net,
        hours_to_close=hours_to_close,
        price_source=price_source,
    )


def _result_params(
    settings: Settings,
    *,
    source: str,
    extra: dict[str, object] | None = None,
) -> str:
    params: dict[str, object] = {
        "fee_formula": FEE_FORMULA,
        "fee_rate": str(settings.taker_fee_rate),
        "longshot_max_price": str(settings.longshot_max_price),
        "min_edge_net": str(settings.min_edge_net),
        "source": source,
    }
    if extra is not None:
        params.update(extra)
    return json.dumps(params, sort_keys=True)


def _build_result(
    *,
    run_at: datetime,
    profile: Profile,
    trades: list[TradeResult],
    settings: Settings,
    source: str,
    extra_params: dict[str, object] | None = None,
) -> BacktestResult:
    pnls = [trade.pnl for trade in trades]
    gains = sum((pnl for pnl in pnls if pnl > 0), Decimal(0))
    losses = sum((pnl for pnl in pnls if pnl < 0), Decimal(0))
    total_staked = sum((trade.stake for trade in trades), Decimal(0)).quantize(CENT)
    total_pnl = sum(pnls, Decimal(0)).quantize(CENT)
    n_wins = sum(1 for trade in trades if trade.won)
    return BacktestResult(
        run_at=run_at,
        profile=profile,
        n_trades=len(trades),
        n_wins=n_wins,
        total_staked=total_staked,
        total_pnl=total_pnl,
        win_rate=(n_wins / len(trades)) if trades else 0.0,
        profit_factor=(float(gains / abs(losses)) if losses < 0 else None),
        max_drawdown=_max_drawdown(pnls),
        params_json=_result_params(settings, source=source, extra=extra_params),
    )


async def _profile_trades(
    session: AsyncSession, profile: Profile, fee_rate: Decimal
) -> list[TradeResult]:
    rows = (
        await session.execute(
            select(Signal, Market)
            .join(Market, Signal.market_id == Market.id)
            .where(
                Signal.profile == profile,
                Signal.status == "PROPOSED",
                Market.winner.is_not(None),
            )
            .order_by(Signal.ts)
        )
    ).all()
    trades: list[TradeResult] = []
    for signal, market in rows:
        if market.winner is None:
            continue
        trade = _trade_result(
            ts=signal.ts,
            stake=signal.stake,
            market_price=signal.market_price,
            model_prob=signal.model_prob,
            winner=market.winner,
            fee_rate=fee_rate,
            market_id=market.id,
            bucket_kind=market.bucket_kind,
            bucket_label=market.group_item_title,
            price_source="stored_signal",
        )
        if trade is not None:
            trades.append(trade)
    return trades


async def _latest_ensemble_members_at(
    session: AsyncSession,
    *,
    city_slug: str,
    target_date: date,
    models: list[str],
    as_of: datetime,
) -> tuple[list[float], int]:
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
                    ForecastSnapshot.fetched_at <= as_of,
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


async def _event_market_probs_at(
    session: AsyncSession,
    settings: Settings,
    event_row: Event,
    city: City,
    as_of: datetime,
) -> EventReplayProbs | None:
    markets = list(
        (
            await session.execute(
                select(Market)
                .where(Market.event_id == event_row.id)
                .order_by(Market.group_item_threshold)
            )
        )
        .scalars()
        .all()
    )
    if not markets:
        return None

    members, lead_days = await _latest_ensemble_members_at(
        session,
        city_slug=city.slug,
        target_date=event_row.target_date,
        models=settings.ensemble_models,
        as_of=as_of,
    )
    if not members:
        return None

    bias = await _city_bias(session, city.slug, settings.ensemble_models, lead_days)
    unit: Literal["C", "F"] = "F" if city.unit == "F" else "C"
    rounding: Rounding = "floor" if city.rounding == "floor" else "round"
    buckets = [market_bucket(market, unit) for market in markets]
    probs = bucket_probabilities(
        members,
        buckets,
        unit=unit,
        rounding=rounding,
        bias_c=bias,
        spread_inflation=settings.spread_inflation,
        clamp_epsilon=settings.prob_clamp_epsilon,
    )
    return EventReplayProbs(
        probs_by_market={market.id: prob for market, prob in zip(markets, probs, strict=True)},
        lead_days=lead_days,
    )


def _historical_forecast_available_at(target_date: date, lead_days: int) -> datetime:
    forecast_date = target_date - timedelta(days=lead_days)
    return datetime.combine(forecast_date, time.min, tzinfo=UTC)


async def _historical_forecast_members_at(
    session: AsyncSession,
    *,
    city_slug: str,
    target_date: date,
    models: list[str],
    as_of: datetime,
) -> tuple[list[float], int]:
    rows = (
        await session.execute(
            select(ForecastSnapshot).where(
                ForecastSnapshot.city_slug == city_slug,
                ForecastSnapshot.target_date == target_date,
                ForecastSnapshot.source == "historical",
                ForecastSnapshot.model.in_(models),
                ForecastSnapshot.tmax_c.is_not(None),
            )
        )
    ).scalars().all()

    latest_by_model: dict[str, ForecastSnapshot] = {}
    for row in rows:
        if _historical_forecast_available_at(row.target_date, row.lead_days) > as_of:
            continue
        current = latest_by_model.get(row.model)
        if current is None:
            latest_by_model[row.model] = row
            continue
        current_available_at = _historical_forecast_available_at(
            current.target_date, current.lead_days
        )
        row_available_at = _historical_forecast_available_at(row.target_date, row.lead_days)
        if row_available_at > current_available_at or (
            row_available_at == current_available_at and row.fetched_at > current.fetched_at
        ):
            latest_by_model[row.model] = row

    selected = list(latest_by_model.values())
    members = [row.tmax_c for row in selected if row.tmax_c is not None]
    lead_days = max((row.lead_days for row in selected), default=0)
    return members, lead_days


def _preferred_observed(rows: list[DailyObservedMax]) -> dict[tuple[str, date], float]:
    observed: dict[tuple[str, date], DailyObservedMax] = {}
    for row in rows:
        key = (row.city_slug, row.target_date)
        current = observed.get(key)
        current_priority = OBSERVED_SOURCE_PRIORITY.get(current.source, 0) if current else -1
        if OBSERVED_SOURCE_PRIORITY.get(row.source, 0) > current_priority:
            observed[key] = row
    return {key: row.tmax_c for key, row in observed.items()}


async def _historical_city_bias(
    session: AsyncSession,
    *,
    city_slug: str,
    models: list[str],
    lead_days: int,
    target_date: date,
) -> float:
    forecast_rows = (
        await session.execute(
            select(ForecastSnapshot).where(
                ForecastSnapshot.city_slug == city_slug,
                ForecastSnapshot.target_date < target_date,
                ForecastSnapshot.source == "historical",
                ForecastSnapshot.model.in_(models),
                ForecastSnapshot.lead_days == lead_days,
                ForecastSnapshot.tmax_c.is_not(None),
            )
        )
    ).scalars().all()
    observed_rows = (
        await session.execute(
            select(DailyObservedMax).where(
                DailyObservedMax.city_slug == city_slug,
                DailyObservedMax.target_date < target_date,
                DailyObservedMax.source.in_(["era5", "resolution", "metar"]),
            )
        )
    ).scalars().all()
    observed = _preferred_observed(list(observed_rows))

    latest_by_key: dict[tuple[str, date], ForecastSnapshot] = {}
    for forecast in forecast_rows:
        key = (forecast.model, forecast.target_date)
        current = latest_by_key.get(key)
        if current is None or forecast.fetched_at > current.fetched_at:
            latest_by_key[key] = forecast

    residuals: list[float] = []
    for forecast in latest_by_key.values():
        actual = observed.get((forecast.city_slug, forecast.target_date))
        if actual is None or forecast.tmax_c is None:
            continue
        residuals.append(actual - forecast.tmax_c)
    return sum(residuals) / len(residuals) if residuals else 0.0


async def _event_historical_probs_at(
    session: AsyncSession,
    settings: Settings,
    event_row: Event,
    city: City,
    as_of: datetime,
    bias_cache: dict[tuple[str, date, int], float],
) -> EventReplayProbs | None:
    markets = list(
        (
            await session.execute(
                select(Market)
                .where(Market.event_id == event_row.id)
                .order_by(Market.group_item_threshold)
            )
        )
        .scalars()
        .all()
    )
    if not markets:
        return None

    members, lead_days = await _historical_forecast_members_at(
        session,
        city_slug=city.slug,
        target_date=event_row.target_date,
        models=settings.deterministic_models,
        as_of=as_of,
    )
    if not members:
        return None

    cache_key = (city.slug, event_row.target_date, lead_days)
    if cache_key not in bias_cache:
        bias_cache[cache_key] = await _historical_city_bias(
            session,
            city_slug=city.slug,
            models=settings.deterministic_models,
            lead_days=lead_days,
            target_date=event_row.target_date,
        )
    unit: Literal["C", "F"] = "F" if city.unit == "F" else "C"
    rounding: Rounding = "floor" if city.rounding == "floor" else "round"
    buckets = [market_bucket(market, unit) for market in markets]
    probs = bucket_probabilities(
        members,
        buckets,
        unit=unit,
        rounding=rounding,
        bias_c=bias_cache[cache_key],
        spread_inflation=settings.spread_inflation,
        clamp_epsilon=settings.prob_clamp_epsilon,
    )
    return EventReplayProbs(
        probs_by_market={market.id: prob for market, prob in zip(markets, probs, strict=True)},
        lead_days=lead_days,
    )


def _is_recent_duplicate(
    last_signals: dict[tuple[str, Profile], tuple[datetime, Decimal]],
    *,
    market_id: str,
    profile: Profile,
    ts: datetime,
    edge_net: Decimal,
) -> bool:
    last = last_signals.get((market_id, profile))
    if last is None:
        return False
    last_ts, last_edge = last
    return last_ts >= ts - SIGNAL_DEDUPE_WINDOW and abs(last_edge - edge_net) < SIGNAL_EDGE_DELTA


def _parse_hour_bucket(raw: object, fallback: datetime) -> datetime:
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return fallback.replace(minute=0, second=0, microsecond=0)


async def _replay_profile_trades(
    session: AsyncSession, settings: Settings
) -> tuple[dict[Profile, list[TradeResult]], int]:
    rows = (
        await session.execute(
            select(MarketPriceSnapshot, Market, Event, City)
            .join(Market, MarketPriceSnapshot.market_id == Market.id)
            .join(Event, Market.event_id == Event.id)
            .join(City, Event.city_slug == City.slug)
            .where(
                Market.winner.is_not(None),
                MarketPriceSnapshot.best_ask.is_not(None),
                Event.end_date.is_not(None),
            )
            .order_by(MarketPriceSnapshot.ts, Market.id)
        )
    ).all()

    trades: dict[Profile, list[TradeResult]] = {"longshot": [], "max_edge": []}
    prob_cache: dict[tuple[str, datetime], EventReplayProbs | None] = {}
    last_signals: dict[tuple[str, Profile], tuple[datetime, Decimal]] = {}
    exposure_by_market_day: defaultdict[tuple[str, date], Decimal] = defaultdict(Decimal)
    n_candidate_snapshots = 0

    for price_row, market, event_row, city in rows:
        if event_row.end_date is None or market.winner is None:
            continue
        hours_to_close = (event_row.end_date - price_row.ts).total_seconds() / 3600.0
        if not (settings.min_hours_to_close <= hours_to_close <= settings.max_hours_to_close):
            continue

        ask = price_row.best_ask
        if ask is None or not (Decimal(0) < ask < Decimal(1)):
            continue

        cache_key = (event_row.id, price_row.ts)
        if cache_key not in prob_cache:
            prob_cache[cache_key] = await _event_market_probs_at(
                session, settings, event_row, city, price_row.ts
            )
        event_probs = prob_cache[cache_key]
        if event_probs is None:
            continue
        prob = event_probs.probs_by_market.get(market.id)
        if prob is None:
            continue

        n_candidate_snapshots += 1
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
        if stake <= 0:
            continue

        profiles: list[Profile] = ["max_edge"]
        if ask <= settings.longshot_max_price:
            profiles.append("longshot")

        for profile in profiles:
            if _is_recent_duplicate(
                last_signals,
                market_id=market.id,
                profile=profile,
                ts=price_row.ts,
                edge_net=e_net,
            ):
                continue

            exposure_key = (market.id, price_row.ts.date())
            if exposure_by_market_day[exposure_key] + stake > settings.max_exposure_per_market:
                continue

            trade = _trade_result(
                ts=price_row.ts,
                stake=stake,
                market_price=ask,
                model_prob=prob,
                winner=market.winner,
                fee_rate=settings.taker_fee_rate,
                market_id=market.id,
                event_id=event_row.id,
                city_slug=city.slug,
                target_date=event_row.target_date,
                lead_days=event_probs.lead_days,
                bucket_kind=market.bucket_kind,
                bucket_label=market.group_item_title,
                edge_net=e_net,
                hours_to_close=hours_to_close,
                price_source="market_price_snapshot",
            )
            if trade is None:
                continue

            exposure_by_market_day[exposure_key] += stake
            last_signals[(market.id, profile)] = (price_row.ts, e_net)
            trades[profile].append(trade)
            logger.debug(
                "replay signal %s %s ask=%s p=%.3f edge_gross=%s edge_net=%s stake=%s",
                profile,
                market.group_item_title,
                ask,
                prob,
                e_gross,
                e_net,
                stake,
            )

    return trades, n_candidate_snapshots


async def _historical_price_profile_trades(
    session: AsyncSession, settings: Settings
) -> tuple[dict[Profile, list[TradeResult]], int, dict[str, int], dict[str, int], dict[str, int]]:
    start = datetime.now(UTC).date() - timedelta(days=settings.validation_history_days)

    trade_filters = [
        Market.winner.is_not(None),
        Event.end_date.is_not(None),
        Event.target_date >= start,
        MarketTradeHistoryPoint.ts <= Event.end_date,
    ]
    price_filters = [
        Market.winner.is_not(None),
        Event.end_date.is_not(None),
        Event.target_date >= start,
        MarketPriceHistoryPoint.ts <= Event.end_date,
    ]
    if settings.cities is not None:
        trade_filters.append(Event.city_slug.in_(settings.cities))
        price_filters.append(Event.city_slug.in_(settings.cities))

    raw_trade_count_query = (
        select(func.count(MarketTradeHistoryPoint.id))
        .select_from(MarketTradeHistoryPoint)
        .join(Market, MarketTradeHistoryPoint.market_id == Market.id)
        .join(Event, Market.event_id == Event.id)
        .join(City, Event.city_slug == City.slug)
        .where(*trade_filters)
    )
    raw_price_count_query = (
        select(func.count(MarketPriceHistoryPoint.id))
        .select_from(MarketPriceHistoryPoint)
        .join(Market, MarketPriceHistoryPoint.market_id == Market.id)
        .join(Event, Market.event_id == Event.id)
        .join(City, Event.city_slug == City.slug)
        .where(*price_filters)
    )
    raw_source_counts = {
        "data_api_trades": int((await session.execute(raw_trade_count_query)).scalar_one() or 0),
        "clob_prices_history": int(
            (await session.execute(raw_price_count_query)).scalar_one() or 0
        ),
    }

    trade_bucket = func.strftime("%Y-%m-%d %H:00:00", MarketTradeHistoryPoint.ts)
    trade_rank = func.row_number().over(
        partition_by=(MarketTradeHistoryPoint.market_id, trade_bucket),
        order_by=(MarketTradeHistoryPoint.ts.desc(), MarketTradeHistoryPoint.id.desc()),
    )
    sampled_trade_ids = (
        select(
            MarketTradeHistoryPoint.id.label("trade_id"),
            trade_bucket.label("sampled_ts"),
            trade_rank.label("trade_rank"),
        )
        .select_from(MarketTradeHistoryPoint)
        .join(Market, MarketTradeHistoryPoint.market_id == Market.id)
        .join(Event, Market.event_id == Event.id)
        .join(City, Event.city_slug == City.slug)
        .where(*trade_filters)
    ).subquery()

    trade_query = (
        select(
            MarketTradeHistoryPoint,
            Market,
            Event,
            City,
            sampled_trade_ids.c.sampled_ts,
        )
        .select_from(MarketTradeHistoryPoint)
        .join(sampled_trade_ids, MarketTradeHistoryPoint.id == sampled_trade_ids.c.trade_id)
        .join(Market, MarketTradeHistoryPoint.market_id == Market.id)
        .join(Event, Market.event_id == Event.id)
        .join(City, Event.city_slug == City.slug)
        .where(sampled_trade_ids.c.trade_rank == 1)
        .order_by(MarketTradeHistoryPoint.ts, Market.id)
    )
    trade_rows = (await session.execute(trade_query)).all()
    trade_market_ids = {row.market_id for row, _, _, _, _ in trade_rows}

    price_query: Select[tuple[MarketPriceHistoryPoint, Market, Event, City]] = (
        select(MarketPriceHistoryPoint, Market, Event, City)
        .select_from(MarketPriceHistoryPoint)
        .join(Market, MarketPriceHistoryPoint.market_id == Market.id)
        .join(Event, Market.event_id == Event.id)
        .join(City, Event.city_slug == City.slug)
        .where(*price_filters)
        .order_by(MarketPriceHistoryPoint.ts, Market.id)
    )
    price_rows = (await session.execute(price_query)).all()
    fallback_price_rows = [
        (row, market, event, city)
        for row, market, event, city in price_rows
        if row.market_id not in trade_market_ids
    ]
    sampled_source_counts = {
        "data_api_trades": len(trade_rows),
        "clob_prices_history": len(fallback_price_rows),
    }

    rows: list[tuple[HistoricalMarketPoint, Market, Event, City]] = [
        (
            HistoricalMarketPoint(
                ts=row.ts,
                sampled_ts=_parse_hour_bucket(sampled_ts, row.ts),
                market_id=row.market_id,
                price=row.price,
                source="data_api_trades",
            ),
            market,
            event,
            city,
        )
        for row, market, event, city, sampled_ts in trade_rows
    ]
    rows.extend(
        (
            HistoricalMarketPoint(
                ts=row.ts,
                sampled_ts=row.ts,
                market_id=row.market_id,
                price=row.price,
                source="clob_prices_history",
            ),
            market,
            event,
            city,
        )
        for row, market, event, city in fallback_price_rows
    )
    rows.sort(key=lambda item: (item[0].ts, item[0].market_id))

    trades: dict[Profile, list[TradeResult]] = {"longshot": [], "max_edge": []}
    prob_cache: dict[tuple[str, datetime], EventReplayProbs | None] = {}
    bias_cache: dict[tuple[str, date, int], float] = {}
    last_signals: dict[tuple[str, Profile], tuple[datetime, Decimal]] = {}
    exposure_by_market_day: defaultdict[tuple[str, date], Decimal] = defaultdict(Decimal)
    n_candidate_points = 0
    source_counts: dict[str, int] = {"data_api_trades": 0, "clob_prices_history": 0}

    for price_row, market, event_row, city in rows:
        if event_row.end_date is None or market.winner is None:
            continue
        hours_to_close = (event_row.end_date - price_row.ts).total_seconds() / 3600.0
        if not (settings.min_hours_to_close <= hours_to_close <= settings.max_hours_to_close):
            continue

        price = price_row.price
        if not (Decimal(0) < price < Decimal(1)):
            continue

        cache_key = (event_row.id, price_row.sampled_ts)
        if cache_key not in prob_cache:
            prob_cache[cache_key] = await _event_historical_probs_at(
                session, settings, event_row, city, price_row.sampled_ts, bias_cache
            )
        event_probs = prob_cache[cache_key]
        if event_probs is None:
            continue
        prob = event_probs.probs_by_market.get(market.id)
        if prob is None:
            continue

        n_candidate_points += 1
        source_counts[price_row.source] = source_counts.get(price_row.source, 0) + 1
        e_net = net_edge(prob, price, settings.taker_fee_rate)
        if e_net < settings.min_edge_net:
            continue

        cost = cost_per_share(price, settings.taker_fee_rate)
        stake = kelly_stake(
            prob,
            cost,
            bankroll=settings.bankroll,
            kelly_multiplier=settings.kelly_fraction,
            max_stake_per_order=settings.max_stake_per_order,
        )
        if stake <= 0:
            continue

        profiles: list[Profile] = ["max_edge"]
        if price <= settings.longshot_max_price:
            profiles.append("longshot")

        for profile in profiles:
            if _is_recent_duplicate(
                last_signals,
                market_id=market.id,
                profile=profile,
                ts=price_row.ts,
                edge_net=e_net,
            ):
                continue

            exposure_key = (market.id, price_row.ts.date())
            if exposure_by_market_day[exposure_key] + stake > settings.max_exposure_per_market:
                continue

            trade = _trade_result(
                ts=price_row.ts,
                stake=stake,
                market_price=price,
                model_prob=prob,
                winner=market.winner,
                fee_rate=settings.taker_fee_rate,
                market_id=market.id,
                event_id=event_row.id,
                city_slug=city.slug,
                target_date=event_row.target_date,
                lead_days=event_probs.lead_days,
                bucket_kind=market.bucket_kind,
                bucket_label=market.group_item_title,
                edge_net=e_net,
                hours_to_close=hours_to_close,
                price_source=price_row.source,
            )
            if trade is None:
                continue

            exposure_by_market_day[exposure_key] += stake
            last_signals[(market.id, profile)] = (price_row.ts, e_net)
            trades[profile].append(trade)

    return trades, n_candidate_points, source_counts, raw_source_counts, sampled_source_counts


async def run_backtest(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    mode: BacktestMode = "stored-signals",
) -> list[BacktestResult]:
    run_at = datetime.now(UTC)
    results: list[BacktestResult] = []
    async with session_factory() as session, session.begin():
        if mode in ("stored-signals", "both"):
            for profile in PROFILES:
                trades = await _profile_trades(session, profile, settings.taker_fee_rate)
                result = _build_result(
                    run_at=run_at,
                    profile=profile,
                    trades=trades,
                    settings=settings,
                    source="stored_signals_resolved_markets",
                    extra_params=_profile_metrics(trades),
                )
                session.add(result)
                results.append(result)

        if mode in ("replay", "both"):
            replay_trades, n_candidate_snapshots = await _replay_profile_trades(session, settings)
            for profile in PROFILES:
                trades = replay_trades[profile]
                result = _build_result(
                    run_at=run_at,
                    profile=profile,
                    trades=trades,
                    settings=settings,
                    source="replay_price_snapshots",
                    extra_params={
                        **_profile_metrics(
                            trades, n_candidate_snapshots=n_candidate_snapshots
                        ),
                        "execution_proxy": REPLAY_EXECUTION_PROXY,
                    },
                )
                session.add(result)
                results.append(result)

        if mode == "historical-price":
            (
                historical_trades,
                n_candidate_points,
                price_source_counts,
                raw_price_source_counts,
                sampled_price_source_counts,
            ) = await _historical_price_profile_trades(session, settings)
            has_trades = price_source_counts.get("data_api_trades", 0) > 0
            has_prices = price_source_counts.get("clob_prices_history", 0) > 0
            price_sampling = (
                HISTORICAL_TRADE_PRICE_SAMPLING
                if raw_price_source_counts.get("data_api_trades", 0) > 0
                else HISTORICAL_PRICE_HISTORY_SAMPLING
            )
            if has_trades and has_prices:
                execution_proxy = HISTORICAL_MIXED_EXECUTION_PROXY
            elif has_trades:
                execution_proxy = HISTORICAL_TRADE_EXECUTION_PROXY
            else:
                execution_proxy = HISTORICAL_PRICE_EXECUTION_PROXY
            for profile in PROFILES:
                trades = historical_trades[profile]
                result = _build_result(
                    run_at=run_at,
                    profile=profile,
                    trades=trades,
                    settings=settings,
                    source="historical_price_points",
                    extra_params={
                        **_profile_metrics(trades),
                        **_bootstrap_metrics(trades),
                        **_concentration_metrics(trades),
                        "execution_proxy": execution_proxy,
                        "model_input_source": HISTORICAL_MODEL_INPUT_SOURCE,
                        "n_candidate_price_points": n_candidate_points,
                        "n_raw_price_points": sum(raw_price_source_counts.values()),
                        "n_sampled_price_points": sum(sampled_price_source_counts.values()),
                        "price_sampling": price_sampling,
                        "price_source_counts": price_source_counts,
                        "price_source_raw_counts": raw_price_source_counts,
                        "price_source_sampled_counts": sampled_price_source_counts,
                        "walk_forward_calibration": True,
                    },
                )
                session.add(result)
                results.append(result)
        await session.flush()
    return results


async def run(
    settings: Settings, *, mode: BacktestMode = "stored-signals"
) -> list[BacktestResult]:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await run_backtest(session_factory, settings, mode=mode)
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Weather Bot backtests.")
    parser.add_argument(
        "--mode",
        choices=("stored-signals", "replay", "historical-price", "both"),
        default="stored-signals",
        help="Backtest mode to run.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    results = asyncio.run(run(get_settings(), mode=cast(BacktestMode, args.mode)))
    for result in results:
        logger.info(
            "backtest %s: trades=%d pnl=%s params=%s",
            result.profile,
            result.n_trades,
            result.total_pnl,
            result.params_json,
        )


if __name__ == "__main__":
    main()
