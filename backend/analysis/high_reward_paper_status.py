"""Operational paper status for the high-reward repair v5 fast lane.

This report is derived from current paper signals/orders/fills. It does not
create signals, paper orders, paper fills, credentials, approvals, or live
orders.
"""

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.operational_quarantine import quarantine_payloads
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    City,
    Event,
    Market,
    MarketPriceSnapshot,
    PaperFill,
    PaperOrder,
    Signal,
    SignalStrategyAudit,
    StrategyRepairRun,
)
from app.db.session import create_engine, create_session_factory
from app.execution.paper import taker_fee
from app.strategy.edge import cost_per_share
from app.strategy.engine import (
    SIGNAL_DEDUPE_WINDOW,
    SIGNAL_EDGE_DELTA,
    event_model_probs,
)
from app.strategy.high_reward_policy import (
    evaluate_high_reward_policy,
    latest_high_reward_policy,
    variant_constraints,
)
from app.strategy.sizing import kelly_stake

MONEY_PRECISION = Decimal("0.00001")
MIN_PAYOFF_RATIO = Decimal("3.00")
DEFAULT_POLICY_NAME = "repair_v5_high_reward_v1"
TARGET_CITIES = ("atlanta", "seattle", "toronto")


def _json_dumps(payload: object) -> str:
    return json.dumps(payload, default=str, sort_keys=True)


def _parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _repair_policy_name(row: StrategyRepairRun | None) -> str | None:
    if row is None:
        return None
    payload = _parse_json(row.best_variant_json)
    value = payload.get("policy_name") or payload.get("name")
    return value if isinstance(value, str) else None


async def _latest_high_reward_repair(session: AsyncSession) -> StrategyRepairRun | None:
    rows = (
        await session.execute(
            select(StrategyRepairRun)
            .where(StrategyRepairRun.status == "PROMISING")
            .order_by(StrategyRepairRun.run_at.desc(), StrategyRepairRun.id.desc())
            .limit(20)
        )
    ).scalars().all()
    for row in rows:
        payload = _parse_json(row.best_variant_json)
        if payload.get("policy_version") == "repair_v5_high_reward":
            return row
    return None


def _string_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def _expected_settlement_price(market: Market, token_id: str) -> Decimal:
    if market.winner is None:
        return Decimal("0")
    if token_id == market.yes_token_id:
        return Decimal("1") if market.winner else Decimal("0")
    if token_id == market.no_token_id:
        return Decimal("0") if market.winner else Decimal("1")
    return Decimal("0")


def _outcome_side(market: Market, token_id: str) -> str:
    if token_id == market.yes_token_id:
        return "YES"
    if token_id == market.no_token_id:
        return "NO"
    return "UNKNOWN"


def _payoff_metrics(pnls: list[Decimal]) -> dict[str, str | int | float | None]:
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl <= 0]
    average_win = (
        (sum(wins, Decimal("0")) / Decimal(len(wins))).quantize(MONEY_PRECISION)
        if wins
        else None
    )
    average_loss = (
        (abs(sum(losses, Decimal("0"))) / Decimal(len(losses))).quantize(MONEY_PRECISION)
        if losses
        else None
    )
    payoff_ratio = (
        (average_win / average_loss).quantize(Decimal("0.0001"))
        if average_win is not None and average_loss is not None and average_loss != Decimal("0")
        else None
    )
    total_pnl = sum(pnls, Decimal("0")).quantize(MONEY_PRECISION)
    return {
        "n": len(pnls),
        "wins": len(wins),
        "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "average_win": str(average_win) if average_win is not None else None,
        "average_loss": str(average_loss) if average_loss is not None else None,
        "payoff_ratio": str(payoff_ratio) if payoff_ratio is not None else None,
        "total_pnl": str(total_pnl),
    }


def _max_loss_streak(pnls: list[Decimal]) -> int:
    current = 0
    longest = 0
    for pnl in pnls:
        if pnl > 0:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def _next_operational_action(
    *,
    status: str,
    blockers: list[str],
    missing_coverage: list[str],
    pending_targets: list[dict[str, object]],
    gate_progress: dict[str, object],
    has_resolved_pnl: bool,
    total_pnl: Decimal,
    payoff_gate_passed: bool,
) -> dict[str, object]:
    if status == "PAPER_NOT_STARTED":
        return {
            "code": "start_fast_lane_scheduler",
            "severity": "info",
            "detail": "Start paper collectors/scanner with STRATEGY_POLICY_MODE=repair_v5.",
        }
    if blockers:
        return {
            "code": "investigate_blockers",
            "severity": "danger",
            "detail": "Paper fast lane has reconciliation or policy blockers.",
            "blockers": blockers,
        }
    if missing_coverage:
        return {
            "code": "continue_scheduler_until_coverage",
            "severity": "warning",
            "detail": "At least one approved city has no paper entry fill yet.",
            "missing_coverage": missing_coverage,
            "pending_targets": len(pending_targets),
        }
    if pending_targets:
        return {
            "code": "wait_for_pending_settlements",
            "severity": "info",
            "detail": "Keep the scheduler running until pending targets resolve.",
            "pending_targets": len(pending_targets),
        }
    if not bool(gate_progress.get("sample_gate_passed")):
        return {
            "code": "continue_until_sample_gate",
            "severity": "info",
            "detail": "Sample gate is still open: need 30 forward days or 50 resolved fills.",
            "remaining_forward_days": gate_progress.get("remaining_forward_days"),
            "remaining_resolved_fills": gate_progress.get("remaining_resolved_fills"),
        }
    if not has_resolved_pnl:
        return {
            "code": "wait_for_first_settlement",
            "severity": "info",
            "detail": "Entry fills exist, but no resolved PnL is available yet.",
        }
    if total_pnl <= 0:
        return {
            "code": "paper_pnl_gate_failed",
            "severity": "warning",
            "detail": "Sample is available, but paper PnL is not positive.",
            "paper_pnl": str(total_pnl),
        }
    if not payoff_gate_passed:
        return {
            "code": "payoff_gate_failed",
            "severity": "warning",
            "detail": "Sample is available, but payoff ratio is below the high-reward gate.",
            "required_payoff_ratio": str(MIN_PAYOFF_RATIO),
        }
    if status == "PAPER_READY_FOR_MEASUREMENT":
        return {
            "code": "run_measurement_review",
            "severity": "success",
            "detail": "Paper fast lane is ready for measurement review.",
        }
    return {
        "code": "continue_scheduler",
        "severity": "info",
        "detail": "Keep the paper scheduler running and monitor measurement gates.",
    }


def _int_value(value: object) -> int:
    return int(value) if isinstance(value, int) else 0


def _decimal_value(value: object, default: Decimal = Decimal("-999999")) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _diagnostic_sample_sort_key(
    sample: dict[str, object],
) -> tuple[int, int, Decimal, Decimal, Decimal, Decimal]:
    reason = str(sample.get("reason") or "")
    actionability = str(sample.get("actionability_reason") or "")
    status_score = 0
    if actionability == "ready_to_signal":
        status_score = 4
    elif actionability == "recent_duplicate":
        status_score = 3
    elif reason == "eligible":
        status_score = 2

    price_gap = _decimal_value(sample.get("price_to_variant_max"))
    delta_gap = _decimal_value(sample.get("probability_delta_to_min"))
    edge_net = _decimal_value(sample.get("edge_net"), Decimal("0"))
    binding_gap = min(price_gap, delta_gap)
    price_score = 0 if reason == "missing_runtime_price" else 1
    return (status_score, price_score, binding_gap, price_gap, delta_gap, edge_net)


def _paper_forward_gate_progress(
    *,
    run_at: datetime,
    active_cities: list[str],
    city_rows: dict[str, dict[str, object]],
    fills: Sequence[PaperFill],
) -> dict[str, object]:
    entry_fills = [fill for fill in fills if fill.liquidity == "TAKER"]
    settlement_fills = [fill for fill in fills if fill.liquidity == "SETTLEMENT"]
    first_entry_at = min((fill.ts for fill in entry_fills), default=None)
    last_entry_at = max((fill.ts for fill in entry_fills), default=None)
    forward_days = (
        (run_at - first_entry_at).total_seconds() / 86400.0
        if first_entry_at is not None
        else 0.0
    )
    cities_with_entry_fills = [
        city
        for city in active_cities
        if _int_value(city_rows.get(city, {}).get("entry_fills", 0)) > 0
    ]
    sample_gate_passed = forward_days >= 30.0 or len(settlement_fills) >= 50
    coverage_gate_passed = len(cities_with_entry_fills) == len(active_cities)
    missing_coverage = [city for city in active_cities if city not in cities_with_entry_fills]
    return {
        "forward_started_at": first_entry_at.isoformat() if first_entry_at else None,
        "last_entry_fill_at": last_entry_at.isoformat() if last_entry_at else None,
        "forward_days_elapsed": round(forward_days, 4),
        "required_forward_days": 30,
        "remaining_forward_days": round(max(30.0 - forward_days, 0.0), 4),
        "resolved_fills": len(settlement_fills),
        "required_resolved_fills": 50,
        "remaining_resolved_fills": max(50 - len(settlement_fills), 0),
        "sample_gate_passed": sample_gate_passed,
        "coverage_gate_passed": coverage_gate_passed,
        "cities_with_entry_fills": cities_with_entry_fills,
        "missing_coverage": missing_coverage,
        "required_cities": active_cities,
    }


async def _latest_price_snapshot(
    session: AsyncSession, market_id: str
) -> MarketPriceSnapshot | None:
    return (
        await session.execute(
            select(MarketPriceSnapshot)
            .where(MarketPriceSnapshot.market_id == market_id)
            .order_by(MarketPriceSnapshot.ts.desc(), MarketPriceSnapshot.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _post_policy_actionability(
    session: AsyncSession,
    settings: Settings,
    *,
    market_id: str,
    now: datetime,
    model_prob: float,
    market_price: Decimal,
    edge_net: Decimal,
) -> dict[str, object]:
    last = (
        await session.execute(
            select(Signal)
            .where(
                Signal.market_id == market_id,
                Signal.profile == "max_edge",
                Signal.ts >= now - SIGNAL_DEDUPE_WINDOW,
            )
            .order_by(Signal.ts.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is not None and abs(last.edge_net - edge_net) < SIGNAL_EDGE_DELTA:
        return {"reason": "recent_duplicate", "stake": "0.00000", "exposure": "0.00000"}

    cost = cost_per_share(market_price, settings.taker_fee_rate)
    stake = kelly_stake(
        model_prob,
        cost,
        bankroll=settings.bankroll,
        kelly_multiplier=settings.kelly_fraction,
        max_stake_per_order=settings.max_stake_per_order,
    )
    if stake <= 0:
        return {"reason": "kelly_stake_zero", "stake": str(stake), "exposure": "0.00000"}

    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    stakes = (
        await session.execute(
            select(Signal.stake).where(
                Signal.market_id == market_id,
                Signal.status == "PROPOSED",
                Signal.ts >= start_of_day,
            )
        )
    ).scalars().all()
    exposure = sum(stakes, Decimal("0"))
    if exposure + stake > settings.max_exposure_per_market:
        return {
            "reason": "max_exposure_per_market",
            "stake": str(stake),
            "exposure": str(exposure),
        }
    return {"reason": "ready_to_signal", "stake": str(stake), "exposure": str(exposure)}


async def _current_candidate_diagnostics(
    session: AsyncSession,
    settings: Settings,
    *,
    active_cities: list[str],
) -> dict[str, object]:
    policy = await latest_high_reward_policy(session)
    if policy is None:
        return {
            "eligible": 0,
            "actionable": 0,
            "by_city": {},
            "reason_counts": {"missing_promising_policy": 1},
            "actionability_reason_counts": {},
            "samples": [],
        }

    now = datetime.now(UTC)
    by_city: dict[str, dict[str, object]] = {
        city: {
            "eligible": 0,
            "actionable": 0,
            "reason_counts": {},
            "actionability_reason_counts": {},
            "samples": [],
        }
        for city in active_cities
    }
    total_reasons: Counter[str] = Counter()
    total_actionability_reasons: Counter[str] = Counter()
    total_eligible = 0
    total_actionable = 0
    samples: list[dict[str, object]] = []

    events = (
        (
            await session.execute(
                select(Event)
                .where(
                    Event.city_slug.in_(active_cities),
                    Event.active.is_(True),
                    Event.closed.is_(False),
                )
                .order_by(Event.target_date.desc(), Event.id.desc())
            )
        )
        .scalars()
        .all()
    )
    for event in events:
        city_payload = by_city.setdefault(
            event.city_slug,
            {
                "eligible": 0,
                "actionable": 0,
                "reason_counts": {},
                "actionability_reason_counts": {},
                "samples": [],
            },
        )
        if event.end_date is None:
            reason = "missing_event_end_date"
            total_reasons[reason] += 1
            _increment_city_reason(city_payload, reason)
            continue
        hours_to_close = (event.end_date - now).total_seconds() / 3600.0
        if not (settings.min_hours_to_close <= hours_to_close <= settings.max_hours_to_close):
            reason = "outside_hours_to_close"
            total_reasons[reason] += 1
            _increment_city_reason(city_payload, reason)
            continue
        city = await session.get(City, event.city_slug)
        if city is None:
            reason = "missing_city"
            total_reasons[reason] += 1
            _increment_city_reason(city_payload, reason)
            continue
        markets = list(
            (
                await session.execute(
                    select(Market)
                    .where(Market.event_id == event.id, Market.closed.is_(False))
                    .order_by(Market.group_item_threshold)
                )
            )
            .scalars()
            .all()
        )
        if not markets:
            reason = "missing_active_markets"
            total_reasons[reason] += 1
            _increment_city_reason(city_payload, reason)
            continue
        probs = await event_model_probs(session, settings, event, city, markets)
        if probs is None:
            reason = "missing_ensemble"
            total_reasons[reason] += 1
            _increment_city_reason(city_payload, reason)
            continue
        for market, prob in zip(markets, probs, strict=True):
            price = await _latest_price_snapshot(session, market.id)
            decision = await evaluate_high_reward_policy(
                session,
                settings,
                policy=policy,
                city=city,
                bucket_kind=market.bucket_kind,
                target_date=event.target_date,
                raw_yes_prob=prob,
                yes_ask=price.best_ask if price is not None else None,
                yes_bid=price.best_bid if price is not None else None,
            )
            variant = policy.variant_by_city.get(event.city_slug)
            constraints = variant_constraints(variant) if variant is not None else None
            probability_delta = (
                Decimal(str(decision.model_prob)) - decision.market_price
            ).quantize(MONEY_PRECISION)
            variant_max_price = constraints.max_price if constraints is not None else None
            price_to_variant_max = (
                (variant_max_price - decision.market_price).quantize(MONEY_PRECISION)
                if variant_max_price is not None
                else None
            )
            min_probability_delta = constraints.min_delta if constraints is not None else None
            probability_delta_to_min = (
                (probability_delta - min_probability_delta).quantize(MONEY_PRECISION)
                if min_probability_delta is not None
                else None
            )
            reason = decision.reason or "eligible"
            total_reasons[reason] += 1
            _increment_city_reason(city_payload, reason)
            actionability: dict[str, object] = {
                "reason": reason,
                "stake": "0.00000",
                "exposure": "0.00000",
            }
            if decision.eligible:
                total_eligible += 1
                city_payload["eligible"] = _int_value(city_payload["eligible"]) + 1
                actionability = await _post_policy_actionability(
                    session,
                    settings,
                    market_id=market.id,
                    now=now,
                    model_prob=decision.model_prob,
                    market_price=decision.market_price,
                    edge_net=decision.edge_net,
                )
                actionability_reason = str(actionability["reason"])
                total_actionability_reasons[actionability_reason] += 1
                _increment_city_actionability_reason(city_payload, actionability_reason)
                if actionability_reason == "ready_to_signal":
                    total_actionable += 1
                    city_payload["actionable"] = _int_value(city_payload["actionable"]) + 1
            sample = {
                "city_slug": event.city_slug,
                "event_id": event.id,
                "market_id": market.id,
                "bucket": market.group_item_title,
                "side": decision.side,
                "raw_yes_prob": f"{prob:.5f}",
                "model_prob": f"{decision.model_prob:.5f}",
                "market_price": str(decision.market_price),
                "edge_net": str(decision.edge_net),
                "variant_max_price": (
                    str(variant_max_price) if variant_max_price is not None else None
                ),
                "price_to_variant_max": (
                    str(price_to_variant_max) if price_to_variant_max is not None else None
                ),
                "min_probability_delta": (
                    str(min_probability_delta) if min_probability_delta is not None else None
                ),
                "probability_delta": str(probability_delta),
                "probability_delta_to_min": (
                    str(probability_delta_to_min)
                    if probability_delta_to_min is not None
                    else None
                ),
                "reason": reason,
                "actionability_reason": actionability["reason"],
                "stake": actionability["stake"],
                "existing_exposure": actionability["exposure"],
                "hours_to_close": round(hours_to_close, 2),
            }
            city_samples = city_payload["samples"]
            if isinstance(city_samples, list):
                city_samples.append(sample)
            samples.append(sample)

    for city_payload in by_city.values():
        city_samples = city_payload.get("samples")
        if isinstance(city_samples, list):
            city_payload["samples"] = sorted(
                city_samples,
                key=_diagnostic_sample_sort_key,
                reverse=True,
            )[:5]

    return {
        "eligible": total_eligible,
        "actionable": total_actionable,
        "by_city": by_city,
        "reason_counts": dict(sorted(total_reasons.items())),
        "actionability_reason_counts": dict(sorted(total_actionability_reasons.items())),
        "samples": sorted(samples, key=_diagnostic_sample_sort_key, reverse=True)[:10],
    }


def _increment_city_reason(city_payload: dict[str, object], reason: str) -> None:
    raw_counts = city_payload.setdefault("reason_counts", {})
    if not isinstance(raw_counts, dict):
        city_payload["reason_counts"] = {reason: 1}
        return
    raw_counts[reason] = _int_value(raw_counts.get(reason, 0)) + 1


def _increment_city_actionability_reason(
    city_payload: dict[str, object], reason: str
) -> None:
    raw_counts = city_payload.setdefault("actionability_reason_counts", {})
    if not isinstance(raw_counts, dict):
        city_payload["actionability_reason_counts"] = {reason: 1}
        return
    raw_counts[reason] = _int_value(raw_counts.get(reason, 0)) + 1


async def build_high_reward_paper_status(
    session: AsyncSession,
    settings: Settings,
    *,
    policy_name: str = DEFAULT_POLICY_NAME,
) -> dict[str, object]:
    run_at = datetime.now(UTC)
    repair = await _latest_high_reward_repair(session)
    approved_policy = _repair_policy_name(repair)
    repair_payload = _parse_json(repair.best_variant_json if repair else None)
    side_by_city = _string_map(repair_payload.get("side_by_city"))
    active_cities = [
        str(city)
        for city in repair_payload.get("active_cities", TARGET_CITIES)
        if city is not None
    ]
    if not active_cities:
        active_cities = list(TARGET_CITIES)

    rows = (
        await session.execute(
            select(Signal, SignalStrategyAudit, Market, Event, City)
            .join(SignalStrategyAudit, SignalStrategyAudit.signal_id == Signal.id)
            .join(Market, Signal.market_id == Market.id)
            .join(Event, Market.event_id == Event.id)
            .join(City, Event.city_slug == City.slug)
            .where(SignalStrategyAudit.policy_name == policy_name)
            .order_by(Signal.ts, Signal.id)
        )
    ).all()
    signal_ids = [signal.id for signal, *_ in rows]
    orders = (
        (
            await session.execute(
                select(PaperOrder).where(PaperOrder.signal_id.in_(signal_ids))
                if signal_ids
                else select(PaperOrder).where(PaperOrder.id == -1)
            )
        )
        .scalars()
        .all()
    )
    fills = (
        (
            await session.execute(
                select(PaperFill).where(PaperFill.signal_id.in_(signal_ids))
                if signal_ids
                else select(PaperFill).where(PaperFill.id == -1)
            )
        )
        .scalars()
        .all()
    )
    orders_by_signal: dict[int, list[PaperOrder]] = defaultdict(list)
    for order in orders:
        orders_by_signal[order.signal_id].append(order)
    fills_by_signal: dict[int, list[PaperFill]] = defaultdict(list)
    for fill in fills:
        fills_by_signal[fill.signal_id].append(fill)

    city_rows: dict[str, dict[str, object]] = {}
    signal_pnls: list[Decimal] = []
    signal_pnls_by_city: dict[str, list[Decimal]] = defaultdict(list)
    cash_pnl_by_city: dict[str, Decimal] = defaultdict(Decimal)
    target_rows: dict[tuple[str, str, str, bool, bool | None], dict[str, object]] = {}
    blockers: list[str] = []
    reason_counts: Counter[str] = Counter()
    fee_failures = 0
    settlement_failures = 0
    policy_mismatch = approved_policy not in (None, policy_name)
    needs_review_cities: set[str] = set()
    wrong_token_signals = 0

    for city_slug in active_cities:
        city_rows[city_slug] = {
            "city_slug": city_slug,
            "side": side_by_city.get(city_slug),
            "signals": 0,
            "entry_fills": 0,
            "settlement_fills": 0,
            "rejected_orders": 0,
            "paper_pnl": "0.00000",
            "resolved_pnl": "0.00000",
            "payoff_ratio": None,
            "max_loss_streak": 0,
            "avg_slippage": None,
        }

    slippages_by_city: dict[str, list[Decimal]] = defaultdict(list)
    for signal, _audit, market, event, city in rows:
        side = _outcome_side(market, signal.token_id)
        expected_side = side_by_city.get(city.slug)
        if city.needs_review:
            needs_review_cities.add(city.slug)
        if expected_side is not None and side != expected_side:
            wrong_token_signals += 1
        row = city_rows.setdefault(
            city.slug,
            {
                "city_slug": city.slug,
                "side": expected_side or side,
                "signals": 0,
                "entry_fills": 0,
                "settlement_fills": 0,
                "rejected_orders": 0,
                "paper_pnl": "0.00000",
                "resolved_pnl": "0.00000",
                "payoff_ratio": None,
                "max_loss_streak": 0,
                "avg_slippage": None,
            },
        )
        row["signals"] = _int_value(row["signals"]) + 1
        signal_fills = fills_by_signal.get(signal.id, [])
        entry_fills = [fill for fill in signal_fills if fill.liquidity == "TAKER"]
        settlement_fills = [
            fill for fill in signal_fills if fill.liquidity == "SETTLEMENT"
        ]
        target_key = (
            city.slug,
            side,
            event.target_date.isoformat(),
            market.closed,
            market.winner,
        )
        target_row = target_rows.setdefault(
            target_key,
            {
                "city_slug": city.slug,
                "side": side,
                "target_date": event.target_date.isoformat(),
                "closed": market.closed,
                "winner": market.winner,
                "signals": 0,
                "entry_signals": 0,
                "settled_signals": 0,
                "pending_signals": 0,
                "entry_fills": 0,
                "settlement_fills": 0,
            },
        )
        target_row["signals"] = _int_value(target_row["signals"]) + 1
        if entry_fills:
            target_row["entry_signals"] = _int_value(target_row["entry_signals"]) + 1
        if settlement_fills:
            target_row["settled_signals"] = _int_value(target_row["settled_signals"]) + 1
        target_row["pending_signals"] = max(
            _int_value(target_row["entry_signals"])
            - _int_value(target_row["settled_signals"]),
            0,
        )
        target_row["entry_fills"] = _int_value(target_row["entry_fills"]) + len(entry_fills)
        target_row["settlement_fills"] = (
            _int_value(target_row["settlement_fills"]) + len(settlement_fills)
        )
        row["entry_fills"] = _int_value(row["entry_fills"]) + len(entry_fills)
        row["settlement_fills"] = _int_value(row["settlement_fills"]) + len(settlement_fills)
        row["rejected_orders"] = _int_value(row["rejected_orders"]) + sum(
            1 for order in orders_by_signal.get(signal.id, []) if order.status == "REJECTED"
        )
        for order in orders_by_signal.get(signal.id, []):
            if order.reject_reason:
                reason_counts[order.reject_reason] += 1
            if order.slippage is not None:
                slippages_by_city[city.slug].append(order.slippage)
        for fill in entry_fills:
            expected_fee = taker_fee(fill.price, fill.size, settings.taker_fee_rate)
            if fill.fee_paid != expected_fee:
                fee_failures += 1
        for fill in settlement_fills:
            expected_price = _expected_settlement_price(market, fill.token_id)
            if fill.price != expected_price:
                settlement_failures += 1
        signal_cash_pnl = sum((fill.cash_delta for fill in signal_fills), Decimal("0")).quantize(
            MONEY_PRECISION
        )
        cash_pnl_by_city[city.slug] += signal_cash_pnl
        if settlement_fills:
            signal_pnls.append(signal_cash_pnl)
            signal_pnls_by_city[city.slug].append(signal_cash_pnl)

    for city_slug, row in city_rows.items():
        pnls = signal_pnls_by_city.get(city_slug, [])
        metrics = _payoff_metrics(pnls)
        slippages = slippages_by_city.get(city_slug, [])
        row["paper_pnl"] = str(
            cash_pnl_by_city.get(city_slug, Decimal("0")).quantize(MONEY_PRECISION)
        )
        row["resolved_pnl"] = metrics["total_pnl"]
        row["payoff_ratio"] = metrics["payoff_ratio"]
        row["max_loss_streak"] = _max_loss_streak(pnls)
        row["avg_slippage"] = (
            str((sum(slippages, Decimal("0")) / Decimal(len(slippages))).quantize(MONEY_PRECISION))
            if slippages
            else None
        )

    missing_coverage = [
        city
        for city in active_cities
        if _int_value(city_rows.get(city, {}).get("entry_fills", 0)) == 0
    ]
    quarantined = quarantine_payloads(active_cities)
    if policy_mismatch:
        blockers.append("policy_mismatch")
    if needs_review_cities:
        blockers.append("city_needs_review")
    if quarantined:
        blockers.append("operational_quarantine")
    if wrong_token_signals:
        blockers.append("side_token_mismatch")
    if fee_failures:
        blockers.append("fee_reconciliation")
    if settlement_failures:
        blockers.append("settlement_reconciliation")

    gate_progress = _paper_forward_gate_progress(
        run_at=run_at,
        active_cities=active_cities,
        city_rows=city_rows,
        fills=fills,
    )
    pending_targets = sorted(
        (
            target
            for target in target_rows.values()
            if _int_value(target.get("pending_signals")) > 0
        ),
        key=lambda item: (
            str(item["target_date"]),
            str(item["city_slug"]),
            str(item["side"]),
        ),
    )

    candidate_diagnostics = await _current_candidate_diagnostics(
        session, settings, active_cities=active_cities
    )
    candidate_by_city = candidate_diagnostics.get("by_city", {})
    if isinstance(candidate_by_city, dict):
        for city_slug, row in city_rows.items():
            diagnostic = candidate_by_city.get(city_slug, {})
            row["current_candidate_diagnostics"] = (
                diagnostic if isinstance(diagnostic, dict) else {}
            )

    resolved_metrics = _payoff_metrics(signal_pnls)
    cash_pnl = sum((fill.cash_delta for fill in fills), Decimal("0")).quantize(
        MONEY_PRECISION
    )
    payoff = resolved_metrics.get("payoff_ratio")
    payoff_decimal = Decimal(str(payoff)) if payoff is not None else Decimal("0")
    total_pnl = Decimal(str(resolved_metrics["total_pnl"]))
    payoff_gate_passed = (
        payoff_decimal >= MIN_PAYOFF_RATIO
        if payoff is not None
        else bool(signal_pnls) and total_pnl > 0
    )
    if not signal_ids:
        status = "PAPER_NOT_STARTED"
    elif blockers:
        status = "PAPER_FAILED"
    elif missing_coverage or not signal_pnls:
        status = "PAPER_RUNNING"
    elif (
        total_pnl > 0
        and payoff_gate_passed
        and bool(gate_progress["sample_gate_passed"])
        and bool(gate_progress["coverage_gate_passed"])
    ):
        status = "PAPER_READY_FOR_MEASUREMENT"
    else:
        status = "PAPER_RUNNING"

    next_action = _next_operational_action(
        status=status,
        blockers=blockers,
        missing_coverage=missing_coverage,
        pending_targets=pending_targets,
        gate_progress=gate_progress,
        has_resolved_pnl=bool(signal_pnls),
        total_pnl=total_pnl,
        payoff_gate_passed=payoff_gate_passed,
    )

    return {
        "run_at": run_at.isoformat(),
        "status": status,
        "policy_name": policy_name,
        "approved_policy_name": approved_policy,
        "active_cities": active_cities,
        "side_by_city": side_by_city,
        "summary": {
            "signals": len(signal_ids),
            "orders": len(orders),
            "entry_fills": sum(1 for fill in fills if fill.liquidity == "TAKER"),
            "settlement_fills": sum(
                1 for fill in fills if fill.liquidity == "SETTLEMENT"
            ),
            "rejected_orders": sum(1 for order in orders if order.status == "REJECTED"),
            "resolved_signals": len(signal_pnls),
            "paper_pnl": str(cash_pnl),
            "resolved_pnl": resolved_metrics["total_pnl"],
            "payoff_ratio": resolved_metrics["payoff_ratio"],
            "win_rate": resolved_metrics["win_rate"],
            "max_loss_streak": _max_loss_streak(signal_pnls),
            "missing_coverage": missing_coverage,
            "reason_counts": dict(sorted(reason_counts.items())),
            "fee_failures": fee_failures,
            "settlement_failures": settlement_failures,
            "wrong_token_signals": wrong_token_signals,
            "needs_review_cities": sorted(needs_review_cities),
            "quarantine": quarantined,
            "gate_progress": gate_progress,
            "pending_targets": pending_targets,
            "next_action": next_action,
            "current_candidate_diagnostics": {
                "eligible": candidate_diagnostics.get("eligible", 0),
                "actionable": candidate_diagnostics.get("actionable", 0),
                "reason_counts": candidate_diagnostics.get("reason_counts", {}),
                "actionability_reason_counts": candidate_diagnostics.get(
                    "actionability_reason_counts", {}
                ),
                "samples": candidate_diagnostics.get("samples", []),
            },
        },
        "cities": list(city_rows.values()),
        "blockers": blockers,
        "diagnostic_only": True,
        "live_release": False,
    }


async def run(settings: Settings) -> dict[str, object]:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            return await build_high_reward_paper_status(session, settings)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Show high-reward paper fast-lane status.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = asyncio.run(run(get_settings()))
    if args.json:
        print(_json_dumps(payload))
    else:
        print(f"status={payload['status']} policy={payload['policy_name']}")


if __name__ == "__main__":
    main()
