"""Historical error diagnostics for the weather strategy.

This module does not approve live trading. It explains why historical validation
failed by segmenting simulated historical trades without writing artificial
signals.
"""

import argparse
import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.backtest import (
    HISTORICAL_TRADE_PRICE_SAMPLING,
    PROFILES,
    TradeResult,
    _historical_price_profile_trades,
)
from analysis.historical_validation import MIN_HISTORICAL_TRADES, parse_cities
from app.config import Settings, get_settings
from app.db.models import Base, HistoricalDiagnosticsRun
from app.db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
RATE = Decimal("0.0001")
MIN_SEGMENT_TRADES = 20
MAX_SEGMENT_ROWS = 12
MAX_LOSS_ROWS = 10


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


def _decimal(value: Decimal | None, places: Decimal = Decimal("0.00001")) -> str | None:
    return None if value is None else str(value.quantize(places))


def _avg_decimal(values: list[Decimal], places: Decimal = Decimal("0.00001")) -> str | None:
    if not values:
        return None
    return str((sum(values, Decimal(0)) / Decimal(len(values))).quantize(places))


def _avg_float(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _brier_score(trades: list[TradeResult], source: str) -> float | None:
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


def _roi(total_pnl: Decimal, total_staked: Decimal) -> str | None:
    if total_staked <= 0:
        return None
    return str((total_pnl / total_staked).quantize(RATE))


def _metrics(trades: list[TradeResult]) -> dict[str, object]:
    total_staked = sum((trade.stake for trade in trades), Decimal(0)).quantize(CENT)
    total_pnl = sum((trade.pnl for trade in trades), Decimal(0)).quantize(CENT)
    wins = sum(1 for trade in trades if trade.won)
    model_probs = [trade.model_prob for trade in trades if trade.model_prob is not None]
    market_prices = [trade.market_price for trade in trades if trade.market_price is not None]
    edge_values = [trade.edge_net for trade in trades if trade.edge_net is not None]
    outcome_values = [trade.outcome for trade in trades]
    return {
        "n_trades": len(trades),
        "n_wins": wins,
        "win_rate": wins / len(trades) if trades else 0.0,
        "observed_rate": _avg_float(outcome_values),
        "avg_model_prob": _avg_float(model_probs),
        "avg_market_price": _avg_decimal(market_prices),
        "avg_edge_net": _avg_decimal(edge_values),
        "total_staked": str(total_staked),
        "total_pnl": str(total_pnl),
        "avg_pnl": _avg_decimal([trade.pnl for trade in trades], CENT),
        "roi": _roi(total_pnl, total_staked),
        "brier_model": _brier_score(trades, "model"),
        "brier_market": _brier_score(trades, "market"),
        "brier_delta": _brier_delta(trades),
    }


def _probability_bucket(probability: float | None) -> str | None:
    if probability is None:
        return None
    idx = min(max(int(probability * 10), 0), 9)
    return f"{idx / 10:.1f}-{(idx + 1) / 10:.1f}"


def _price_bucket(price: Decimal | None) -> str | None:
    if price is None:
        return None
    buckets = [
        (Decimal("0.00"), Decimal("0.05")),
        (Decimal("0.05"), Decimal("0.10")),
        (Decimal("0.10"), Decimal("0.20")),
        (Decimal("0.20"), Decimal("0.40")),
        (Decimal("0.40"), Decimal("0.60")),
        (Decimal("0.60"), Decimal("0.80")),
        (Decimal("0.80"), Decimal("0.95")),
        (Decimal("0.95"), Decimal("1.00")),
    ]
    for low, high in buckets:
        if low <= price < high:
            return f"{low}-{high}"
    return "out_of_range"


def _edge_bucket(edge: Decimal | None) -> str | None:
    if edge is None:
        return None
    buckets = [
        (Decimal("0.00"), Decimal("0.10")),
        (Decimal("0.10"), Decimal("0.25")),
        (Decimal("0.25"), Decimal("0.50")),
        (Decimal("0.50"), Decimal("0.75")),
    ]
    for low, high in buckets:
        if low <= edge < high:
            return f"{low}-{high}"
    return "0.75+"


def _hours_bucket(hours_to_close: float | None) -> str | None:
    if hours_to_close is None:
        return None
    if hours_to_close < 6:
        return "0-6h"
    if hours_to_close < 12:
        return "6-12h"
    if hours_to_close < 24:
        return "12-24h"
    if hours_to_close < 48:
        return "24-48h"
    return "48h+"


def _segment_rows(
    trades: list[TradeResult],
    group: str,
    key_fn: Callable[[TradeResult], str | None],
) -> list[dict[str, object]]:
    grouped: defaultdict[str, list[TradeResult]] = defaultdict(list)
    for trade in trades:
        key = key_fn(trade)
        if key is not None:
            grouped[key].append(trade)

    rows: list[dict[str, object]] = []
    for key, group_trades in grouped.items():
        metrics = _metrics(group_trades)
        metrics.update({"group": group, "segment": key})
        rows.append(metrics)
    return sorted(rows, key=lambda row: Decimal(str(row["total_pnl"])))[:MAX_SEGMENT_ROWS]


def _calibration_rows(trades: list[TradeResult]) -> list[dict[str, object]]:
    grouped: defaultdict[str, list[TradeResult]] = defaultdict(list)
    for trade in trades:
        key = _probability_bucket(trade.model_prob)
        if key is not None:
            grouped[key].append(trade)

    rows: list[dict[str, object]] = []
    for bucket, bucket_trades in sorted(grouped.items()):
        metrics = _metrics(bucket_trades)
        observed_rate = metrics["observed_rate"]
        avg_model_prob = metrics["avg_model_prob"]
        overconfidence = (
            avg_model_prob - observed_rate
            if isinstance(avg_model_prob, float) and isinstance(observed_rate, float)
            else None
        )
        rows.append(
            {
                "bucket": bucket,
                "n_trades": metrics["n_trades"],
                "observed_rate": observed_rate,
                "avg_model_prob": avg_model_prob,
                "avg_market_price": metrics["avg_market_price"],
                "model_overconfidence": overconfidence,
                "brier_delta": metrics["brier_delta"],
                "total_pnl": metrics["total_pnl"],
            }
        )
    return rows


def _top_losing_trades(trades: list[TradeResult]) -> list[dict[str, object]]:
    losing = sorted(trades, key=lambda trade: trade.pnl)[:MAX_LOSS_ROWS]
    rows: list[dict[str, object]] = []
    for trade in losing:
        rows.append(
            {
                "ts": trade.ts.isoformat() if trade.ts is not None else None,
                "event_id": trade.event_id,
                "market_id": trade.market_id,
                "city_slug": trade.city_slug,
                "target_date": trade.target_date.isoformat() if trade.target_date else None,
                "bucket_label": trade.bucket_label,
                "bucket_kind": trade.bucket_kind,
                "model_prob": trade.model_prob,
                "market_price": _decimal(trade.market_price),
                "edge_net": _decimal(trade.edge_net),
                "stake": str(trade.stake.quantize(CENT)),
                "pnl": str(trade.pnl.quantize(CENT)),
                "won": trade.won,
                "price_source": trade.price_source,
            }
        )
    return rows


def _profile_segments(trades: list[TradeResult]) -> dict[str, list[dict[str, object]]]:
    return {
        "by_city": _segment_rows(trades, "city", lambda trade: trade.city_slug),
        "by_bucket_kind": _segment_rows(
            trades, "bucket_kind", lambda trade: trade.bucket_kind
        ),
        "by_price_bucket": _segment_rows(
            trades, "price_bucket", lambda trade: _price_bucket(trade.market_price)
        ),
        "by_model_prob_bucket": _segment_rows(
            trades,
            "model_prob_bucket",
            lambda trade: _probability_bucket(trade.model_prob),
        ),
        "by_edge_bucket": _segment_rows(
            trades, "edge_bucket", lambda trade: _edge_bucket(trade.edge_net)
        ),
        "by_hours_to_close": _segment_rows(
            trades,
            "hours_to_close",
            lambda trade: _hours_bucket(trade.hours_to_close),
        ),
    }


def _worst_segments(
    profile_segments: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for group_name, segments in profile_segments.items():
        for segment in segments:
            if int(segment["n_trades"]) < MIN_SEGMENT_TRADES:
                continue
            rows.append({**segment, "segment_group": group_name})
    return sorted(rows, key=lambda row: Decimal(str(row["total_pnl"])))[:MAX_SEGMENT_ROWS]


def _recommendations(
    *,
    max_edge_trades: list[TradeResult],
    max_edge_metrics: dict[str, object],
    max_edge_segments: dict[str, list[dict[str, object]]],
    calibration: list[dict[str, object]],
) -> dict[str, object]:
    total_pnl = Decimal(str(max_edge_metrics["total_pnl"]))
    brier_delta = max_edge_metrics["brier_delta"]
    overconfident_buckets = [
        row
        for row in calibration
        if int(row["n_trades"]) >= MIN_SEGMENT_TRADES
        and isinstance(row["model_overconfidence"], float)
        and row["model_overconfidence"] > 0.15
    ]
    checks = {
        "enough_trades": len(max_edge_trades) >= MIN_HISTORICAL_TRADES,
        "positive_pnl": total_pnl > 0,
        "model_beats_market": isinstance(brier_delta, float) and brier_delta > 0,
        "overconfidence_detected": len(overconfident_buckets) > 0,
    }
    actions: list[dict[str, object]] = []
    if not checks["model_beats_market"]:
        actions.append(
            {
                "key": "calibrate_probabilities",
                "priority": 1,
                "reason": "Model Brier is worse than market-implied probability.",
            }
        )
    if overconfident_buckets:
        actions.append(
            {
                "key": "cap_overconfident_buckets",
                "priority": 2,
                "reason": "Some probability buckets are materially overconfident.",
                "buckets": [row["bucket"] for row in overconfident_buckets],
            }
        )
    if not checks["positive_pnl"]:
        actions.append(
            {
                "key": "raise_or_segment_entry_thresholds",
                "priority": 3,
                "reason": "Historical net PnL after fees is negative.",
            }
        )
    actions.append(
        {
            "key": "inspect_worst_segments_before_live",
            "priority": 4,
            "reason": "Disable or recalibrate worst segments before forward paper approval.",
        }
    )
    return {
        "checks": checks,
        "actions": actions,
        "worst_segments": _worst_segments(max_edge_segments),
        "top_losing_trades": _top_losing_trades(max_edge_trades),
    }


def _status(max_edge_trades: list[TradeResult], max_edge_metrics: dict[str, object]) -> str:
    if len(max_edge_trades) < MIN_HISTORICAL_TRADES:
        return "INSUFFICIENT_HISTORY"
    total_pnl = Decimal(str(max_edge_metrics["total_pnl"]))
    brier_delta = max_edge_metrics["brier_delta"]
    if total_pnl <= 0 or not isinstance(brier_delta, float) or brier_delta <= 0:
        return "NEEDS_MODEL_REPAIR"
    return "PROMISING"


async def generate_historical_diagnostics_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
) -> HistoricalDiagnosticsRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    selected_cities = cities if cities is not None else settings.cities
    run_settings = settings.model_copy(
        update={"cities": selected_cities, "validation_history_days": history_days}
    )
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)

    async with session_factory() as session:
        (
            trades_by_profile,
            n_candidate_price_points,
            price_source_counts,
            raw_price_source_counts,
            sampled_price_source_counts,
        ) = await _historical_price_profile_trades(session, run_settings)

    profile_metrics = {
        profile: _metrics(trades_by_profile[profile]) for profile in PROFILES
    }
    segments = {
        profile: _profile_segments(trades_by_profile[profile]) for profile in PROFILES
    }
    calibration = {
        profile: _calibration_rows(trades_by_profile[profile]) for profile in PROFILES
    }
    max_edge_metrics = profile_metrics["max_edge"]
    recommendations = _recommendations(
        max_edge_trades=trades_by_profile["max_edge"],
        max_edge_metrics=max_edge_metrics,
        max_edge_segments=segments["max_edge"],
        calibration=calibration["max_edge"],
    )
    status = _status(trades_by_profile["max_edge"], max_edge_metrics)
    summary = {
        "profiles": profile_metrics,
        "preferred_profile": "max_edge",
        "execution_proxy": "historical_last_trade_no_book_depth",
        "price_sampling": HISTORICAL_TRADE_PRICE_SAMPLING,
        "n_candidate_price_points": n_candidate_price_points,
        "n_raw_price_points": sum(raw_price_source_counts.values()),
        "n_sampled_price_points": sum(sampled_price_source_counts.values()),
        "price_source_counts": price_source_counts,
        "price_source_raw_counts": raw_price_source_counts,
        "price_source_sampled_counts": sampled_price_source_counts,
        "min_segment_trades": MIN_SEGMENT_TRADES,
    }

    async with session_factory() as session, session.begin():
        row = HistoricalDiagnosticsRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            cities_json=_json(selected_cities or run_settings.cities or []),
            summary_json=_json(summary),
            segments_json=_json(segments),
            calibration_json=_json(calibration),
            recommendations_json=_json(recommendations),
        )
        session.add(row)
        await session.flush()
        logger.info(
            "historical diagnostics: status=%s trades=%d pnl=%s brier_delta=%s",
            status,
            len(trades_by_profile["max_edge"]),
            max_edge_metrics["total_pnl"],
            max_edge_metrics["brier_delta"],
        )
        return row


async def run(
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
) -> HistoricalDiagnosticsRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_historical_diagnostics_report(
            session_factory, settings, cities=cities, days=days
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run historical strategy diagnostics.")
    parser.add_argument("--cities", help="Comma-separated city slugs.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def _run_to_jsonable(row: HistoricalDiagnosticsRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "cities": json.loads(row.cities_json),
        "summary": json.loads(row.summary_json),
        "segments": json.loads(row.segments_json),
        "calibration": json.loads(row.calibration_json),
        "recommendations": json.loads(row.recommendations_json),
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.json else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    row = asyncio.run(
        run(get_settings(), cities=parse_cities(args.cities), days=args.days)
    )
    if args.json:
        print(json.dumps(_run_to_jsonable(row), sort_keys=True))


if __name__ == "__main__":
    main()
