import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import type {
  BacktestResult,
  CalibrationMetric,
  CityVolatilityMetric,
  EvidenceResponse,
  HistoryBackfillResponse,
  HistoricalValidationResponse,
  LiveReadinessResponse,
  MeasurementResponse,
} from "@/types/api"

export const analysisKeys = {
  calibration: ["analysis", "calibration"] as const,
  backtests: ["analysis", "backtests"] as const,
  cityVolatility: ["analysis", "city-volatility"] as const,
  evidence: ["analysis", "evidence"] as const,
  historyBackfill: ["analysis", "history-backfill"] as const,
  historicalValidation: ["analysis", "historical-validation"] as const,
  liveReadiness: ["analysis", "live-readiness"] as const,
  measurement: ["analysis", "measurement"] as const,
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
