"""Read-only analysis endpoints."""

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.deps import SessionDep
from app.api.schemas import (
    BacktestOut,
    CalibrationOut,
    CityEdgeRankingResponse,
    CityEdgeRankingRunOut,
    CityOnboardingResponse,
    CityOnboardingRunOut,
    CityPromotionApplyResponse,
    CityPromotionApplyRunOut,
    CityResearchAuditResponse,
    CityResearchAuditRunOut,
    CityResolutionPromotionAuditResponse,
    CityResolutionPromotionAuditRunOut,
    CityVolatilityOut,
    DiscoveryCandidateAuditResponse,
    DiscoveryCandidateAuditRunOut,
    EvidenceResponse,
    EvidenceRunOut,
    FeatureCandidateAuditResponse,
    FeatureCandidateAuditRunOut,
    FeatureDiscoveryResponse,
    FeatureDiscoveryRunOut,
    HighRewardCityHuntResponse,
    HighRewardCityHuntRunOut,
    HighRewardPaperStatusResponse,
    HistoricalDiagnosticsResponse,
    HistoricalDiagnosticsRunOut,
    HistoricalValidationResponse,
    HistoricalValidationRunOut,
    HistoryBackfillResponse,
    HistoryBackfillRunOut,
    LiveReadinessResponse,
    MeasurementResponse,
    MeasurementRunOut,
    StrategyDiscoveryResponse,
    StrategyDiscoveryRunOut,
    StrategyExperimentResponse,
    StrategyExperimentRunOut,
    StrategyHypothesisAuditResponse,
    StrategyHypothesisAuditRunOut,
    StrategyRepairResponse,
    StrategyRepairRunOut,
    StrategyShadowDecisionOut,
    StrategyShadowDecisionResponse,
    WeatherCityDiscoveryResponse,
    WeatherCityDiscoveryRunOut,
)
from app.config import get_settings
from app.db.models import (
    BacktestResult,
    CalibrationMetric,
    CityEdgeRankingRun,
    CityOnboardingRun,
    CityPromotionApplyRun,
    CityResearchAuditRun,
    CityResolutionPromotionAuditRun,
    CityVolatilityMetric,
    DiscoveryCandidateAuditRun,
    EvidenceRun,
    FeatureCandidateAuditRun,
    FeatureDiscoveryRun,
    HighRewardCityHuntRun,
    HistoricalDiagnosticsRun,
    HistoricalValidationRun,
    HistoryBackfillRun,
    MeasurementRun,
    StrategyDiscoveryRun,
    StrategyExperimentRun,
    StrategyHypothesisAuditRun,
    StrategyRepairRun,
    StrategyShadowDecision,
    WeatherCityDiscoveryRun,
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


@router.get("/high-reward-paper-status")
async def get_high_reward_paper_status(session: SessionDep) -> HighRewardPaperStatusResponse:
    from analysis.high_reward_paper_status import build_high_reward_paper_status

    payload = await build_high_reward_paper_status(session, get_settings())
    return HighRewardPaperStatusResponse.model_validate(payload)


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


@router.get("/historical-diagnostics")
async def get_historical_diagnostics(session: SessionDep) -> HistoricalDiagnosticsResponse:
    rows = (
        (
            await session.execute(
                select(HistoricalDiagnosticsRun)
                .order_by(HistoricalDiagnosticsRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [HistoricalDiagnosticsRunOut.model_validate(row) for row in rows]
    return HistoricalDiagnosticsResponse(
        latest=history[0] if history else None,
        history=history,
    )


@router.get("/strategy-repair")
async def get_strategy_repair(session: SessionDep) -> StrategyRepairResponse:
    rows = (
        (
            await session.execute(
                select(StrategyRepairRun).order_by(StrategyRepairRun.run_at.desc()).limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [StrategyRepairRunOut.model_validate(row) for row in rows]
    return StrategyRepairResponse(latest=history[0] if history else None, history=history)


@router.get("/strategy-hypothesis-audit")
async def get_strategy_hypothesis_audit(
    session: SessionDep,
) -> StrategyHypothesisAuditResponse:
    rows = (
        (
            await session.execute(
                select(StrategyHypothesisAuditRun)
                .order_by(StrategyHypothesisAuditRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [StrategyHypothesisAuditRunOut.model_validate(row) for row in rows]
    return StrategyHypothesisAuditResponse(
        latest=history[0] if history else None,
        history=history,
    )


@router.get("/strategy-experiments")
async def get_strategy_experiments(session: SessionDep) -> StrategyExperimentResponse:
    rows = (
        (
            await session.execute(
                select(StrategyExperimentRun)
                .order_by(StrategyExperimentRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [StrategyExperimentRunOut.model_validate(row) for row in rows]
    return StrategyExperimentResponse(latest=history[0] if history else None, history=history)


@router.get("/strategy-discovery")
async def get_strategy_discovery(session: SessionDep) -> StrategyDiscoveryResponse:
    rows = (
        (
            await session.execute(
                select(StrategyDiscoveryRun)
                .order_by(StrategyDiscoveryRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [StrategyDiscoveryRunOut.model_validate(row) for row in rows]
    return StrategyDiscoveryResponse(latest=history[0] if history else None, history=history)


@router.get("/feature-discovery")
async def get_feature_discovery(session: SessionDep) -> FeatureDiscoveryResponse:
    rows = (
        (
            await session.execute(
                select(FeatureDiscoveryRun)
                .order_by(FeatureDiscoveryRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [FeatureDiscoveryRunOut.model_validate(row) for row in rows]
    return FeatureDiscoveryResponse(latest=history[0] if history else None, history=history)


@router.get("/feature-candidate-audit")
async def get_feature_candidate_audit(session: SessionDep) -> FeatureCandidateAuditResponse:
    rows = (
        (
            await session.execute(
                select(FeatureCandidateAuditRun)
                .order_by(FeatureCandidateAuditRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [FeatureCandidateAuditRunOut.model_validate(row) for row in rows]
    return FeatureCandidateAuditResponse(
        latest=history[0] if history else None,
        history=history,
    )


@router.get("/high-reward-city-hunt")
async def get_high_reward_city_hunt(session: SessionDep) -> HighRewardCityHuntResponse:
    rows = (
        (
            await session.execute(
                select(HighRewardCityHuntRun)
                .order_by(HighRewardCityHuntRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [HighRewardCityHuntRunOut.model_validate(row) for row in rows]
    return HighRewardCityHuntResponse(latest=history[0] if history else None, history=history)


@router.get("/strategy-shadow")
async def get_strategy_shadow(session: SessionDep) -> StrategyShadowDecisionResponse:
    rows = (
        (
            await session.execute(
                select(StrategyShadowDecision)
                .order_by(StrategyShadowDecision.ts.desc(), StrategyShadowDecision.id.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return StrategyShadowDecisionResponse(
        latest=[StrategyShadowDecisionOut.model_validate(row) for row in rows]
    )


@router.get("/discovery-candidate-audit")
async def get_discovery_candidate_audit(
    session: SessionDep,
) -> DiscoveryCandidateAuditResponse:
    rows = (
        (
            await session.execute(
                select(DiscoveryCandidateAuditRun)
                .order_by(DiscoveryCandidateAuditRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [DiscoveryCandidateAuditRunOut.model_validate(row) for row in rows]
    return DiscoveryCandidateAuditResponse(latest=history[0] if history else None, history=history)


@router.get("/city-research-audit")
async def get_city_research_audit(session: SessionDep) -> CityResearchAuditResponse:
    rows = (
        (
            await session.execute(
                select(CityResearchAuditRun)
                .order_by(CityResearchAuditRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [CityResearchAuditRunOut.model_validate(row) for row in rows]
    return CityResearchAuditResponse(latest=history[0] if history else None, history=history)


@router.get("/city-edge-ranking")
async def get_city_edge_ranking(session: SessionDep) -> CityEdgeRankingResponse:
    rows = (
        (
            await session.execute(
                select(CityEdgeRankingRun)
                .order_by(CityEdgeRankingRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [CityEdgeRankingRunOut.model_validate(row) for row in rows]
    return CityEdgeRankingResponse(latest=history[0] if history else None, history=history)


@router.get("/weather-city-discovery")
async def get_weather_city_discovery(session: SessionDep) -> WeatherCityDiscoveryResponse:
    rows = (
        (
            await session.execute(
                select(WeatherCityDiscoveryRun)
                .order_by(WeatherCityDiscoveryRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [WeatherCityDiscoveryRunOut.model_validate(row) for row in rows]
    return WeatherCityDiscoveryResponse(latest=history[0] if history else None, history=history)


@router.get("/city-resolution-promotion-audit")
async def get_city_resolution_promotion_audit(
    session: SessionDep,
) -> CityResolutionPromotionAuditResponse:
    rows = (
        (
            await session.execute(
                select(CityResolutionPromotionAuditRun)
                .order_by(CityResolutionPromotionAuditRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [CityResolutionPromotionAuditRunOut.model_validate(row) for row in rows]
    return CityResolutionPromotionAuditResponse(
        latest=history[0] if history else None,
        history=history,
    )


@router.get("/city-promotion-apply")
async def get_city_promotion_apply(session: SessionDep) -> CityPromotionApplyResponse:
    rows = (
        (
            await session.execute(
                select(CityPromotionApplyRun)
                .order_by(CityPromotionApplyRun.run_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [CityPromotionApplyRunOut.model_validate(row) for row in rows]
    return CityPromotionApplyResponse(latest=history[0] if history else None, history=history)


@router.get("/city-onboarding")
async def get_city_onboarding(session: SessionDep) -> CityOnboardingResponse:
    rows = (
        (
            await session.execute(
                select(CityOnboardingRun).order_by(CityOnboardingRun.run_at.desc()).limit(12)
            )
        )
        .scalars()
        .all()
    )
    history = [CityOnboardingRunOut.model_validate(row) for row in rows]
    return CityOnboardingResponse(latest=history[0] if history else None, history=history)


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
