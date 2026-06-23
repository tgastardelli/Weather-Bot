"""Runtime evaluator for the high-reward repair v5 policy.

The historical hunt lives under ``analysis``; this module keeps the runtime
decision small and paper-safe so the strategy engine does not import analysis
code.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import City, StrategyRepairRun
from app.strategy.edge import gross_edge, net_edge

ZERO = Decimal("0")
ONE = Decimal("1")
PRICE_PRECISION = Decimal("0.00001")
VARIANT_PATTERN = re.compile(
    r"^(?P<family>cheap_tail_yes|cheap_tail_no)_(?P<side>yes|no)_"
    r"pxlte(?P<price>\d+_\d+)_delta(?P<delta>\d+_\d+)$"
)

HighRewardSide = Literal["YES", "NO"]


@dataclass(frozen=True)
class HighRewardRuntimePolicy:
    policy_name: str
    active_cities: frozenset[str]
    side_by_city: dict[str, HighRewardSide]
    variant_by_city: dict[str, str]
    family_by_city: dict[str, str]


@dataclass(frozen=True)
class HighRewardDecision:
    policy_name: str
    side: HighRewardSide
    model_prob: float
    market_price: Decimal
    edge_gross: Decimal
    edge_net: Decimal
    segment_key: str
    eligible: bool
    reason: str | None


@dataclass(frozen=True)
class HighRewardVariantConstraints:
    family: str
    side: HighRewardSide
    max_price: Decimal | None
    min_delta: Decimal


def _parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _string_map(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if value is not None}


def _policy_name(row: StrategyRepairRun) -> str | None:
    best = _parse_json(row.best_variant_json)
    value = best.get("policy_name") or best.get("name")
    return value if isinstance(value, str) else None


async def latest_high_reward_policy(
    session: AsyncSession, requested_policy_prefix: str = "repair_v5"
) -> HighRewardRuntimePolicy | None:
    """Load the latest promising high-reward V5 policy for runtime scanning."""

    rows = (
        await session.execute(
            select(StrategyRepairRun)
            .where(StrategyRepairRun.status == "PROMISING")
            .order_by(StrategyRepairRun.run_at.desc(), StrategyRepairRun.id.desc())
            .limit(20)
        )
    ).scalars().all()
    for row in rows:
        best = _parse_json(row.best_variant_json)
        policy_name = _policy_name(row)
        if policy_name is None or not policy_name.startswith(requested_policy_prefix):
            continue
        if best.get("policy_version") != "repair_v5_high_reward":
            continue
        side_by_city = _string_map(best.get("side_by_city"))
        variant_by_city = _string_map(best.get("variant_by_city"))
        family_by_city = _string_map(best.get("family_by_city"))
        active = best.get("active_cities")
        active_cities = (
            frozenset(str(city) for city in active if city is not None)
            if isinstance(active, list)
            else frozenset(side_by_city)
        )
        if not active_cities:
            continue
        if any(side not in {"YES", "NO"} for side in side_by_city.values()):
            continue
        return HighRewardRuntimePolicy(
            policy_name=policy_name,
            active_cities=active_cities,
            side_by_city={
                city: cast(HighRewardSide, side)
                for city, side in side_by_city.items()
                if side in {"YES", "NO"}
            },
            variant_by_city=variant_by_city,
            family_by_city=family_by_city,
        )
    return None


def no_price_from_yes_bid(yes_bid: Decimal | None) -> Decimal | None:
    if yes_bid is None:
        return None
    price = (ONE - yes_bid).quantize(PRICE_PRECISION)
    return price if ZERO < price < ONE else None


async def evaluate_high_reward_policy(
    _session: AsyncSession,
    settings: Settings,
    *,
    policy: HighRewardRuntimePolicy,
    city: City,
    bucket_kind: str,
    target_date: date,
    raw_yes_prob: float,
    yes_ask: Decimal | None,
    yes_bid: Decimal | None,
) -> HighRewardDecision:
    """Evaluate the approved high-reward V5 city/side/variant decision."""

    side = policy.side_by_city.get(city.slug)
    variant = policy.variant_by_city.get(city.slug)
    family = policy.family_by_city.get(city.slug, "unknown")
    month = f"month-{target_date.month:02d}"
    segment_key = (
        f"repair_v5_high_reward|{city.slug}|{family}|{side or 'UNKNOWN'}|"
        f"{variant or 'missing_variant'}|{bucket_kind}|{month}"
    )
    if city.slug not in policy.active_cities or side is None or variant is None:
        return _decision(
            policy,
            side="YES",
            model_prob=raw_yes_prob,
            market_price=yes_ask or ZERO,
            segment_key=segment_key,
            eligible=False,
            reason="city_not_in_high_reward_policy",
            settings=settings,
        )
    if city.needs_review:
        return _decision(
            policy,
            side=side,
            model_prob=_side_probability(raw_yes_prob, side),
            market_price=_side_price(side, yes_ask, yes_bid) or ZERO,
            segment_key=segment_key,
            eligible=False,
            reason="city_needs_review",
            settings=settings,
        )

    price = _side_price(side, yes_ask, yes_bid)
    if price is None:
        return _decision(
            policy,
            side=side,
            model_prob=_side_probability(raw_yes_prob, side),
            market_price=ZERO,
            segment_key=segment_key,
            eligible=False,
            reason="missing_runtime_price",
            settings=settings,
        )
    model_prob = _side_probability(raw_yes_prob, side)
    reason = _variant_reject_reason(
        variant=variant,
        side=side,
        raw_yes_prob=raw_yes_prob,
        decision_price=price,
        model_prob=model_prob,
    )
    if reason is not None:
        return _decision(
            policy,
            side=side,
            model_prob=model_prob,
            market_price=price,
            segment_key=segment_key,
            eligible=False,
            reason=reason,
            settings=settings,
        )
    edge = net_edge(model_prob, price, settings.taker_fee_rate)
    if edge < settings.min_edge_net:
        return _decision(
            policy,
            side=side,
            model_prob=model_prob,
            market_price=price,
            segment_key=segment_key,
            eligible=False,
            reason="min_edge_net",
            settings=settings,
        )
    return _decision(
        policy,
        side=side,
        model_prob=model_prob,
        market_price=price,
        segment_key=segment_key,
        eligible=True,
        reason=None,
        settings=settings,
    )


def _decision(
    policy: HighRewardRuntimePolicy,
    *,
    side: HighRewardSide,
    model_prob: float,
    market_price: Decimal,
    segment_key: str,
    eligible: bool,
    reason: str | None,
    settings: Settings,
) -> HighRewardDecision:
    return HighRewardDecision(
        policy_name=policy.policy_name,
        side=side,
        model_prob=model_prob,
        market_price=market_price,
        edge_gross=gross_edge(model_prob, market_price) if market_price > ZERO else ZERO,
        edge_net=(
            net_edge(model_prob, market_price, settings.taker_fee_rate)
            if market_price > ZERO
            else ZERO
        ),
        segment_key=segment_key,
        eligible=eligible,
        reason=reason,
    )


def _side_probability(raw_yes_prob: float, side: HighRewardSide) -> float:
    return 1.0 - raw_yes_prob if side == "NO" else raw_yes_prob


def _side_price(
    side: HighRewardSide, yes_ask: Decimal | None, yes_bid: Decimal | None
) -> Decimal | None:
    if side == "NO":
        return no_price_from_yes_bid(yes_bid)
    if yes_ask is None or not (ZERO < yes_ask < ONE):
        return None
    return yes_ask


def _variant_reject_reason(
    *,
    variant: str,
    side: HighRewardSide,
    raw_yes_prob: float,
    decision_price: Decimal,
    model_prob: float,
) -> str | None:
    parsed = _parse_variant(variant)
    if parsed is None:
        return "unsupported_high_reward_variant"
    variant_family, variant_side, max_price, min_delta = parsed
    if side != variant_side:
        return "side_mismatch"
    if max_price is not None and decision_price > max_price:
        return "variant_price_filter"
    if Decimal(str(model_prob)) - decision_price < min_delta:
        return "variant_probability_delta"
    if variant_family == "cheap_tail_yes" and raw_yes_prob < 0.10:
        return "cheap_tail_yes_raw_prob_floor"
    if variant_family == "cheap_tail_no" and raw_yes_prob > 0.90:
        return "cheap_tail_no_raw_prob_ceiling"
    return None


def variant_constraints(variant: str) -> HighRewardVariantConstraints | None:
    parsed = _parse_variant(variant)
    if parsed is None:
        return None
    family, side, max_price, min_delta = parsed
    return HighRewardVariantConstraints(
        family=family,
        side=side,
        max_price=max_price,
        min_delta=min_delta,
    )


def _parse_variant(
    variant: str,
) -> tuple[str, HighRewardSide, Decimal | None, Decimal] | None:
    match = VARIANT_PATTERN.match(variant)
    if match is None:
        return None
    side_part = match.group("side").upper()
    if side_part not in {"YES", "NO"}:
        return None
    return (
        match.group("family"),
        cast(HighRewardSide, side_part),
        _decimal_from_variant_suffix(match.group("price")),
        _decimal_from_variant_suffix(match.group("delta")),
    )


def _decimal_from_variant_suffix(raw: str) -> Decimal:
    return Decimal(raw.replace("_", "."))
