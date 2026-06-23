"""City promotion apply tests."""

import json
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.city_promotion_apply import apply_city_promotions
from app.config import Settings
from app.db.models import (
    City,
    CityPromotionApplyRun,
    CityResolutionPromotionAuditRun,
    PaperFill,
    PaperOrder,
    Signal,
)


def _city(slug: str, *, needs_review: bool = True) -> City:
    return City(
        slug=slug,
        name=slug.title(),
        series_slug=f"{slug}-daily-weather",
        station_code="KAAA",
        station_name=None,
        latitude=1.0,
        longitude=1.0,
        timezone="UTC",
        unit="F",
        resolution_source="wunderground",
        resolution_url=None,
        rounding="round",
        needs_review=needs_review,
        active=True,
        updated_at=datetime(2026, 6, 18, tzinfo=UTC),
    )


def _audit_row(
    slug: str,
    *,
    status: str = "LIVE_ELIGIBLE_CANDIDATE",
    mismatch_rate: str = "0.0000",
    missing_observations: int = 0,
    resolution_points: int = 120,
    resolution_source_used: str | None = "resolution",
    market_history_points: int = 1000,
) -> dict[str, object]:
    return {
        "city_slug": slug,
        "promotion_status": status,
        "mismatch_rate": mismatch_rate,
        "missing_observations": missing_observations,
        "resolution_points": resolution_points,
        "resolution_source_used": resolution_source_used,
        "market_history_points": market_history_points,
    }


async def _add_audit(
    session_factory: async_sessionmaker[AsyncSession],
    rows: list[dict[str, object]],
    *,
    run_at: datetime | None = None,
) -> None:
    now = run_at or datetime(2026, 6, 19, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            CityResolutionPromotionAuditRun(
                run_at=now,
                status="READY_FOR_EXPANDED_DISCOVERY",
                window_start=date(2025, 1, 1),
                window_end=date(2026, 6, 19),
                cities_json=json.dumps([row["city_slug"] for row in rows]),
                summary_json=json.dumps({"promotable_cities": [row["city_slug"] for row in rows]}),
                resolution_json=json.dumps({"cities": rows}),
                gates_json=json.dumps({"live_release": {"passed": False}}),
            )
        )


async def test_city_promotion_apply_promotes_only_valid_audited_city(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        session.add(_city("london"))
    await _add_audit(session_factory, [_audit_row("london")])

    row = await apply_city_promotions(
        session_factory,
        Settings(),
        cities=["london"],
    )

    async with session_factory() as session:
        city = await session.get(City, "london")
        persisted = (await session.execute(select(CityPromotionApplyRun))).scalar_one()
        signals = (await session.execute(select(func.count(Signal.id)))).scalar_one()
        orders = (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        fills = (await session.execute(select(func.count(PaperFill.id)))).scalar_one()

    assert row.id == persisted.id
    assert city is not None
    assert city.needs_review is False
    assert row.status == "PROMOTED"
    assert json.loads(row.promoted_cities_json)[0]["city_slug"] == "london"
    assert signals == 0
    assert orders == 0
    assert fills == 0


async def test_city_promotion_apply_blocks_city_without_official_resolution(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        session.add(_city("dallas"))
    await _add_audit(
        session_factory,
        [
            _audit_row(
                "dallas",
                mismatch_rate="0.0300",
                resolution_source_used="era5",
            )
        ],
    )

    row = await apply_city_promotions(
        session_factory,
        Settings(),
        cities=["dallas"],
    )

    async with session_factory() as session:
        city = await session.get(City, "dallas")

    blocked = json.loads(row.blocked_json)
    assert city is not None
    assert city.needs_review is True
    assert row.status == "BLOCKED"
    assert "mismatch_rate_above_threshold" in blocked[0]["blockers"]
    assert "resolution_source_not_official" in blocked[0]["blockers"]


async def test_city_promotion_apply_never_promotes_quarantined_city(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        session.add(_city("nyc"))
    await _add_audit(session_factory, [_audit_row("nyc")])

    row = await apply_city_promotions(
        session_factory,
        Settings(),
        cities=["nyc"],
    )

    async with session_factory() as session:
        city = await session.get(City, "nyc")

    blocked = json.loads(row.blocked_json)
    assert city is not None
    assert city.needs_review is True
    assert row.status == "BLOCKED"
    assert "operational_quarantine" in blocked[0]["blockers"]


async def test_city_promotion_apply_uses_latest_audit_when_many_exist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        session.add(_city("miami"))
    await _add_audit(
        session_factory,
        [_audit_row("miami", status="DATA_REVIEW", mismatch_rate="0.5000")],
        run_at=datetime(2026, 6, 18, tzinfo=UTC),
    )
    await _add_audit(
        session_factory,
        [_audit_row("miami")],
        run_at=datetime(2026, 6, 19, tzinfo=UTC),
    )

    row = await apply_city_promotions(
        session_factory,
        Settings(),
        cities=["miami"],
    )

    async with session_factory() as session:
        city = await session.get(City, "miami")

    assert city is not None
    assert city.needs_review is False
    assert row.status == "PROMOTED"
