"""Diagnostic shadow decisions for discovery policies.

Shadow decisions are research-only records. They never create signals, paper
orders, paper fills, credentials, approvals, or live orders.
"""

import argparse
import asyncio
import json
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.high_reward_city_hunt import (
    _decision as _high_reward_decision,
)
from analysis.high_reward_city_hunt import (
    _decision_price as _high_reward_decision_price,
)
from analysis.high_reward_city_hunt import (
    _variant_name as _high_reward_variant_name,
)
from analysis.high_reward_city_hunt import (
    _variants as _high_reward_variants,
)
from analysis.historical_validation import parse_cities
from analysis.strategy_discovery import (
    DiscoveryVariant,
    SegmentStats,
    _calibrated_probability,
    _decision_price,
    _reason,
    _variant_segment_key,
    _variants,
)
from analysis.strategy_repair import HistoricalCandidate, _historical_candidates
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    HighRewardCityHuntRun,
    PaperFill,
    PaperOrder,
    Signal,
    StrategyDiscoveryRun,
    StrategyShadowDecision,
)
from app.db.session import create_engine, create_session_factory
from app.strategy.edge import net_edge

logger = logging.getLogger(__name__)

DEFAULT_SHADOW_POLICY = "discovery_v4_shadow"
DEFAULT_HIGH_REWARD_SHADOW_POLICY = "high_reward_shadow_v1"
ShadowSource = Literal["strategy-discovery", "high-reward-city-hunt"]
MIN_HIGH_REWARD_SHADOW_TRADES = 15
MIN_HIGH_REWARD_PAYOFF_RATIO = 3.0


async def _artifact_counts(session: AsyncSession) -> dict[str, int]:
    return {
        "signals": int((await session.execute(select(func.count(Signal.id)))).scalar_one()),
        "paper_orders": int(
            (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        ),
        "paper_fills": int((await session.execute(select(func.count(PaperFill.id)))).scalar_one()),
    }


async def _latest_discovery(session: AsyncSession) -> StrategyDiscoveryRun | None:
    return (
        await session.execute(
            select(StrategyDiscoveryRun).order_by(
                StrategyDiscoveryRun.run_at.desc(), StrategyDiscoveryRun.id.desc()
            )
        )
    ).scalar_one_or_none()


async def _latest_high_reward_hunt(session: AsyncSession) -> HighRewardCityHuntRun | None:
    return (
        await session.execute(
            select(HighRewardCityHuntRun)
            .where(HighRewardCityHuntRun.status == "READY_FOR_SHADOW_FAST_LANE")
            .order_by(HighRewardCityHuntRun.run_at.desc(), HighRewardCityHuntRun.id.desc())
        )
    ).scalar_one_or_none()


def _json_loads_dict(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def _variant_name_from_discovery(row: StrategyDiscoveryRun) -> str | None:
    best = _json_loads_dict(row.best_family_json)
    last_fold = best.get("last_fold_payload")
    if isinstance(last_fold, dict) and isinstance(last_fold.get("name"), str):
        return str(last_fold["name"])
    return None


def _variant_by_name(discovery_version: str, variant_name: str | None) -> DiscoveryVariant | None:
    variants = _variants(discovery_version)
    if variant_name is not None:
        for variant in variants:
            if variant.name == variant_name:
                return variant
    return variants[0] if variants else None


def _high_reward_approved(row: HighRewardCityHuntRun) -> list[dict[str, Any]]:
    payload = _json_loads_dict(row.candidates_json)
    approved = payload.get("approved")
    if not isinstance(approved, list):
        return []
    return [item for item in approved if isinstance(item, dict)]


def _high_reward_approved_all(row: HighRewardCityHuntRun) -> list[dict[str, Any]]:
    payload = _json_loads_dict(row.candidates_json)
    approved_all = payload.get("approved_all")
    if not isinstance(approved_all, list):
        return _high_reward_approved(row)
    return [item for item in approved_all if isinstance(item, dict)]


def _as_decimal(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(str(value))
    except ValueError:
        return 0.0


def _is_high_reward_shadow_variant(row: dict[str, Any]) -> bool:
    return (
        str(row.get("city_slug") or "") != ""
        and str(row.get("variant") or "") != ""
        and int(row.get("n_trades") or 0) >= MIN_HIGH_REWARD_SHADOW_TRADES
        and _as_decimal(row.get("total_pnl")) > 0
        and _as_decimal(row.get("roi")) > 0
        and _as_decimal(row.get("payoff_ratio")) >= MIN_HIGH_REWARD_PAYOFF_RATIO
    )


def _high_reward_variant_rows_by_city(
    row: HighRewardCityHuntRun,
) -> dict[str, list[dict[str, Any]]]:
    candidates = _json_loads_dict(row.candidates_json)
    rankings = _json_loads_dict(row.rankings_json)
    sources = []
    for key in ("approved", "approved_all"):
        value = candidates.get(key)
        if isinstance(value, list):
            sources.extend(item for item in value if isinstance(item, dict))
    top_variants = rankings.get("top_variants")
    if isinstance(top_variants, list):
        sources.extend(item for item in top_variants if isinstance(item, dict))

    by_city: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for item in sources:
        if not _is_high_reward_shadow_variant(item):
            continue
        city = str(item.get("city_slug"))
        variant = str(item.get("variant"))
        key = (city, variant)
        if key in seen:
            continue
        seen.add(key)
        by_city.setdefault(city, []).append(item)

    for rows in by_city.values():
        rows.sort(
            key=lambda item: (
                item.get("blockers") == [],
                _as_decimal(item.get("payoff_ratio")),
                _as_decimal(item.get("total_pnl")),
                int(item.get("n_trades") or 0),
            ),
            reverse=True,
        )
    return by_city


def _high_reward_city_pool(
    *,
    primary: list[dict[str, Any]],
    approved_all: list[dict[str, Any]],
    variants_by_city: dict[str, list[dict[str, Any]]],
    requested_cities: list[str] | None,
) -> list[str]:
    if requested_cities is not None:
        allowed = set(variants_by_city)
        return [city for city in requested_cities if city in allowed]

    primary_cities = [str(item.get("city_slug")) for item in primary if item.get("city_slug")]
    ranked = [
        item
        for item in approved_all
        if str(item.get("city_slug") or "") in variants_by_city
    ]
    ranked.sort(
        key=lambda item: (
            item.get("blockers") == [],
            _as_decimal(item.get("payoff_ratio")),
            _as_decimal(item.get("total_pnl")),
            int(item.get("n_trades") or 0),
        ),
        reverse=True,
    )
    pool: list[str] = []
    for city in primary_cities + [str(item.get("city_slug")) for item in ranked]:
        if city and city not in pool:
            pool.append(city)
    return pool


async def _generate_high_reward_shadow_decisions(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None,
    days: int | None,
    policy_name: str,
    limit: int,
) -> dict[str, object]:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    async with session_factory() as session:
        counts_before = await _artifact_counts(session)
        hunt = await _latest_high_reward_hunt(session)
        if hunt is None:
            return {
                "status": "NO_APPROVED_HUNT",
                "policy_name": policy_name,
                "decisions_written": 0,
                "counts_before": counts_before,
                "counts_after": counts_before,
            }
        approved = _high_reward_approved(hunt)
        approved_all = _high_reward_approved_all(hunt)
        if not approved:
            return {
                "status": "NO_APPROVED_HUNT",
                "policy_name": policy_name,
                "hunt_run_id": hunt.id,
                "decisions_written": 0,
                "counts_before": counts_before,
                "counts_after": counts_before,
            }
        approved_by_city = {str(item.get("city_slug")): item for item in approved}
        approved_all_by_city = {
            str(item.get("city_slug")): item
            for item in approved_all
            if str(item.get("city_slug") or "")
        }
        variant_rows_by_city = _high_reward_variant_rows_by_city(hunt)
        selected_cities = _high_reward_city_pool(
            primary=approved,
            approved_all=approved_all,
            variants_by_city=variant_rows_by_city,
            requested_cities=cities,
        )
        run_settings = settings.model_copy(
            update={"cities": selected_cities, "validation_history_days": history_days}
        )
        candidates, _, _, _, _ = await _historical_candidates(session, run_settings)

    if not candidates:
        async with session_factory() as session:
            counts_after = await _artifact_counts(session)
        return {
            "status": "NO_FORWARD_CANDIDATES",
            "policy_name": policy_name,
            "hunt_run_id": hunt.id,
            "selected_cities": selected_cities,
            "decisions_written": 0,
            "counts_before": counts_before,
            "counts_after": counts_after,
        }

    city_windows: dict[str, dict[str, str]] = {}
    shadow: list[HistoricalCandidate] = []
    for city in selected_cities:
        city_candidates = [candidate for candidate in candidates if candidate.city_slug == city]
        if not city_candidates:
            continue
        city_max_target = max(candidate.target_date for candidate in city_candidates)
        city_shadow_start = city_max_target - timedelta(days=30)
        city_windows[city] = {
            "start": city_shadow_start.isoformat(),
            "end": city_max_target.isoformat(),
        }
        shadow.extend(
            candidate
            for candidate in city_candidates
            if candidate.target_date >= city_shadow_start
        )
    variants_by_name = {
        _high_reward_variant_name(variant): variant for variant in _high_reward_variants()
    }
    rows: list[StrategyShadowDecision] = []

    for candidate in shadow:
        approved_city = approved_all_by_city.get(candidate.city_slug)
        if approved_city is None:
            continue
        variant_rows = variant_rows_by_city.get(candidate.city_slug) or [approved_city]
        selected_variant_row: dict[str, Any] | None = None
        selected_variant = None
        selected_decision = None
        for variant_row in variant_rows:
            variant = variants_by_name.get(str(variant_row.get("variant") or ""))
            if variant is None:
                continue
            decision = _high_reward_decision(candidate, variant, settings)
            if decision is not None:
                selected_variant_row = variant_row
                selected_variant = variant
                selected_decision = decision
                break
            if selected_variant_row is None:
                selected_variant_row = variant_row
                selected_variant = variant
        if selected_variant is None or selected_variant_row is None:
            continue
        variant = selected_variant
        decision = selected_decision
        side = str(selected_variant_row.get("side") or variant.side)
        segment_key = "|".join(
            (
                "high_reward",
                candidate.city_slug,
                str(selected_variant_row.get("family") or variant.family),
                side,
                str(selected_variant_row.get("variant") or _high_reward_variant_name(variant)),
                candidate.bucket_kind,
                f"month-{candidate.target_date.month:02d}",
            )
        )
        if decision is None:
            price = _high_reward_decision_price(candidate, variant.side)
            probability = 1.0 - candidate.raw_prob if variant.side == "NO" else candidate.raw_prob
            edge = net_edge(probability, price, settings.taker_fee_rate)
            reason = "high_reward_filter"
            would_trade = False
        else:
            price = decision.decision_price
            probability = decision.model_prob
            edge = decision.edge_net
            reason = None
            would_trade = True
        rows.append(
            StrategyShadowDecision(
                ts=candidate.ts,
                policy_name=policy_name,
                market_id=candidate.market_id,
                event_id=candidate.event_id,
                city_slug=candidate.city_slug,
                target_date=candidate.target_date,
                raw_prob=candidate.raw_prob,
                calibrated_prob=probability,
                market_price=price,
                edge_net=edge,
                reason=reason,
                would_trade=would_trade,
                segment_key=segment_key,
            )
        )

    max_rows = max(limit, 0)
    if max_rows == 0:
        rows = []
    elif len(rows) > max_rows:
        trade_rows = [row for row in rows if row.would_trade]
        blocked_rows = [row for row in rows if not row.would_trade]
        rows = (trade_rows + blocked_rows)[:max_rows]

    reasons: Counter[str] = Counter()
    by_city: Counter[str] = Counter()
    decision_by_city: Counter[str] = Counter()
    side_by_city: dict[str, str] = {}
    active_variant_by_city: dict[str, str] = {}
    for row in rows:
        decision_by_city[row.city_slug] += 1
        reasons["would_trade" if row.would_trade else str(row.reason or "blocked")] += 1
        if not row.would_trade:
            continue
        by_city[row.city_slug] += 1
        parts = row.segment_key.split("|")
        if len(parts) >= 5:
            side_by_city[row.city_slug] = parts[3]
            active_variant_by_city[row.city_slug] = parts[4]

    async with session_factory() as session, session.begin():
        await session.execute(
            delete(StrategyShadowDecision).where(StrategyShadowDecision.policy_name == policy_name)
        )
        session.add_all(rows)

    async with session_factory() as session:
        counts_after = await _artifact_counts(session)

    would_trade = reasons.get("would_trade", 0)
    covered_cities = sorted(city for city in selected_cities if decision_by_city.get(city, 0) > 0)
    active_trade_cities = sorted(city for city in selected_cities if by_city.get(city, 0) > 0)
    primary_active_trade_cities = sorted(
        city for city in approved_by_city if by_city.get(city, 0) > 0
    )
    fallback_active_trade_cities = [
        city for city in active_trade_cities if city not in approved_by_city
    ]
    status = (
        "SHADOW_READY_FOR_REVIEW"
        if would_trade >= 50 and len(active_trade_cities) >= 3
        else "SHADOW_RUNNING"
        if rows
        else "NO_FORWARD_CANDIDATES"
    )
    return {
        "status": status,
        "run_at": run_at.isoformat(),
        "policy_name": policy_name,
        "source": "high-reward-city-hunt",
        "hunt_run_id": hunt.id,
        "selected_cities": selected_cities,
        "approved_cities": sorted(approved_by_city),
        "approved_all_cities": sorted(approved_all_by_city),
        "covered_cities": covered_cities,
        "active_trade_cities": active_trade_cities,
        "primary_active_trade_cities": primary_active_trade_cities,
        "fallback_active_trade_cities": fallback_active_trade_cities,
        "side_by_city": side_by_city,
        "active_variant_by_city": active_variant_by_city,
        "variant_candidates_by_city": {
            city: [str(item.get("variant") or "") for item in rows]
            for city, rows in sorted(variant_rows_by_city.items())
            if city in selected_cities
        },
        "decision_counts_by_city": dict(sorted(decision_by_city.items())),
        "would_trade_counts_by_city": dict(sorted(by_city.items())),
        "shadow_window_by_city": city_windows,
        "decisions_written": len(rows),
        "would_trade": would_trade,
        "reason_counts": dict(sorted(reasons.items())),
        "counts_before": counts_before,
        "counts_after": counts_after,
        "trading_artifacts_unchanged": counts_before == counts_after,
    }


async def generate_shadow_decisions(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
    policy_name: str = DEFAULT_SHADOW_POLICY,
    limit: int = 500,
    source: ShadowSource = "strategy-discovery",
) -> dict[str, object]:
    if source == "high-reward-city-hunt":
        return await _generate_high_reward_shadow_decisions(
            session_factory,
            settings,
            cities=cities,
            days=days,
            policy_name=policy_name,
            limit=limit,
        )

    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    async with session_factory() as session:
        counts_before = await _artifact_counts(session)
        discovery = await _latest_discovery(session)
        if discovery is None:
            return {
                "status": "NO_DISCOVERY_RUN",
                "policy_name": policy_name,
                "decisions_written": 0,
                "counts_before": counts_before,
                "counts_after": counts_before,
            }
        summary = _json_loads_dict(discovery.summary_json)
        discovery_version = str(summary.get("discovery_version") or "v4")
        selected_cities = cities or [str(city) for city in json.loads(discovery.cities_json)]
        variant = _variant_by_name(discovery_version, _variant_name_from_discovery(discovery))
        if variant is None:
            return {
                "status": "NO_VARIANT",
                "policy_name": policy_name,
                "discovery_run_id": discovery.id,
                "decisions_written": 0,
                "counts_before": counts_before,
                "counts_after": counts_before,
            }

        run_settings = settings.model_copy(
            update={"cities": selected_cities, "validation_history_days": history_days}
        )
        candidates, _, _, _, _ = await _historical_candidates(session, run_settings)

    if not candidates:
        async with session_factory() as session:
            counts_after = await _artifact_counts(session)
        return {
            "status": "NO_CANDIDATES",
            "policy_name": policy_name,
            "discovery_run_id": discovery.id,
            "decisions_written": 0,
            "counts_before": counts_before,
            "counts_after": counts_after,
        }

    max_target = max(candidate.target_date for candidate in candidates)
    shadow_start = max_target - timedelta(days=30)
    train = [candidate for candidate in candidates if candidate.target_date < shadow_start]
    shadow = [candidate for candidate in candidates if candidate.target_date >= shadow_start]
    segments = {}
    for candidate in train:
        key = _variant_segment_key(candidate, variant)
        segment = segments.setdefault(key, SegmentStats(key=key))
        segment.add(candidate, settings.taker_fee_rate)

    rows: list[StrategyShadowDecision] = []
    reasons: Counter[str] = Counter()
    for candidate in shadow[: max(limit, 0)]:
        key = _variant_segment_key(candidate, variant)
        segment = segments.get(key)
        reason = _reason(candidate, segment, variant, settings.taker_fee_rate)
        calibrated = (
            _calibrated_probability(candidate, segment, variant)
            if segment is not None
            else candidate.raw_prob
        )
        price = _decision_price(candidate, variant)
        edge = net_edge(calibrated, price, settings.taker_fee_rate)
        reasons[str(reason or "would_trade")] += 1
        rows.append(
            StrategyShadowDecision(
                ts=candidate.ts,
                policy_name=policy_name,
                market_id=candidate.market_id,
                event_id=candidate.event_id,
                city_slug=candidate.city_slug,
                target_date=candidate.target_date,
                raw_prob=candidate.raw_prob,
                calibrated_prob=calibrated,
                market_price=price,
                edge_net=edge,
                reason=reason,
                would_trade=reason is None,
                segment_key=key,
            )
        )

    async with session_factory() as session, session.begin():
        await session.execute(
            delete(StrategyShadowDecision).where(StrategyShadowDecision.policy_name == policy_name)
        )
        session.add_all(rows)

    async with session_factory() as session:
        counts_after = await _artifact_counts(session)

    return {
        "status": "OK",
        "run_at": run_at.isoformat(),
        "policy_name": policy_name,
        "discovery_run_id": discovery.id,
        "variant_name": getattr(variant, "name", None),
        "family": getattr(variant, "family", None),
        "side": getattr(variant, "side", None),
        "selected_cities": selected_cities,
        "shadow_start": shadow_start.isoformat(),
        "decisions_written": len(rows),
        "would_trade": reasons.get("would_trade", 0),
        "reason_counts": dict(sorted(reasons.items())),
        "counts_before": counts_before,
        "counts_after": counts_after,
        "trading_artifacts_unchanged": counts_before == counts_after,
    }


async def run(
    *,
    cities: list[str] | None = None,
    days: int | None = None,
    policy_name: str = DEFAULT_SHADOW_POLICY,
    limit: int = 500,
    source: ShadowSource = "strategy-discovery",
) -> dict[str, object]:
    settings = get_settings()
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_shadow_decisions(
            session_factory,
            settings,
            cities=cities,
            days=days,
            policy_name=policy_name,
            limit=limit,
            source=source,
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate diagnostic strategy shadow decisions.")
    parser.add_argument("--cities", help="Comma-separated city slugs.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--policy-name", default=DEFAULT_SHADOW_POLICY)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument(
        "--source",
        choices=("strategy-discovery", "high-reward-city-hunt"),
        default="strategy-discovery",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    payload = asyncio.run(
        run(
            cities=parse_cities(args.cities),
            days=args.days,
            policy_name=args.policy_name,
            limit=args.limit,
            source=args.source,
        )
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"strategy shadow status={payload['status']} "
            f"decisions={payload['decisions_written']}"
        )


if __name__ == "__main__":
    main()
