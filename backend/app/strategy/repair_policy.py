"""Runtime strategy policy application for repaired probability models."""

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    Signal,
    SignalStrategyAudit,
    StrategyCalibrationSegment,
    StrategyRepairRun,
)
from app.strategy.probability_calibration import (
    ProbabilityContext,
    calibration_keys,
    segment_key,
)
from app.strategy.repair_decision import (
    RepairPolicyParams,
    RepairSegmentStats,
    evaluate_repair_policy,
)


@dataclass(frozen=True)
class StrategyPolicyDecision:
    policy_name: str
    raw_prob: float
    model_prob: float
    segment_key: str | None
    n_samples: int
    eligible: bool
    reason: str | None


def _parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _float_param(value: object, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _int_param(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _decimal_param(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except ValueError:
        return None


def _policy_name(row: StrategyRepairRun) -> str | None:
    payload = _parse_json(row.best_variant_json)
    value = payload.get("policy_name") or payload.get("name")
    return value if isinstance(value, str) else None


async def apply_strategy_policy(
    session: AsyncSession,
    settings: Settings,
    context: ProbabilityContext,
) -> StrategyPolicyDecision:
    if settings.strategy_policy_mode == "raw":
        return StrategyPolicyDecision(
            policy_name="raw",
            raw_prob=context.model_prob,
            model_prob=context.model_prob,
            segment_key=None,
            n_samples=0,
            eligible=True,
            reason=None,
        )

    requested_policy_prefix = settings.strategy_policy_mode
    repair_runs = (
        await session.execute(
            select(StrategyRepairRun)
            .where(StrategyRepairRun.status == "PROMISING")
            .order_by(StrategyRepairRun.run_at.desc(), StrategyRepairRun.id.desc())
            .limit(10)
        )
    ).scalars().all()
    repair_run = next(
        (
            row
            for row in repair_runs
            if (_policy_name(row) or "").startswith(requested_policy_prefix)
        ),
        None,
    )
    if repair_run is None:
        return StrategyPolicyDecision(
            policy_name=requested_policy_prefix,
            raw_prob=context.model_prob,
            model_prob=context.model_prob,
            segment_key=None,
            n_samples=0,
            eligible=False,
            reason="repair_policy_not_promising",
        )

    best = _parse_json(repair_run.best_variant_json)
    policy_name = str(best.get("policy_name") or best.get("name") or "repair_v2")
    alpha = _float_param(best.get("alpha"), 1.0)
    cap = _float_param(best.get("probability_cap") or best.get("cap"), 0.80)
    min_samples = _int_param(best.get("min_calibration_samples"), 50)
    segment_scope = str(best.get("segment_scope") or "fallback")
    raw_keys = calibration_keys(context)
    if requested_policy_prefix in {"repair_v3", "repair_v4"} or segment_scope == "specific_only":
        raw_keys = raw_keys[:1]
    keys = [segment_key(key) for key in raw_keys]
    lookup_keys = [*keys]
    if "global" not in lookup_keys:
        lookup_keys.append("global")
    rows = (
        await session.execute(
            select(StrategyCalibrationSegment).where(
                StrategyCalibrationSegment.run_id == repair_run.id,
                StrategyCalibrationSegment.policy_name == policy_name,
                StrategyCalibrationSegment.segment_key.in_(lookup_keys),
            )
        )
    ).scalars().all()
    by_key = {row.segment_key: row for row in rows}
    selected = next(
        (
            by_key[key]
            for key in keys
            if key in by_key and by_key[key].eligible and by_key[key].n >= min_samples
        ),
        None,
    )
    if selected is None:
        return StrategyPolicyDecision(
            policy_name=policy_name,
            raw_prob=context.model_prob,
            model_prob=context.model_prob,
            segment_key=None,
            n_samples=0,
            eligible=False,
            reason="no_eligible_segment",
        )
    global_segment = by_key.get("global")
    global_rate = (
        global_segment.observed_rate
        if global_segment is not None and global_segment.n > 0
        else selected.observed_rate
    )
    min_edge_net = _decimal_param(best.get("min_edge_net")) or settings.min_edge_net
    price_floor = _decimal_param(best.get("price_floor"))
    params = RepairPolicyParams(
        policy_name=policy_name,
        policy_version=requested_policy_prefix,
        alpha=alpha,
        probability_cap=cap,
        min_samples=min_samples,
        min_edge_net=min_edge_net,
        segment_scope=segment_scope,
        price_floor=price_floor,
    )
    decision = evaluate_repair_policy(
        params=params,
        context=context,
        fee_rate=settings.taker_fee_rate,
        segment=RepairSegmentStats(
            segment_key=selected.segment_key,
            n=selected.n,
            wins=selected.wins,
            observed_rate=selected.observed_rate,
            brier_delta=selected.brier_delta,
            pnl=selected.pnl,
        ),
        global_rate=global_rate,
    )
    return StrategyPolicyDecision(
        policy_name=decision.policy_name,
        raw_prob=decision.raw_prob,
        model_prob=decision.calibrated_prob,
        segment_key=decision.segment_key,
        n_samples=decision.n_samples,
        eligible=decision.eligible,
        reason=decision.reason,
    )


def add_signal_strategy_audit(
    session: AsyncSession,
    signal: Signal,
    decision: StrategyPolicyDecision,
) -> None:
    session.add(
        SignalStrategyAudit(
            signal_id=signal.id,
            ts=signal.ts,
            policy_name=decision.policy_name,
            segment_key=decision.segment_key,
            raw_model_prob=decision.raw_prob,
            calibrated_model_prob=decision.model_prob,
            n_samples=decision.n_samples,
            eligible=decision.eligible,
            reason=decision.reason,
        )
    )
