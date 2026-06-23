"""Manual one-shot collectors for local dashboard population."""

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.forecasts import collect_forecasts
from app.collectors.markets import CollectStats, collect_markets
from app.collectors.observations import collect_observations
from app.collectors.resolutions import collect_resolutions
from app.config import Settings, get_settings
from app.db.models import Base, EnsembleMember
from app.db.session import create_engine, create_session_factory
from app.execution.paper import settle_resolved_positions, submit_proposed_signals
from app.polymarket.client import PolymarketPublicClient
from app.strategy.engine import scan_and_store_signals
from app.weather.metar import MetarClient
from app.weather.open_meteo import OpenMeteoClient

logger = logging.getLogger(__name__)

JobName = Literal["markets", "all"]
HIGH_REWARD_FAST_LANE_CITIES = ["atlanta", "seattle", "toronto"]


class PolymarketClientLike(Protocol):
    async def list_weather_events(
        self, *, active: bool = True, closed: bool = False, page_size: int = 100
    ) -> list[dict[str, Any]]: ...

    async def get_book(self, token_id: str) -> dict[str, Any]: ...

    async def get_event(self, event_id: str) -> dict[str, Any]: ...


@dataclass
class RunOnceResult:
    job: JobName
    events_upserted: int = 0
    markets_upserted: int = 0
    price_snapshots: int = 0
    forecast_snapshots: int = 0
    ensemble_members: int = 0
    observations_inserted: int = 0
    resolutions: int = 0
    signals_created: int = 0
    paper_orders: int = 0
    paper_fills: int = 0
    paper_rejections: int = 0
    paper_settlements: int = 0
    evidence_reports: int = 0
    measurement_reports: int = 0
    errors: list[str] = field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        if self.job == "markets":
            return {
                "job": self.job,
                "events_upserted": self.events_upserted,
                "markets_upserted": self.markets_upserted,
                "price_snapshots": self.price_snapshots,
                "errors": self.errors,
            }
        return {
            "job": self.job,
            "events_upserted": self.events_upserted,
            "markets_upserted": self.markets_upserted,
            "price_snapshots": self.price_snapshots,
            "forecast_snapshots": self.forecast_snapshots,
            "ensemble_members": self.ensemble_members,
            "observations_inserted": self.observations_inserted,
            "resolutions": self.resolutions,
            "signals_created": self.signals_created,
            "paper_orders": self.paper_orders,
            "paper_fills": self.paper_fills,
            "paper_rejections": self.paper_rejections,
            "paper_settlements": self.paper_settlements,
            "evidence_reports": self.evidence_reports,
            "measurement_reports": self.measurement_reports,
            "errors": self.errors,
        }


def parse_cities(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    cities = [part.strip() for part in raw.split(",") if part.strip()]
    return cities or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Weather Bot collectors once.")
    parser.add_argument("job", choices=("markets", "all"))
    parser.add_argument(
        "--cities",
        help="Comma-separated Polymarket city slugs, e.g. seoul,hong-kong,nyc.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--no-signals",
        action="store_true",
        help="Skip the strategy signal scan after collection.",
    )
    parser.add_argument(
        "--no-evidence",
        action="store_true",
        help="Skip the evidence report after an all collection run.",
    )
    parser.add_argument(
        "--high-reward-fast-lane",
        action="store_true",
        help=(
            "Run paper-only repair_v5 high-reward collection for "
            "atlanta,seattle,toronto."
        ),
    )
    return parser


def apply_high_reward_fast_lane_settings(settings: Settings) -> Settings:
    """Return paper-only settings for the approved high-reward V5 fast lane."""
    return settings.model_copy(
        update={
            "cities": HIGH_REWARD_FAST_LANE_CITIES,
            "strategy_policy_mode": "repair_v5",
            "mode": "paper",
            "live_trading_enabled": False,
        }
    )


async def _scan_signals(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> tuple[int, int, int, int]:
    async with session_factory() as session, session.begin():
        signals = await scan_and_store_signals(session, settings)
        paper_stats = await submit_proposed_signals(session, settings, signals=signals)
        return len(signals), paper_stats.orders, paper_stats.fills, paper_stats.rejected


async def _settle_paper(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> tuple[int, int]:
    async with session_factory() as session, session.begin():
        paper_stats = await settle_resolved_positions(session, settings)
        return paper_stats.settled, paper_stats.fills


async def _count_ensemble_members(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        count = (
            await session.execute(select(func.count(EnsembleMember.id)))
        ).scalar_one()
        return int(count)


async def _generate_evidence(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> int:
    from analysis.evidence import generate_evidence_report

    await generate_evidence_report(session_factory, settings, cities=settings.cities)
    return 1


async def _generate_measurement(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> int:
    from analysis.measurement import build_measurement_report

    await build_measurement_report(session_factory, settings)
    return 1


def _merge_market_stats(result: RunOnceResult, stats: CollectStats) -> None:
    result.events_upserted += stats.events_upserted
    result.markets_upserted += stats.markets_upserted
    result.price_snapshots += stats.price_snapshots
    result.errors.extend(stats.errors)


async def run_once(
    job: JobName,
    *,
    settings: Settings | None = None,
    cities: list[str] | None = None,
    include_signals: bool = True,
    include_evidence: bool = True,
    high_reward_fast_lane: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    pm_client: PolymarketClientLike | None = None,
    om_client: OpenMeteoClient | None = None,
    metar_client: MetarClient | None = None,
) -> RunOnceResult:
    base_settings = settings or get_settings()
    if high_reward_fast_lane:
        run_settings = apply_high_reward_fast_lane_settings(base_settings)
    else:
        run_settings = (
            base_settings.model_copy(update={"cities": cities})
            if cities is not None
            else base_settings
        )
    result = RunOnceResult(job=job)

    engine = None
    http = None
    owns_pm_client = pm_client is None
    if session_factory is None:
        engine = create_engine(run_settings.db_url)
        session_factory = create_session_factory(engine)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    if pm_client is None or (job == "all" and (om_client is None or metar_client is None)):
        http = httpx.AsyncClient(timeout=30.0)
    try:
        pm = pm_client or PolymarketPublicClient(cast(httpx.AsyncClient, http))
        market_stats = await collect_markets(
            session_factory, cast(PolymarketPublicClient, pm), run_settings
        )
        _merge_market_stats(result, market_stats)
        if job == "markets" and include_signals:
            signals, orders, fills, rejections = await _scan_signals(
                session_factory, run_settings
            )
            result.signals_created += signals
            result.paper_orders += orders
            result.paper_fills += fills
            result.paper_rejections += rejections

        if job == "all":
            om = om_client or OpenMeteoClient(cast(httpx.AsyncClient, http))
            metar = metar_client or MetarClient(cast(httpx.AsyncClient, http))
            result.forecast_snapshots = await collect_forecasts(
                session_factory, om, run_settings
            )
            result.ensemble_members = await _count_ensemble_members(session_factory)
            result.observations_inserted = await collect_observations(
                session_factory, metar, run_settings
            )
            result.resolutions = await collect_resolutions(
                session_factory, cast(PolymarketPublicClient, pm)
            )
            settlements, settlement_fills = await _settle_paper(
                session_factory, run_settings
            )
            result.paper_settlements += settlements
            result.paper_fills += settlement_fills
            if include_signals:
                signals, orders, fills, rejections = await _scan_signals(
                    session_factory, run_settings
                )
                result.signals_created += signals
                result.paper_orders += orders
                result.paper_fills += fills
                result.paper_rejections += rejections
            if include_evidence:
                result.evidence_reports += await _generate_evidence(
                    session_factory, run_settings
                )
                result.measurement_reports += await _generate_measurement(
                    session_factory, run_settings
                )
    finally:
        if owns_pm_client and isinstance(pm, PolymarketPublicClient):
            await pm.aclose()
        if http is not None:
            await http.aclose()
        if engine is not None:
            await engine.dispose()
    return result


def _print_result(result: RunOnceResult, as_json: bool) -> None:
    payload = result.to_jsonable()
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.json else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    result = asyncio.run(
        run_once(
            cast(JobName, args.job),
            cities=parse_cities(args.cities),
            include_signals=not args.no_signals,
            include_evidence=not args.no_evidence,
            high_reward_fast_lane=bool(args.high_reward_fast_lane),
        )
    )
    _print_result(result, as_json=bool(args.json))


if __name__ == "__main__":
    main()
