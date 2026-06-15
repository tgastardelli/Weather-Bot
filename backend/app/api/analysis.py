"""Read-only analysis endpoints."""

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.deps import SessionDep
from app.api.schemas import (
    BacktestOut,
    CalibrationOut,
    CityVolatilityOut,
    EvidenceResponse,
    EvidenceRunOut,
    HistoricalValidationResponse,
    HistoricalValidationRunOut,
    HistoryBackfillResponse,
    HistoryBackfillRunOut,
    LiveReadinessResponse,
    MeasurementResponse,
    MeasurementRunOut,
)
from app.config import get_settings
from app.db.models import (
    BacktestResult,
    CalibrationMetric,
    CityVolatilityMetric,
    EvidenceRun,
    HistoricalValidationRun,
    HistoryBackfillRun,
    MeasurementRun,
)
from app.execution.live import build_live_readiness_report, fetch_geoblock_status

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("/calibration")
async def list_calibration(session: SessionDep) -> list[CalibrationOut]:
    rows = (
        (
            await session.execute(
                select(CalibrationMetric).order_by(
                    CalibrationMetric.city_slug,
                    CalibrationMetric.model,
                    CalibrationMetric.lead_days,
                )
            )
        )
        .scalars()
        .all()
    )
    return [CalibrationOut.model_validate(row) for row in rows]


@router.get("/backtests")
async def list_backtests(session: SessionDep) -> list[BacktestOut]:
    rows = (
        (
            await session.execute(
                select(BacktestResult).order_by(BacktestResult.run_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [BacktestOut.model_validate(row) for row in rows]


@router.get("/city-volatility")
async def list_city_volatility(session: SessionDep) -> list[CityVolatilityOut]:
    latest_run = (
        await session.execute(select(func.max(CityVolatilityMetric.computed_at)))
    ).scalar_one_or_none()
    if latest_run is None:
        return []

    rows = (
        (
            await session.execute(
                select(CityVolatilityMetric)
                .where(CityVolatilityMetric.computed_at == latest_run)
                .order_by(CityVolatilityMetric.reward_volatility_score.desc())
            )
        )
        .scalars()
        .all()
    )
    return [CityVolatilityOut.model_validate(row) for row in rows]


@router.get("/evidence")
async def get_evidence(session: SessionDep) -> EvidenceResponse:
    rows = (
        (
            await session.execute(
                select(EvidenceRun).order_by(EvidenceRun.run_at.desc()).limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [EvidenceRunOut.model_validate(row) for row in rows]
    return EvidenceResponse(latest=history[0] if history else None, history=history)


@router.get("/measurement")
async def get_measurement(session: SessionDep) -> MeasurementResponse:
    rows = (
        (
            await session.execute(
                select(MeasurementRun).order_by(MeasurementRun.run_at.desc()).limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [MeasurementRunOut.model_validate(row) for row in rows]
    return MeasurementResponse(latest=history[0] if history else None, history=history)


@router.get("/historical-validation")
async def get_historical_validation(session: SessionDep) -> HistoricalValidationResponse:
    rows = (
        (
            await session.execute(
                select(HistoricalValidationRun)
                .order_by(HistoricalValidationRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [HistoricalValidationRunOut.model_validate(row) for row in rows]
    return HistoricalValidationResponse(
        latest=history[0] if history else None,
        history=history,
    )


@router.get("/history-backfill")
async def get_history_backfill(session: SessionDep) -> HistoryBackfillResponse:
    rows = (
        (
            await session.execute(
                select(HistoryBackfillRun)
                .order_by(HistoryBackfillRun.run_at.desc())
                .limit(24)
            )
        )
        .scalars()
        .all()
    )
    history = [HistoryBackfillRunOut.model_validate(row) for row in rows]
    return HistoryBackfillResponse(latest=history[0] if history else None, history=history)


@router.get("/live-readiness")
async def get_live_readiness(
    request: Request, session: SessionDep
) -> LiveReadinessResponse:
    settings = get_settings()
    geoblock = None
    if settings.mode == "live":
        geoblock = await fetch_geoblock_status(getattr(request.app.state, "http", None))
    report = await build_live_readiness_report(session, settings, geoblock=geoblock)
    return LiveReadinessResponse.model_validate(report.as_jsonable())
