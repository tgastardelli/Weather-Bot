"""Historical validation report over climate and Polymarket price history."""

import argparse
import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.backtest import (
    HISTORICAL_PRICE_EXECUTION_PROXY,
    HISTORICAL_TRADE_EXECUTION_PROXY,
    run_backtest,
)
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    City,
    DailyObservedMax,
    Event,
    ForecastSnapshot,
    HistoricalValidationRun,
    Market,
    MarketPriceHistoryPoint,
    MarketTradeHistoryPoint,
)
from app.db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)

MIN_SAMPLES_PER_CITY = 120
MIN_HISTORICAL_TRADES = 50
MAX_TOP_5_ABS_PNL_SHARE = Decimal("0.6000")


def parse_cities(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    values = [part.strip() for part in raw.split(",") if part.strip()]
    return values or None


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _gate(passed: bool, value: object, required: object) -> dict[str, object]:
    return {"passed": passed, "value": value, "required": required}


def _params(raw: str) -> dict[str, Any]:
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _selected_cities(
    session: AsyncSession, settings: Settings, cities: list[str] | None
) -> list[City]:
    selected = cities if cities is not None else settings.cities
    query = select(City).where(City.active.is_(True))
    if selected is not None:
        query = query.where(City.slug.in_(selected))
    rows = list((await session.execute(query)).scalars().all())
    if selected is None:
        return rows
    by_slug = {city.slug: city for city in rows}
    return [by_slug[slug] for slug in selected if slug in by_slug]


async def _forecast_observed_pairs(
    session: AsyncSession, city_slug: str, start: date, end: date
) -> int:
    forecast_dates = set(
        (
            await session.execute(
                select(ForecastSnapshot.target_date).where(
                    ForecastSnapshot.city_slug == city_slug,
                    ForecastSnapshot.source == "historical",
                    ForecastSnapshot.target_date >= start,
                    ForecastSnapshot.target_date <= end,
                    ForecastSnapshot.tmax_c.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    observed_dates = set(
        (
            await session.execute(
                select(DailyObservedMax.target_date).where(
                    DailyObservedMax.city_slug == city_slug,
                    DailyObservedMax.target_date >= start,
                    DailyObservedMax.target_date <= end,
                    DailyObservedMax.source.in_(["era5", "resolution", "metar"]),
                )
            )
        )
        .scalars()
        .all()
    )
    return len(forecast_dates & observed_dates)


async def _city_coverage(
    session: AsyncSession, city: City, start: date, end: date
) -> dict[str, object]:
    price_points = (
        await session.execute(
            select(func.count(MarketPriceHistoryPoint.id))
            .join(Market, MarketPriceHistoryPoint.market_id == Market.id)
            .join(Event, Market.event_id == Event.id)
            .where(
                Event.city_slug == city.slug,
                Event.target_date >= start,
                Event.target_date <= end,
            )
        )
    ).scalar_one()
    trade_points = (
        await session.execute(
            select(func.count(MarketTradeHistoryPoint.id))
            .join(Market, MarketTradeHistoryPoint.market_id == Market.id)
            .join(Event, Market.event_id == Event.id)
            .where(
                Event.city_slug == city.slug,
                Event.target_date >= start,
                Event.target_date <= end,
            )
        )
    ).scalar_one()
    resolved_events = (
        await session.execute(
            select(func.count(func.distinct(Event.id)))
            .join(Market, Market.event_id == Event.id)
            .where(
                Event.city_slug == city.slug,
                Event.target_date >= start,
                Event.target_date <= end,
                Market.winner.is_not(None),
            )
        )
    ).scalar_one()
    forecast_pairs = await _forecast_observed_pairs(session, city.slug, start, end)
    return {
        "city_slug": city.slug,
        "forecast_observed_pairs": forecast_pairs,
        "market_price_history_points": int(price_points or 0),
        "market_trade_history_points": int(trade_points or 0),
        "resolved_events": int(resolved_events or 0),
        "needs_review": city.needs_review,
    }


def _profile_payload(profile: str, total_pnl: str, params: dict[str, Any]) -> dict[str, object]:
    return {
        "profile": profile,
        "source": params.get("source"),
        "execution_proxy": params.get("execution_proxy"),
        "model_input_source": params.get("model_input_source"),
        "n_resolved_trades": params.get("n_resolved_trades"),
        "total_pnl": total_pnl,
        "roi": params.get("roi"),
        "brier_model": params.get("brier_model"),
        "brier_market": params.get("brier_market"),
        "brier_delta": params.get("brier_delta"),
        "max_loss_streak": params.get("max_loss_streak"),
        "avg_edge_net": params.get("avg_edge_net"),
        "avg_market_price": params.get("avg_market_price"),
        "top_5_abs_pnl_share": params.get("top_5_abs_pnl_share"),
        "price_source_counts": params.get("price_source_counts"),
        "bootstrap_iterations": params.get("bootstrap_iterations"),
        "pnl_ci_low": params.get("pnl_ci_low"),
        "pnl_ci_high": params.get("pnl_ci_high"),
        "roi_ci_low": params.get("roi_ci_low"),
        "roi_ci_high": params.get("roi_ci_high"),
    }


def _status_from_gates(gates: dict[str, dict[str, object]]) -> str:
    if all(gate["passed"] is True for gate in gates.values()):
        return "PROMISING"
    if not gates["historical_samples"]["passed"] or not gates["historical_trades"]["passed"]:
        return "INSUFFICIENT_HISTORY"
    return "FAILED"


async def generate_historical_validation_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
) -> HistoricalValidationRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    selected_cities = cities if cities is not None else settings.cities
    run_settings = settings.model_copy(
        update={"cities": selected_cities, "validation_history_days": history_days}
    )
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)

    backtest_results = await run_backtest(session_factory, run_settings, mode="historical-price")
    by_profile = {result.profile: result for result in backtest_results}
    max_edge = by_profile.get("max_edge")
    max_edge_params = _params(max_edge.params_json) if max_edge is not None else {}

    async with session_factory() as session:
        city_rows = await _selected_cities(session, run_settings, selected_cities)
        coverage = [
            await _city_coverage(session, city, window_start, window_end) for city in city_rows
        ]
        total_price_points = (
            await session.execute(
                select(func.count(MarketPriceHistoryPoint.id))
                .join(Market, MarketPriceHistoryPoint.market_id == Market.id)
                .join(Event, Market.event_id == Event.id)
                .where(Event.target_date >= window_start, Event.target_date <= window_end)
            )
        ).scalar_one()
        total_trade_points = (
            await session.execute(
                select(func.count(MarketTradeHistoryPoint.id))
                .join(Market, MarketTradeHistoryPoint.market_id == Market.id)
                .join(Event, Market.event_id == Event.id)
                .where(Event.target_date >= window_start, Event.target_date <= window_end)
            )
        ).scalar_one()
        if selected_cities is not None:
            selected_set = set(selected_cities)
            missing_cities = sorted(selected_set - {city.slug for city in city_rows})
        else:
            missing_cities = []

    min_pairs = min(
        (_as_int(row["forecast_observed_pairs"]) or 0 for row in coverage),
        default=0,
    )
    needs_review = [row["city_slug"] for row in coverage if row["needs_review"]]
    max_edge_trades = _as_int(max_edge_params.get("n_resolved_trades")) or 0
    max_edge_brier_delta = _as_float(max_edge_params.get("brier_delta"))
    max_edge_pnl = max_edge.total_pnl if max_edge is not None else Decimal("0")
    concentration = _as_decimal(max_edge_params.get("top_5_abs_pnl_share"))

    gates = {
        "historical_samples": _gate(
            min_pairs >= MIN_SAMPLES_PER_CITY,
            {"min_forecast_observed_pairs": min_pairs},
            {"min_per_city": MIN_SAMPLES_PER_CITY},
        ),
        "historical_trades": _gate(
            max_edge_trades >= MIN_HISTORICAL_TRADES,
            {"max_edge_trades": max_edge_trades},
            {"min_trades": MIN_HISTORICAL_TRADES},
        ),
        "max_edge_brier": _gate(
            max_edge_brier_delta is not None and max_edge_brier_delta > 0,
            {"brier_delta": max_edge_brier_delta},
            {"brier_delta_gt": 0},
        ),
        "historical_pnl": _gate(
            max_edge_pnl > 0,
            {"max_edge_total_pnl": str(max_edge_pnl)},
            {"total_pnl_gt": "0"},
        ),
        "concentration": _gate(
            concentration is not None and concentration <= MAX_TOP_5_ABS_PNL_SHARE,
            {"top_5_abs_pnl_share": str(concentration) if concentration is not None else None},
            {"top_5_abs_pnl_share_lte": str(MAX_TOP_5_ABS_PNL_SHARE)},
        ),
        "city_quality": _gate(
            not needs_review and not missing_cities,
            {"needs_review": needs_review, "missing_cities": missing_cities},
            {"needs_review": [], "missing_cities": []},
        ),
    }
    status = _status_from_gates(gates)

    data_health = {
        "window_days": history_days,
        "market_price_history_points": int(total_price_points or 0),
        "market_trade_history_points": int(total_trade_points or 0),
        "coverage_by_city": {str(row["city_slug"]): row for row in coverage},
    }
    model_health = {
        "min_forecast_observed_pairs": min_pairs,
        "city_quality": coverage,
        "walk_forward_calibration": True,
    }
    trading = {
        "profiles": {
            profile: _profile_payload(
                result.profile,
                str(result.total_pnl),
                _params(result.params_json),
            )
            for profile, result in by_profile.items()
        },
        "preferred_profile": "max_edge",
        "preferred_source": "historical_price_points",
        "execution_proxy": (
            max_edge_params.get("execution_proxy")
            or (
                HISTORICAL_TRADE_EXECUTION_PROXY
                if int(total_trade_points or 0) > 0
                else HISTORICAL_PRICE_EXECUTION_PROXY
            )
        ),
        "price_source_counts": max_edge_params.get(
            "price_source_counts",
            {
                "clob_prices_history": int(total_price_points or 0),
                "data_api_trades": int(total_trade_points or 0),
            },
        ),
    }

    async with session_factory() as session, session.begin():
        row = HistoricalValidationRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            cities_json=_json(selected_cities or [city.slug for city in city_rows]),
            data_health_json=_json(data_health),
            model_health_json=_json(model_health),
            trading_json=_json(trading),
            gates_json=_json(gates),
        )
        session.add(row)
        await session.flush()
        logger.info(
            "historical validation: status=%s pairs=%d trades=%d pnl=%s",
            status,
            min_pairs,
            max_edge_trades,
            max_edge_pnl,
        )
        return row


async def run(
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
) -> HistoricalValidationRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_historical_validation_report(
            session_factory, settings, cities=cities, days=days
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run historical strategy validation.")
    parser.add_argument("--cities", help="Comma-separated city slugs.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def _run_to_jsonable(row: HistoricalValidationRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "cities": json.loads(row.cities_json),
        "data_health": json.loads(row.data_health_json),
        "model_health": json.loads(row.model_health_json),
        "trading": json.loads(row.trading_json),
        "gates": json.loads(row.gates_json),
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
