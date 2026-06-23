"""Repair persisted weather event target dates from Gamma event slugs."""

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.historical_validation import parse_cities
from app.config import Settings, get_settings
from app.db.models import Base, Event
from app.db.session import create_engine, create_session_factory
from app.polymarket.normalize import target_date_from_event_slug

logger = logging.getLogger(__name__)


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


async def repair_event_target_dates(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    window_start = run_at.date() - timedelta(days=history_days)
    selected = set(cities or [])

    async with session_factory() as session:
        query = select(Event).where(Event.target_date >= window_start)
        if selected:
            query = query.where(Event.city_slug.in_(selected))
        events = (
            (await session.execute(query.order_by(Event.city_slug, Event.target_date)))
            .scalars()
            .all()
        )

        updates: list[dict[str, object]] = []
        skipped = 0
        for event in events:
            inferred = target_date_from_event_slug(event.slug, event.end_date)
            if inferred is None:
                skipped += 1
                continue
            if inferred == event.target_date:
                continue
            updates.append(
                {
                    "event_id": event.id,
                    "city_slug": event.city_slug,
                    "slug": event.slug,
                    "old_target_date": event.target_date.isoformat(),
                    "new_target_date": inferred.isoformat(),
                    "end_date": event.end_date.isoformat() if event.end_date else None,
                }
            )
            if not dry_run:
                event.target_date = inferred
                event.updated_at = run_at
        if not dry_run:
            await session.commit()

    by_city: dict[str, int] = {}
    for update in updates:
        city = str(update["city_slug"])
        by_city[city] = by_city.get(city, 0) + 1

    payload: dict[str, object] = {
        "run_at": run_at.isoformat(),
        "dry_run": dry_run,
        "window_start": window_start.isoformat(),
        "cities": sorted(selected) if selected else "all",
        "events_scanned": len(events),
        "events_updated": len(updates),
        "events_skipped_no_slug_date": skipped,
        "updates_by_city": by_city,
        "sample_updates": updates[:20],
    }
    logger.info(
        "event target-date repair: scanned=%d updated=%d dry_run=%s",
        len(events),
        len(updates),
        dry_run,
    )
    return payload


async def run_repair(
    *,
    cities: list[str] | None = None,
    days: int | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    settings = get_settings()
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await repair_event_target_dates(
            session_factory,
            settings,
            cities=cities,
            days=days,
            dry_run=dry_run,
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair weather event target dates from slugs.")
    parser.add_argument("--cities", help="Comma-separated city slugs.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    payload = asyncio.run(
        run_repair(cities=parse_cities(args.cities), days=args.days, dry_run=args.dry_run)
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_json(payload))


if __name__ == "__main__":
    main()
