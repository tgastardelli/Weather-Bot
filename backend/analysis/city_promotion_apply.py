"""Apply audited city promotions into the operational registry.

This command only flips ``City.needs_review`` after a successful resolution
promotion audit. It never creates signals, orders, fills, credentials, or live
readiness approvals.
"""

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.city_resolution_promotion_audit import MAX_MISMATCH_RATE
from analysis.historical_validation import parse_cities
from analysis.operational_quarantine import is_operationally_quarantined, quarantine_payload
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    City,
    CityPromotionApplyRun,
    CityResolutionPromotionAuditRun,
    PaperFill,
    PaperOrder,
    Signal,
)
from app.db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)

PROMOTION_SOURCE = "city_promotion_apply"


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


def _float_value(value: object, default: float = 1.0) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _int_value(value: object, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


async def _latest_audit(
    session: AsyncSession,
) -> CityResolutionPromotionAuditRun | None:
    return (
        await session.execute(
            select(CityResolutionPromotionAuditRun).order_by(
                CityResolutionPromotionAuditRun.run_at.desc(),
                CityResolutionPromotionAuditRun.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _artifact_counts(session: AsyncSession) -> dict[str, int]:
    return {
        "signals": int((await session.execute(select(func.count(Signal.id)))).scalar_one()),
        "paper_orders": int(
            (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        ),
        "paper_fills": int((await session.execute(select(func.count(PaperFill.id)))).scalar_one()),
    }


def _audit_cities(audit: CityResolutionPromotionAuditRun | None) -> dict[str, dict[str, Any]]:
    if audit is None:
        return {}
    try:
        payload = json.loads(audit.resolution_json)
    except json.JSONDecodeError:
        return {}
    cities = payload.get("cities")
    if not isinstance(cities, list):
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for row in cities:
        if isinstance(row, dict) and isinstance(row.get("city_slug"), str):
            rows[str(row["city_slug"])] = row
    return rows


def _promotion_blockers(
    city: City | None,
    audit_row: dict[str, Any] | None,
) -> list[str]:
    blockers: list[str] = []
    if city is None:
        return ["city_not_found"]
    if is_operationally_quarantined(city.slug):
        blockers.append("operational_quarantine")
    if audit_row is None:
        blockers.append("missing_promotion_audit")
        return blockers
    if audit_row.get("promotion_status") != "LIVE_ELIGIBLE_CANDIDATE":
        blockers.append("audit_not_live_eligible_candidate")
    if _float_value(audit_row.get("mismatch_rate")) > MAX_MISMATCH_RATE:
        blockers.append("mismatch_rate_above_threshold")
    if _int_value(audit_row.get("missing_observations")) > 0:
        blockers.append("missing_observations")
    if _int_value(audit_row.get("resolution_points")) <= 0:
        blockers.append("missing_resolution_points")
    if audit_row.get("resolution_source_used") != "resolution":
        blockers.append("resolution_source_not_official")
    if _int_value(audit_row.get("market_history_points")) <= 0:
        blockers.append("missing_market_history")
    return sorted(set(blockers))


async def apply_city_promotions(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None,
) -> CityPromotionApplyRun:
    del settings
    run_at = datetime.now(UTC)
    requested = sorted(set(cities or []))

    async with session_factory() as session:
        counts_before = await _artifact_counts(session)
        audit = await _latest_audit(session)
        audit_rows = _audit_cities(audit)
        if not requested:
            requested = sorted(audit_rows)
        city_rows = (
            await session.execute(select(City).where(City.slug.in_(requested)).order_by(City.slug))
        ).scalars().all()
        city_by_slug = {city.slug: city for city in city_rows}

    promoted: list[dict[str, object]] = []
    blocked: list[dict[str, object]] = []
    async with session_factory() as session, session.begin():
        for city_slug in requested:
            city = await session.get(City, city_slug)
            audit_row = audit_rows.get(city_slug)
            blockers = _promotion_blockers(city, audit_row)
            if blockers:
                blocked.append(
                    {
                        "city_slug": city_slug,
                        "blockers": blockers,
                        "quarantine": quarantine_payload(city_slug),
                        "audit": audit_row,
                    }
                )
                continue
            assert city is not None
            city.needs_review = False
            city.updated_at = run_at
            promoted.append(
                {
                    "city_slug": city.slug,
                    "previous_needs_review": city_by_slug[city_slug].needs_review,
                    "needs_review": False,
                    "audit_run_id": audit.id if audit is not None else None,
                    "mismatch_rate": audit_row.get("mismatch_rate") if audit_row else None,
                    "resolution_source_used": (
                        audit_row.get("resolution_source_used") if audit_row else None
                    ),
                    "market_history_points": (
                        audit_row.get("market_history_points") if audit_row else None
                    ),
                }
            )

    async with session_factory() as session:
        counts_after = await _artifact_counts(session)

    gates = {
        "audit_available": {
            "passed": audit is not None,
            "value": {"audit_run_id": audit.id if audit is not None else None},
            "required": "latest city_resolution_promotion_audit run",
        },
        "promoted_city": {
            "passed": bool(promoted),
            "value": {"promoted": [row["city_slug"] for row in promoted]},
            "required": {
                "promotion_status": "LIVE_ELIGIBLE_CANDIDATE",
                "mismatch_rate_lte": MAX_MISMATCH_RATE,
                "resolution_source_used": "resolution",
            },
        },
        "quarantine_block": {
            "passed": all(not is_operationally_quarantined(city) for city in requested),
            "value": {
                "blocked_quarantined": [
                    row["city_slug"]
                    for row in blocked
                    if "operational_quarantine" in row["blockers"]
                ]
            },
            "required": "operationally quarantined cities cannot be promoted",
        },
        "trading_artifacts_unchanged": {
            "passed": counts_before == counts_after,
            "value": {"before": counts_before, "after": counts_after},
            "required": "promotion apply must not create signals/orders/fills",
        },
        "live_release": {
            "passed": False,
            "value": "city_registry_only",
            "required": "repair PROMISING plus measurement READY_FOR_LIVE_REVIEW",
        },
    }
    status = "PROMOTED" if promoted else "BLOCKED"
    summary = {
        "source": PROMOTION_SOURCE,
        "diagnostic_only": False,
        "cannot_approve_live": True,
        "requested_cities": requested,
        "promoted_cities": [str(row["city_slug"]) for row in promoted],
        "blocked_cities": [str(row["city_slug"]) for row in blocked],
        "audit_run_id": audit.id if audit is not None else None,
        "next_action": "run_city_edge_ranking" if promoted else "fix_resolution_promotion_audit",
    }

    async with session_factory() as session, session.begin():
        run = CityPromotionApplyRun(
            run_at=run_at,
            status=status,
            requested_cities_json=_json(requested),
            promoted_cities_json=_json(promoted),
            blocked_json=_json(blocked),
            summary_json=_json(summary),
            gates_json=_json(gates),
        )
        session.add(run)
        await session.flush()
        logger.info(
            "city promotion apply: status=%s promoted=%s blocked=%s",
            status,
            summary["promoted_cities"],
            summary["blocked_cities"],
        )
        return run


async def run_report(
    *,
    cities: list[str] | None = None,
) -> CityPromotionApplyRun:
    settings = get_settings()
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await apply_city_promotions(session_factory, settings, cities=cities)
    finally:
        await engine.dispose()


def _row_payload(row: CityPromotionApplyRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "requested_cities": json.loads(row.requested_cities_json),
        "promoted_cities": json.loads(row.promoted_cities_json),
        "blocked": json.loads(row.blocked_json),
        "summary": json.loads(row.summary_json),
        "gates": json.loads(row.gates_json),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply audited city promotions.")
    parser.add_argument("--cities", help="Comma-separated city slugs.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    row = asyncio.run(run_report(cities=parse_cities(args.cities)))
    if args.json:
        print(json.dumps(_row_payload(row), indent=2, sort_keys=True))
    else:
        print(f"city promotion apply status={row.status} run_id={row.id}")


if __name__ == "__main__":
    main()
