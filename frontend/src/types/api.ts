export interface City {
  slug: string
  name: string
  station_code: string | null
  unit: string
  resolution_source: string | null
  rounding: string
  needs_review: boolean
  active: boolean
}

export interface Bucket {
  market_id: string
  label: string
  kind: string
  low: string | null
  high: string | null
  yes_token_id: string
  best_bid: string | null
  best_ask: string | null
  mid: string | null
  model_prob: number | null
  edge_net: string | null
  winner: boolean | null
}

export interface Event {
  id: string
  slug: string
  title: string
  city_slug: string
  target_date: string
  end_date: string | null
  closed: boolean
  volume: number | null
  liquidity: number | null
  buckets: Bucket[]
}

export interface PricePoint {
  ts: string
  market_id: string
  label: string
  mid: string | null
}

export interface ForecastPoint {
  fetched_at: string
  model: string
  source: string
  target_date: string
  tmax_c: number | null
  p10: number | null
  p50: number | null
  p90: number | null
}

export interface ObservationPoint {
  observed_at: string
  temp_c: number
}

export interface EventDetail {
  event: Event
  prices: PricePoint[]
  forecasts: ForecastPoint[]
  observations: ObservationPoint[]
}

export interface Signal {
  id: number
  ts: string
  market_id: string
  side: string
  profile: string
  model_prob: number
  market_price: string
  edge_gross: string
  edge_net: string
  stake: string
  status: string
  reason: string | null
  bucket_label: string
  event_slug: string
  city_slug: string
}

export interface CalibrationMetric {
  city_slug: string
  model: string
  lead_days: number
  bias_c: number
  mae_c: number
  residual_std_c: number
  n_samples: number
  computed_at: string
}

export interface BacktestResult {
  run_at: string
  profile: string
  n_trades: number
  n_wins: number
  total_staked: string
  total_pnl: string
  win_rate: number
  profit_factor: number | null
  max_drawdown: string
  params_json: string
}

export interface CityVolatilityMetric {
  computed_at: string
  city_slug: string
  station_code: string | null
  n_samples: number
  forecast_mae_c: number
  tail_miss_rate_2c: number
  tail_miss_rate_3c: number
  tail_miss_rate_5c: number
  upside_surprise_rate_3c: number
  downside_surprise_rate_3c: number
  avg_intraday_range_c: number
  p90_intraday_range_c: number
  max_3h_move_c: number
  max_6h_move_c: number
  reward_volatility_score: number
  data_quality: string
  lead_mae_json: string
  params_json: string
}

export interface EvidenceRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  cities_json: string
  data_health_json: string
  model_health_json: string
  trading_json: string
  gates_json: string
}

export interface EvidenceResponse {
  latest: EvidenceRun | null
  history: EvidenceRun[]
}

export interface MeasurementRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  summary_json: string
  metrics_json: string
  checks_json: string
}

export interface MeasurementResponse {
  latest: MeasurementRun | null
  history: MeasurementRun[]
}

export interface HistoricalValidationRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  cities_json: string
  data_health_json: string
  model_health_json: string
  trading_json: string
  gates_json: string
}

export interface HistoricalValidationResponse {
  latest: HistoricalValidationRun | null
  history: HistoricalValidationRun[]
}

export interface HistoricalDiagnosticsRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  cities_json: string
  summary_json: string
  segments_json: string
  calibration_json: string
  recommendations_json: string
}

export interface HistoricalDiagnosticsResponse {
  latest: HistoricalDiagnosticsRun | null
  history: HistoricalDiagnosticsRun[]
}

export interface StrategyRepairRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  cities_json: string
  summary_json: string
  baseline_json: string
  variants_json: string
  best_variant_json: string
  gates_json: string
}

export interface StrategyRepairResponse {
  latest: StrategyRepairRun | null
  history: StrategyRepairRun[]
}

export interface StrategyHypothesisAuditRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  cities_json: string
  summary_json: string
  blockers_json: string
  timing_json: string
  bucket_audit_json: string
  stability_json: string
  segments_json: string
}

export interface StrategyHypothesisAuditResponse {
  latest: StrategyHypothesisAuditRun | null
  history: StrategyHypothesisAuditRun[]
}

export interface StrategyExperimentRun {
  id: number
  run_at: string
  status: string
  experiment_set: string
  window_start: string | null
  window_end: string | null
  cities_json: string
  summary_json: string
  variants_json: string
  best_variant_json: string
  gates_json: string
  shadow_json: string
}

export interface StrategyExperimentResponse {
  latest: StrategyExperimentRun | null
  history: StrategyExperimentRun[]
}

export interface StrategyDiscoveryRun {
  id: number
  run_at: string
  status: string
  universe: string
  window_start: string | null
  window_end: string | null
  cities_json: string
  summary_json: string
  families_json: string
  best_family_json: string
  folds_json: string
  gates_json: string
}

export interface StrategyDiscoveryResponse {
  latest: StrategyDiscoveryRun | null
  history: StrategyDiscoveryRun[]
}

export interface FeatureDiscoveryRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  cities_json: string
  summary_json: string
  families_json: string
  best_family_json: string
  folds_json: string
  gates_json: string
}

export interface FeatureDiscoveryResponse {
  latest: FeatureDiscoveryRun | null
  history: FeatureDiscoveryRun[]
}

export interface FeatureCandidateAuditRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  feature_discovery_run_id: number | null
  cities_json: string
  summary_json: string
  profile_json: string
  segments_json: string
  decision_trace_json: string
  gates_json: string
}

export interface FeatureCandidateAuditResponse {
  latest: FeatureCandidateAuditRun | null
  history: FeatureCandidateAuditRun[]
}

export interface HighRewardCityHuntRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  cities_json: string
  summary_json: string
  rankings_json: string
  candidates_json: string
  gates_json: string
}

export interface HighRewardCityHuntResponse {
  latest: HighRewardCityHuntRun | null
  history: HighRewardCityHuntRun[]
}

export interface HighRewardPaperStatusResponse {
  run_at: string
  status: string
  policy_name: string
  approved_policy_name: string | null
  active_cities: string[]
  side_by_city: Record<string, string>
  summary: Record<string, unknown>
  cities: Record<string, unknown>[]
  blockers: string[]
  diagnostic_only: boolean
  live_release: boolean
}

export interface StrategyShadowDecision {
  id: number
  ts: string
  policy_name: string
  market_id: string
  event_id: string
  city_slug: string
  target_date: string
  raw_prob: number
  calibrated_prob: number
  market_price: string
  edge_net: string
  reason: string | null
  would_trade: boolean
  segment_key: string | null
}

export interface StrategyShadowDecisionResponse {
  latest: StrategyShadowDecision[]
}

export interface DiscoveryCandidateAuditRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  discovery_run_id: number | null
  cities_json: string
  summary_json: string
  concentration_json: string
  folds_json: string
  city_resolution_json: string
  timing_json: string
  segments_json: string
  gates_json: string
}

export interface DiscoveryCandidateAuditResponse {
  latest: DiscoveryCandidateAuditRun | null
  history: DiscoveryCandidateAuditRun[]
}

export interface CityResearchAuditRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  summary_json: string
  cities_json: string
  gates_json: string
}

export interface CityResearchAuditResponse {
  latest: CityResearchAuditRun | null
  history: CityResearchAuditRun[]
}

export interface CityEdgeRankingRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  summary_json: string
  cities_json: string
  research_json: string
  gates_json: string
}

export interface CityEdgeRankingResponse {
  latest: CityEdgeRankingRun | null
  history: CityEdgeRankingRun[]
}

export interface WeatherCityDiscoveryRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  summary_json: string
  cities_json: string
  gates_json: string
}

export interface WeatherCityDiscoveryResponse {
  latest: WeatherCityDiscoveryRun | null
  history: WeatherCityDiscoveryRun[]
}

export interface CityResolutionPromotionAuditRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  cities_json: string
  summary_json: string
  resolution_json: string
  gates_json: string
}

export interface CityResolutionPromotionAuditResponse {
  latest: CityResolutionPromotionAuditRun | null
  history: CityResolutionPromotionAuditRun[]
}

export interface CityPromotionApplyRun {
  id: number
  run_at: string
  status: string
  requested_cities_json: string
  promoted_cities_json: string
  blocked_json: string
  summary_json: string
  gates_json: string
}

export interface CityPromotionApplyResponse {
  latest: CityPromotionApplyRun | null
  history: CityPromotionApplyRun[]
}

export interface CityOnboardingRun {
  id: number
  run_at: string
  status: string
  window_start: string | null
  window_end: string | null
  cities_json: string
  summary_json: string
  checks_json: string
  gates_json: string
}

export interface CityOnboardingResponse {
  latest: CityOnboardingRun | null
  history: CityOnboardingRun[]
}

export interface HistoryBackfillRun {
  id: number
  run_at: string
  completed_at: string | null
  status: string
  window_start: string
  window_end: string
  cities_json: string
  interval: string
  probe_trades: boolean
  events_seen: number
  markets_upserted: number
  history_points: number
  trade_history_points: number
  rejected_trade_sources: number
  source_status_json: string
  errors_json: string
  params_json: string
}

export interface HistoryBackfillResponse {
  latest: HistoryBackfillRun | null
  history: HistoryBackfillRun[]
}

export interface LiveReadinessResponse {
  status: string
  mode: string
  ready_for_live_review: boolean
  checks: Record<string, unknown>
  blockers: string[]
  risk_limits: Record<string, string>
  geoblock: Record<string, unknown>
  last_error: string | null
}
