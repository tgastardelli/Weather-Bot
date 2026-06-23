"""Promote high-reward shadow evidence into a repair v5 candidate.

This module is still paper/shadow-only. It reads diagnostic shadow decisions,
recomputes settlement PnL from resolved markets, and persists a StrategyRepairRun
only when the asymmetric-payoff gates pass. It never creates signals, orders, or
fills.
"""

import argparse
import asyncio
import json
import logging
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.backtest import (
    HISTORICAL_TRADE_EXECUTION_PROXY,
    HISTORICAL_TRADE_PRICE_SAMPLING,
    TradeResult,
    _bootstrap_metrics,
    _concentration_metrics,
    _profile_metrics,
    _trade_result,
)
from analysis.operational_quarantine import quarantine_payloads
from analysis.strategy_shadow import DEFAULT_HIGH_REWARD_SHADOW_POLICY
from app.config import Settings, get_settings
from app.db.models import (
    Base,
    City,
    Market,
    PaperFill,
    PaperOrder,
    Signal,
    StrategyRepairRun,
    StrategyShadowDecision,
)
from app.db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)

DEFAULT_POLICY_NAME = "repair_v5_high_reward_v1"
MIN_REPAIR_TRADES = 50
MIN_REPAIR_CITIES = 3
MIN_CITY_SHADOW_TRADES = 15
MIN_PAYOFF_RATIO = Decimal("3.00")
CENT = Decimal("0.01")


def _json(payload: object) -> str:
    return json.dumps(payload, default=str, sort_keys=True)


def _parse_shadow_segment(segment_key: str | None) -> dict[str, str | None]:
    if not segment_key:
        return {
            "city_slug": None,
            "family": None,
            "side": None,
            "variant": None,
            "bucket_kind": None,
            "month": None,
        }
    parts = segment_key.split("|")
    return {
        "city_slug": parts[1] if len(parts) > 1 else None,
        "family": parts[2] if len(parts) > 2 else None,
        "side": parts[3] if len(parts) > 3 else None,
        "variant": parts[4] if len(parts) > 4 else None,
        "bucket_kind": parts[5] if len(parts) > 5 else None,
        "month": parts[6] if len(parts) > 6 else None,
    }


async def _artifact_counts(session: AsyncSession) -> dict[str, int]:
    return {
        "signals": int((await session.execute(select(func.count(Signal.id)))).scalar_one()),
        "paper_orders": int(
            (await session.execute(select(func.count(PaperOrder.id)))).scalar_one()
        ),
        "paper_fills": int((await session.execute(select(func.count(PaperFill.id)))).scalar_one()),
    }


def _payoff_metrics(trades: list[TradeResult]) -> dict[str, object]:
    pnls = [trade.pnl for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl <= 0]
    average_win = (sum(wins, Decimal("0")) / Decimal(len(wins))).quantize(CENT) if wins else None
    average_loss = (
        (abs(sum(losses, Decimal("0"))) / Decimal(len(losses))).quantize(CENT)
        if losses
        else None
    )
    payoff_ratio = None
    if average_win is not None and average_loss is not None and average_loss > 0:
        payoff_ratio = (average_win / average_loss).quantize(Decimal("0.0001"))
    total_staked = sum((trade.stake for trade in trades), Decimal("0")).quantize(CENT)
    total_pnl = sum(pnls, Decimal("0")).quantize(CENT)
    profit_factor = (
        (sum(wins, Decimal("0")) / abs(sum(losses, Decimal("0")))).quantize(
            Decimal("0.0001")
        )
        if losses and sum(losses, Decimal("0")) < 0
        else None
    )
    return {
        "n_resolved_trades": len(trades),
        "n_wins": len(wins),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "average_win": str(average_win) if average_win is not None else None,
        "average_loss": str(average_loss) if average_loss is not None else None,
        "payoff_ratio": str(payoff_ratio) if payoff_ratio is not None else None,
        "total_staked": str(total_staked),
        "total_pnl": str(total_pnl),
        "roi": (
            str((total_pnl / total_staked).quantize(Decimal("0.0001")))
            if total_staked > 0
            else None
        ),
        "profit_factor": str(profit_factor) if profit_factor is not None else None,
    }


def _profile_payload(trades: list[TradeResult]) -> dict[str, object]:
    return {
        **_profile_metrics(trades, n_candidate_snapshots=len(trades)),
        **_payoff_metrics(trades),
        "bootstrap": _bootstrap_metrics(trades),
        "concentration": _concentration_metrics(trades),
    }


def _select_operable_city_trades(
    trades: list[TradeResult],
) -> tuple[list[TradeResult], dict[str, dict[str, object]]]:
    by_city: dict[str, list[TradeResult]] = defaultdict(list)
    for trade in trades:
        if trade.city_slug is not None:
            by_city[trade.city_slug].append(trade)

    selected_cities: set[str] = set()
    city_review: dict[str, dict[str, object]] = {}
    for city_slug, city_trades in sorted(by_city.items()):
        metrics = _payoff_metrics(city_trades)
        total_pnl = Decimal(str(metrics.get("total_pnl") or "0"))
        payoff = metrics.get("payoff_ratio")
        payoff_decimal = Decimal(str(payoff)) if payoff is not None else Decimal("0")
        blockers: list[str] = []
        if len(city_trades) < MIN_CITY_SHADOW_TRADES:
            blockers.append("low_city_shadow_trades")
        if total_pnl <= 0:
            blockers.append("non_positive_city_pnl")
        if payoff_decimal < MIN_PAYOFF_RATIO:
            blockers.append("city_payoff_ratio_below_3x")
        selected = not blockers
        if selected:
            selected_cities.add(city_slug)
        city_review[city_slug] = {
            **metrics,
            "selected": selected,
            "blockers": blockers,
        }

    selected_trades = [
        trade
        for trade in trades
        if trade.city_slug is not None and trade.city_slug in selected_cities
    ]
    return selected_trades, city_review


def _gate(passed: bool, *, value: object, required: object, reason: str) -> dict[str, object]:
    return {"passed": passed, "value": value, "required": required, "reason": reason}


def _status(gates: dict[str, dict[str, object]]) -> str:
    if all(gate["passed"] is True for gate in gates.values()):
        return "PROMISING"
    return "SHADOW_REVIEW"


async def _shadow_trades(
    session: AsyncSession, settings: Settings, *, shadow_policy_name: str
) -> tuple[list[TradeResult], dict[str, object]]:
    rows = (
        await session.execute(
            select(StrategyShadowDecision, Market, City)
            .join(Market, StrategyShadowDecision.market_id == Market.id)
            .join(City, StrategyShadowDecision.city_slug == City.slug)
            .where(
                StrategyShadowDecision.policy_name == shadow_policy_name,
                StrategyShadowDecision.would_trade.is_(True),
                Market.winner.is_not(None),
            )
            .order_by(StrategyShadowDecision.ts, StrategyShadowDecision.id)
        )
    ).all()
    trades: list[TradeResult] = []
    side_by_city: dict[str, str] = {}
    variant_by_city: dict[str, str] = {}
    family_by_city: dict[str, str] = {}
    needs_review_cities: set[str] = set()
    skipped: Counter[str] = Counter()
    for decision, market, city in rows:
        segment = _parse_shadow_segment(decision.segment_key)
        side = segment["side"]
        if side not in {"YES", "NO"}:
            skipped["missing_side"] += 1
            continue
        if city.needs_review:
            needs_review_cities.add(city.slug)
            skipped["needs_review"] += 1
            continue
        winner = market.winner if side == "YES" else not market.winner
        trade = _trade_result(
            ts=decision.ts,
            stake=settings.max_stake_per_order,
            market_price=decision.market_price,
            model_prob=decision.calibrated_prob,
            winner=winner,
            fee_rate=settings.taker_fee_rate,
            market_id=decision.market_id,
            event_id=decision.event_id,
            city_slug=decision.city_slug,
            target_date=decision.target_date,
            bucket_kind=segment["bucket_kind"],
            bucket_label=market.group_item_title,
            edge_net=decision.edge_net,
            price_source="strategy_shadow_decision",
        )
        if trade is None:
            skipped["invalid_trade"] += 1
            continue
        trades.append(trade)
        side_by_city[decision.city_slug] = str(side)
        if segment["variant"] is not None:
            variant_by_city[decision.city_slug] = str(segment["variant"])
        if segment["family"] is not None:
            family_by_city[decision.city_slug] = str(segment["family"])
    metadata = {
        "raw_shadow_would_trade": len(rows),
        "skipped_counts": dict(sorted(skipped.items())),
        "needs_review_cities": sorted(needs_review_cities),
        "side_by_city": side_by_city,
        "variant_by_city": variant_by_city,
        "family_by_city": family_by_city,
    }
    return trades, metadata


def _segment_rows(trades: list[TradeResult]) -> list[dict[str, object]]:
    by_segment: dict[str, list[TradeResult]] = defaultdict(list)
    for trade in trades:
        city = trade.city_slug or "unknown"
        bucket_kind = trade.bucket_kind or "unknown"
        label = trade.bucket_label or "unknown"
        by_segment[f"{city}|{bucket_kind}|{label}"].append(trade)

    rows: list[dict[str, object]] = []
    for key, segment_trades in sorted(by_segment.items()):
        metrics = _payoff_metrics(segment_trades)
        rows.append(
            {
                "segment_key": key,
                "n": len(segment_trades),
                "wins": sum(1 for trade in segment_trades if trade.won),
                "observed_rate": (
                    sum(1 for trade in segment_trades if trade.won) / len(segment_trades)
                    if segment_trades
                    else 0.0
                ),
                "pnl": metrics["total_pnl"],
                "payoff_ratio": metrics["payoff_ratio"],
                "eligible": (
                    len(segment_trades) > 0
                    and Decimal(str(metrics["total_pnl"])) > 0
                    and metrics["payoff_ratio"] is not None
                    and Decimal(str(metrics["payoff_ratio"])) >= MIN_PAYOFF_RATIO
                ),
            }
        )
    return rows


async def generate_high_reward_repair_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    shadow_policy_name: str = DEFAULT_HIGH_REWARD_SHADOW_POLICY,
    policy_name: str = DEFAULT_POLICY_NAME,
) -> StrategyRepairRun:
    run_at = datetime.now(UTC)
    async with session_factory() as session:
        counts_before = await _artifact_counts(session)
        raw_trades, shadow_metadata = await _shadow_trades(
            session, settings, shadow_policy_name=shadow_policy_name
        )
        counts_after = await _artifact_counts(session)

    trades, city_review = _select_operable_city_trades(raw_trades)
    active_cities = sorted({trade.city_slug for trade in trades if trade.city_slug is not None})
    active_city_set = set(active_cities)
    side_by_active_city = {
        city: side
        for city, side in shadow_metadata["side_by_city"].items()  # type: ignore[union-attr]
        if city in active_city_set
    }
    variant_by_active_city = {
        city: variant
        for city, variant in shadow_metadata["variant_by_city"].items()  # type: ignore[union-attr]
        if city in active_city_set
    }
    family_by_active_city = {
        city: family
        for city, family in shadow_metadata["family_by_city"].items()  # type: ignore[union-attr]
        if city in active_city_set
    }
    window_dates = [trade.target_date for trade in trades if trade.target_date is not None]
    window_start: date | None = min(window_dates) if window_dates else None
    window_end: date | None = max(window_dates) if window_dates else None
    payload = _profile_payload(trades)
    payoff = payload.get("payoff_ratio")
    total_pnl = Decimal(str(payload.get("total_pnl") or "0"))
    payoff_decimal = Decimal(str(payoff)) if payoff is not None else Decimal("0")
    quarantined = quarantine_payloads(active_cities)
    bootstrap = payload.get("bootstrap")
    pnl_ci_low = None
    if isinstance(bootstrap, dict) and bootstrap.get("pnl_ci_low") is not None:
        pnl_ci_low = Decimal(str(bootstrap["pnl_ci_low"]))

    gates = {
        "shadow_policy": _gate(
            bool(trades),
            value={
                "shadow_policy_name": shadow_policy_name,
                "resolved_would_trade": len(raw_trades),
                "selected_resolved_trades": len(trades),
            },
            required="existing resolved shadow decisions",
            reason="Repair V5 high reward must be derived from shadow evidence.",
        ),
        "three_active_cities": _gate(
            len(active_cities) >= MIN_REPAIR_CITIES,
            value=active_cities,
            required={"min_cities": MIN_REPAIR_CITIES},
            reason="High-reward live path needs at least three active operational cities.",
        ),
        "resolved_trade_count": _gate(
            len(trades) >= MIN_REPAIR_TRADES,
            value=len(trades),
            required={"min_resolved_trades": MIN_REPAIR_TRADES},
            reason="The aggressive policy still needs enough resolved shadow evidence.",
        ),
        "positive_pnl": _gate(
            total_pnl > 0,
            value=str(total_pnl),
            required={"total_pnl_gt": "0"},
            reason="High-risk/high-reward policy must be profitable after fee.",
        ),
        "payoff_asymmetry": _gate(
            payoff_decimal >= MIN_PAYOFF_RATIO,
            value=str(payoff_decimal),
            required={"payoff_ratio_gte": str(MIN_PAYOFF_RATIO)},
            reason="Low winrate is acceptable only when average win dominates average loss.",
        ),
        "bootstrap_not_clearly_negative": _gate(
            pnl_ci_low is None or pnl_ci_low >= 0,
            value=str(pnl_ci_low) if pnl_ci_low is not None else None,
            required={"pnl_ci_low_gte": "0"},
            reason="Bootstrap interval should not be clearly negative.",
        ),
        "operational_cities": _gate(
            not quarantined and not shadow_metadata["needs_review_cities"],
            value={
                "quarantine": quarantined,
                "needs_review": shadow_metadata["needs_review_cities"],
            },
            required="no quarantined or needs_review cities",
            reason="Research-only cities cannot become a V5 operational policy.",
        ),
        "artifact_safety": _gate(
            counts_before == counts_after,
            value={"before": counts_before, "after": counts_after},
            required="no signals, paper orders, or paper fills created",
            reason="Repair generation must remain analysis-only.",
        ),
    }
    status = _status(gates)
    segments = _segment_rows(trades)
    best_variant = {
        "name": policy_name,
        "policy_name": policy_name,
        "policy_version": "repair_v5_high_reward",
        "source_shadow_policy": shadow_policy_name,
        "source": "high_reward_shadow_decisions",
        "selection": "positive_city_pnl_payoff_filtered",
        "execution_proxy": HISTORICAL_TRADE_EXECUTION_PROXY,
        "price_sampling": HISTORICAL_TRADE_PRICE_SAMPLING,
        "profiles": {"max_edge": payload},
        "side_by_city": side_by_active_city,
        "variant_by_city": variant_by_active_city,
        "family_by_city": family_by_active_city,
        "active_cities": active_cities,
        "city_review": city_review,
        "segments": segments,
        "diagnostic_brier_not_live_gate": True,
    }
    baseline = {
        "name": "shadow_high_reward_input",
        "profiles": {"max_edge": payload},
        "source_shadow_policy": shadow_policy_name,
    }
    summary = {
        "preferred_profile": "max_edge",
        "policy_name": policy_name,
        "policy_version": "repair_v5_high_reward",
        "source_shadow_policy": shadow_policy_name,
        "status_reason": (
            "high_reward_gates_passed" if status == "PROMISING" else "shadow_gates_failed"
        ),
        "active_cities": active_cities,
        "raw_shadow_would_trade": len(raw_trades),
        "selected_shadow_trades": len(trades),
        "city_review": city_review,
        "side_by_city": side_by_active_city,
        "variant_by_city": variant_by_active_city,
        "segments": segments,
        "best_variant_pnl": payload["total_pnl"],
        "best_variant_roi": payload["roi"],
        "best_variant_payoff_ratio": payload["payoff_ratio"],
        "best_variant_win_rate": payload["win_rate"],
        "best_variant_brier_delta": payload["brier_delta"],
        "diagnostic_brier_not_live_gate": True,
        "artifact_counts_before": counts_before,
        "artifact_counts_after": counts_after,
        "next_action": (
            "implement_repair_v5_high_reward_runtime"
            if status == "PROMISING"
            else "continue_shadow_or_revisit_high_reward_hunt"
        ),
    }
    async with session_factory() as session, session.begin():
        row = StrategyRepairRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            cities_json=_json(active_cities),
            summary_json=_json(summary),
            baseline_json=_json(baseline),
            variants_json=_json([baseline, best_variant]),
            best_variant_json=_json(best_variant),
            gates_json=_json(gates),
        )
        session.add(row)
        await session.flush()
        logger.info(
            "high reward repair: status=%s policy=%s cities=%d trades=%d",
            status,
            policy_name,
            len(active_cities),
            len(trades),
        )
        return row


async def run(
    settings: Settings,
    *,
    shadow_policy_name: str = DEFAULT_HIGH_REWARD_SHADOW_POLICY,
    policy_name: str = DEFAULT_POLICY_NAME,
) -> StrategyRepairRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await generate_high_reward_repair_report(
            session_factory,
            settings,
            shadow_policy_name=shadow_policy_name,
            policy_name=policy_name,
        )
    finally:
        await engine.dispose()


def _row_payload(row: StrategyRepairRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "cities": json.loads(row.cities_json),
        "summary": json.loads(row.summary_json),
        "baseline": json.loads(row.baseline_json),
        "variants": json.loads(row.variants_json),
        "best_variant": json.loads(row.best_variant_json),
        "gates": json.loads(row.gates_json),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote high-reward shadow evidence into repair_v5_high_reward."
    )
    parser.add_argument("--shadow-policy", default=DEFAULT_HIGH_REWARD_SHADOW_POLICY)
    parser.add_argument("--policy-name", default=DEFAULT_POLICY_NAME)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.json else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    row = asyncio.run(
        run(
            get_settings(),
            shadow_policy_name=args.shadow_policy,
            policy_name=args.policy_name,
        )
    )
    if args.json:
        print(json.dumps(_row_payload(row), sort_keys=True))
    else:
        print(f"high reward repair status={row.status} policy={args.policy_name}")


if __name__ == "__main__":
    main()
