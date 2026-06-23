"""Shared repaired-policy decision rules.

The historical repair backtest and runtime signal scanner both call these
helpers so a policy that passes history is evaluated the same way in paper.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.strategy.edge import net_edge
from app.strategy.probability_calibration import (
    DEFAULT_SMOOTHING_PRIOR,
    ProbabilityContext,
    edge_bucket,
)


@dataclass(frozen=True)
class RepairPolicyParams:
    policy_name: str
    policy_version: str
    alpha: float
    probability_cap: float
    min_samples: int
    min_edge_net: Decimal
    segment_scope: str = "fallback"
    price_floor: Decimal | None = None


@dataclass(frozen=True)
class RepairSegmentStats:
    segment_key: str
    n: int
    wins: int
    observed_rate: float
    brier_delta: float | None
    pnl: Decimal

    @property
    def avg_cost_per_share(self) -> Decimal | None:
        if self.n <= 0:
            return None
        total_settlement = Decimal(self.wins)
        return ((total_settlement - self.pnl) / Decimal(self.n)).quantize(Decimal("0.00001"))


@dataclass(frozen=True)
class RepairPolicyEvaluation:
    policy_name: str
    raw_prob: float
    calibrated_prob: float
    segment_key: str | None
    n_samples: int
    eligible: bool
    reason: str | None
    edge_net: Decimal


def market_aware_probability(
    *,
    market_price: Decimal,
    wins: int,
    n: int,
    global_rate: float,
    alpha: float,
    probability_cap: float,
    smoothing_prior: int = DEFAULT_SMOOTHING_PRIOR,
) -> float:
    p_smoothed = (wins + (smoothing_prior * global_rate)) / (n + smoothing_prior)
    anchored = float(market_price) + alpha * (p_smoothed - float(market_price))
    return min(max(anchored, 0.0), probability_cap)


def evaluate_repair_policy(
    *,
    params: RepairPolicyParams,
    context: ProbabilityContext,
    fee_rate: Decimal,
    segment: RepairSegmentStats | None,
    global_rate: float,
) -> RepairPolicyEvaluation:
    if segment is None:
        return _decision(
            params=params,
            context=context,
            calibrated_prob=context.model_prob,
            segment_key=None,
            n_samples=0,
            eligible=False,
            reason="no_eligible_segment",
            fee_rate=fee_rate,
        )

    calibrated_prob = market_aware_probability(
        market_price=context.market_price,
        wins=segment.wins,
        n=segment.n,
        global_rate=global_rate,
        alpha=params.alpha,
        probability_cap=params.probability_cap,
    )

    reason = _ineligible_reason(params, context, segment, calibrated_prob, fee_rate)
    if reason is not None:
        return _decision(
            params=params,
            context=context,
            calibrated_prob=calibrated_prob,
            segment_key=segment.segment_key,
            n_samples=segment.n,
            eligible=False,
            reason=reason,
            fee_rate=fee_rate,
        )

    edge_net = net_edge(calibrated_prob, context.market_price, fee_rate)
    if edge_net < params.min_edge_net:
        return _decision(
            params=params,
            context=context,
            calibrated_prob=calibrated_prob,
            segment_key=segment.segment_key,
            n_samples=segment.n,
            eligible=False,
            reason="min_edge_net",
            fee_rate=fee_rate,
        )

    return RepairPolicyEvaluation(
        policy_name=params.policy_name,
        raw_prob=context.model_prob,
        calibrated_prob=calibrated_prob,
        segment_key=segment.segment_key,
        n_samples=segment.n,
        eligible=True,
        reason=None,
        edge_net=edge_net,
    )


def _ineligible_reason(
    params: RepairPolicyParams,
    context: ProbabilityContext,
    segment: RepairSegmentStats,
    calibrated_prob: float,
    fee_rate: Decimal,
) -> str | None:
    if params.segment_scope == "specific_only" and not segment.segment_key.startswith(
        "specific|"
    ):
        return "non_specific_segment"
    if segment.n < params.min_samples:
        return "min_samples"
    if segment.brier_delta is None or segment.brier_delta <= 0:
        return "segment_brier"
    if segment.pnl <= Decimal("0"):
        return "segment_pnl"
    avg_cost = segment.avg_cost_per_share
    if avg_cost is None or Decimal(str(segment.observed_rate)) <= avg_cost:
        return "segment_cost"
    if params.price_floor is not None and context.market_price < params.price_floor:
        return "low_price_diagnostic_only"
    if (
        params.policy_version in {"repair_v3", "repair_v4"}
        and edge_bucket(net_edge(context.model_prob, context.market_price, fee_rate)) == "0.75+"
        and context.model_prob - calibrated_prob > 0.15
    ):
        return "extreme_edge_overconfidence"
    return None


def _decision(
    *,
    params: RepairPolicyParams,
    context: ProbabilityContext,
    calibrated_prob: float,
    segment_key: str | None,
    n_samples: int,
    eligible: bool,
    reason: str | None,
    fee_rate: Decimal,
) -> RepairPolicyEvaluation:
    return RepairPolicyEvaluation(
        policy_name=params.policy_name,
        raw_prob=context.model_prob,
        calibrated_prob=calibrated_prob,
        segment_key=segment_key,
        n_samples=n_samples,
        eligible=eligible,
        reason=reason,
        edge_net=net_edge(calibrated_prob, context.market_price, fee_rate),
    )
