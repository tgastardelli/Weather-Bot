"""Diagnostic strategy experiments that cannot approve live trading.

This module intentionally writes to ``strategy_experiment_runs`` instead of
``strategy_repair_runs``. Experiments can identify a validation candidate or a
shadow-paper candidate, but live readiness still depends on the strict repair
and measurement gates.
"""

import argparse
import asyncio
import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.backtest import (
    TradeResult,
    _bootstrap_metrics,
    _concentration_metrics,
    _is_recent_duplicate,
    _trade_metrics,
    _trade_result,
)
from analysis.historical_validation import (
    MIN_HISTORICAL_TRADES,
    parse_cities,
)
from analysis.strategy_repair import HistoricalCandidate, _historical_candidates
from app.config import Settings, get_settings
from app.db.models import Base, City, StrategyExperimentRun
from app.db.session import create_engine, create_session_factory
from app.strategy.edge import cost_per_share
from app.strategy.probability_calibration import (
    ProbabilityContext,
    WalkForwardMarketAwareCalibrator,
    calibration_keys,
    hours_to_close_bucket,
    price_bucket,
    probability_bucket,
    segment_key,
)
from app.strategy.repair_decision import (
    RepairPolicyParams,
    RepairSegmentStats,
    evaluate_repair_policy,
)
from app.strategy.sizing import kelly_stake

logger = logging.getLogger(__name__)

EXPERIMENT_SET = "flexible_validation_v1"
EXPERIMENT_SOURCE = "strategy_experiments_historical_price_points"
EXECUTION_PROXY = "historical_last_trade_no_book_depth"
PRICE_SAMPLING = "last_trade_per_market_per_60m_bucket"
ALPHAS = (0.02, 0.05, 0.10, 0.15)
CAPS = (0.20, 0.30, 0.40)
MIN_SAMPLES = (30, 50, 100)
MIN_EDGES = (Decimal("0.000"), Decimal("0.005"), Decimal("0.010"), Decimal("0.020"))
MODEL_PROB_BUCKETS = {"0.3-0.4", "0.4-0.5", "0.5-0.6", "0.6-0.7"}
PRICE_BUCKETS = {"0.05-0.10", "0.10-0.20", "0.20-0.40"}
HOUR_BUCKETS = {"6-12h", "12-24h", "24-48h"}
SAMPLE_LIMIT = 24
MAX_TOP_5_ABS_PNL_SHARE = Decimal("0.40")


@dataclass(frozen=True)
class ExperimentVariant:
    name: str
    alpha: float
    probability_cap: float
    min_samples: int
    min_edge_net: Decimal


@dataclass
class ModelMetricAccumulator:
    n: int = 0
    wins: int = 0
    brier_model_sum: float = 0.0
    brier_market_sum: float = 0.0
    calibrated_prob_sum: float = 0.0
    market_price_sum: Decimal = Decimal("0")

    def add(self, candidate: HistoricalCandidate, calibrated_prob: float) -> None:
        outcome = 1.0 if candidate.winner else 0.0
        self.n += 1
        self.wins += 1 if candidate.winner else 0
        self.brier_model_sum += (calibrated_prob - outcome) ** 2
        self.brier_market_sum += (float(candidate.price) - outcome) ** 2
        self.calibrated_prob_sum += calibrated_prob
        self.market_price_sum += candidate.price

    def as_payload(self) -> dict[str, object]:
        if self.n <= 0:
            return {
                "n_candidates": 0,
                "observed_rate": None,
                "avg_calibrated_prob": None,
                "avg_market_price": None,
                "brier_model": None,
                "brier_market": None,
                "brier_delta": None,
            }
        brier_model = self.brier_model_sum / self.n
        brier_market = self.brier_market_sum / self.n
        return {
            "n_candidates": self.n,
            "observed_rate": self.wins / self.n,
            "avg_calibrated_prob": self.calibrated_prob_sum / self.n,
            "avg_market_price": str(
                (self.market_price_sum / Decimal(self.n)).quantize(Decimal("0.00001"))
            ),
            "brier_model": brier_model,
            "brier_market": brier_market,
            "brier_delta": brier_market - brier_model,
        }


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True)


def _experiment_variants() -> list[ExperimentVariant]:
    variants: list[ExperimentVariant] = []
    for alpha in ALPHAS:
        for cap in CAPS:
            for min_samples in MIN_SAMPLES:
                for min_edge in MIN_EDGES:
                    variants.append(
                        ExperimentVariant(
                            name=(
                                "flex_v1"
                                f"_a{alpha:.2f}"
                                f"_n{min_samples}"
                                f"_cap{cap:.2f}"
                                f"_edge{min_edge}"
                            ).replace(".", "_"),
                            alpha=alpha,
                            probability_cap=cap,
                            min_samples=min_samples,
                            min_edge_net=min_edge,
                        )
                    )
    return variants


async def _blocked_city_slugs(session: AsyncSession, settings: Settings) -> set[str]:
    query = select(City.slug).where(City.needs_review.is_(True))
    if settings.cities is not None:
        query = query.where(City.slug.in_(settings.cities))
    return {str(row) for row in (await session.execute(query)).scalars().all()}


async def _city_quality(
    session: AsyncSession, settings: Settings
) -> tuple[bool, dict[str, object]]:
    selected = settings.cities or []
    rows = (
        await session.execute(
            select(City).where(City.slug.in_(selected)) if selected else select(City)
        )
    ).scalars().all()
    seen = {row.slug for row in rows}
    missing = sorted(set(selected) - seen)
    needs_review = sorted(row.slug for row in rows if row.needs_review)
    return not missing and not needs_review, {
        "missing_cities": missing,
        "needs_review": needs_review,
    }


def _candidate_segment_key(candidate: HistoricalCandidate) -> str:
    return segment_key(calibration_keys(_context(candidate))[0])


def _context(candidate: HistoricalCandidate) -> ProbabilityContext:
    return ProbabilityContext(
        city_slug=candidate.city_slug,
        bucket_kind=candidate.bucket_kind,
        model_prob=candidate.raw_prob,
        market_price=candidate.price,
        hours_to_close=candidate.hours_to_close,
        target_date=candidate.target_date,
    )


def _is_extreme_overconfidence(candidate: HistoricalCandidate) -> bool:
    return (
        candidate.bucket_kind == "above"
        and probability_bucket(candidate.raw_prob) == "0.9-1.0"
        and price_bucket(candidate.price) == "0.95-1.00"
    )


def _diagnostic_scope_reason(candidate: HistoricalCandidate) -> str | None:
    if _is_extreme_overconfidence(candidate):
        return "blocked_extreme_above_high_price"
    if probability_bucket(candidate.raw_prob) not in MODEL_PROB_BUCKETS:
        return "outside_model_prob_scope"
    if price_bucket(candidate.price) not in PRICE_BUCKETS:
        return "outside_price_scope"
    if hours_to_close_bucket(candidate.hours_to_close) not in HOUR_BUCKETS:
        return "outside_hours_scope"
    return None


def _segment_stats(
    calibration_segment_key: str | None,
    *,
    n: int,
    wins: int,
    observed_rate: float | None,
    brier_delta: float | None,
    pnl: Decimal,
) -> RepairSegmentStats | None:
    if calibration_segment_key is None or observed_rate is None:
        return None
    return RepairSegmentStats(
        segment_key=calibration_segment_key,
        n=n,
        wins=wins,
        observed_rate=observed_rate,
        brier_delta=brier_delta,
        pnl=pnl,
    )


def _evaluate_variant(
    candidates: list[HistoricalCandidate],
    settings: Settings,
    variant: ExperimentVariant,
    *,
    blocked_city_slugs: set[str],
) -> dict[str, object]:
    calibrator = WalkForwardMarketAwareCalibrator(
        min_samples=variant.min_samples,
        probability_cap=variant.probability_cap,
        alpha=variant.alpha,
        fee_rate=settings.taker_fee_rate,
        segment_scope="specific_only",
    )
    params = RepairPolicyParams(
        policy_name=variant.name,
        policy_version="strategy_experiment",
        alpha=variant.alpha,
        probability_cap=variant.probability_cap,
        min_samples=variant.min_samples,
        min_edge_net=variant.min_edge_net,
        segment_scope="specific_only",
        price_floor=None,
    )
    trades: list[TradeResult] = []
    model_metrics = ModelMetricAccumulator()
    blocked_counts: Counter[str] = Counter()
    scoped_by_segment: defaultdict[str, ModelMetricAccumulator] = defaultdict(
        ModelMetricAccumulator
    )
    samples: list[dict[str, object]] = []
    last_signals: dict[tuple[str, str], tuple[datetime, Decimal]] = {}
    exposure_by_market_day: defaultdict[tuple[str, object], Decimal] = defaultdict(Decimal)

    for candidate in candidates:
        context = _context(candidate)
        scope_reason = _diagnostic_scope_reason(candidate)
        calibration = calibrator.calibrate(context)
        segment = _segment_stats(
            calibration.segment_key,
            n=calibration.n_samples,
            wins=calibration.wins,
            observed_rate=calibration.observed_rate,
            brier_delta=calibration.brier_delta,
            pnl=calibration.pnl,
        )
        decision = evaluate_repair_policy(
            params=params,
            context=context,
            fee_rate=settings.taker_fee_rate,
            segment=segment,
            global_rate=calibrator.global_rate(default=candidate.raw_prob),
        )
        reason = scope_reason
        would_trade = False
        stake = Decimal("0")
        if reason is None:
            model_metrics.add(candidate, decision.calibrated_prob)
            scoped_by_segment[_candidate_segment_key(candidate)].add(
                candidate, decision.calibrated_prob
            )
            reason = decision.reason
            if candidate.city_slug in blocked_city_slugs:
                reason = "city_needs_review"
            elif decision.edge_net < 0:
                reason = "negative_calibrated_edge"
            elif decision.eligible:
                stake = kelly_stake(
                    decision.calibrated_prob,
                    cost_per_share(candidate.price, settings.taker_fee_rate),
                    bankroll=settings.bankroll,
                    kelly_multiplier=settings.kelly_fraction,
                    max_stake_per_order=settings.max_stake_per_order,
                )
                if stake <= 0:
                    reason = "kelly_stake_zero"
                elif _is_recent_duplicate(
                    last_signals,
                    market_id=candidate.market_id,
                    profile="max_edge",
                    ts=candidate.ts,
                    edge_net=decision.edge_net,
                ):
                    reason = "duplicate"
                elif (
                    exposure_by_market_day[(candidate.market_id, candidate.ts.date())] + stake
                    > settings.max_exposure_per_market
                ):
                    reason = "max_exposure"
                else:
                    would_trade = True
                    reason = None
                    trade = _trade_result(
                        ts=candidate.ts,
                        stake=stake,
                        market_price=candidate.price,
                        model_prob=decision.calibrated_prob,
                        winner=candidate.winner,
                        fee_rate=settings.taker_fee_rate,
                        market_id=candidate.market_id,
                        event_id=candidate.event_id,
                        city_slug=candidate.city_slug,
                        target_date=candidate.target_date,
                        bucket_kind=candidate.bucket_kind,
                        bucket_label=candidate.bucket_label,
                        edge_net=decision.edge_net,
                        hours_to_close=candidate.hours_to_close,
                        price_source=candidate.price_source,
                    )
                    if trade is not None:
                        trades.append(trade)
                        last_signals[(candidate.market_id, "max_edge")] = (
                            candidate.ts,
                            decision.edge_net,
                        )
                        exposure_by_market_day[(candidate.market_id, candidate.ts.date())] += (
                            stake
                        )

        if reason is not None:
            blocked_counts[reason] += 1
        if len(samples) < SAMPLE_LIMIT and (
            scope_reason is None or _is_extreme_overconfidence(candidate)
        ):
            samples.append(
                {
                    "ts": candidate.ts.isoformat(),
                    "market_id": candidate.market_id,
                    "city_slug": candidate.city_slug,
                    "bucket_kind": candidate.bucket_kind,
                    "market_price": str(candidate.price),
                    "raw_prob": candidate.raw_prob,
                    "calibrated_prob": decision.calibrated_prob,
                    "edge_net": str(decision.edge_net),
                    "cost_per_share": str(cost_per_share(candidate.price, settings.taker_fee_rate)),
                    "segment_key": _candidate_segment_key(candidate),
                    "reason": reason,
                    "would_trade": would_trade,
                    "stake": str(stake),
                }
            )
        calibrator.observe(context, 1.0 if candidate.winner else 0.0, decision.calibrated_prob)

    trade_payload = {
        **_trade_metrics(trades),
        **_bootstrap_metrics(trades),
        **_concentration_metrics(trades),
    }
    segment_rows = []
    for key, metrics in sorted(
        scoped_by_segment.items(), key=lambda item: item[1].n, reverse=True
    )[:20]:
        segment_rows.append({"segment_key": key, **metrics.as_payload()})
    return {
        "name": variant.name,
        "experiment_set": EXPERIMENT_SET,
        "alpha": variant.alpha,
        "probability_cap": variant.probability_cap,
        "min_calibration_samples": variant.min_samples,
        "min_edge_net": str(variant.min_edge_net),
        "policy_name": variant.name,
        "policy_version": "strategy_experiment",
        "diagnostic_only": True,
        "cannot_approve_live": True,
        "execution_proxy": EXECUTION_PROXY,
        "price_sampling": PRICE_SAMPLING,
        "model_validation": model_metrics.as_payload(),
        "profiles": {"max_edge": trade_payload},
        "blocked_counts": dict(sorted(blocked_counts.items())),
        "segment_calibration": segment_rows,
        "shadow_sample": samples,
    }


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _variant_score(variant: dict[str, object]) -> tuple[int, int, Decimal, float]:
    profiles = variant.get("profiles")
    max_edge = profiles.get("max_edge") if isinstance(profiles, dict) else None
    model_validation = variant.get("model_validation")
    if not isinstance(max_edge, dict) or not isinstance(model_validation, dict):
        return (-1, 0, Decimal("-999999"), -999999.0)
    n_trades = int(max_edge.get("n_resolved_trades") or 0)
    pnl = _decimal(max_edge.get("total_pnl")) or Decimal("-999999")
    brier = model_validation.get("brier_delta")
    brier_value = float(brier) if isinstance(brier, int | float) else -999999.0
    gates = 0
    gates += 1 if brier_value > 0 else 0
    gates += 1 if pnl > 0 else 0
    gates += 1 if n_trades >= MIN_HISTORICAL_TRADES else 0
    return (gates, n_trades, pnl, brier_value)


def _gates(
    best_variant: dict[str, object] | None,
    city_quality: tuple[bool, dict[str, object]],
) -> dict[str, object]:
    model_validation = (
        best_variant.get("model_validation") if isinstance(best_variant, dict) else {}
    )
    profiles = best_variant.get("profiles") if isinstance(best_variant, dict) else {}
    max_edge = profiles.get("max_edge") if isinstance(profiles, dict) else {}
    model_validation = model_validation if isinstance(model_validation, dict) else {}
    max_edge = max_edge if isinstance(max_edge, dict) else {}
    n_trades = int(max_edge.get("n_resolved_trades") or 0)
    pnl = _decimal(max_edge.get("total_pnl")) or Decimal("0")
    brier_delta = model_validation.get("brier_delta")
    brier_pass = isinstance(brier_delta, int | float) and float(brier_delta) > 0
    concentration = _decimal(max_edge.get("top_5_abs_pnl_share"))
    pnl_ci_high = _decimal(max_edge.get("pnl_ci_high"))
    city_pass, city_value = city_quality
    return {
        "diagnostic_brier": {
            "passed": brier_pass,
            "value": {"brier_delta": brier_delta},
            "required": {"brier_delta_gt": 0},
        },
        "proxy_pnl": {
            "passed": pnl > 0,
            "value": {"max_edge_total_pnl": str(pnl)},
            "required": {"total_pnl_gt": "0"},
        },
        "oos_or_historical_sample": {
            "passed": n_trades >= MIN_HISTORICAL_TRADES,
            "value": {"max_edge_trades": n_trades},
            "required": {"min_trades": MIN_HISTORICAL_TRADES},
        },
        "concentration": {
            "passed": concentration is not None
            and concentration <= MAX_TOP_5_ABS_PNL_SHARE,
            "value": {
                "top_5_abs_pnl_share": str(concentration) if concentration is not None else None
            },
            "required": {"top_5_abs_pnl_share_lte": str(MAX_TOP_5_ABS_PNL_SHARE)},
        },
        "bootstrap": {
            "passed": pnl_ci_high is not None and pnl_ci_high >= 0,
            "value": {"pnl_ci_high": str(pnl_ci_high) if pnl_ci_high is not None else None},
            "required": {"pnl_ci_high_gte": "0"},
        },
        "city_quality": {
            "passed": city_pass,
            "value": city_value,
            "required": {"needs_review": [], "missing_cities": []},
        },
        "live_release": {
            "passed": False,
            "value": "diagnostic_only",
            "required": "strategy_repair PROMISING plus measurement READY_FOR_LIVE_REVIEW",
        },
    }


def _status(gates: dict[str, object]) -> str:
    live_gate = gates.get("live_release")
    research_gates = [
        gate
        for key, gate in gates.items()
        if key != "live_release" and isinstance(gate, dict)
    ]
    if any(
        gate.get("passed") is False
        for key, gate in gates.items()
        if key == "city_quality" and isinstance(gate, dict)
    ):
        return "REJECTED"
    if all(gate.get("passed") is True for gate in research_gates):
        return "READY_FOR_SHADOW_PAPER"
    if (
        isinstance(live_gate, dict)
        and gates["diagnostic_brier"]["passed"] is True  # type: ignore[index]
        and gates["oos_or_historical_sample"]["passed"] is True  # type: ignore[index]
    ):
        return "VALIDATION_CANDIDATE"
    return "NO_STABLE_EDGE"


def _compact_variant(variant: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in variant.items()
        if key not in {"segment_calibration", "shadow_sample"}
    }


async def generate_strategy_experiment_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
    experiment_set: str = EXPERIMENT_SET,
) -> StrategyExperimentRun:
    if experiment_set != EXPERIMENT_SET:
        raise ValueError(f"unsupported experiment_set: {experiment_set}")
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    selected_cities = cities if cities is not None else settings.cities
    run_settings = settings.model_copy(
        update={"cities": selected_cities, "validation_history_days": history_days}
    )
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)

    async with session_factory() as session:
        candidates, n_candidates, source_counts, raw_counts, sampled_counts = (
            await _historical_candidates(session, run_settings)
        )
        blocked_city_slugs = await _blocked_city_slugs(session, run_settings)
        city_quality = await _city_quality(session, run_settings)

    variants = [
        _evaluate_variant(
            candidates,
            run_settings,
            variant,
            blocked_city_slugs=blocked_city_slugs,
        )
        for variant in _experiment_variants()
    ]
    best_variant = max(variants, key=_variant_score) if variants else None
    compact_variants = [_compact_variant(variant) for variant in variants]
    gates = _gates(best_variant, city_quality)
    status = _status(gates)
    summary = {
        "experiment_set": experiment_set,
        "diagnostic_only": True,
        "cannot_approve_live": True,
        "best_variant": best_variant.get("name") if best_variant else None,
        "best_variant_pnl": (
            best_variant.get("profiles", {})
            .get("max_edge", {})
            .get("total_pnl")
            if isinstance(best_variant, dict)
            else None
        ),
        "best_variant_brier_delta": (
            best_variant.get("model_validation", {}).get("brier_delta")
            if isinstance(best_variant, dict)
            else None
        ),
        "n_candidate_price_points": n_candidates,
        "price_source_counts": source_counts,
        "price_source_raw_counts": raw_counts,
        "price_source_sampled_counts": sampled_counts,
        "execution_proxy": EXECUTION_PROXY,
        "price_sampling": PRICE_SAMPLING,
        "blocked_extreme_segment": "above|0.9-1.0|0.95-1.00",
        "next_action": (
            "activate_shadow_paper"
            if status == "READY_FOR_SHADOW_PAPER"
            else "review_validation_candidate"
            if status == "VALIDATION_CANDIDATE"
            else "review_weather_hypothesis"
        ),
    }
    shadow = {
        "mode": "diagnostic_historical_only",
        "forward_shadow_enabled": False,
        "table": "strategy_shadow_decisions",
        "note": "No signals, paper orders, paper fills, or live orders are created.",
    }

    async with session_factory() as session, session.begin():
        row = StrategyExperimentRun(
            run_at=run_at,
            status=status,
            experiment_set=experiment_set,
            window_start=window_start,
            window_end=window_end,
            cities_json=_json(selected_cities or run_settings.cities or []),
            summary_json=_json(summary),
            variants_json=_json(compact_variants),
            best_variant_json=_json(best_variant or {}),
            gates_json=_json(gates),
            shadow_json=_json(shadow),
        )
        session.add(row)
        await session.flush()
        logger.info(
            "strategy experiment: status=%s best=%s",
            status,
            summary["best_variant"],
        )
        return row


async def run(
    settings: Settings,
    *,
    cities: list[str] | None = None,
    days: int | None = None,
    experiment_set: str = EXPERIMENT_SET,
) -> StrategyExperimentRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_strategy_experiment_report(
            session_factory,
            settings,
            cities=cities,
            days=days,
            experiment_set=experiment_set,
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run diagnostic strategy experiments.")
    parser.add_argument("--cities", help="Comma-separated city slugs.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--experiment-set", default=EXPERIMENT_SET)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def _run_to_jsonable(row: StrategyExperimentRun) -> dict[str, object]:
    variants = json.loads(row.variants_json)
    variants_list = variants if isinstance(variants, list) else []
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "experiment_set": row.experiment_set,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "cities": json.loads(row.cities_json),
        "summary": json.loads(row.summary_json),
        "variants_count": len(variants_list),
        "variants": variants_list[:12],
        "best_variant": json.loads(row.best_variant_json),
        "gates": json.loads(row.gates_json),
        "shadow": json.loads(row.shadow_json),
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.json else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    row = asyncio.run(
        run(
            get_settings(),
            cities=parse_cities(args.cities),
            days=args.days,
            experiment_set=args.experiment_set,
        )
    )
    if args.json:
        print(json.dumps(_run_to_jsonable(row), sort_keys=True))


if __name__ == "__main__":
    main()
