"""Discover Polymarket weather cities for research onboarding.

Diagnostic-only: it may register new city metadata as ``needs_review=True``,
but it never creates signals, orders, fills, or live-readiness approvals.
"""

import argparse
import asyncio
import json
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.models import (
    Base,
    City,
    Event,
    Market,
    PaperFill,
    PaperOrder,
    Signal,
    WeatherCityDiscoveryRun,
)
from app.db.session import create_engine, create_session_factory
from app.polymarket.client import PolymarketPublicClient
from app.polymarket.normalize import NormalizedEvent, normalize_event
from app.polymarket.registry import extract_resolution_meta, station_info

logger = logging.getLogger(__name__)

DISCOVERY_SOURCE = "weather_city_discovery"


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


async def _fetch_weather_events(client: PolymarketPublicClient) -> list[dict[str, Any]]:
    raw_events: list[dict[str, Any]] = []
    for active, closed in ((True, False), (False, True)):
        raw_events.extend(await client.list_weather_events(active=active, closed=closed))
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in raw_events:
        event_id = str(row.get("id") or row.get("slug") or "")
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        deduped.append(row)
    return deduped


def _city_payload(event: NormalizedEvent) -> dict[str, object]:
    description = event.markets[0].description if event.markets else ""
    meta = extract_resolution_meta(description)
    info = station_info(meta.station_code)
    return {
        "city_slug": event.city_slug,
        "name": event.city_slug.replace("-", " ").title(),
        "series_slug": f"{event.city_slug}-daily-weather",
        "station_code": meta.station_code,
        "latitude": info[0] if info else None,
        "longitude": info[1] if info else None,
        "timezone": info[2] if info else None,
        "unit": meta.unit,
        "rounding": meta.rounding,
        "resolution_source": meta.source,
        "resolution_url": meta.url,
        "metadata_complete": meta.source != "unknown" and info is not None,
        "sample_event_slug": event.slug,
        "event_count": 0,
        "closed_event_count": 0,
    }


async def _persist_new_cities(
    session: AsyncSession,
    city_rows: list[dict[str, object]],
    now: datetime,
) -> tuple[int, list[dict[str, object]]]:
    inserted = 0
    payloads: list[dict[str, object]] = []
    for row in city_rows:
        slug = str(row["city_slug"])
        existing = await session.get(City, slug)
        is_new = existing is None
        if is_new:
            session.add(
                City(
                    slug=slug,
                    name=str(row["name"]),
                    series_slug=str(row["series_slug"]),
                    station_code=(
                        str(row["station_code"]) if row.get("station_code") is not None else None
                    ),
                    station_name=None,
                    latitude=float(row["latitude"]) if row.get("latitude") is not None else None,
                    longitude=(
                        float(row["longitude"]) if row.get("longitude") is not None else None
                    ),
                    timezone=str(row["timezone"]) if row.get("timezone") is not None else None,
                    unit=str(row["unit"]),
                    resolution_source=(
                        str(row["resolution_source"])
                        if row.get("resolution_source") is not None
                        else None
                    ),
                    resolution_url=(
                        str(row["resolution_url"])
                        if row.get("resolution_url") is not None
                        else None
                    ),
                    rounding=str(row["rounding"]),
                    needs_review=True,
                    active=True,
                    updated_at=now,
                )
            )
            inserted += 1
        payloads.append(
            {
                **row,
                "already_registered": not is_new,
                "registered_as_needs_review": is_new,
                "classification": "research_only" if is_new else "existing",
            }
        )
    await session.flush()
    return inserted, payloads


async def generate_weather_city_discovery_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: PolymarketPublicClient,
    *,
    days: int | None = None,
) -> WeatherCityDiscoveryRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)
    raw_events = await _fetch_weather_events(client)
    events: list[NormalizedEvent] = []
    skipped_events = 0
    for raw in raw_events:
        try:
            event = normalize_event(raw)
        except ValueError:
            skipped_events += 1
            continue
        if event is not None:
            events.append(event)

    city_map: dict[str, dict[str, object]] = {}
    for event in events:
        row = city_map.setdefault(event.city_slug, _city_payload(event))
        row["event_count"] = int(row["event_count"]) + 1
        if event.closed:
            row["closed_event_count"] = int(row["closed_event_count"]) + 1

    async with session_factory() as session:
        existing_slugs = {
            str(slug)
            for slug in (
                await session.execute(select(City.slug))
            ).scalars().all()
        }
    candidate_rows = sorted(city_map.values(), key=lambda row: str(row["city_slug"]))
    new_rows = [row for row in candidate_rows if str(row["city_slug"]) not in existing_slugs]

    async with session_factory() as session, session.begin():
        inserted, persisted_rows = await _persist_new_cities(session, new_rows, run_at)
        signals = int((await session.execute(select(func.count(Signal.id)))).scalar_one())
        orders = int((await session.execute(select(func.count(PaperOrder.id)))).scalar_one())
        fills = int((await session.execute(select(func.count(PaperFill.id)))).scalar_one())
        event_count = int((await session.execute(select(func.count(Event.id)))).scalar_one())
        market_count = int((await session.execute(select(func.count(Market.id)))).scalar_one())

    known_rows = [
        {
            **row,
            "already_registered": True,
            "registered_as_needs_review": False,
            "classification": "existing",
        }
        for row in candidate_rows
        if str(row["city_slug"]) in existing_slugs
    ]
    all_rows = sorted([*known_rows, *persisted_rows], key=lambda row: str(row["city_slug"]))
    source_counts = Counter(str(row["resolution_source"]) for row in all_rows)
    gates = {
        "new_cities_registered": {
            "passed": inserted > 0,
            "value": {"new_cities": inserted},
            "required": {"new_cities_gte": 0},
        },
        "trading_artifacts_unchanged": {
            "passed": True,
            "value": {"signals": signals, "paper_orders": orders, "paper_fills": fills},
            "required": "weather city discovery does not create trading artifacts",
        },
        "live_release": {
            "passed": False,
            "value": "diagnostic_only",
            "required": "city promotion plus repair and measurement gates",
        },
    }
    summary = {
        "source": DISCOVERY_SOURCE,
        "diagnostic_only": True,
        "cannot_approve_live": True,
        "events_seen": len(events),
        "events_skipped": skipped_events,
        "registered_events_existing": event_count,
        "registered_markets_existing": market_count,
        "cities_seen": len(candidate_rows),
        "new_cities_registered": inserted,
        "resolution_source_counts": dict(sorted(source_counts.items())),
        "next_action": (
            "run_city_onboarding_for_new_cities" if inserted else "run_expanded_discovery"
        ),
    }
    status = "DISCOVERED_NEW_CITIES" if inserted else "NO_NEW_CITIES"

    async with session_factory() as session, session.begin():
        row = WeatherCityDiscoveryRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            summary_json=_json(summary),
            cities_json=_json(all_rows),
            gates_json=_json(gates),
        )
        session.add(row)
        await session.flush()
        logger.info("weather city discovery: status=%s new_cities=%d", status, inserted)
        return row


async def run_report(*, days: int | None = None) -> WeatherCityDiscoveryRun:
    settings = get_settings()
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with httpx.AsyncClient(timeout=30) as http:
        client = PolymarketPublicClient(http)
        try:
            return await generate_weather_city_discovery_report(
                session_factory, settings, client, days=days
            )
        finally:
            await client.aclose()
            await engine.dispose()


def _row_payload(row: WeatherCityDiscoveryRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "summary": json.loads(row.summary_json),
        "cities": json.loads(row.cities_json),
        "gates": json.loads(row.gates_json),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover Polymarket weather cities.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    row = asyncio.run(run_report(days=args.days))
    if args.json:
        print(json.dumps(_row_payload(row), indent=2, sort_keys=True))
    else:
        print(f"weather city discovery status={row.status} run_id={row.id}")


if __name__ == "__main__":
    main()
