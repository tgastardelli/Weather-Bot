"""Persisted evidence reports for the paper strategy."""

import argparse
import asyncio
import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.models import (
    BacktestResult,
    Base,
    BookSnapshot,
    CalibrationMetric,
    City,
    CityVolatilityMetric,
    DailyObservedMax,
    EnsembleMember,
    Event,
    EvidenceRun,
    ForecastSnapshot,
    Market,
    MarketPriceSnapshot,
    MeasurementRun,
    Observation,
    Resolution,
    Signal,
)
from app.db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)

EVIDENCE_CITIES: tuple[str, ...] = ("seoul", "tokyo", "hong-kong")
MIN_FORWARD_DAYS = 30
MIN_RESOLVED_TRADES = 50


def _json_dumps(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_float(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


async def _count(session: AsyncSession, stmt: Any) -> int:
    return int((await session.execute(stmt)).scalar_one() or 0)


def _event_city_filter(cities: list[str]) -> Any:
    return Event.city_slug.in_(cities)


async def _data_health(
    session: AsyncSession, cities: list[str]
) -> tuple[dict[str, Any], date | None, date | None]:
    events_total = await _count(
        session,
        select(func.count(Event.id)).where(_event_city_filter(cities)),
    )
    active_events = await _count(
        session,
        select(func.count(Event.id)).where(
            _event_city_filter(cities),
            Event.active.is_(True),
            Event.closed.is_(False),
        ),
    )
    markets_total = await _count(
        session,
        select(func.count(Market.id)).join(Event, Market.event_id == Event.id).where(
            _event_city_filter(cities)
        ),
    )
    price_snapshots = await _count(
        session,
        select(func.count(MarketPriceSnapshot.id))
        .join(Market, MarketPriceSnapshot.market_id == Market.id)
        .join(Event, Market.event_id == Event.id)
        .where(_event_city_filter(cities)),
    )
    book_snapshots = await _count(
        session,
        select(func.count(BookSnapshot.id))
        .join(Market, BookSnapshot.token_id == Market.yes_token_id)
        .join(Event, Market.event_id == Event.id)
        .where(_event_city_filter(cities)),
    )
    forecast_snapshots = await _count(
        session,
        select(func.count(ForecastSnapshot.id)).where(ForecastSnapshot.city_slug.in_(cities)),
    )
    ensemble_snapshots = await _count(
        session,
        select(func.count(ForecastSnapshot.id)).where(
            ForecastSnapshot.city_slug.in_(cities),
            ForecastSnapshot.source == "open_meteo_ensemble",
        ),
    )
    ensemble_members = await _count(
        session,
        select(func.count(EnsembleMember.id))
        .join(ForecastSnapshot, EnsembleMember.snapshot_id == ForecastSnapshot.id)
        .where(ForecastSnapshot.city_slug.in_(cities)),
    )
    active_event_ensemble_members = await _count(
        session,
        select(func.count(EnsembleMember.id))
        .join(ForecastSnapshot, EnsembleMember.snapshot_id == ForecastSnapshot.id)
        .join(
            Event,
            and_(
                ForecastSnapshot.city_slug == Event.city_slug,
                ForecastSnapshot.target_date == Event.target_date,
            ),
        )
        .where(
            _event_city_filter(cities),
            Event.active.is_(True),
            Event.closed.is_(False),
            ForecastSnapshot.source == "open_meteo_ensemble",
        ),
    )
    observations = await _count(
        session,
        select(func.count(Observation.id)).where(Observation.city_slug.in_(cities)),
    )
    daily_observed_max = await _count(
        session,
        select(func.count(DailyObservedMax.id)).where(DailyObservedMax.city_slug.in_(cities)),
    )
    resolutions = await _count(
        session,
        select(func.count(Resolution.event_id))
        .join(Event, Resolution.event_id == Event.id)
        .where(_event_city_filter(cities)),
    )
    resolved_markets = await _count(
        session,
        select(func.count(Market.id)).join(Event, Market.event_id == Event.id).where(
            _event_city_filter(cities),
            Market.winner.is_not(None),
        ),
    )
    signals = await _count(
        session,
        select(func.count(Signal.id))
        .join(Market, Signal.market_id == Market.id)
        .join(Event, Market.event_id == Event.id)
        .where(_event_city_filter(cities)),
    )

    start_dt, end_dt = (
        await session.execute(
            select(func.min(MarketPriceSnapshot.ts), func.max(MarketPriceSnapshot.ts))
            .join(Market, MarketPriceSnapshot.market_id == Market.id)
            .join(Event, Market.event_id == Event.id)
            .where(_event_city_filter(cities))
        )
    ).one()
    window_start = start_dt.date() if start_dt is not None else None
    window_end = end_dt.date() if end_dt is not None else None
    forward_days = (
        (window_end - window_start).days + 1
        if window_start is not None and window_end is not None
        else 0
    )

    coverage_by_city: dict[str, dict[str, int]] = {}
    for city in cities:
        coverage_by_city[city] = {
            "events": await _count(
                session,
                select(func.count(Event.id)).where(Event.city_slug == city),
            ),
            "price_snapshots": await _count(
                session,
                select(func.count(MarketPriceSnapshot.id))
                .join(Market, MarketPriceSnapshot.market_id == Market.id)
                .join(Event, Market.event_id == Event.id)
                .where(Event.city_slug == city),
            ),
            "forecast_snapshots": await _count(
                session,
                select(func.count(ForecastSnapshot.id)).where(
                    ForecastSnapshot.city_slug == city
                ),
            ),
            "ensemble_members": await _count(
                session,
                select(func.count(EnsembleMember.id))
                .join(ForecastSnapshot, EnsembleMember.snapshot_id == ForecastSnapshot.id)
                .where(ForecastSnapshot.city_slug == city),
            ),
            "resolutions": await _count(
                session,
                select(func.count(Resolution.event_id))
                .join(Event, Resolution.event_id == Event.id)
                .where(Event.city_slug == city),
            ),
        }

    return (
        {
            "focus_cities": cities,
            "events": events_total,
            "active_events": active_events,
            "markets": markets_total,
            "price_snapshots": price_snapshots,
            "book_snapshots": book_snapshots,
            "forecast_snapshots": forecast_snapshots,
            "ensemble_snapshots": ensemble_snapshots,
            "ensemble_members": ensemble_members,
            "active_event_ensemble_members": active_event_ensemble_members,
            "observations": observations,
            "daily_observed_max": daily_observed_max,
            "resolutions": resolutions,
            "resolved_markets": resolved_markets,
            "signals": signals,
            "forward_days": forward_days,
            "coverage_by_city": coverage_by_city,
            "window_start": window_start.isoformat() if window_start else None,
            "window_end": window_end.isoformat() if window_end else None,
        },
        window_start,
        window_end,
    )


async def _model_health(session: AsyncSession, cities: list[str]) -> dict[str, Any]:
    calibration_rows = (
        (
            await session.execute(
                select(CalibrationMetric)
                .where(CalibrationMetric.city_slug.in_(cities))
                .order_by(
                    CalibrationMetric.city_slug,
                    CalibrationMetric.model,
                    CalibrationMetric.lead_days,
                )
            )
        )
        .scalars()
        .all()
    )
    calibration = [
        {
            "city_slug": row.city_slug,
            "model": row.model,
            "lead_days": row.lead_days,
            "bias_c": row.bias_c,
            "mae_c": row.mae_c,
            "residual_std_c": row.residual_std_c,
            "n_samples": row.n_samples,
        }
        for row in calibration_rows
    ]

    latest_vol_run = (
        await session.execute(
            select(func.max(CityVolatilityMetric.computed_at)).where(
                CityVolatilityMetric.city_slug.in_(cities)
            )
        )
    ).scalar_one_or_none()
    volatility_rows: list[CityVolatilityMetric] = []
    if latest_vol_run is not None:
        volatility_rows = list(
            (
                await session.execute(
                    select(CityVolatilityMetric)
                    .where(
                        CityVolatilityMetric.city_slug.in_(cities),
                        CityVolatilityMetric.computed_at == latest_vol_run,
                    )
                    .order_by(CityVolatilityMetric.reward_volatility_score.desc())
                )
            )
            .scalars()
            .all()
        )

    cities_by_slug = {
        city.slug: city
        for city in (
            await session.execute(select(City).where(City.slug.in_(cities)))
        ).scalars()
    }
    volatility_by_slug = {row.city_slug: row for row in volatility_rows}
    city_quality = []
    for slug in cities:
        city = cities_by_slug.get(slug)
        volatility = volatility_by_slug.get(slug)
        city_quality.append(
            {
                "city_slug": slug,
                "station_code": city.station_code if city else None,
                "needs_review": True if city is None else city.needs_review,
                "missing_registry": city is None,
                "data_quality": volatility.data_quality if volatility else None,
                "reward_volatility_score": (
                    volatility.reward_volatility_score if volatility else None
                ),
                "tail_miss_rate_3c": volatility.tail_miss_rate_3c if volatility else None,
                "forecast_mae_c": volatility.forecast_mae_c if volatility else None,
            }
        )

    avg_mae = (
        sum(row.mae_c for row in calibration_rows) / len(calibration_rows)
        if calibration_rows
        else None
    )
    max_tail_3c = (
        max((row.tail_miss_rate_3c for row in volatility_rows), default=None)
        if volatility_rows
        else None
    )
    return {
        "calibration": calibration,
        "city_quality": city_quality,
        "summary": {
            "calibration_rows": len(calibration_rows),
            "avg_mae_c": avg_mae,
            "latest_volatility_run": latest_vol_run.isoformat() if latest_vol_run else None,
            "max_tail_miss_rate_3c": max_tail_3c,
        },
    }


def _profile_payload(row: BacktestResult) -> dict[str, Any]:
    params = _parse_json(row.params_json)
    roi = params.get("roi")
    if roi is None and row.total_staked > 0:
        roi = str((row.total_pnl / row.total_staked).quantize(Decimal("0.0001")))
    brier_model = _as_float(params.get("brier_model"))
    brier_market = _as_float(params.get("brier_market"))
    brier_delta = _as_float(params.get("brier_delta"))
    if brier_delta is None and brier_model is not None and brier_market is not None:
        brier_delta = brier_market - brier_model
    return {
        "run_at": row.run_at.isoformat(),
        "profile": row.profile,
        "source": params.get("source"),
        "n_trades": row.n_trades,
        "n_resolved_trades": _as_int(params.get("n_resolved_trades")) or row.n_trades,
        "n_wins": row.n_wins,
        "total_staked": str(row.total_staked),
        "total_pnl": str(row.total_pnl),
        "roi": roi,
        "win_rate": row.win_rate,
        "profit_factor": row.profit_factor,
        "max_drawdown": str(row.max_drawdown),
        "brier_model": brier_model,
        "brier_market": brier_market,
        "brier_delta": brier_delta,
        "max_loss_streak": _as_int(params.get("max_loss_streak")),
        "avg_edge_net": params.get("avg_edge_net"),
        "avg_market_price": params.get("avg_market_price"),
        "execution_proxy": params.get("execution_proxy"),
        "by_city": params.get("by_city", {}),
        "by_lead_days": params.get("by_lead_days", {}),
        "by_bucket_kind": params.get("by_bucket_kind", {}),
    }


async def _trading_health(session: AsyncSession) -> dict[str, Any]:
    rows = (
        (
            await session.execute(
                select(BacktestResult).order_by(BacktestResult.run_at.desc())
            )
        )
        .scalars()
        .all()
    )
    profiles: dict[str, dict[str, Any]] = {}
    for row in rows:
        params = _parse_json(row.params_json)
        source = params.get("source")
        current = profiles.get(row.profile)
        if current is None:
            profiles[row.profile] = _profile_payload(row)
            continue
        current_source = current.get("source")
        if current_source != "replay_price_snapshots" and source == "replay_price_snapshots":
            profiles[row.profile] = _profile_payload(row)

    measurement = (
        await session.execute(
            select(MeasurementRun).order_by(MeasurementRun.run_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    return {
        "profiles": profiles,
        "execution_proxy": "best_ask_taker_no_depth_slippage",
        "preferred_source": "replay_price_snapshots",
        "measurement": (
            {
                "run_at": measurement.run_at.isoformat(),
                "status": measurement.status,
                "summary": _parse_json(measurement.summary_json),
                "metrics": _parse_json(measurement.metrics_json),
            }
            if measurement is not None
            else None
        ),
    }


def _gate(passed: bool, *, value: object, required: object, reason: str) -> dict[str, Any]:
    return {
        "passed": passed,
        "value": value,
        "required": required,
        "reason": reason,
    }


def _gates(
    *,
    data_health: dict[str, Any],
    model_health: dict[str, Any],
    trading_health: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    profiles = trading_health.get("profiles")
    max_edge = profiles.get("max_edge") if isinstance(profiles, dict) else None
    max_edge = max_edge if isinstance(max_edge, dict) else {}

    n_resolved = _as_int(max_edge.get("n_resolved_trades")) or 0
    brier_delta = _as_float(max_edge.get("brier_delta"))
    total_pnl = _as_decimal(max_edge.get("total_pnl")) or Decimal(0)
    forward_days = _as_int(data_health.get("forward_days")) or 0
    active_event_ensemble_members = _as_int(
        data_health.get("active_event_ensemble_members")
    ) or 0

    city_quality = model_health.get("city_quality")
    review_cities = []
    if isinstance(city_quality, list):
        review_cities = [
            row.get("city_slug")
            for row in city_quality
            if isinstance(row, dict)
            and (row.get("needs_review") is True or row.get("missing_registry") is True)
        ]

    gates = {
        "ensemble_members": _gate(
            active_event_ensemble_members > 0,
            value=active_event_ensemble_members,
            required="> 0 for active focus-city events",
            reason="Active Seul, Tokyo and Hong Kong events must have ensemble members.",
        ),
        "sample_size": _gate(
            forward_days >= MIN_FORWARD_DAYS and n_resolved >= MIN_RESOLVED_TRADES,
            value={"forward_days": forward_days, "n_resolved_trades": n_resolved},
            required={
                "forward_days": MIN_FORWARD_DAYS,
                "n_resolved_trades": MIN_RESOLVED_TRADES,
            },
            reason=(
                "Use the later milestone: enough forward collection and enough "
                "resolved trades."
            ),
        ),
        "max_edge_brier": _gate(
            brier_delta is not None and brier_delta > 0,
            value=brier_delta,
            required="brier_market - brier_model > 0",
            reason="max_edge model probabilities must beat market implied probabilities.",
        ),
        "replay_pnl": _gate(
            total_pnl > 0,
            value=str(total_pnl),
            required="> 0 after fees",
            reason="Replay forward PnL must be net positive after taker fees.",
        ),
        "city_quality": _gate(
            not review_cities,
            value=review_cities,
            required="no focus city needs_review or missing registry",
            reason="No approved conclusion while any focus city needs manual review.",
        ),
    }
    status = "PROMISING" if all(gate["passed"] for gate in gates.values()) else "COLLECTING"
    return status, gates


def _resolve_cities(cities: list[str] | None) -> list[str]:
    if cities is None:
        return list(EVIDENCE_CITIES)
    cleaned = [city.strip() for city in cities if city.strip()]
    return cleaned or list(EVIDENCE_CITIES)


async def generate_evidence_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None = None,
    now: datetime | None = None,
) -> EvidenceRun:
    """Build and persist one evidence report for the focus universe."""
    run_at = now or datetime.now(UTC)
    focus_cities = _resolve_cities(cities)

    async with session_factory() as session, session.begin():
        data_health, window_start, window_end = await _data_health(session, focus_cities)
        model_health = await _model_health(session, focus_cities)
        trading_health = await _trading_health(session)
        status, gates = _gates(
            data_health=data_health,
            model_health=model_health,
            trading_health=trading_health,
        )
        row = EvidenceRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            cities_json=_json_dumps(focus_cities),
            data_health_json=_json_dumps(data_health),
            model_health_json=_json_dumps(model_health),
            trading_json=_json_dumps(trading_health),
            gates_json=_json_dumps(gates),
        )
        session.add(row)
        await session.flush()
        logger.info(
            "evidence report: status=%s cities=%s forward_days=%s",
            status,
            ",".join(focus_cities),
            data_health["forward_days"],
        )
        return row


async def run(settings: Settings, *, cities: list[str] | None = None) -> EvidenceRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_evidence_report(session_factory, settings, cities=cities)
    finally:
        await engine.dispose()


def _parse_cities(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a persisted evidence report.")
    parser.add_argument(
        "--cities",
        help="Comma-separated focus city slugs. Defaults to seoul,tokyo,hong-kong.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = asyncio.run(run(get_settings(), cities=_parse_cities(args.cities)))
    logger.info("evidence %s status=%s", result.run_at.isoformat(), result.status)


if __name__ == "__main__":
    main()
