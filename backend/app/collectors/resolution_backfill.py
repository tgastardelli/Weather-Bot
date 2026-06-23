"""Backfill official/reconstructable daily resolution temperatures.

This collector writes only DailyObservedMax(source="resolution") plus an audit
run. It never creates signals, orders, fills, credentials, or live artifacts.
"""

import argparse
import asyncio
import csv
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.historical_validation import parse_cities
from app.config import Settings, get_settings
from app.db.models import Base, City, DailyObservedMax, ResolutionBackfillRun
from app.db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)

ResolutionSource = Literal["wunderground", "csv"]
CSV_COLUMNS = {"city_slug", "target_date", "station_code", "tmax", "unit", "source_url"}
MAX_SAMPLE_ROWS = 20
WUNDERGROUND_CONCURRENCY = 8
_WU_NO_DATA_RE = re.compile(r"No\s+Data\s+Recorded|No\s+data\s+recorded", re.IGNORECASE)
_WU_HISTORY_VALUE_RE = re.compile(
    r'"temperatureHigh"\s*:\s*\{[^{}]*"value"\s*:\s*(?P<value>-?\d+(?:\.\d+)?)',
    re.IGNORECASE,
)
_WU_TABLE_MAX_RE = re.compile(
    r"(?:Max(?:imum)?\s+Temperature|High)\D{0,120}(?P<value>-?\d+(?:\.\d+)?)\s*°?\s*F",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ResolutionPoint:
    city_slug: str
    target_date: date
    station_code: str
    tmax_c: float
    original_tmax: Decimal
    unit: Literal["C", "F"]
    source_url: str | None
    source_kind: str


@dataclass(frozen=True)
class FetchResult:
    points: list[ResolutionPoint]
    errors: list[dict[str, object]]


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


def _decimal(raw: str, *, field: str) -> Decimal:
    try:
        return Decimal(raw.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"invalid_{field}") from exc


def _unit(raw: str) -> Literal["C", "F"]:
    unit = raw.strip().upper()
    if unit not in {"C", "F"}:
        raise ValueError("invalid_unit")
    return "F" if unit == "F" else "C"


def _to_celsius(value: Decimal, unit: Literal["C", "F"]) -> float:
    celsius = (value - Decimal("32")) * Decimal(5) / Decimal(9) if unit == "F" else value
    return float(celsius)


def _window(days: int) -> tuple[date, date]:
    end = datetime.now(UTC).date() - timedelta(days=1)
    return end - timedelta(days=days), end


def _wunderground_url(city: City, target_date: date) -> str | None:
    if not city.resolution_url:
        return None
    base = city.resolution_url.rstrip("/")
    return f"{base}/date/{target_date.year}-{target_date.month}-{target_date.day}"


def parse_wunderground_tmax_f(text: str) -> Decimal | None:
    """Extract a daily high temperature in Fahrenheit from a Wunderground page."""
    if _WU_NO_DATA_RE.search(text):
        return None
    match = _WU_HISTORY_VALUE_RE.search(text)
    if match:
        return Decimal(match.group("value"))
    match = _WU_TABLE_MAX_RE.search(text)
    if match:
        return Decimal(match.group("value"))
    return None


def load_resolution_csv(path: Path) -> tuple[list[ResolutionPoint], list[dict[str, object]]]:
    rows: list[ResolutionPoint] = []
    errors: list[dict[str, object]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing = sorted(CSV_COLUMNS - fieldnames)
        if missing:
            return [], [{"reason": "missing_columns", "columns": missing}]
        for index, row in enumerate(reader, start=2):
            try:
                unit = _unit(row["unit"])
                tmax = _decimal(row["tmax"], field="tmax")
                rows.append(
                    ResolutionPoint(
                        city_slug=row["city_slug"].strip(),
                        target_date=date.fromisoformat(row["target_date"].strip()),
                        station_code=row["station_code"].strip(),
                        tmax_c=_to_celsius(tmax, unit),
                        original_tmax=tmax,
                        unit=unit,
                        source_url=row.get("source_url") or None,
                        source_kind="csv",
                    )
                )
            except (KeyError, ValueError) as exc:
                errors.append({"line": index, "reason": str(exc)})
    if errors:
        return [], errors
    return rows, []


def _quality_errors(
    points: list[ResolutionPoint],
    *,
    source: ResolutionSource,
) -> list[dict[str, object]]:
    if source != "wunderground" or len(points) < 30:
        return []
    unique_values = {str(point.original_tmax) for point in points}
    if len(unique_values) <= 2:
        return [
            {
                "reason": "suspicious_constant_wunderground_series",
                "points": len(points),
                "distinct_original_tmax": len(unique_values),
                "sample_values": sorted(unique_values)[:5],
            }
        ]
    return []


async def _fetch_wunderground_city(
    http: httpx.AsyncClient,
    city: City,
    *,
    start: date,
    end: date,
) -> FetchResult:
    semaphore = asyncio.Semaphore(WUNDERGROUND_CONCURRENCY)
    dates = [
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
    ]

    async def fetch_one(target_date: date) -> ResolutionPoint | dict[str, object]:
        url = _wunderground_url(city, target_date)
        if url is None or city.station_code is None:
            return {
                "city_slug": city.slug,
                "target_date": target_date.isoformat(),
                "reason": "missing_url",
            }
        try:
            async with semaphore:
                response = await http.get(url)
            response.raise_for_status()
            tmax_f = parse_wunderground_tmax_f(response.text)
            if tmax_f is None:
                return {
                    "city_slug": city.slug,
                    "target_date": target_date.isoformat(),
                    "reason": "unparseable_wunderground_page",
                    "source_url": url,
                }
            return ResolutionPoint(
                city_slug=city.slug,
                target_date=target_date,
                station_code=city.station_code,
                tmax_c=_to_celsius(tmax_f, "F"),
                original_tmax=tmax_f,
                unit="F",
                source_url=url,
                source_kind="wunderground",
            )
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "city_slug": city.slug,
                "target_date": target_date.isoformat(),
                "reason": type(exc).__name__,
                "source_url": url,
            }

    results = await asyncio.gather(*(fetch_one(target_date) for target_date in dates))
    points = [item for item in results if isinstance(item, ResolutionPoint)]
    errors = [item for item in results if isinstance(item, dict)]
    return FetchResult(points=points, errors=errors)


async def _selected_cities(session: AsyncSession, cities: list[str] | None) -> list[City]:
    query = select(City).where(City.active.is_(True)).order_by(City.slug)
    if cities is not None:
        query = query.where(City.slug.in_(cities))
    return list((await session.execute(query)).scalars().all())


async def _artifact_counts(session: AsyncSession) -> dict[str, int]:
    from app.db.models import PaperFill, PaperOrder, Signal

    return {
        "signals": int((await session.execute(select(func.count(Signal.id)))).scalar_one()),
        "paper_orders": int(
            (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        ),
        "paper_fills": int((await session.execute(select(func.count(PaperFill.id)))).scalar_one()),
    }


async def _persist_points(
    session_factory: async_sessionmaker[AsyncSession],
    points: list[ResolutionPoint],
) -> int:
    written = 0
    async with session_factory() as session, session.begin():
        for point in points:
            stmt = (
                sqlite_insert(DailyObservedMax)
                .values(
                    city_slug=point.city_slug,
                    target_date=point.target_date,
                    tmax_c=point.tmax_c,
                    source="resolution",
                )
                .on_conflict_do_update(
                    index_elements=["city_slug", "target_date", "source"],
                    set_={"tmax_c": point.tmax_c},
                )
            )
            result = await session.execute(stmt)
            written += int(getattr(result, "rowcount", 0) or 0)
    return written


async def generate_resolution_backfill(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None,
    days: int,
    source: ResolutionSource,
    csv_path: Path | None = None,
) -> ResolutionBackfillRun:
    run_at = datetime.now(UTC)
    window_start, window_end = _window(days)
    async with session_factory() as session:
        city_rows = await _selected_cities(session, cities or settings.cities)
        counts_before = await _artifact_counts(session)

    selected = [city.slug for city in city_rows]
    points: list[ResolutionPoint] = []
    errors: list[dict[str, object]] = []
    if csv_path is not None:
        points, errors = load_resolution_csv(csv_path)
        allowed = set(selected)
        points = [
            point
            for point in points
            if point.city_slug in allowed
            and window_start <= point.target_date <= window_end
        ]
    elif source == "wunderground":
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
            for city in city_rows:
                result = await _fetch_wunderground_city(
                    http,
                    city,
                    start=window_start,
                    end=window_end,
                )
                points.extend(result.points)
                errors.extend(result.errors)
    else:
        errors.append({"reason": "csv_source_requires_csv_path"})

    quality_errors = _quality_errors(points, source=source)
    errors.extend(quality_errors)
    fatal_errors = (csv_path is not None and bool(errors)) or bool(quality_errors)
    written = 0 if fatal_errors else await _persist_points(session_factory, points)
    async with session_factory() as session:
        counts_after = await _artifact_counts(session)

    status = "OK" if points and not errors else "PARTIAL" if written > 0 else "DATA_REVIEW"
    summary = {
        "source": "resolution_backfill",
        "requested_source": source,
        "csv_path": str(csv_path) if csv_path is not None else None,
        "cities": selected,
        "points_loaded": len(points),
        "points_written": written,
        "errors": len(errors),
        "diagnostic_only": True,
        "cannot_approve_live": True,
    }
    gates = {
        "resolution_points": {
            "passed": written > 0,
            "value": {"points_written": written},
            "required": {"points_written_gt": 0},
        },
        "source_valid": {
            "passed": len(errors) == 0,
            "value": {"errors": errors[:MAX_SAMPLE_ROWS]},
            "required": "all parsed rows valid before writing",
        },
        "trading_artifacts_unchanged": {
            "passed": counts_before == counts_after,
            "value": {"before": counts_before, "after": counts_after},
            "required": "resolution backfill must not create signals/orders/fills",
        },
        "live_release": {
            "passed": False,
            "value": "diagnostic_only",
            "required": "repair PROMISING plus measurement READY_FOR_LIVE_REVIEW",
        },
    }
    row_payload = [
        {
            "city_slug": point.city_slug,
            "target_date": point.target_date.isoformat(),
            "station_code": point.station_code,
            "tmax_c": point.tmax_c,
            "original_tmax": str(point.original_tmax),
            "unit": point.unit,
            "source_url": point.source_url,
            "source_kind": point.source_kind,
        }
        for point in points[:MAX_SAMPLE_ROWS]
    ]
    async with session_factory() as session, session.begin():
        run = ResolutionBackfillRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            cities_json=_json(selected),
            source=source,
            summary_json=_json(summary),
            rows_json=_json(row_payload),
            errors_json=_json(errors[:MAX_SAMPLE_ROWS]),
            gates_json=_json(gates),
        )
        session.add(run)
        await session.flush()
        logger.info(
            "resolution backfill: status=%s source=%s points=%d errors=%d",
            status,
            source,
            written,
            len(errors),
        )
        return run


async def run_backfill(
    settings: Settings,
    *,
    cities: list[str] | None,
    days: int,
    source: ResolutionSource,
    csv_path: Path | None = None,
) -> ResolutionBackfillRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_resolution_backfill(
            session_factory,
            settings,
            cities=cities,
            days=days,
            source=source,
            csv_path=csv_path,
        )
    finally:
        await engine.dispose()


def _row_payload(row: ResolutionBackfillRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "cities": json.loads(row.cities_json),
        "source": row.source,
        "summary": json.loads(row.summary_json),
        "rows": json.loads(row.rows_json),
        "errors": json.loads(row.errors_json),
        "gates": json.loads(row.gates_json),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill official resolution observations.")
    parser.add_argument("--cities", help="Comma-separated city slugs.")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--source", choices=["wunderground", "csv"], default="wunderground")
    parser.add_argument("--csv", dest="csv_path", help="CSV fallback path.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    row = asyncio.run(
        run_backfill(
            get_settings(),
            cities=parse_cities(args.cities),
            days=args.days,
            source=args.source,
            csv_path=Path(args.csv_path) if args.csv_path else None,
        )
    )
    if args.json:
        print(json.dumps(_row_payload(row), indent=2, sort_keys=True))
    else:
        print(f"resolution backfill status={row.status} run_id={row.id}")


if __name__ == "__main__":
    main()
