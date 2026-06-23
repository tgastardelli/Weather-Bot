"""Live-readiness guardrails.

This module deliberately does not place real orders. It only proves whether the
paper/historical gates and safety switches would allow a future live engine.
"""

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.operational_quarantine import quarantine_payloads
from app.config import Settings
from app.db.models import (
    HistoricalValidationRun,
    MeasurementRun,
    Signal,
    StrategyDiscoveryRun,
    StrategyExperimentRun,
    StrategyRepairRun,
)

GEOBLOCK_URL = "https://polymarket.com/api/geoblock"
MICRO_CAPITAL_BANKROLL_CAP = Decimal("100")
MICRO_CAPITAL_MAX_STAKE = Decimal("5")
MICRO_CAPITAL_MAX_EXPOSURE = Decimal("15")
MICRO_CAPITAL_MAX_DAILY_LOSS = Decimal("10")


class LiveTradingBlocked(RuntimeError):
    """Raised when live execution is requested before readiness gates pass."""


class SupportsGet(Protocol):
    async def get(self, url: str, **kwargs: Any) -> httpx.Response: ...


@dataclass(frozen=True)
class GeoblockStatus:
    status: str
    allowed: bool
    payload: dict[str, object]
    error: str | None = None


@dataclass(frozen=True)
class LiveReadinessReport:
    status: str
    mode: str
    ready_for_live_review: bool
    checks: dict[str, dict[str, object]]
    blockers: list[str]
    risk_limits: dict[str, str]
    geoblock: dict[str, object]
    last_error: str | None

    def as_jsonable(self) -> dict[str, object]:
        return {
            "status": self.status,
            "mode": self.mode,
            "ready_for_live_review": self.ready_for_live_review,
            "checks": self.checks,
            "blockers": self.blockers,
            "risk_limits": self.risk_limits,
            "geoblock": self.geoblock,
            "last_error": self.last_error,
        }


async def fetch_geoblock_status(http: SupportsGet | None) -> GeoblockStatus:
    if http is None:
        return GeoblockStatus("UNKNOWN", False, {}, "http_client_unavailable")
    try:
        response = await http.get(GEOBLOCK_URL, timeout=10.0)
        response.raise_for_status()
        payload_raw: object = response.json()
    except Exception as exc:
        return GeoblockStatus("UNKNOWN", False, {}, str(exc))
    payload = payload_raw if isinstance(payload_raw, dict) else {}
    allowed = _parse_geoblock_allowed(payload)
    status = "ALLOWED" if allowed else "BLOCKED"
    return GeoblockStatus(status, allowed, _sanitize_geoblock_payload(payload), None)


async def build_live_readiness_report(
    session: AsyncSession,
    settings: Settings,
    *,
    geoblock: GeoblockStatus | None = None,
) -> LiveReadinessReport:
    measurement = (
        await session.execute(
            select(MeasurementRun).order_by(MeasurementRun.run_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    historical = (
        await session.execute(
            select(HistoricalValidationRun)
            .order_by(HistoricalValidationRun.run_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    repair = (
        await session.execute(
            select(StrategyRepairRun)
            .order_by(StrategyRepairRun.run_at.desc(), StrategyRepairRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    experiment = (
        await session.execute(
            select(StrategyExperimentRun)
            .order_by(StrategyExperimentRun.run_at.desc(), StrategyExperimentRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    discovery = (
        await session.execute(
            select(StrategyDiscoveryRun)
            .order_by(StrategyDiscoveryRun.run_at.desc(), StrategyDiscoveryRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    repair_policy_name = _repair_policy_name(repair)
    measurement_policy_name = _measurement_policy_name(measurement)
    strategy_cities = _run_cities(repair.cities_json if repair is not None else None)
    if repair is None and historical is not None:
        strategy_cities = _run_cities(historical.cities_json)
    quarantined_strategy_cities = quarantine_payloads(set(strategy_cities))
    repaired_policy_matches = (
        repair_policy_name is not None
        and repair_policy_name.startswith(("repair_v2", "repair_v3", "repair_v4", "repair_v5"))
        and measurement_policy_name == repair_policy_name
    )
    strategy_gate_passed = (
        repair.status == "PROMISING"
        if repair is not None
        else historical is not None and historical.status == "PROMISING"
    )
    risk_limits = _risk_limits(settings)
    geoblock_status = geoblock or GeoblockStatus("UNKNOWN", False, {}, "not_checked")
    checks = {
        "mode_live": _check(
            settings.mode == "live",
            value=settings.mode,
            required="MODE=live",
            reason="Live trading requires an explicit runtime mode.",
        ),
        "micro_capital_lock": _check(
            settings.live_trading_enabled,
            value=settings.live_trading_enabled,
            required=True,
            reason="A second explicit live toggle is required before real orders.",
        ),
        "risk_limits": _check(
            _risk_limits_within_micro_capital(settings),
            value=risk_limits,
            required={
                "max_stake_per_order_lte": str(MICRO_CAPITAL_MAX_STAKE),
                "max_exposure_per_market_lte": str(MICRO_CAPITAL_MAX_EXPOSURE),
                "max_daily_loss_lte": str(MICRO_CAPITAL_MAX_DAILY_LOSS),
                "bankroll_cap_lte": str(MICRO_CAPITAL_BANKROLL_CAP),
            },
            reason="Micro-capital pilot must start with conservative risk caps.",
        ),
        "kill_switch": _check(
            settings.live_kill_switch_enabled and not settings.live_kill_switch_engaged,
            value={
                "enabled": settings.live_kill_switch_enabled,
                "engaged": settings.live_kill_switch_engaged,
            },
            required={"enabled": True, "engaged": False},
            reason="Kill switch must be functional and not currently engaged.",
        ),
        "geoblock": _check(
            geoblock_status.allowed,
            value={"status": geoblock_status.status, "error": geoblock_status.error},
            required="ALLOWED",
            reason="Polymarket geoblock preflight must pass before live orders.",
        ),
        "strategy_repair": _check(
            strategy_gate_passed,
            value={
                "strategy_repair_status": repair.status if repair else None,
                "historical_validation_status": historical.status if historical else None,
                "policy_name": repair_policy_name,
            },
            required=(
                "strategy_repair PROMISING or historical_validation PROMISING "
                "when no repair run exists"
            ),
            reason="The repaired historical strategy must pass before any capital is used.",
        ),
        "operational_cities": _check(
            not quarantined_strategy_cities,
            value={
                "strategy_cities": strategy_cities,
                "operational_quarantine": quarantined_strategy_cities,
            },
            required="approved strategy cannot include quarantined research-only cities",
            reason="Quarantined cities remain diagnostic-only until resolution is verified.",
        ),
        "strategy_experiments": _check(
            True,
            value={
                "latest_experiment_status": experiment.status if experiment else None,
                "experiment_set": experiment.experiment_set if experiment else None,
                "live_release": False,
            },
            required="diagnostic only; never a live-readiness gate",
            reason="Diagnostic experiments can inform research but cannot approve live trading.",
        ),
        "strategy_discovery": _check(
            True,
            value={
                "latest_discovery_status": discovery.status if discovery else None,
                "universe": discovery.universe if discovery else None,
                "live_release": False,
            },
            required="diagnostic only; never a live-readiness gate",
            reason=(
                "Strategy discovery can select shadow-paper research but cannot "
                "approve live trading."
            ),
        ),
        "measurement": _check(
            measurement is not None
            and measurement.status == "READY_FOR_LIVE_REVIEW"
            and (repair_policy_name is None or repaired_policy_matches),
            value={
                "measurement_status": measurement.status if measurement else None,
                "measurement_policy_name": measurement_policy_name,
                "repair_policy_name": repair_policy_name,
            },
            required="READY_FOR_LIVE_REVIEW with the approved repaired policy",
            reason="Paper execution measurement must validate the approved repaired policy.",
        ),
    }
    blockers = [key for key, check in checks.items() if check["passed"] is not True]
    ready = not blockers
    return LiveReadinessReport(
        status="READY_FOR_MICRO_CAPITAL" if ready else "BLOCKED",
        mode=settings.mode,
        ready_for_live_review=ready,
        checks=checks,
        blockers=blockers,
        risk_limits=risk_limits,
        geoblock={
            "status": geoblock_status.status,
            "allowed": geoblock_status.allowed,
            "payload": geoblock_status.payload,
            "error": geoblock_status.error,
        },
        last_error=None if ready else ",".join(blockers),
    )


class LiveEngine:
    """Interface placeholder for future live execution.

    A later micro-capital phase can wire AsyncSecureClient here. For now the
    engine only enforces readiness and refuses to place orders.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def submit_signal(self, session: AsyncSession, signal: Signal) -> None:
        report = await build_live_readiness_report(session, self.settings)
        raise LiveTradingBlocked(
            f"live execution disabled: {report.status}; signal_id={signal.id}; "
            f"blockers={','.join(report.blockers)}"
        )


def _check(passed: bool, *, value: object, required: object, reason: str) -> dict[str, object]:
    return {"passed": passed, "value": value, "required": required, "reason": reason}


def _parse_json(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _run_cities(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(city) for city in parsed if isinstance(city, str)]


def _repair_policy_name(repair: StrategyRepairRun | None) -> str | None:
    if repair is None:
        return None
    payload = _parse_json(repair.best_variant_json)
    value = payload.get("policy_name") or payload.get("name")
    return value if isinstance(value, str) else None


def _measurement_policy_name(measurement: MeasurementRun | None) -> str | None:
    if measurement is None:
        return None
    summary = _parse_json(measurement.summary_json)
    metrics = _parse_json(measurement.metrics_json)
    value = summary.get("policy_name") or metrics.get("strategy_policy_name")
    return value if isinstance(value, str) else None


def _risk_limits(settings: Settings) -> dict[str, str]:
    return {
        "live_bankroll_cap": str(settings.live_bankroll_cap),
        "max_stake_per_order": str(settings.max_stake_per_order),
        "max_exposure_per_market": str(settings.max_exposure_per_market),
        "max_daily_loss": str(settings.max_daily_loss),
    }


def _risk_limits_within_micro_capital(settings: Settings) -> bool:
    return (
        settings.live_bankroll_cap <= MICRO_CAPITAL_BANKROLL_CAP
        and settings.max_stake_per_order <= MICRO_CAPITAL_MAX_STAKE
        and settings.max_exposure_per_market <= MICRO_CAPITAL_MAX_EXPOSURE
        and settings.max_daily_loss <= MICRO_CAPITAL_MAX_DAILY_LOSS
    )


def _parse_geoblock_allowed(payload: dict[str, object]) -> bool:
    for key in ("blocked", "geoBlocked", "geo_blocked", "restricted"):
        value = payload.get(key)
        if isinstance(value, bool):
            return not value
    for key in ("allowed", "canTrade", "can_trade"):
        value = payload.get(key)
        if isinstance(value, bool):
            return value
    return False


def _sanitize_geoblock_payload(payload: dict[str, object]) -> dict[str, object]:
    safe_keys = {"blocked", "geoBlocked", "geo_blocked", "restricted", "allowed", "canTrade"}
    return {key: value for key, value in payload.items() if key in safe_keys}
