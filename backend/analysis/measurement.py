"""Measurement reports proving paper execution accounting."""

import argparse
import asyncio
import json
import logging
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.models import (
    BacktestResult,
    Base,
    Event,
    EvidenceRun,
    ForecastSnapshot,
    Market,
    MeasurementRun,
    PaperEquitySnapshot,
    PaperFill,
    PaperOrder,
    PaperPosition,
    Signal,
    SignalStrategyAudit,
    StrategyRepairRun,
)
from app.db.session import create_engine, create_session_factory
from app.execution.paper import taker_fee

logger = logging.getLogger(__name__)

MIN_FORWARD_DAYS = 30
MIN_RESOLVED_FILLS = 50


def _json_dumps(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_float(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _gate(passed: bool, *, value: object, required: object, reason: str) -> dict[str, Any]:
    return {"passed": passed, "value": value, "required": required, "reason": reason}


async def _latest_evidence(session: AsyncSession) -> EvidenceRun | None:
    return (
        await session.execute(select(EvidenceRun).order_by(EvidenceRun.run_at.desc()).limit(1))
    ).scalar_one_or_none()


async def _latest_replay(session: AsyncSession) -> BacktestResult | None:
    rows = (
        await session.execute(
            select(BacktestResult)
            .where(BacktestResult.profile == "max_edge")
            .order_by(BacktestResult.run_at.desc())
        )
    ).scalars().all()
    for row in rows:
        params = _parse_json(row.params_json)
        if params.get("source") == "replay_price_snapshots":
            return row
    return None


async def _latest_strategy_repair(session: AsyncSession) -> StrategyRepairRun | None:
    return (
        await session.execute(
            select(StrategyRepairRun).order_by(
                StrategyRepairRun.run_at.desc(), StrategyRepairRun.id.desc()
            ).limit(1)
        )
    ).scalar_one_or_none()


def _repair_policy_name(repair: StrategyRepairRun | None) -> str | None:
    if repair is None:
        return None
    payload = _parse_json(repair.best_variant_json)
    value = payload.get("policy_name") or payload.get("name")
    return value if isinstance(value, str) else None


def _is_repaired_policy(policy_name: str | None) -> bool:
    return policy_name is not None and policy_name.startswith(
        ("repair_v2", "repair_v3", "repair_v4", "repair_v5")
    )


async def _policy_counts(
    session: AsyncSession, fills: list[PaperFill]
) -> Counter[str]:
    signal_ids = sorted({fill.signal_id for fill in fills})
    if not signal_ids:
        return Counter()
    audits = (
        await session.execute(
            select(SignalStrategyAudit).where(SignalStrategyAudit.signal_id.in_(signal_ids))
        )
    ).scalars().all()
    by_signal = {audit.signal_id: audit.policy_name for audit in audits}
    return Counter(by_signal.get(fill.signal_id, "missing_audit") for fill in fills)


async def _signal_ids_for_policy(session: AsyncSession, policy_name: str | None) -> set[int]:
    if policy_name is None:
        return set()
    return set(
        (
            await session.execute(
                select(SignalStrategyAudit.signal_id).where(
                    SignalStrategyAudit.policy_name == policy_name
                )
            )
        )
        .scalars()
        .all()
    )


async def _forecast_asof_failures(session: AsyncSession, fills: list[PaperFill]) -> int:
    failures = 0
    for fill in fills:
        row = (
            await session.execute(
                select(Signal, Market, Event)
                .join(Market, Signal.market_id == Market.id)
                .join(Event, Market.event_id == Event.id)
                .where(Signal.id == fill.signal_id)
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            failures += 1
            continue
        signal, _market, event = row
        found = (
            await session.execute(
                select(ForecastSnapshot.id)
                .where(
                    ForecastSnapshot.city_slug == event.city_slug,
                    ForecastSnapshot.target_date == event.target_date,
                    ForecastSnapshot.source == "open_meteo_ensemble",
                    ForecastSnapshot.fetched_at <= signal.ts,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if found is None:
            failures += 1
    return failures


async def _settlement_price_failures(session: AsyncSession, fills: list[PaperFill]) -> int:
    failures = 0
    for fill in fills:
        market = await session.get(Market, fill.market_id)
        if market is None or market.winner is None:
            failures += 1
            continue
        expected = _expected_settlement_price(market, fill.token_id)
        if fill.price != expected:
            failures += 1
    return failures


def _expected_settlement_price(market: Market, token_id: str) -> Decimal:
    if market.winner is None:
        return Decimal("0")
    if token_id == market.yes_token_id:
        return Decimal("1") if market.winner else Decimal("0")
    if token_id == market.no_token_id:
        return Decimal("0") if market.winner else Decimal("1")
    return Decimal("0")


async def _fill_outcome_counts(
    session: AsyncSession, fills: list[PaperFill]
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for fill in fills:
        market = await session.get(Market, fill.market_id)
        if market is None:
            counts["unknown"] += 1
        elif fill.token_id == market.yes_token_id:
            counts["YES"] += 1
        elif fill.token_id == market.no_token_id:
            counts["NO"] += 1
        else:
            counts["unknown"] += 1
    return dict(sorted(counts.items()))


async def _paper_pnl_by_city(
    session: AsyncSession, fills: Sequence[PaperFill]
) -> dict[str, str]:
    totals: dict[str, Decimal] = {}
    for fill in fills:
        row = (
            await session.execute(
                select(Event.city_slug)
                .join(Market, Event.id == Market.event_id)
                .where(Market.id == fill.market_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        city_slug = row or "unknown"
        totals[city_slug] = totals.get(city_slug, Decimal("0")) + fill.cash_delta
    return {
        city: str(value.quantize(Decimal("0.00001")))
        for city, value in sorted(totals.items())
    }


def _avg_decimal(values: list[Decimal]) -> str | None:
    if not values:
        return None
    return str((sum(values, Decimal("0")) / Decimal(len(values))).quantize(Decimal("0.00001")))


async def _paper_brier_metrics(
    session: AsyncSession, settlement_fills: list[PaperFill]
) -> dict[str, float | int | None]:
    signal_ids = sorted({fill.signal_id for fill in settlement_fills})
    if not signal_ids:
        return {"n": 0, "model": None, "market": None, "delta": None}

    signals = (
        await session.execute(select(Signal).where(Signal.id.in_(signal_ids)))
    ).scalars().all()
    signal_by_id = {signal.id: signal for signal in signals}
    outcome_by_signal: dict[int, float] = {}
    for fill in settlement_fills:
        signal = signal_by_id.get(fill.signal_id)
        if signal is None:
            continue
        outcome_by_signal[signal.id] = 1.0 if fill.price == Decimal("1") else 0.0

    model_terms: list[float] = []
    market_terms: list[float] = []
    for signal_id, outcome in outcome_by_signal.items():
        signal = signal_by_id.get(signal_id)
        if signal is None:
            continue
        model_prob = float(signal.model_prob)
        market_prob = float(signal.market_price)
        model_terms.append((model_prob - outcome) ** 2)
        market_terms.append((market_prob - outcome) ** 2)

    if not model_terms:
        return {"n": 0, "model": None, "market": None, "delta": None}
    model = sum(model_terms) / len(model_terms)
    market = sum(market_terms) / len(market_terms)
    return {"n": len(model_terms), "model": model, "market": market, "delta": market - model}


async def build_measurement_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> MeasurementRun:
    run_at = now or datetime.now(UTC)
    async with session_factory() as session, session.begin():
        all_orders = (await session.execute(select(PaperOrder))).scalars().all()
        all_fills = (await session.execute(select(PaperFill))).scalars().all()
        latest_equity = (
            await session.execute(
                select(PaperEquitySnapshot)
                .order_by(PaperEquitySnapshot.ts.desc(), PaperEquitySnapshot.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        evidence = await _latest_evidence(session)
        evidence_gates = _parse_json(evidence.gates_json if evidence else None)
        evidence_data = _parse_json(evidence.data_health_json if evidence else None)
        replay = await _latest_replay(session)
        replay_params = _parse_json(replay.params_json if replay else None)
        strategy_repair = await _latest_strategy_repair(session)
        approved_policy_name = _repair_policy_name(strategy_repair)
        policy_signal_ids = await _signal_ids_for_policy(session, approved_policy_name)
        policy_scope_active = _is_repaired_policy(approved_policy_name)
        fills = (
            [fill for fill in all_fills if fill.signal_id in policy_signal_ids]
            if policy_scope_active
            else all_fills
        )
        orders = (
            [order for order in all_orders if order.signal_id in policy_signal_ids]
            if policy_scope_active
            else all_orders
        )
        entry_fills = [fill for fill in fills if fill.liquidity == "TAKER"]
        settlement_fills = [fill for fill in fills if fill.liquidity == "SETTLEMENT"]
        fill_policy_counts = await _policy_counts(session, entry_fills)
        all_entry_fills = [fill for fill in all_fills if fill.liquidity == "TAKER"]
        all_fill_policy_counts = await _policy_counts(session, all_entry_fills)
        entry_fills_by_outcome = await _fill_outcome_counts(session, entry_fills)
        settlement_fills_by_outcome = await _fill_outcome_counts(session, settlement_fills)
        pnl_by_city = await _paper_pnl_by_city(session, fills)
        expected_policy_counts: Counter[str] = (
            Counter({approved_policy_name: len(entry_fills)})
            if approved_policy_name is not None
            else Counter()
        )

        fee_failures = sum(
            1
            for fill in entry_fills
            if fill.fee_paid != taker_fee(fill.price, fill.size, settings.taker_fee_rate)
        )
        link_failures = sum(
            1 for fill in entry_fills if fill.signal_id is None or fill.book_snapshot_id is None
        )
        forecast_failures = await _forecast_asof_failures(session, entry_fills)
        settlement_price_failures = await _settlement_price_failures(session, settlement_fills)
        unsettled_resolved_positions = (
            await session.execute(
                select(func.count(PaperPosition.token_id))
                .join(Market, PaperPosition.market_id == Market.id)
                .where(
                    PaperPosition.qty > Decimal("0"),
                    PaperPosition.settled.is_(False),
                    Market.winner.is_not(None),
                )
            )
        ).scalar_one()
        filled_orders = [order for order in orders if order.status in {"FILLED", "PARTIAL"}]
        slippages = [order.slippage for order in filled_orders if order.slippage is not None]
        slippage_failures = sum(1 for order in filled_orders if order.slippage is None)

        cash_from_fills = (
            settings.paper_initial_cash
            + sum((fill.cash_delta for fill in all_fills), Decimal("0"))
        ).quantize(Decimal("0.00001"))
        latest_cash = latest_equity.cash if latest_equity else None
        ledger_cash_matches = latest_cash == cash_from_fills if latest_cash is not None else False
        paper_pnl = (
            sum((fill.cash_delta for fill in fills), Decimal("0")).quantize(Decimal("0.00001"))
            if policy_scope_active
            else (
                (latest_equity.equity - settings.paper_initial_cash).quantize(Decimal("0.00001"))
                if latest_equity
                else Decimal("0")
            )
        )
        replay_pnl = replay.total_pnl if replay else None
        replay_delta = (
            (paper_pnl - replay_pnl).quantize(Decimal("0.00001"))
            if replay_pnl is not None
            else None
        )
        forward_days = _as_int(evidence_data.get("forward_days")) or 0
        paper_brier = await _paper_brier_metrics(session, settlement_fills)
        brier_delta = _as_float(paper_brier.get("delta"))
        if brier_delta is None:
            brier_delta = _as_float(replay_params.get("brier_delta"))
        if brier_delta is None and replay is not None:
            brier_model = _as_float(replay_params.get("brier_model"))
            brier_market = _as_float(replay_params.get("brier_market"))
            if brier_model is not None and brier_market is not None:
                brier_delta = brier_market - brier_model

        checks = {
            "fee_reconciliation": _gate(
                bool(entry_fills) and fee_failures == 0,
                value={"entry_fills": len(entry_fills), "failures": fee_failures},
                required="all taker fills match official fee formula",
                reason="Paper fees must reconcile to shares * feeRate * p * (1 - p).",
            ),
            "fill_audit_links": _gate(
                bool(entry_fills) and link_failures == 0,
                value={"entry_fills": len(entry_fills), "failures": link_failures},
                required="signal_id and book_snapshot_id on every taker fill",
                reason="Every paper fill must be auditable to a signal and captured book.",
            ),
            "forecast_asof": _gate(
                bool(entry_fills) and forecast_failures == 0,
                value={"entry_fills": len(entry_fills), "failures": forecast_failures},
                required="ensemble fetched_at <= signal.ts",
                reason="Signals filled in paper must have an as-of ensemble snapshot.",
            ),
            "ledger_reconciliation": _gate(
                latest_equity is not None and ledger_cash_matches,
                value={"latest_cash": str(latest_cash), "cash_from_fills": str(cash_from_fills)},
                required="latest equity cash equals initial cash plus fill cash deltas",
                reason="Paper PnL must reconcile through the ledger.",
            ),
            "settlement_reconciliation": _gate(
                settlement_price_failures == 0 and int(unsettled_resolved_positions) == 0,
                value={
                    "settlement_fills": len(settlement_fills),
                    "price_failures": settlement_price_failures,
                    "unsettled_resolved_positions": int(unsettled_resolved_positions),
                },
                required="resolved paper positions settled at market winner",
                reason="Settlement paper fills must match resolved market outcomes.",
            ),
            "replay_comparison": _gate(
                replay is not None and bool(entry_fills),
                value={
                    "paper_pnl": str(paper_pnl),
                    "replay_pnl": str(replay_pnl) if replay_pnl is not None else None,
                    "delta": str(replay_delta) if replay_delta is not None else None,
                },
                required="latest max_edge replay and paper fills available",
                reason="Replay vs paper differences must be visible for slippage review.",
            ),
            "slippage_reconciliation": _gate(
                bool(filled_orders) and slippage_failures == 0,
                value={
                    "filled_orders": len(filled_orders),
                    "failures": slippage_failures,
                    "avg_slippage": _avg_decimal(slippages),
                },
                required="slippage recorded on every filled paper order",
                reason="Paper execution must expose slippage for every fillable order.",
            ),
            "ensemble_members": _gate(
                evidence_gates.get("ensemble_members", {}).get("passed") is True,
                value=evidence_gates.get("ensemble_members", {}).get("value"),
                required="ensemble gate passed",
                reason="Measurement is invalid without ensemble coverage.",
            ),
            "sample_size": _gate(
                forward_days >= MIN_FORWARD_DAYS or len(settlement_fills) >= MIN_RESOLVED_FILLS,
                value={"forward_days": forward_days, "resolved_paper_fills": len(settlement_fills)},
                required={
                    "forward_days": MIN_FORWARD_DAYS,
                    "resolved_paper_fills": MIN_RESOLVED_FILLS,
                },
                reason="Live review needs enough forward time or enough settled paper fills.",
            ),
            "max_edge_brier": _gate(
                brier_delta is not None and brier_delta > 0,
                value=brier_delta,
                required="brier_market - brier_model > 0",
                reason="The max_edge model must beat market implied probabilities.",
            ),
            "paper_pnl": _gate(
                paper_pnl > Decimal("0"),
                value=str(paper_pnl),
                required="paper PnL > 0 after fees",
                reason="Paper execution must be profitable after fees.",
            ),
            "city_quality": _gate(
                evidence_gates.get("city_quality", {}).get("passed") is True,
                value=evidence_gates.get("city_quality", {}).get("value"),
                required="no focus city needs_review",
                reason="No live review while focus-city metadata needs manual review.",
            ),
            "strategy_policy": _gate(
                strategy_repair is not None
                and strategy_repair.status == "PROMISING"
                and _is_repaired_policy(approved_policy_name)
                and bool(entry_fills)
                and fill_policy_counts == expected_policy_counts,
                value={
                    "strategy_repair_status": strategy_repair.status
                    if strategy_repair
                    else None,
                    "approved_policy_name": approved_policy_name,
                    "paper_fill_policy_counts": dict(fill_policy_counts),
                    "all_paper_fill_policy_counts": dict(all_fill_policy_counts),
                    "policy_scope_active": policy_scope_active,
                },
                required="all paper entry fills use the PROMISING repaired policy",
                reason="Paper execution must validate the same repaired policy before live review.",
            ),
        }
        status = (
            "READY_FOR_LIVE_REVIEW"
            if all(check["passed"] for check in checks.values())
            else "MEASURING"
        )

        fill_ts = [fill.ts.date() for fill in fills]
        window_start: date | None = min(fill_ts) if fill_ts else None
        window_end: date | None = max(fill_ts) if fill_ts else None
        summary = {
            "orders": len(orders),
            "filled_orders": len(filled_orders),
            "rejected_orders": sum(1 for order in orders if order.status == "REJECTED"),
            "all_orders": len(all_orders),
            "all_entry_fills": len(all_entry_fills),
            "entry_fills": len(entry_fills),
            "settlement_fills": len(settlement_fills),
            "policy_name": approved_policy_name,
            "paper_fill_policy_counts": dict(fill_policy_counts),
            "all_paper_fill_policy_counts": dict(all_fill_policy_counts),
            "policy_scope_active": policy_scope_active,
            "entry_fills_by_outcome": entry_fills_by_outcome,
            "settlement_fills_by_outcome": settlement_fills_by_outcome,
            "paper_pnl_by_city": pnl_by_city,
            "paper_pnl": str(paper_pnl),
            "cash": str(latest_cash) if latest_cash is not None else None,
            "equity": str(latest_equity.equity) if latest_equity else None,
            "readiness": status,
        }
        metrics = {
            "total_fee_paid": str(sum((fill.fee_paid for fill in entry_fills), Decimal("0"))),
            "avg_slippage": _avg_decimal(slippages),
            "paper_vs_replay_pnl_delta": str(replay_delta) if replay_delta is not None else None,
            "execution_proxy": "captured_book_taker_fak",
            "fee_formula": "shares * feeRate * p * (1 - p)",
            "latest_replay_run_at": replay.run_at.isoformat() if replay else None,
            "strategy_repair_run_id": strategy_repair.id if strategy_repair else None,
            "strategy_policy_name": approved_policy_name,
            "paper_brier_n": paper_brier["n"],
            "paper_brier_model": paper_brier["model"],
            "paper_brier_market": paper_brier["market"],
            "paper_brier_delta": paper_brier["delta"],
        }
        row = MeasurementRun(
            run_at=run_at,
            status=status,
            window_start=window_start,
            window_end=window_end,
            summary_json=_json_dumps(summary),
            metrics_json=_json_dumps(metrics),
            checks_json=_json_dumps(checks),
        )
        session.add(row)
        await session.flush()
        logger.info(
            "measurement report: status=%s orders=%d fills=%d",
            status,
            len(orders),
            len(fills),
        )
        return row


async def run(settings: Settings) -> MeasurementRun:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await build_measurement_report(session_factory, settings)
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a persisted measurement report.")
    parser.add_argument("--json", action="store_true", help="Print the persisted report as JSON.")
    return parser


def measurement_run_payload(row: MeasurementRun) -> dict[str, object]:
    return {
        "id": row.id,
        "run_at": row.run_at.isoformat(),
        "status": row.status,
        "window_start": row.window_start.isoformat() if row.window_start else None,
        "window_end": row.window_end.isoformat() if row.window_end else None,
        "summary": _parse_json(row.summary_json),
        "metrics": _parse_json(row.metrics_json),
        "checks": _parse_json(row.checks_json),
    }


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = asyncio.run(run(get_settings()))
    if args.json:
        print(_json_dumps(measurement_run_payload(result)))
        return
    logger.info("measurement %s status=%s", result.run_at.isoformat(), result.status)


if __name__ == "__main__":
    main()
