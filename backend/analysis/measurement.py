"""Measurement reports proving paper execution accounting."""

import argparse
import asyncio
import json
import logging
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
        expected = Decimal("1") if market.winner else Decimal("0")
        if fill.price != expected:
            failures += 1
    return failures


def _avg_decimal(values: list[Decimal]) -> str | None:
    if not values:
        return None
    return str((sum(values, Decimal("0")) / Decimal(len(values))).quantize(Decimal("0.00001")))


async def build_measurement_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> MeasurementRun:
    run_at = now or datetime.now(UTC)
    async with session_factory() as session, session.begin():
        orders = (await session.execute(select(PaperOrder))).scalars().all()
        fills = (await session.execute(select(PaperFill))).scalars().all()
        entry_fills = [fill for fill in fills if fill.liquidity == "TAKER"]
        settlement_fills = [fill for fill in fills if fill.liquidity == "SETTLEMENT"]
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

        cash_from_fills = (
            settings.paper_initial_cash
            + sum((fill.cash_delta for fill in fills), Decimal("0"))
        ).quantize(Decimal("0.00001"))
        latest_cash = latest_equity.cash if latest_equity else None
        ledger_cash_matches = latest_cash == cash_from_fills if latest_cash is not None else False
        paper_pnl = (
            (latest_equity.equity - settings.paper_initial_cash).quantize(Decimal("0.00001"))
            if latest_equity
            else Decimal("0")
        )
        replay_pnl = replay.total_pnl if replay else None
        replay_delta = (
            (paper_pnl - replay_pnl).quantize(Decimal("0.00001"))
            if replay_pnl is not None
            else None
        )
        forward_days = _as_int(evidence_data.get("forward_days")) or 0
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
            "ensemble_members": _gate(
                evidence_gates.get("ensemble_members", {}).get("passed") is True,
                value=evidence_gates.get("ensemble_members", {}).get("value"),
                required="ensemble gate passed",
                reason="Measurement is invalid without ensemble coverage.",
            ),
            "sample_size": _gate(
                forward_days >= MIN_FORWARD_DAYS and len(settlement_fills) >= MIN_RESOLVED_FILLS,
                value={"forward_days": forward_days, "resolved_paper_fills": len(settlement_fills)},
                required={
                    "forward_days": MIN_FORWARD_DAYS,
                    "resolved_paper_fills": MIN_RESOLVED_FILLS,
                },
                reason="Live review needs both forward time and settled paper fills.",
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
        }
        status = (
            "READY_FOR_LIVE_REVIEW"
            if all(check["passed"] for check in checks.values())
            else "MEASURING"
        )

        fill_ts = [fill.ts.date() for fill in fills]
        window_start: date | None = min(fill_ts) if fill_ts else None
        window_end: date | None = max(fill_ts) if fill_ts else None
        filled_orders = [order for order in orders if order.status in {"FILLED", "PARTIAL"}]
        slippages = [order.slippage for order in filled_orders if order.slippage is not None]
        summary = {
            "orders": len(orders),
            "filled_orders": len(filled_orders),
            "rejected_orders": sum(1 for order in orders if order.status == "REJECTED"),
            "entry_fills": len(entry_fills),
            "settlement_fills": len(settlement_fills),
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
    return argparse.ArgumentParser(description="Generate a persisted measurement report.")


def main() -> None:
    build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = asyncio.run(run(get_settings()))
    logger.info("measurement %s status=%s", result.run_at.isoformat(), result.status)


if __name__ == "__main__":
    main()
