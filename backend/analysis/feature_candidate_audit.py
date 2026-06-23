"""Audit the latest feature-discovery candidate before any repair_v5 work.

Diagnostic-only: never creates signals, paper orders, fills, or live approvals.
"""

import argparse
import asyncio
import json
import logging
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.backtest import TradeResult, _is_recent_duplicate, _trade_result
from analysis.feature_discovery import (
    FeatureCandidate,
    FeatureSegmentStats,
    FeatureVariant,
    _build_segments,
    _calibrated_probability,
    _decision_price,
    _decision_winner,
    _enrich_candidates,
    _reason,
    _segment_key,
    _variants,
)
from analysis.historical_validation import MIN_HISTORICAL_TRADES
from analysis.operational_quarantine import quarantine_payloads
from analysis.strategy_discovery import _profile_payload
from analysis.strategy_repair import _historical_candidates
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    FeatureCandidateAuditRun,
    FeatureDiscoveryRun,
    PaperFill,
    PaperOrder,
    Signal,
)
from app.db.session import create_engine, create_session_factory
from app.strategy.edge import cost_per_share, net_edge
from app.strategy.sizing import kelly_stake

logger = logging.getLogger(__name__)

AUDIT_SOURCE = "feature_candidate_audit"
SAMPLE_LIMIT = 20


@dataclass(frozen=True)
class AuditDecision:
    candidate: FeatureCandidate
    fold_index: int
    family: str
    side: str
    segment_key: str
    reason: str | None
    market_price: Decimal
    raw_prob: float
    calibrated_prob: float
    edge_net: Decimal
    stake: Decimal
    winner: bool
    pnl: Decimal
    trade: TradeResult | None


def _json(value: object) -> str:
    return json.dumps(value, default=str, sort_keys=True)


async def _latest_feature_candidate(session: AsyncSession) -> FeatureDiscoveryRun | None:
    return (
        await session.execute(
            select(FeatureDiscoveryRun)
            .where(FeatureDiscoveryRun.status == "FEATURE_CANDIDATE")
            .order_by(FeatureDiscoveryRun.run_at.desc(), FeatureDiscoveryRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _variant_by_name(name: str) -> FeatureVariant | None:
    for variant in _variants():
        if variant.name == name:
            return variant
    return None


def _simulate_with_decisions(
    candidates: list[FeatureCandidate],
    segments: dict[str, FeatureSegmentStats],
    variant: FeatureVariant,
    settings: Settings,
    *,
    fold_index: int,
) -> tuple[list[TradeResult], list[AuditDecision], Counter[str]]:
    trades: list[TradeResult] = []
    decisions: list[AuditDecision] = []
    blocked: Counter[str] = Counter()
    last_signals: dict[tuple[str, str], tuple[datetime, Decimal]] = {}
    exposure_by_market_day: defaultdict[tuple[str, date], Decimal] = defaultdict(Decimal)

    for candidate in candidates:
        segment_key = _segment_key(candidate, variant)
        segment = segments.get(segment_key)
        reason = _reason(candidate, segment, variant, settings.taker_fee_rate)
        calibrated = (
            _calibrated_probability(candidate, segment, variant)
            if segment is not None
            else candidate.base.raw_prob
        )
        decision_price = _decision_price(candidate, variant)
        edge = net_edge(calibrated, decision_price, settings.taker_fee_rate)
        stake = Decimal("0")
        trade: TradeResult | None = None
        pnl = Decimal("0")
        winner = _decision_winner(candidate, variant)

        if reason is None:
            stake = kelly_stake(
                calibrated,
                cost_per_share(decision_price, settings.taker_fee_rate),
                bankroll=settings.bankroll,
                kelly_multiplier=settings.kelly_fraction,
                max_stake_per_order=settings.max_stake_per_order,
            )
            if stake <= Decimal("0"):
                reason = "kelly_stake_zero"
            elif _is_recent_duplicate(
                last_signals,
                market_id=candidate.base.market_id,
                profile="max_edge",
                ts=candidate.base.ts,
                edge_net=edge,
            ):
                reason = "duplicate"
            elif (
                exposure_by_market_day[(candidate.base.market_id, candidate.base.ts.date())]
                + stake
                > settings.max_exposure_per_market
            ):
                reason = "max_exposure"
            else:
                trade = _trade_result(
                    ts=candidate.base.ts,
                    stake=stake,
                    market_price=decision_price,
                    model_prob=calibrated,
                    winner=winner,
                    fee_rate=settings.taker_fee_rate,
                    market_id=candidate.base.market_id,
                    event_id=candidate.base.event_id,
                    city_slug=candidate.base.city_slug,
                    target_date=candidate.base.target_date,
                    bucket_kind=candidate.base.bucket_kind,
                    bucket_label=candidate.base.bucket_label,
                    edge_net=edge,
                    hours_to_close=candidate.base.hours_to_close,
                    price_source=candidate.base.price_source,
                )
                if trade is None:
                    reason = "invalid_trade"
                else:
                    pnl = trade.pnl
                    trades.append(trade)
                    exposure_by_market_day[
                        (candidate.base.market_id, candidate.base.ts.date())
                    ] += stake
                    last_signals[(candidate.base.market_id, "max_edge")] = (
                        candidate.base.ts,
                        edge,
                    )
        if reason is not None:
            blocked[reason] += 1

        decisions.append(
            AuditDecision(
                candidate=candidate,
                fold_index=fold_index,
                family=variant.family,
                side=variant.side,
                segment_key=segment_key,
                reason=reason,
                market_price=decision_price,
                raw_prob=candidate.base.raw_prob,
                calibrated_prob=calibrated,
                edge_net=edge,
                stake=stake,
                winner=winner,
                pnl=pnl,
                trade=trade,
            )
        )
    return trades, decisions, blocked


def _replay_candidate(
    candidates: list[FeatureCandidate],
    discovery: FeatureDiscoveryRun,
    settings: Settings,
) -> tuple[list[TradeResult], list[AuditDecision], list[dict[str, object]], Counter[str]]:
    folds_raw = json.loads(discovery.folds_json)
    trades: list[TradeResult] = []
    decisions: list[AuditDecision] = []
    folds: list[dict[str, object]] = []
    blocked_total: Counter[str] = Counter()

    for fold in folds_raw:
        if not isinstance(fold, dict) or fold.get("valid") is not True:
            continue
        variant_name = str(fold.get("selected_variant") or "")
        variant = _variant_by_name(variant_name)
        window = fold.get("fold_window")
        if variant is None or not isinstance(window, dict):
            continue
        fold_start = date.fromisoformat(str(window["start"]))
        fold_end = date.fromisoformat(str(window["end"]))
        train = [item for item in candidates if item.base.target_date < fold_start]
        fold_candidates = [
            item for item in candidates if fold_start <= item.base.target_date <= fold_end
        ]
        segments = _build_segments(train, settings.taker_fee_rate, variant)
        fold_trades, fold_decisions, blocked = _simulate_with_decisions(
            fold_candidates,
            segments,
            variant,
            settings,
            fold_index=int(fold.get("index") or 0),
        )
        trades.extend(fold_trades)
        decisions.extend(fold_decisions)
        blocked_total.update(blocked)
        profile = _profile_payload(fold_trades)
        folds.append(
            {
                "index": fold.get("index"),
                "fold_window": window,
                "selected_family": variant.family,
                "selected_variant": variant.name,
                "selected_side": variant.side,
                "n_fold_candidates": len(fold_candidates),
                "n_oos_trades": len(fold_trades),
                "pnl": profile["total_pnl"],
                "brier_delta": profile["brier_delta"],
                "blocked_counts": dict(blocked),
            }
        )
    return trades, decisions, folds, blocked_total


def _decision_trace(
    decisions: list[AuditDecision], *, limit: int = SAMPLE_LIMIT
) -> dict[str, object]:
    traded = [decision for decision in decisions if decision.trade is not None]
    top_winners = sorted(traded, key=lambda decision: decision.pnl, reverse=True)[:limit]
    top_losers = sorted(traded, key=lambda decision: decision.pnl)[:limit]
    blocked = Counter(decision.reason or "would_trade" for decision in decisions)

    def row(decision: AuditDecision) -> dict[str, object]:
        return {
            "ts": decision.candidate.base.ts.isoformat(),
            "city_slug": decision.candidate.base.city_slug,
            "market_id": decision.candidate.base.market_id,
            "fold_index": decision.fold_index,
            "family": decision.family,
            "side": decision.side,
            "segment_key": decision.segment_key,
            "market_price": str(decision.market_price),
            "raw_prob": decision.raw_prob,
            "calibrated_prob": decision.calibrated_prob,
            "edge_net": str(decision.edge_net),
            "stake": str(decision.stake),
            "winner": decision.winner,
            "pnl": str(decision.pnl),
            "reason": decision.reason,
            "features": {
                "threshold_distance": decision.candidate.threshold_distance_bucket,
                "ensemble_spread": decision.candidate.ensemble_spread_bucket,
                "forecast_revision": decision.candidate.forecast_revision_bucket,
                "lead_time": decision.candidate.lead_time_bucket,
                "market_price_bucket": decision.candidate.market_price_bucket,
                "price_momentum_6h": decision.candidate.price_momentum_6h_bucket,
                "price_momentum_24h": decision.candidate.price_momentum_24h_bucket,
                "city_error_regime": decision.candidate.city_error_regime,
            },
        }

    return {
        "blocked_counts": dict(blocked),
        "top_winners": [row(decision) for decision in top_winners],
        "top_losers": [row(decision) for decision in top_losers],
        "samples": [row(decision) for decision in decisions[:limit]],
    }


def _profile_with_city_share(trades: list[TradeResult]) -> dict[str, object]:
    profile = _profile_payload(trades)
    by_city: defaultdict[str, Decimal] = defaultdict(Decimal)
    for trade in trades:
        if trade.city_slug is not None:
            by_city[trade.city_slug] += trade.pnl
    total_abs = sum((abs(value) for value in by_city.values()), Decimal("0"))
    if total_abs <= 0:
        city_share = {"top_city": None, "top_city_abs_pnl_share": None, "city_count": len(by_city)}
    else:
        city, pnl = max(by_city.items(), key=lambda item: abs(item[1]))
        city_share = {
            "top_city": city,
            "top_city_abs_pnl_share": str((abs(pnl) / total_abs).quantize(Decimal("0.0001"))),
            "city_count": len(by_city),
            "by_city_pnl": {
                key: str(value.quantize(Decimal("0.01"))) for key, value in by_city.items()
            },
        }
    profile["city_pnl_share"] = city_share
    profile["traded_cities"] = sorted(by_city)
    return profile


def _group_metrics(
    decisions: list[AuditDecision],
    key_fn: Callable[[AuditDecision], object],
    *,
    limit: int = 12,
) -> list[dict[str, object]]:
    groups: defaultdict[str, list[AuditDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.trade is not None:
            groups[str(key_fn(decision))].append(decision)
    rows: list[dict[str, object]] = []
    for key, group in groups.items():
        trades = [decision.trade for decision in group if decision.trade is not None]
        profile = _profile_with_city_share(trades)
        rows.append(
            {
                "key": key,
                "n_resolved_trades": profile["n_resolved_trades"],
                "total_pnl": profile["total_pnl"],
                "brier_delta": profile["brier_delta"],
                "top_5_abs_pnl_share": profile["top_5_abs_pnl_share"],
                "pnl_ci_high": profile["pnl_ci_high"],
                "folds": sorted({decision.fold_index for decision in group}),
                "fold_count": len({decision.fold_index for decision in group}),
                "cities": sorted({decision.candidate.base.city_slug for decision in group}),
            }
        )
    rows.sort(key=lambda row: Decimal(str(row["total_pnl"])), reverse=True)
    return rows[:limit]


def _approved_subset(rows: list[dict[str, object]]) -> dict[str, object] | None:
    for row in rows:
        brier = row.get("brier_delta")
        pnl = Decimal(str(row.get("total_pnl") or "0"))
        n = int(row.get("n_resolved_trades") or 0)
        top_5 = Decimal(str(row.get("top_5_abs_pnl_share") or "999"))
        pnl_ci_high = Decimal(str(row.get("pnl_ci_high") or "-999999"))
        fold_count = int(row.get("fold_count") or 0)
        if (
            isinstance(brier, int | float)
            and float(brier) > 0
            and pnl > 0
            and n >= MIN_HISTORICAL_TRADES
            and fold_count >= 3
            and top_5 <= Decimal("0.40")
            and pnl_ci_high >= 0
        ):
            return row
    return None


def _gates(
    profile: dict[str, object],
    approved_subset: dict[str, object] | None,
    *,
    discovery: FeatureDiscoveryRun | None,
    quarantined_traded_cities: set[str],
) -> dict[str, object]:
    brier = profile.get("brier_delta")
    pnl = Decimal(str(profile.get("total_pnl") or "0"))
    n = int(profile.get("n_resolved_trades") or 0)
    top_5 = Decimal(str(profile.get("top_5_abs_pnl_share") or "999"))
    pnl_ci_high = Decimal(str(profile.get("pnl_ci_high") or "-999999"))
    return {
        "feature_candidate": {
            "passed": discovery is not None and discovery.status == "FEATURE_CANDIDATE",
            "value": {"status": discovery.status if discovery is not None else None},
            "required": "latest FeatureDiscoveryRun.status = FEATURE_CANDIDATE",
        },
        "aggregate_brier": {
            "passed": isinstance(brier, int | float) and float(brier) > 0,
            "value": {"brier_delta": brier},
            "required": {"brier_delta_gt": 0},
        },
        "aggregate_pnl": {
            "passed": pnl > 0,
            "value": {"total_pnl": str(pnl)},
            "required": {"total_pnl_gt": "0"},
        },
        "aggregate_trades": {
            "passed": n >= MIN_HISTORICAL_TRADES,
            "value": {"n_resolved_trades": n},
            "required": {"min_trades": MIN_HISTORICAL_TRADES},
        },
        "aggregate_concentration": {
            "passed": top_5 <= Decimal("0.40"),
            "value": {"top_5_abs_pnl_share": str(top_5)},
            "required": {"top_5_abs_pnl_share_lte": "0.40"},
        },
        "aggregate_bootstrap": {
            "passed": pnl_ci_high >= 0,
            "value": {"pnl_ci_high": str(pnl_ci_high)},
            "required": {"pnl_ci_high_gte": "0"},
        },
        "approved_subset": {
            "passed": approved_subset is not None,
            "value": approved_subset or {},
            "required": (
                "subset with positive Brier, positive PnL, 50+ trades, "
                "3+ folds, concentration <= 0.40"
            ),
        },
        "operational_quarantine": {
            "passed": not quarantined_traded_cities,
            "value": {
                "traded_quarantined_cities": sorted(quarantined_traded_cities),
                "quarantine": quarantine_payloads(quarantined_traded_cities),
            },
            "required": "quarantined cities cannot approve repair/shadow/live",
        },
        "live_release": {
            "passed": False,
            "value": "diagnostic_only",
            "required": "repair_v5 PROMISING plus measurement READY_FOR_LIVE_REVIEW",
        },
    }


def _status(gates: dict[str, dict[str, object]]) -> str:
    if gates["feature_candidate"]["passed"] is not True:
        return "DATA_REVIEW"
    if (
        gates["approved_subset"]["passed"] is True
        and gates["operational_quarantine"]["passed"] is True
    ):
        return "READY_FOR_REPAIR_V5"
    if gates["aggregate_pnl"]["passed"] is True or gates["aggregate_brier"]["passed"] is True:
        return "CANDIDATE_REVIEW"
    return "REJECTED_FEATURE_EDGE"


async def _artifact_counts(session: AsyncSession) -> dict[str, int]:
    return {
        "signals": int((await session.execute(select(func.count(Signal.id)))).scalar_one() or 0),
        "paper_orders": int(
            (await session.execute(select(func.count(PaperOrder.id)))).scalar_one() or 0
        ),
        "paper_fills": int(
            (await session.execute(select(func.count(PaperFill.id)))).scalar_one() or 0
        ),
    }


async def _persist_empty(
    session: AsyncSession,
    *,
    run_at: datetime,
    window_start: date,
    window_end: date,
) -> FeatureCandidateAuditRun:
    gates = _gates({}, None, discovery=None, quarantined_traded_cities=set())
    row = FeatureCandidateAuditRun(
        run_at=run_at,
        status="DATA_REVIEW",
        window_start=window_start,
        window_end=window_end,
        feature_discovery_run_id=None,
        cities_json="[]",
        summary_json=_json(
            {
                "source": AUDIT_SOURCE,
                "diagnostic_only": True,
                "cannot_approve_live": True,
                "reason": "no_feature_candidate",
                "next_action": "run_feature_discovery",
            }
        ),
        profile_json="{}",
        segments_json="{}",
        decision_trace_json="{}",
        gates_json=_json(gates),
    )
    session.add(row)
    await session.flush()
    return row


async def generate_feature_candidate_audit_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    days: int | None = None,
) -> FeatureCandidateAuditRun:
    run_at = datetime.now(UTC)
    history_days = days if days is not None else settings.validation_history_days
    window_end = run_at.date()
    window_start = window_end - timedelta(days=history_days)

    async with session_factory() as session:
        discovery = await _latest_feature_candidate(session)
        before_counts = await _artifact_counts(session)
        if discovery is None:
            pass
        else:
            selected_cities = json.loads(discovery.cities_json)
            run_settings = settings.model_copy(
                update={"cities": selected_cities, "validation_history_days": history_days}
            )
            raw_candidates, n_candidates, source_counts, raw_counts, sampled_counts = (
                await _historical_candidates(session, run_settings)
            )
    if discovery is None:
        async with session_factory() as session, session.begin():
            return await _persist_empty(
                session,
                run_at=run_at,
                window_start=window_start,
                window_end=window_end,
            )

    feature_candidates = _enrich_candidates(raw_candidates)
    trades, decisions, _folds, blocked = _replay_candidate(
        feature_candidates, discovery, run_settings
    )
    profile = _profile_with_city_share(trades)
    segments = {
        "by_city": _group_metrics(decisions, lambda decision: decision.candidate.base.city_slug),
        "by_segment": _group_metrics(decisions, lambda decision: decision.segment_key),
        "by_bucket_kind": _group_metrics(
            decisions, lambda decision: decision.candidate.base.bucket_kind
        ),
        "by_price_bucket": _group_metrics(
            decisions, lambda decision: decision.candidate.market_price_bucket
        ),
        "by_momentum_6h": _group_metrics(
            decisions, lambda decision: decision.candidate.price_momentum_6h_bucket
        ),
        "by_error_regime": _group_metrics(
            decisions, lambda decision: decision.candidate.city_error_regime
        ),
        "blocked_counts": dict(blocked),
    }
    approved_subset = _approved_subset(segments["by_segment"])
    traded_cities = set(profile.get("traded_cities") or [])
    summary_raw = json.loads(discovery.summary_json)
    quarantined = set(str(city) for city in summary_raw.get("excluded_quarantined", []) or [])
    gates = _gates(
        profile,
        approved_subset,
        discovery=discovery,
        quarantined_traded_cities=traded_cities & quarantined,
    )
    status = _status(gates)  # type: ignore[arg-type]
    aggregate_brier = profile.get("brier_delta")
    aggregate_pnl = Decimal(str(profile.get("total_pnl") or "0"))
    explanation = (
        "positive_pnl_with_negative_brier"
        if aggregate_pnl > 0
        and not (isinstance(aggregate_brier, int | float) and aggregate_brier > 0)
        else "aggregate_metrics_aligned"
    )

    async with session_factory() as session, session.begin():
        after_counts = await _artifact_counts(session)
        summary = {
            "source": AUDIT_SOURCE,
            "diagnostic_only": True,
            "cannot_approve_live": True,
            "feature_discovery_run_id": discovery.id,
            "feature_discovery_status": discovery.status,
            "best_family": summary_raw.get("best_family"),
            "explanation": explanation,
            "approved_subset_key": approved_subset.get("key") if approved_subset else None,
            "n_candidate_price_points": n_candidates,
            "price_source_counts": source_counts,
            "price_source_raw_counts": raw_counts,
            "price_source_sampled_counts": sampled_counts,
            "artifact_counts_before": before_counts,
            "artifact_counts_after": after_counts,
            "next_action": (
                "plan_repair_v5_feature_subset"
                if status == "READY_FOR_REPAIR_V5"
                else "reject_or_design_new_features"
                if status == "REJECTED_FEATURE_EDGE"
                else "review_feature_candidate_blockers"
            ),
        }
        row = FeatureCandidateAuditRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            feature_discovery_run_id=discovery.id,
            cities_json=discovery.cities_json,
            summary_json=_json(summary),
            profile_json=_json(profile),
            segments_json=_json(segments),
            decision_trace_json=_json(_decision_trace(decisions)),
            gates_json=_json(gates),
        )
        session.add(row)
        await session.flush()
        logger.info("feature candidate audit: status=%s discovery=%s", status, discovery.id)
        return row


async def run(settings: Settings, *, days: int | None = None) -> FeatureCandidateAuditRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_feature_candidate_audit_report(session_factory, settings, days=days)
    finally:
        await engine.dispose()


def _row_payload(row: FeatureCandidateAuditRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "feature_discovery_run_id": row.feature_discovery_run_id,
        "cities": json.loads(row.cities_json),
        "summary": json.loads(row.summary_json),
        "profile": json.loads(row.profile_json),
        "segments": json.loads(row.segments_json),
        "decision_trace": json.loads(row.decision_trace_json),
        "gates": json.loads(row.gates_json),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit latest feature discovery candidate.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()
    row = asyncio.run(run(get_settings(), days=args.days))
    if args.json:
        print(json.dumps(_row_payload(row), sort_keys=True))
    else:
        print(f"feature candidate audit status={row.status} run_id={row.id}")


if __name__ == "__main__":
    main()
