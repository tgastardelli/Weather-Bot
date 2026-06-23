"""Audit whether research-only cities can become live-eligible candidates."""

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.discovery_candidate_audit import _city_resolution_audit
from analysis.historical_validation import parse_cities
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    City,
    CityResolutionPromotionAuditRun,
    Event,
    Market,
    MarketPriceHistoryPoint,
    MarketTradeHistoryPoint,
    PaperFill,
    PaperOrder,
    Signal,
)
from app.db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)

AUDIT_SOURCE = "city_resolution_promotion_audit"
MAX_MISMATCH_RATE = 0.02


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


async def _history_counts_by_city(session: AsyncSession) -> dict[str, int]:
    trade_rows = (
        await session.execute(
            select(Event.city_slug, func.count(MarketTradeHistoryPoint.id))
            .select_from(Event)
            .join(Market, Market.event_id == Event.id)
            .join(MarketTradeHistoryPoint)
            .group_by(Event.city_slug)
        )
    ).all()
    price_rows = (
        await session.execute(
            select(Event.city_slug, func.count(MarketPriceHistoryPoint.id))
            .select_from(Event)
            .join(Market, Market.event_id == Event.id)
            .join(MarketPriceHistoryPoint)
            .group_by(Event.city_slug)
        )
    ).all()
    counts: dict[str, int] = {}
    for city, count in trade_rows:
        counts[str(city)] = counts.get(str(city), 0) + int(count or 0)
    for city, count in price_rows:
        counts[str(city)] = counts.get(str(city), 0) + int(count or 0)
    return counts


async def _previous_mismatch_rates(
    session: AsyncSession,
) -> dict[str, str]:
    previous = (
        await session.execute(
            select(CityResolutionPromotionAuditRun)
            .order_by(
                CityResolutionPromotionAuditRun.run_at.desc(),
                CityResolutionPromotionAuditRun.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if previous is None:
        return {}
    try:
        payload = json.loads(previous.resolution_json)
    except json.JSONDecodeError:
        return {}
    cities = payload.get("cities")
    if not isinstance(cities, list):
        return {}
    rates: dict[str, str] = {}
    for row in cities:
        if isinstance(row, dict) and row.get("city_slug") is not None:
            rates[str(row["city_slug"])] = str(row.get("mismatch_rate") or "")
    return rates


def _city_status(row: dict[str, Any], city: City | None, market_history_points: int) -> str:
    audited = int(row.get("audited_markets") or 0)
    mismatches = int(row.get("mismatches") or 0)
    missing = int(row.get("missing_observations") or 0)
    mismatch_rate = mismatches / audited if audited > 0 else 1.0
    if city is None:
        return "excluded"
    if audited <= 0 or missing > 0:
        return "DATA_REVIEW"
    if mismatch_rate > MAX_MISMATCH_RATE:
        return "DATA_REVIEW"
    if market_history_points <= 0:
        return "DATA_REVIEW"
    return "LIVE_ELIGIBLE_CANDIDATE"


async def generate_city_resolution_promotion_audit_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
) -> CityResolutionPromotionAuditRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)

    async with session_factory() as session:
        city_rows = (
            await session.execute(select(City).where(City.active.is_(True)).order_by(City.slug))
        ).scalars().all()
        selected = [city.slug for city in city_rows if cities is None or city.slug in cities]
        resolution = await _city_resolution_audit(
            session,
            traded_cities=set(selected),
            research_only=set(selected),
            window_start=window_start,
            window_end=window_end,
        )
        market_history = await _history_counts_by_city(session)
        previous_rates = await _previous_mismatch_rates(session)
        signals = int((await session.execute(select(func.count(Signal.id)))).scalar_one())
        orders = int((await session.execute(select(func.count(PaperOrder.id)))).scalar_one())
        fills = int((await session.execute(select(func.count(PaperFill.id)))).scalar_one())

    city_by_slug = {city.slug: city for city in city_rows}
    audited_rows: list[dict[str, object]] = []
    for row in resolution.get("cities", []):
        if not isinstance(row, dict):
            continue
        slug = str(row.get("city_slug") or "unknown")
        audited = int(row.get("audited_markets") or 0)
        mismatches = int(row.get("mismatches") or 0)
        mismatch_rate = mismatches / audited if audited > 0 else 1.0
        status = _city_status(row, city_by_slug.get(slug), market_history.get(slug, 0))
        audited_rows.append(
            {
                **row,
                "market_history_points": market_history.get(slug, 0),
                "mismatch_rate": f"{mismatch_rate:.4f}",
                "mismatch_rate_before_after": {
                    "before": previous_rates.get(slug),
                    "after": f"{mismatch_rate:.4f}",
                },
                "promotion_status": status,
                "can_enter_shadow": status == "LIVE_ELIGIBLE_CANDIDATE",
                "can_enter_live": False,
            }
        )

    promotable = [
        row for row in audited_rows if row.get("promotion_status") == "LIVE_ELIGIBLE_CANDIDATE"
    ]
    gates = {
        "resolution_reconstructable": {
            "passed": len(audited_rows) > 0,
            "value": {"audited_cities": [row.get("city_slug") for row in audited_rows]},
            "required": {"audited_cities_gt": 0},
        },
        "promotable_city": {
            "passed": bool(promotable),
            "value": {"promotable": [row.get("city_slug") for row in promotable]},
            "required": {"mismatch_rate_lte": MAX_MISMATCH_RATE},
        },
        "trading_artifacts_unchanged": {
            "passed": True,
            "value": {"signals": signals, "paper_orders": orders, "paper_fills": fills},
            "required": "promotion audit does not create trading artifacts",
        },
        "live_release": {
            "passed": False,
            "value": "diagnostic_only",
            "required": "repair PROMISING plus measurement READY_FOR_LIVE_REVIEW",
        },
    }
    summary = {
        "source": AUDIT_SOURCE,
        "diagnostic_only": True,
        "cannot_approve_live": True,
        "requested_cities": selected,
        "promotable_cities": [str(row.get("city_slug")) for row in promotable],
        "next_action": "run_expanded_discovery" if promotable else "fix_resolution_mapping",
    }
    status = "READY_FOR_EXPANDED_DISCOVERY" if promotable else "DATA_REVIEW"

    async with session_factory() as session, session.begin():
        run = CityResolutionPromotionAuditRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            cities_json=_json(selected),
            summary_json=_json(summary),
            resolution_json=_json({"cities": audited_rows, "raw": resolution}),
            gates_json=_json(gates),
        )
        session.add(run)
        await session.flush()
        logger.info("city promotion audit: status=%s promotable=%d", status, len(promotable))
        return run


async def run_report(
    *,
    cities: list[str] | None = None,
    days: int | None = None,
) -> CityResolutionPromotionAuditRun:
    settings = get_settings()
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_city_resolution_promotion_audit_report(
            session_factory,
            settings,
            cities=cities,
            days=days,
        )
    finally:
        await engine.dispose()


def _row_payload(row: CityResolutionPromotionAuditRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "cities": json.loads(row.cities_json),
        "summary": json.loads(row.summary_json),
        "resolution": json.loads(row.resolution_json),
        "gates": json.loads(row.gates_json),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit city resolution for promotion.")
    parser.add_argument("--cities", help="Comma-separated city slugs.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    row = asyncio.run(run_report(cities=parse_cities(args.cities), days=args.days))
    if args.json:
        print(json.dumps(_row_payload(row), indent=2, sort_keys=True))
    else:
        print(f"city promotion audit status={row.status} run_id={row.id}")


if __name__ == "__main__":
    main()
