import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import type {
  BacktestResult,
  CalibrationMetric,
  CityEdgeRankingResponse,
  CityOnboardingResponse,
  CityPromotionApplyResponse,
  CityResolutionPromotionAuditResponse,
  CityResearchAuditResponse,
  CityVolatilityMetric,
  DiscoveryCandidateAuditResponse,
  EvidenceResponse,
  FeatureCandidateAuditResponse,
  FeatureDiscoveryResponse,
  HighRewardCityHuntResponse,
  HighRewardPaperStatusResponse,
  HistoryBackfillResponse,
  HistoricalDiagnosticsResponse,
  HistoricalValidationResponse,
  LiveReadinessResponse,
  MeasurementResponse,
  StrategyDiscoveryResponse,
  StrategyExperimentResponse,
  StrategyHypothesisAuditResponse,
  StrategyRepairResponse,
  StrategyShadowDecisionResponse,
  WeatherCityDiscoveryResponse,
} from "@/types/api"

export const analysisKeys = {
  calibration: ["analysis", "calibration"] as const,
  backtests: ["analysis", "backtests"] as const,
  cityVolatility: ["analysis", "city-volatility"] as const,
  cityEdgeRanking: ["analysis", "city-edge-ranking"] as const,
  cityOnboarding: ["analysis", "city-onboarding"] as const,
  cityPromotionApply: ["analysis", "city-promotion-apply"] as const,
  cityResearchAudit: ["analysis", "city-research-audit"] as const,
  cityResolutionPromotionAudit: ["analysis", "city-resolution-promotion-audit"] as const,
  discoveryCandidateAudit: ["analysis", "discovery-candidate-audit"] as const,
  evidence: ["analysis", "evidence"] as const,
  featureCandidateAudit: ["analysis", "feature-candidate-audit"] as const,
  featureDiscovery: ["analysis", "feature-discovery"] as const,
  highRewardCityHunt: ["analysis", "high-reward-city-hunt"] as const,
  highRewardPaperStatus: ["analysis", "high-reward-paper-status"] as const,
  historyBackfill: ["analysis", "history-backfill"] as const,
  historicalDiagnostics: ["analysis", "historical-diagnostics"] as const,
  historicalValidation: ["analysis", "historical-validation"] as const,
  liveReadiness: ["analysis", "live-readiness"] as const,
  measurement: ["analysis", "measurement"] as const,
  strategyDiscovery: ["analysis", "strategy-discovery"] as const,
  strategyExperiments: ["analysis", "strategy-experiments"] as const,
  strategyHypothesisAudit: ["analysis", "strategy-hypothesis-audit"] as const,
  strategyRepair: ["analysis", "strategy-repair"] as const,
  strategyShadow: ["analysis", "strategy-shadow"] as const,
  weatherCityDiscovery: ["analysis", "weather-city-discovery"] as const,
}

export function useCalibration() {
  return useQuery({
    queryKey: analysisKeys.calibration,
    queryFn: () => api<CalibrationMetric[]>("/analysis/calibration"),
    staleTime: 60_000,
  })
}

export function useBacktests() {
  return useQuery({
    queryKey: analysisKeys.backtests,
    queryFn: () => api<BacktestResult[]>("/analysis/backtests"),
    staleTime: 60_000,
  })
}

export function useCityVolatility() {
  return useQuery({
    queryKey: analysisKeys.cityVolatility,
    queryFn: () => api<CityVolatilityMetric[]>("/analysis/city-volatility"),
    staleTime: 300_000,
  })
}

export function useCityEdgeRanking() {
  return useQuery({
    queryKey: analysisKeys.cityEdgeRanking,
    queryFn: () => api<CityEdgeRankingResponse>("/analysis/city-edge-ranking"),
    staleTime: 300_000,
  })
}

export function useWeatherCityDiscovery() {
  return useQuery({
    queryKey: analysisKeys.weatherCityDiscovery,
    queryFn: () => api<WeatherCityDiscoveryResponse>("/analysis/weather-city-discovery"),
    staleTime: 300_000,
  })
}

export function useCityResolutionPromotionAudit() {
  return useQuery({
    queryKey: analysisKeys.cityResolutionPromotionAudit,
    queryFn: () =>
      api<CityResolutionPromotionAuditResponse>(
        "/analysis/city-resolution-promotion-audit",
      ),
    staleTime: 300_000,
  })
}

export function useCityPromotionApply() {
  return useQuery({
    queryKey: analysisKeys.cityPromotionApply,
    queryFn: () => api<CityPromotionApplyResponse>("/analysis/city-promotion-apply"),
    staleTime: 300_000,
  })
}

export function useCityResearchAudit() {
  return useQuery({
    queryKey: analysisKeys.cityResearchAudit,
    queryFn: () => api<CityResearchAuditResponse>("/analysis/city-research-audit"),
    staleTime: 300_000,
  })
}

export function useCityOnboarding() {
  return useQuery({
    queryKey: analysisKeys.cityOnboarding,
    queryFn: () => api<CityOnboardingResponse>("/analysis/city-onboarding"),
    staleTime: 300_000,
  })
}

export function useDiscoveryCandidateAudit() {
  return useQuery({
    queryKey: analysisKeys.discoveryCandidateAudit,
    queryFn: () =>
      api<DiscoveryCandidateAuditResponse>("/analysis/discovery-candidate-audit"),
    staleTime: 60_000,
  })
}

export function useEvidence() {
  return useQuery({
    queryKey: analysisKeys.evidence,
    queryFn: () => api<EvidenceResponse>("/analysis/evidence"),
    staleTime: 60_000,
  })
}

export function useMeasurement() {
  return useQuery({
    queryKey: analysisKeys.measurement,
    queryFn: () => api<MeasurementResponse>("/analysis/measurement"),
    staleTime: 60_000,
  })
}

export function useHistoricalValidation() {
  return useQuery({
    queryKey: analysisKeys.historicalValidation,
    queryFn: () => api<HistoricalValidationResponse>("/analysis/historical-validation"),
    staleTime: 60_000,
  })
}

export function useHistoricalDiagnostics() {
  return useQuery({
    queryKey: analysisKeys.historicalDiagnostics,
    queryFn: () => api<HistoricalDiagnosticsResponse>("/analysis/historical-diagnostics"),
    staleTime: 60_000,
  })
}

export function useStrategyRepair() {
  return useQuery({
    queryKey: analysisKeys.strategyRepair,
    queryFn: () => api<StrategyRepairResponse>("/analysis/strategy-repair"),
    staleTime: 60_000,
  })
}

export function useStrategyHypothesisAudit() {
  return useQuery({
    queryKey: analysisKeys.strategyHypothesisAudit,
    queryFn: () =>
      api<StrategyHypothesisAuditResponse>("/analysis/strategy-hypothesis-audit"),
    staleTime: 60_000,
  })
}

export function useStrategyExperiments() {
  return useQuery({
    queryKey: analysisKeys.strategyExperiments,
    queryFn: () => api<StrategyExperimentResponse>("/analysis/strategy-experiments"),
    staleTime: 60_000,
  })
}

export function useStrategyDiscovery() {
  return useQuery({
    queryKey: analysisKeys.strategyDiscovery,
    queryFn: () => api<StrategyDiscoveryResponse>("/analysis/strategy-discovery"),
    staleTime: 60_000,
  })
}

export function useFeatureDiscovery() {
  return useQuery({
    queryKey: analysisKeys.featureDiscovery,
    queryFn: () => api<FeatureDiscoveryResponse>("/analysis/feature-discovery"),
    staleTime: 60_000,
  })
}

export function useFeatureCandidateAudit() {
  return useQuery({
    queryKey: analysisKeys.featureCandidateAudit,
    queryFn: () => api<FeatureCandidateAuditResponse>("/analysis/feature-candidate-audit"),
    staleTime: 60_000,
  })
}

export function useHighRewardCityHunt() {
  return useQuery({
    queryKey: analysisKeys.highRewardCityHunt,
    queryFn: () => api<HighRewardCityHuntResponse>("/analysis/high-reward-city-hunt"),
    staleTime: 60_000,
  })
}

export function useHighRewardPaperStatus() {
  return useQuery({
    queryKey: analysisKeys.highRewardPaperStatus,
    queryFn: () =>
      api<HighRewardPaperStatusResponse>("/analysis/high-reward-paper-status"),
    staleTime: 60_000,
  })
}

export function useStrategyShadow() {
  return useQuery({
    queryKey: analysisKeys.strategyShadow,
    queryFn: () => api<StrategyShadowDecisionResponse>("/analysis/strategy-shadow"),
    staleTime: 60_000,
  })
}

export function useHistoryBackfill() {
  return useQuery({
    queryKey: analysisKeys.historyBackfill,
    queryFn: () => api<HistoryBackfillResponse>("/analysis/history-backfill"),
    staleTime: 60_000,
  })
}

export function useLiveReadiness() {
  return useQuery({
    queryKey: analysisKeys.liveReadiness,
    queryFn: () => api<LiveReadinessResponse>("/analysis/live-readiness"),
    staleTime: 60_000,
  })
}
