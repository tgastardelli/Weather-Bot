import { EmptyState } from "@/components/EmptyState"
import { LoadingPanel } from "@/components/LoadingPanel"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table"
import {
  useBacktests,
  useCalibration,
  useCityEdgeRanking,
  useCityOnboarding,
  useCityPromotionApply,
  useCityResolutionPromotionAudit,
  useCityResearchAudit,
  useDiscoveryCandidateAudit,
  useEvidence,
  useFeatureCandidateAudit,
  useFeatureDiscovery,
  useHighRewardCityHunt,
  useHighRewardPaperStatus,
  useHistoryBackfill,
  useHistoricalDiagnostics,
  useHistoricalValidation,
  useLiveReadiness,
  useMeasurement,
  useStrategyDiscovery,
  useStrategyExperiments,
  useStrategyHypothesisAudit,
  useStrategyRepair,
  useStrategyShadow,
  useWeatherCityDiscovery,
} from "@/hooks/use-analysis"
import {
  formatDate,
  formatLocalTime,
  formatMoney,
  formatProbability,
  formatSignedMoney,
} from "@/lib/format"
import { cn } from "@/lib/utils"

export function AnalysisPage() {
  const calibration = useCalibration()
  const backtests = useBacktests()
  const cityEdgeRanking = useCityEdgeRanking()
  const cityOnboarding = useCityOnboarding()
  const cityPromotionApply = useCityPromotionApply()
  const cityResolutionPromotionAudit = useCityResolutionPromotionAudit()
  const cityResearchAudit = useCityResearchAudit()
  const weatherCityDiscovery = useWeatherCityDiscovery()
  const discoveryCandidateAudit = useDiscoveryCandidateAudit()
  const evidence = useEvidence()
  const measurement = useMeasurement()
  const historicalValidation = useHistoricalValidation()
  const historicalDiagnostics = useHistoricalDiagnostics()
  const strategyRepair = useStrategyRepair()
  const strategyHypothesisAudit = useStrategyHypothesisAudit()
  const strategyDiscovery = useStrategyDiscovery()
  const featureDiscovery = useFeatureDiscovery()
  const featureCandidateAudit = useFeatureCandidateAudit()
  const highRewardCityHunt = useHighRewardCityHunt()
  const highRewardPaperStatus = useHighRewardPaperStatus()
  const strategyShadow = useStrategyShadow()
  const strategyExperiments = useStrategyExperiments()
  const historyBackfill = useHistoryBackfill()
  const liveReadiness = useLiveReadiness()
  const latestEvidence = evidence.data?.latest ?? null
  const latestCityOnboarding = cityOnboarding.data?.latest ?? null
  const cityOnboardingData =
    latestCityOnboarding == null ? null : parseCityOnboarding(latestCityOnboarding)
  const latestCityResearchAudit = cityResearchAudit.data?.latest ?? null
  const cityResearchAuditData =
    latestCityResearchAudit == null ? null : parseCityResearchAudit(latestCityResearchAudit)
  const latestCityEdgeRanking = cityEdgeRanking.data?.latest ?? null
  const cityEdgeRankingData =
    latestCityEdgeRanking == null ? null : parseCityEdgeRanking(latestCityEdgeRanking)
  const latestWeatherCityDiscovery = weatherCityDiscovery.data?.latest ?? null
  const weatherCityDiscoveryData =
    latestWeatherCityDiscovery == null
      ? null
      : parseWeatherCityDiscovery(latestWeatherCityDiscovery)
  const latestCityResolutionPromotionAudit =
    cityResolutionPromotionAudit.data?.latest ?? null
  const cityResolutionPromotionAuditData =
    latestCityResolutionPromotionAudit == null
      ? null
      : parseCityResolutionPromotionAudit(latestCityResolutionPromotionAudit)
  const latestCityPromotionApply = cityPromotionApply.data?.latest ?? null
  const cityPromotionApplyData =
    latestCityPromotionApply == null ? null : parseCityPromotionApply(latestCityPromotionApply)
  const latestDiscoveryCandidateAudit = discoveryCandidateAudit.data?.latest ?? null
  const discoveryCandidateAuditData =
    latestDiscoveryCandidateAudit == null
      ? null
      : parseDiscoveryCandidateAudit(latestDiscoveryCandidateAudit)
  const evidenceData = latestEvidence == null ? null : parseEvidence(latestEvidence)
  const latestMeasurement = measurement.data?.latest ?? null
  const measurementData =
    latestMeasurement == null ? null : parseMeasurement(latestMeasurement)
  const latestHistorical = historicalValidation.data?.latest ?? null
  const historicalData =
    latestHistorical == null ? null : parseHistoricalValidation(latestHistorical)
  const latestHistoricalDiagnostics = historicalDiagnostics.data?.latest ?? null
  const historicalDiagnosticsData =
    latestHistoricalDiagnostics == null
      ? null
      : parseHistoricalDiagnostics(latestHistoricalDiagnostics)
  const latestStrategyRepair = strategyRepair.data?.latest ?? null
  const strategyRepairData =
    latestStrategyRepair == null ? null : parseStrategyRepair(latestStrategyRepair)
  const latestStrategyHypothesisAudit = strategyHypothesisAudit.data?.latest ?? null
  const strategyHypothesisAuditData =
    latestStrategyHypothesisAudit == null
      ? null
      : parseStrategyHypothesisAudit(latestStrategyHypothesisAudit)
  const latestStrategyExperiment = strategyExperiments.data?.latest ?? null
  const strategyExperimentData =
    latestStrategyExperiment == null
      ? null
      : parseStrategyExperiment(latestStrategyExperiment)
  const latestStrategyDiscovery = strategyDiscovery.data?.latest ?? null
  const strategyDiscoveryData =
    latestStrategyDiscovery == null
      ? null
      : parseStrategyDiscovery(latestStrategyDiscovery)
  const latestFeatureDiscovery = featureDiscovery.data?.latest ?? null
  const featureDiscoveryData =
    latestFeatureDiscovery == null ? null : parseFeatureDiscovery(latestFeatureDiscovery)
  const latestFeatureCandidateAudit = featureCandidateAudit.data?.latest ?? null
  const featureCandidateAuditData =
    latestFeatureCandidateAudit == null
      ? null
      : parseFeatureCandidateAudit(latestFeatureCandidateAudit)
  const latestHighRewardCityHunt = highRewardCityHunt.data?.latest ?? null
  const highRewardCityHuntData =
    latestHighRewardCityHunt == null
      ? null
      : parseHighRewardCityHunt(latestHighRewardCityHunt)
  const highRewardPaperStatusData =
    highRewardPaperStatus.data == null
      ? null
      : parseHighRewardPaperStatus(highRewardPaperStatus.data)
  const strategyShadowData = parseStrategyShadow(strategyShadow.data?.latest ?? [])
  const latestHistoryBackfill = historyBackfill.data?.latest ?? null
  const liveReadinessData = liveReadiness.data ?? null
  const queryError = [
    calibration.error,
    backtests.error,
    cityEdgeRanking.error,
    cityOnboarding.error,
    cityPromotionApply.error,
    cityResolutionPromotionAudit.error,
    cityResearchAudit.error,
    discoveryCandidateAudit.error,
    evidence.error,
    measurement.error,
    historicalValidation.error,
    historicalDiagnostics.error,
    strategyRepair.error,
    strategyHypothesisAudit.error,
    strategyDiscovery.error,
    featureDiscovery.error,
    featureCandidateAudit.error,
    highRewardCityHunt.error,
    highRewardPaperStatus.error,
    strategyShadow.error,
    strategyExperiments.error,
    historyBackfill.error,
    liveReadiness.error,
    weatherCityDiscovery.error,
  ].find((error): error is Error => error instanceof Error)
  const loading =
    calibration.isLoading ||
    backtests.isLoading ||
    cityEdgeRanking.isLoading ||
    cityOnboarding.isLoading ||
    cityPromotionApply.isLoading ||
    cityResolutionPromotionAudit.isLoading ||
    cityResearchAudit.isLoading ||
    discoveryCandidateAudit.isLoading ||
    evidence.isLoading ||
    measurement.isLoading ||
    historicalValidation.isLoading ||
    historicalDiagnostics.isLoading ||
    strategyRepair.isLoading ||
    strategyHypothesisAudit.isLoading ||
    strategyDiscovery.isLoading ||
    featureDiscovery.isLoading ||
    featureCandidateAudit.isLoading ||
    highRewardCityHunt.isLoading ||
    highRewardPaperStatus.isLoading ||
    strategyExperiments.isLoading ||
    historyBackfill.isLoading ||
    liveReadiness.isLoading ||
    weatherCityDiscovery.isLoading
  const hasData =
    (calibration.data?.length ?? 0) > 0 ||
    (backtests.data?.length ?? 0) > 0 ||
    latestCityEdgeRanking != null ||
    latestCityOnboarding != null ||
    latestCityPromotionApply != null ||
    latestCityResolutionPromotionAudit != null ||
    latestCityResearchAudit != null ||
    latestWeatherCityDiscovery != null ||
    latestDiscoveryCandidateAudit != null ||
    latestEvidence != null ||
    latestMeasurement != null ||
    latestHistorical != null ||
    latestHistoricalDiagnostics != null ||
    latestStrategyRepair != null ||
    latestStrategyHypothesisAudit != null ||
    latestStrategyDiscovery != null ||
    latestFeatureDiscovery != null ||
    latestFeatureCandidateAudit != null ||
    latestHighRewardCityHunt != null ||
    highRewardPaperStatusData != null ||
    latestStrategyExperiment != null ||
    latestHistoryBackfill != null ||
    liveReadinessData != null

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">Analysis</h2>
        <p className="text-sm text-stone-600">
          Evidence, calibration metrics and stored backtest runs
        </p>
      </div>

      {queryError == null ? null : (
        <div className="rounded border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          Analysis API error: {queryError.message}
        </div>
      )}
      {loading ? <LoadingPanel /> : null}
      {!loading && !hasData ? (
        <EmptyState title="No analysis yet" detail="Run collectors, calibration and backtests." />
      ) : null}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Evidence</CardTitle>
          {latestEvidence == null ? null : (
            <Badge tone={latestEvidence.status === "PROMISING" ? "success" : "warning"}>
              {latestEvidence.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestEvidence == null || evidenceData == null ? (
            <EmptyState
              title="No evidence yet"
              detail="Run the all collector or evidence script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat label="Run" value={formatLocalTime(latestEvidence.run_at)} />
                <EvidenceStat
                  label="Window"
                  value={`${formatDate(latestEvidence.window_start)} - ${formatDate(
                    latestEvidence.window_end,
                  )}`}
                />
                <EvidenceStat label="Cities" value={evidenceData.cities.join(", ")} />
                <EvidenceStat
                  label="Forward days"
                  value={formatInteger(evidenceData.dataHealth.forward_days)}
                />
                <EvidenceStat
                  label="Price snapshots"
                  value={formatInteger(evidenceData.dataHealth.price_snapshots)}
                />
                <EvidenceStat
                  label="Books"
                  value={formatInteger(evidenceData.dataHealth.book_snapshots)}
                />
                <EvidenceStat
                  label="Ensemble members"
                  value={formatInteger(evidenceData.dataHealth.ensemble_members)}
                />
                <EvidenceStat
                  label="Resolved markets"
                  value={formatInteger(evidenceData.dataHealth.resolved_markets)}
                />
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">Gates</h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>Gate</TH>
                      <TH>Status</TH>
                      <TH>Value</TH>
                      <TH>Required</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {evidenceData.gates.map((gate) => (
                      <TR key={gate.key}>
                        <TD>{formatGateName(gate.key)}</TD>
                        <TD>
                          <Badge tone={gate.passed ? "success" : "danger"}>
                            {gate.passed ? "Pass" : "Fail"}
                          </Badge>
                        </TD>
                        <TD>{formatUnknown(gate.value)}</TD>
                        <TD>{formatUnknown(gate.required)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">Trading Evidence</h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>Profile</TH>
                      <TH>Source</TH>
                      <TH>Trades</TH>
                      <TH>PnL</TH>
                      <TH>ROI</TH>
                      <TH>Brier Delta</TH>
                      <TH>Loss Streak</TH>
                      <TH>Avg Edge</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {evidenceData.profiles.map((profile) => {
                      const pnl = Number(profile.total_pnl)
                      return (
                        <TR key={profile.profile}>
                          <TD>
                            <Badge tone={profile.profile === "longshot" ? "warning" : "neutral"}>
                              {profile.profile}
                            </Badge>
                          </TD>
                          <TD>{formatSource(profile.source)}</TD>
                          <TD>{formatInteger(profile.n_resolved_trades)}</TD>
                          <TD
                            className={cn(
                              pnl > 0 && "text-emerald-700",
                              pnl < 0 && "text-rose-700",
                            )}
                          >
                            {formatSignedMoney(profile.total_pnl)}
                          </TD>
                          <TD>{formatPercentString(profile.roi)}</TD>
                          <TD>{formatSignedNumber(profile.brier_delta, 4)}</TD>
                          <TD>{formatInteger(profile.max_loss_streak)}</TD>
                          <TD>{formatDecimalString(profile.avg_edge_net, 5)}</TD>
                        </TR>
                      )
                    })}
                  </TBody>
                </Table>
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">Focus Cities</h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>City</TH>
                      <TH>Prices</TH>
                      <TH>Ensembles</TH>
                      <TH>Resolutions</TH>
                      <TH>Score</TH>
                      <TH>Review</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {evidenceData.cityRows.map((city) => (
                      <TR key={city.city_slug}>
                        <TD>{city.city_slug}</TD>
                        <TD>{formatInteger(city.price_snapshots)}</TD>
                        <TD>{formatInteger(city.ensemble_members)}</TD>
                        <TD>{formatInteger(city.resolutions)}</TD>
                        <TD>{formatSignedNumber(city.reward_volatility_score, 2)}</TD>
                        <TD>
                          <Badge tone={city.needs_review ? "warning" : "success"}>
                            {city.needs_review ? "Review" : "OK"}
                          </Badge>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>History Backfill</CardTitle>
          {latestHistoryBackfill == null ? null : (
            <Badge tone={latestHistoryBackfill.status === "COMPLETED" ? "success" : "warning"}>
              {latestHistoryBackfill.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestHistoryBackfill == null ? (
            <EmptyState
              title="No history backfill yet"
              detail="Run the market history backfill with chunks and resume."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat label="Run" value={formatLocalTime(latestHistoryBackfill.run_at)} />
                <EvidenceStat
                  label="Window"
                  value={`${formatDate(latestHistoryBackfill.window_start)} - ${formatDate(
                    latestHistoryBackfill.window_end,
                  )}`}
                />
                <EvidenceStat
                  label="Cities"
                  value={parseStringList(latestHistoryBackfill.cities_json).join(", ") || "-"}
                />
                <EvidenceStat
                  label="Markets"
                  value={formatInteger(latestHistoryBackfill.markets_upserted)}
                />
                <EvidenceStat
                  label="History prices"
                  value={formatInteger(latestHistoryBackfill.history_points)}
                />
                <EvidenceStat
                  label="History trades"
                  value={formatInteger(latestHistoryBackfill.trade_history_points)}
                />
                <EvidenceStat
                  label="Rejected"
                  value={formatInteger(latestHistoryBackfill.rejected_trade_sources)}
                />
                <EvidenceStat
                  label="Sources"
                  value={formatPriceSourceCounts(
                    parseNumberRecord(parseRecord(latestHistoryBackfill.source_status_json)),
                  )}
                />
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Historical Validation</CardTitle>
          {latestHistorical == null ? null : (
            <Badge tone={latestHistorical.status === "PROMISING" ? "success" : "warning"}>
              {latestHistorical.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestHistorical == null || historicalData == null ? (
            <EmptyState
              title="No historical validation yet"
              detail="Run market history backfill and historical validation."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat label="Run" value={formatLocalTime(latestHistorical.run_at)} />
                <EvidenceStat
                  label="Window"
                  value={`${formatDate(latestHistorical.window_start)} - ${formatDate(
                    latestHistorical.window_end,
                  )}`}
                />
                <EvidenceStat label="Cities" value={historicalData.cities.join(", ")} />
                <EvidenceStat
                  label="History prices"
                  value={formatInteger(historicalData.dataHealth.market_price_history_points)}
                />
                <EvidenceStat
                  label="History trades"
                  value={formatInteger(historicalData.dataHealth.market_trade_history_points)}
                />
                <EvidenceStat
                  label="Min pairs"
                  value={formatInteger(historicalData.modelHealth.min_forecast_observed_pairs)}
                />
                <EvidenceStat
                  label="Execution proxy"
                  value={historicalData.executionProxy ?? "-"}
                />
                <EvidenceStat label="Price sampling" value={historicalData.priceSampling ?? "-"} />
                <EvidenceStat
                  label="Raw points"
                  value={formatInteger(historicalData.nRawPricePoints)}
                />
                <EvidenceStat
                  label="Sampled points"
                  value={formatInteger(historicalData.nSampledPricePoints)}
                />
                <EvidenceStat
                  label="Price sources"
                  value={formatPriceSourceCounts(historicalData.priceSourceCounts)}
                />
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">Profiles</h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>Profile</TH>
                      <TH>Trades</TH>
                      <TH>PnL</TH>
                      <TH>ROI</TH>
                      <TH>Brier Delta</TH>
                      <TH>PnL CI</TH>
                      <TH>Top 5 Share</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {historicalData.profiles.map((profile) => {
                      const pnl = Number(profile.total_pnl)
                      return (
                        <TR key={profile.profile}>
                          <TD>
                            <Badge tone={profile.profile === "longshot" ? "warning" : "neutral"}>
                              {profile.profile}
                            </Badge>
                          </TD>
                          <TD>{formatInteger(profile.n_resolved_trades)}</TD>
                          <TD
                            className={cn(
                              pnl > 0 && "text-emerald-700",
                              pnl < 0 && "text-rose-700",
                            )}
                          >
                            {formatSignedMoney(profile.total_pnl)}
                          </TD>
                          <TD>{formatPercentString(profile.roi)}</TD>
                          <TD>{formatSignedNumber(profile.brier_delta, 4)}</TD>
                          <TD>
                            {formatMoneyRange(profile.pnl_ci_low, profile.pnl_ci_high)}
                          </TD>
                          <TD>{formatPercentString(profile.top_5_abs_pnl_share)}</TD>
                        </TR>
                      )
                    })}
                  </TBody>
                </Table>
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">Gates</h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>Gate</TH>
                      <TH>Status</TH>
                      <TH>Value</TH>
                      <TH>Required</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {historicalData.gates.map((gate) => (
                      <TR key={gate.key}>
                        <TD>{formatGateName(gate.key)}</TD>
                        <TD>
                          <Badge tone={gate.passed ? "success" : "danger"}>
                            {gate.passed ? "Pass" : "Fail"}
                          </Badge>
                        </TD>
                        <TD>{formatUnknown(gate.value)}</TD>
                        <TD>{formatUnknown(gate.required)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Historical Diagnostics</CardTitle>
          {latestHistoricalDiagnostics == null ? null : (
            <Badge
              tone={latestHistoricalDiagnostics.status === "PROMISING" ? "success" : "warning"}
            >
              {latestHistoricalDiagnostics.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestHistoricalDiagnostics == null || historicalDiagnosticsData == null ? (
            <EmptyState
              title="No historical diagnostics yet"
              detail="Run the historical diagnostics script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Run"
                  value={formatLocalTime(latestHistoricalDiagnostics.run_at)}
                />
                <EvidenceStat
                  label="Window"
                  value={`${formatDate(latestHistoricalDiagnostics.window_start)} - ${formatDate(
                    latestHistoricalDiagnostics.window_end,
                  )}`}
                />
                <EvidenceStat
                  label="Max edge trades"
                  value={formatInteger(historicalDiagnosticsData.maxEdge.n_trades)}
                />
                <EvidenceStat
                  label="Max edge PnL"
                  value={formatSignedMoney(historicalDiagnosticsData.maxEdge.total_pnl)}
                />
                <EvidenceStat
                  label="Brier delta"
                  value={formatSignedNumber(historicalDiagnosticsData.maxEdge.brier_delta, 4)}
                />
                <EvidenceStat
                  label="Observed rate"
                  value={formatProbability(historicalDiagnosticsData.maxEdge.observed_rate)}
                />
                <EvidenceStat
                  label="Raw points"
                  value={formatInteger(historicalDiagnosticsData.nRawPricePoints)}
                />
                <EvidenceStat
                  label="Sampled points"
                  value={formatInteger(historicalDiagnosticsData.nSampledPricePoints)}
                />
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">Next Actions</h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>Priority</TH>
                      <TH>Action</TH>
                      <TH>Reason</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {historicalDiagnosticsData.actions.map((action) => (
                      <TR key={action.key}>
                        <TD>{formatInteger(action.priority)}</TD>
                        <TD>{formatGateName(action.key)}</TD>
                        <TD>{action.reason}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">Worst Segments</h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>Group</TH>
                      <TH>Segment</TH>
                      <TH>Trades</TH>
                      <TH>PnL</TH>
                      <TH>Win</TH>
                      <TH>Model</TH>
                      <TH>Observed</TH>
                      <TH>Brier Delta</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {historicalDiagnosticsData.worstSegments.map((segment) => (
                      <TR key={`${segment.segment_group}-${segment.segment}`}>
                        <TD>{formatGateName(segment.segment_group)}</TD>
                        <TD>{segment.segment}</TD>
                        <TD>{formatInteger(segment.n_trades)}</TD>
                        <TD>{formatSignedMoney(segment.total_pnl)}</TD>
                        <TD>{formatProbability(segment.win_rate)}</TD>
                        <TD>{formatProbability(segment.avg_model_prob)}</TD>
                        <TD>{formatProbability(segment.observed_rate)}</TD>
                        <TD>{formatSignedNumber(segment.brier_delta, 4)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">
                  Calibration Buckets
                </h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>Model Prob</TH>
                      <TH>Trades</TH>
                      <TH>Model</TH>
                      <TH>Observed</TH>
                      <TH>Market</TH>
                      <TH>Overconfidence</TH>
                      <TH>PnL</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {historicalDiagnosticsData.calibration.map((row) => (
                      <TR key={row.bucket}>
                        <TD>{row.bucket}</TD>
                        <TD>{formatInteger(row.n_trades)}</TD>
                        <TD>{formatProbability(row.avg_model_prob)}</TD>
                        <TD>{formatProbability(row.observed_rate)}</TD>
                        <TD>{formatDecimalString(row.avg_market_price, 3)}</TD>
                        <TD>{formatSignedNumber(row.model_overconfidence, 3)}</TD>
                        <TD>{formatSignedMoney(row.total_pnl)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Strategy Repair</CardTitle>
          {latestStrategyRepair == null ? null : (
            <Badge tone={latestStrategyRepair.status === "PROMISING" ? "success" : "warning"}>
              {latestStrategyRepair.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestStrategyRepair == null || strategyRepairData == null ? (
            <EmptyState
              title="No strategy repair yet"
              detail="Run the strategy repair script after historical diagnostics."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Run"
                  value={formatLocalTime(latestStrategyRepair.run_at)}
                />
                <EvidenceStat
                  label="Window"
                  value={`${formatDate(latestStrategyRepair.window_start)} - ${formatDate(
                    latestStrategyRepair.window_end,
                  )}`}
                />
                <EvidenceStat label="Best variant" value={strategyRepairData.bestVariantName} />
                <EvidenceStat label="Policy" value={strategyRepairData.policyName ?? "-"} />
                <EvidenceStat
                  label="Policy version"
                  value={strategyRepairData.summary.policy_version ?? "-"}
                />
                <EvidenceStat
                  label="Baseline PnL"
                  value={formatSignedMoney(strategyRepairData.summary.baseline_pnl)}
                />
                <EvidenceStat
                  label="Best PnL"
                  value={formatSignedMoney(strategyRepairData.summary.best_variant_pnl)}
                />
                <EvidenceStat
                  label="Baseline Brier"
                  value={formatSignedNumber(strategyRepairData.summary.baseline_brier_delta, 4)}
                />
                <EvidenceStat
                  label="Best Brier"
                  value={formatSignedNumber(strategyRepairData.summary.best_variant_brier_delta, 4)}
                />
                <EvidenceStat
                  label="Probability cap"
                  value={formatProbability(strategyRepairData.summary.probability_cap)}
                />
                <EvidenceStat
                  label="Min samples"
                  value={formatInteger(strategyRepairData.summary.min_calibration_samples)}
                />
                <EvidenceStat
                  label="Alpha"
                  value={formatSignedNumber(strategyRepairData.summary.alpha, 2)}
                />
                <EvidenceStat
                  label="Min edge"
                  value={formatDecimalString(strategyRepairData.summary.min_edge_net, 4)}
                />
                <EvidenceStat
                  label="Validation"
                  value={strategyRepairData.summary.validation_scheme ?? "-"}
                />
                <EvidenceStat
                  label="Holdout"
                  value={formatWindow(strategyRepairData.summary.holdout_window)}
                />
                <EvidenceStat
                  label="Price floor"
                  value={formatDecimalString(strategyRepairData.summary.price_floor, 2)}
                />
                <EvidenceStat
                  label="Low price"
                  value={formatGateName(strategyRepairData.summary.low_price_mode ?? "-")}
                />
                <EvidenceStat
                  label="Eligible segments"
                  value={`${formatInteger(strategyRepairData.summary.eligible_segments)} / ${formatInteger(
                    strategyRepairData.summary.total_segments,
                  )}`}
                />
                <EvidenceStat
                  label="Final eligible"
                  value={formatInteger(strategyRepairData.summary.final_eligible_segments)}
                />
                <EvidenceStat
                  label="Traded segments"
                  value={formatInteger(
                    strategyRepairData.summary.walk_forward_traded_segments ??
                      strategyRepairData.summary.traded_segments,
                  )}
                />
                <EvidenceStat
                  label="Execution proxy"
                  value={strategyRepairData.executionProxy ?? "-"}
                />
                <EvidenceStat label="Price sampling" value={strategyRepairData.priceSampling ?? "-"} />
                <EvidenceStat
                  label="Sampled points"
                  value={formatInteger(strategyRepairData.nSampledPricePoints)}
                />
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">
                  Variant Comparison
                </h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>Variant</TH>
                      <TH>Version</TH>
                      <TH>Calibrated</TH>
                      <TH>Filters</TH>
                      <TH>Scope</TH>
                      <TH>Split</TH>
                      <TH>Alpha</TH>
                      <TH>Cap</TH>
                      <TH>Floor</TH>
                      <TH>Eligible</TH>
                      <TH>Traded</TH>
                      <TH>Trades</TH>
                      <TH>PnL</TH>
                      <TH>ROI</TH>
                      <TH>Brier Delta</TH>
                      <TH>PnL CI</TH>
                      <TH>Blocked</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {strategyRepairData.variants.map((variant) => {
                      const pnl = Number(variant.maxEdge.total_pnl)
                      return (
                        <TR key={variant.name}>
                          <TD>
                            <Badge
                              tone={
                                variant.name === strategyRepairData.bestVariantName
                                  ? "success"
                                  : "neutral"
                              }
                            >
                              {formatGateName(variant.name)}
                            </Badge>
                          </TD>
                          <TD>{variant.policyVersion ?? "-"}</TD>
                          <TD>{variant.calibrate ? "Yes" : "No"}</TD>
                          <TD>{variant.applySegmentFilters ? "Yes" : "No"}</TD>
                          <TD>{formatGateName(variant.segmentScope ?? "-")}</TD>
                          <TD>{formatGateName(variant.validationSplit ?? "-")}</TD>
                          <TD>{formatSignedNumber(variant.alpha, 2)}</TD>
                          <TD>{formatProbability(variant.probabilityCap)}</TD>
                          <TD>{formatDecimalString(variant.priceFloor, 2)}</TD>
                          <TD>
                            {formatInteger(
                              variant.finalEligibleSegments ?? variant.eligibleSegments,
                            )}{" "}
                            /{" "}
                            {formatInteger(variant.totalSegments)}
                          </TD>
                          <TD>
                            {formatInteger(
                              variant.walkForwardTradedSegments ?? variant.tradedSegments,
                            )}
                          </TD>
                          <TD>{formatInteger(variant.maxEdge.n_resolved_trades)}</TD>
                          <TD
                            className={cn(
                              pnl > 0 && "text-emerald-700",
                              pnl < 0 && "text-rose-700",
                            )}
                          >
                            {formatSignedMoney(variant.maxEdge.total_pnl)}
                          </TD>
                          <TD>{formatPercentString(variant.maxEdge.roi)}</TD>
                          <TD>{formatSignedNumber(variant.maxEdge.brier_delta, 4)}</TD>
                          <TD>
                            {formatMoneyRange(
                              variant.maxEdge.pnl_ci_low,
                              variant.maxEdge.pnl_ci_high,
                            )}
                          </TD>
                          <TD>{formatBlockedCounts(variant.blockedCounts)}</TD>
                        </TR>
                      )
                    })}
                  </TBody>
                </Table>
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">Gates</h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>Gate</TH>
                      <TH>Status</TH>
                      <TH>Value</TH>
                      <TH>Required</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {strategyRepairData.gates.map((gate) => (
                      <TR key={gate.key}>
                        <TD>{formatGateName(gate.key)}</TD>
                        <TD>
                          <Badge tone={gate.passed ? "success" : "danger"}>
                            {gate.passed ? "Pass" : "Fail"}
                          </Badge>
                        </TD>
                        <TD>{formatUnknown(gate.value)}</TD>
                        <TD>{formatUnknown(gate.required)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Hypothesis Audit</CardTitle>
          {latestStrategyHypothesisAudit == null ? null : (
            <Badge
              tone={
                latestStrategyHypothesisAudit.status === "READY_FOR_REPAIR_V5"
                  ? "success"
                  : latestStrategyHypothesisAudit.status === "DATA_REVIEW"
                    ? "danger"
                    : "warning"
              }
            >
              {latestStrategyHypothesisAudit.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestStrategyHypothesisAudit == null || strategyHypothesisAuditData == null ? (
            <EmptyState
              title="No hypothesis audit yet"
              detail="Run the strategy hypothesis audit script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Run"
                  value={formatLocalTime(latestStrategyHypothesisAudit.run_at)}
                />
                <EvidenceStat
                  label="Blockers"
                  value={
                    strategyHypothesisAuditData.blockers.length > 0
                      ? strategyHypothesisAuditData.blockers.map(formatGateName).join(", ")
                      : "None"
                  }
                />
                <EvidenceStat
                  label="Next action"
                  value={formatGateName(strategyHypothesisAuditData.summary.nextAction ?? "-")}
                />
                <EvidenceStat
                  label="Policy"
                  value={strategyHypothesisAuditData.stability.selectedPolicyName ?? "-"}
                />
                <EvidenceStat
                  label="Timing"
                  value={strategyHypothesisAuditData.timing.valid ? "Valid" : "Review"}
                />
                <EvidenceStat
                  label="Bucket audit"
                  value={strategyHypothesisAuditData.bucketAudit.valid ? "Valid" : "Review"}
                />
                <EvidenceStat
                  label="Eligible segments"
                  value={formatInteger(strategyHypothesisAuditData.stability.eligibleSegments)}
                />
                <EvidenceStat
                  label="OOS trades"
                  value={formatInteger(strategyHypothesisAuditData.stability.oosTrades)}
                />
                <EvidenceStat
                  label="Actionable OOS"
                  value={formatInteger(
                    strategyHypothesisAuditData.stability.decisionTrace.actionableCandidates,
                  )}
                />
                <EvidenceStat
                  label="Trace blockers"
                  value={formatBlockedCounts(
                    strategyHypothesisAuditData.stability.decisionTrace.blockedCounts,
                  )}
                />
              </div>

              <Table>
                <THead>
                  <TR>
                    <TH>Check</TH>
                    <TH>Value</TH>
                  </TR>
                </THead>
                <TBody>
                  <TR>
                    <TD>After market close trades</TD>
                    <TD>
                      {formatInteger(
                        strategyHypothesisAuditData.timing.dataApiTrades.afterMarketClose,
                      )}
                    </TD>
                  </TR>
                  <TR>
                    <TD>After market close prices</TD>
                    <TD>
                      {formatInteger(
                        strategyHypothesisAuditData.timing.clobPricesHistory.afterMarketClose,
                      )}
                    </TD>
                  </TR>
                  <TR>
                    <TD>Bucket issues</TD>
                    <TD>{formatInteger(strategyHypothesisAuditData.bucketAudit.issueCount)}</TD>
                  </TR>
                  <TR>
                    <TD>OOS candidates in eligible segments</TD>
                    <TD>
                      {formatInteger(strategyHypothesisAuditData.stability.oosCandidates)}
                    </TD>
                  </TR>
                </TBody>
              </Table>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">
                  Decision Trace
                </h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>Reason</TH>
                      <TH>Count</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {Object.entries(
                      strategyHypothesisAuditData.stability.decisionTrace.blockedCounts,
                    ).map(([reason, count]) => (
                      <TR key={reason}>
                        <TD>{formatGateName(reason)}</TD>
                        <TD>{formatInteger(count)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">
                  OOS Candidate Sample
                </h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>City</TH>
                      <TH>Market</TH>
                      <TH>Price</TH>
                      <TH>Raw</TH>
                      <TH>Calibrated</TH>
                      <TH>Edge</TH>
                      <TH>Cost</TH>
                      <TH>Hours</TH>
                      <TH>Reason</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {strategyHypothesisAuditData.stability.decisionTrace.samples.map(
                      (sample) => (
                        <TR key={`${sample.marketId}-${sample.ts}`}>
                          <TD>{sample.citySlug ?? "-"}</TD>
                          <TD>{sample.marketId ?? "-"}</TD>
                          <TD>{formatDecimalString(sample.marketPrice, 4)}</TD>
                          <TD>{formatProbability(sample.rawProb)}</TD>
                          <TD>{formatProbability(sample.calibratedProb)}</TD>
                          <TD>{formatDecimalString(sample.edgeNet, 5)}</TD>
                          <TD>{formatDecimalString(sample.costPerShare, 5)}</TD>
                          <TD>{formatSignedNumber(sample.hoursToClose, 1)}</TD>
                          <TD>{formatGateName(sample.reason ?? "-")}</TD>
                        </TR>
                      ),
                    )}
                  </TBody>
                </Table>
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">
                  Worst Segments
                </h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>Group</TH>
                      <TH>Segment</TH>
                      <TH>Trades</TH>
                      <TH>PnL</TH>
                      <TH>Brier Delta</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {strategyHypothesisAuditData.segments.worstSegments.map((segment) => (
                      <TR key={`${segment.group}-${segment.segment}`}>
                        <TD>{formatGateName(segment.group ?? "-")}</TD>
                        <TD>{formatGateName(segment.segment ?? "-")}</TD>
                        <TD>{formatInteger(segment.nTrades)}</TD>
                        <TD>{formatSignedMoney(segment.totalPnl)}</TD>
                        <TD>{formatSignedNumber(segment.brierDelta, 4)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Weather City Discovery</CardTitle>
          {latestWeatherCityDiscovery == null ? null : (
            <Badge
              tone={
                latestWeatherCityDiscovery.status === "DISCOVERED_NEW_CITIES"
                  ? "success"
                  : "warning"
              }
            >
              {latestWeatherCityDiscovery.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestWeatherCityDiscovery == null || weatherCityDiscoveryData == null ? (
            <EmptyState
              title="No weather city discovery yet"
              detail="Run the weather city discovery script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Run"
                  value={formatLocalTime(latestWeatherCityDiscovery.run_at)}
                />
                <EvidenceStat
                  label="Cities seen"
                  value={formatInteger(weatherCityDiscoveryData.summary.citiesSeen)}
                />
                <EvidenceStat
                  label="New cities"
                  value={formatInteger(weatherCityDiscoveryData.summary.newCitiesRegistered)}
                />
                <EvidenceStat
                  label="Next action"
                  value={formatGateName(weatherCityDiscoveryData.summary.nextAction ?? "-")}
                />
                <EvidenceStat
                  label="Live release"
                  value={weatherCityDiscoveryData.summary.cannotApproveLive ? "Blocked" : "Review"}
                />
              </div>
              <Table>
                <THead>
                  <TR>
                    <TH>City</TH>
                    <TH>Station</TH>
                    <TH>Source</TH>
                    <TH>Metadata</TH>
                    <TH>Registered</TH>
                  </TR>
                </THead>
                <TBody>
                  {weatherCityDiscoveryData.cities.slice(0, 10).map((city) => (
                    <TR key={city.citySlug}>
                      <TD>{city.citySlug}</TD>
                      <TD>{city.stationCode ?? "-"}</TD>
                      <TD>{formatGateName(city.resolutionSource ?? "-")}</TD>
                      <TD>{formatCheck(city.metadataComplete)}</TD>
                      <TD>{city.registeredAsNeedsReview ? "needs_review" : "existing"}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>City Resolution Promotion</CardTitle>
          {latestCityResolutionPromotionAudit == null ? null : (
            <Badge
              tone={
                latestCityResolutionPromotionAudit.status === "READY_FOR_EXPANDED_DISCOVERY"
                  ? "success"
                  : "warning"
              }
            >
              {latestCityResolutionPromotionAudit.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestCityResolutionPromotionAudit == null ||
          cityResolutionPromotionAuditData == null ? (
            <EmptyState
              title="No city promotion audit yet"
              detail="Run the city resolution promotion audit script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Run"
                  value={formatLocalTime(latestCityResolutionPromotionAudit.run_at)}
                />
                <EvidenceStat
                  label="Promotable"
                  value={
                    cityResolutionPromotionAuditData.summary.promotableCities.join(", ") || "-"
                  }
                />
                <EvidenceStat
                  label="Next action"
                  value={formatGateName(
                    cityResolutionPromotionAuditData.summary.nextAction ?? "-",
                  )}
                />
                <EvidenceStat
                  label="Live release"
                  value={
                    cityResolutionPromotionAuditData.summary.cannotApproveLive
                      ? "Blocked"
                      : "Review"
                  }
                />
              </div>
              <Table>
                <THead>
                  <TR>
                    <TH>City</TH>
                    <TH>Status</TH>
                    <TH>Markets</TH>
                    <TH>Mismatches</TH>
                    <TH>Mismatch Rate</TH>
                    <TH>Shadow</TH>
                  </TR>
                </THead>
                <TBody>
                  {cityResolutionPromotionAuditData.cities.slice(0, 10).map((city) => (
                    <TR key={city.citySlug}>
                      <TD>{city.citySlug}</TD>
                      <TD>{formatGateName(city.promotionStatus)}</TD>
                      <TD>{formatInteger(city.auditedMarkets)}</TD>
                      <TD>{formatInteger(city.mismatches)}</TD>
                      <TD>{city.mismatchRate ?? "-"}</TD>
                      <TD>{formatCheck(city.canEnterShadow)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>City Promotion Apply</CardTitle>
          {latestCityPromotionApply == null ? null : (
            <Badge tone={latestCityPromotionApply.status === "PROMOTED" ? "success" : "warning"}>
              {latestCityPromotionApply.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestCityPromotionApply == null || cityPromotionApplyData == null ? (
            <EmptyState
              title="No city promotion apply yet"
              detail="Run the city promotion apply script after a passing resolution audit."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Run"
                  value={formatLocalTime(latestCityPromotionApply.run_at)}
                />
                <EvidenceStat
                  label="Requested"
                  value={cityPromotionApplyData.summary.requestedCities.join(", ") || "-"}
                />
                <EvidenceStat
                  label="Promoted"
                  value={cityPromotionApplyData.summary.promotedCities.join(", ") || "-"}
                />
                <EvidenceStat
                  label="Live release"
                  value={cityPromotionApplyData.summary.cannotApproveLive ? "Blocked" : "Review"}
                />
              </div>
              <Table>
                <THead>
                  <TR>
                    <TH>City</TH>
                    <TH>Result</TH>
                    <TH>Audit Run</TH>
                    <TH>Mismatch Rate</TH>
                    <TH>Resolution</TH>
                    <TH>Blockers</TH>
                  </TR>
                </THead>
                <TBody>
                  {cityPromotionApplyData.rows.slice(0, 10).map((city) => (
                    <TR key={`${city.citySlug}-${city.result}`}>
                      <TD>{city.citySlug}</TD>
                      <TD>
                        <Badge tone={city.result === "promoted" ? "success" : "warning"}>
                          {city.result}
                        </Badge>
                      </TD>
                      <TD>{formatInteger(city.auditRunId)}</TD>
                      <TD>{city.mismatchRate ?? "-"}</TD>
                      <TD>{city.resolutionSourceUsed ?? "-"}</TD>
                      <TD>
                        {city.blockers.length === 0
                          ? "-"
                          : city.blockers.map(formatGateName).join(", ")}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>City Onboarding</CardTitle>
          {latestCityOnboarding == null ? null : (
            <Badge
              tone={
                latestCityOnboarding.status === "READY_FOR_RESEARCH"
                  ? "success"
                  : "warning"
              }
            >
              {latestCityOnboarding.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestCityOnboarding == null || cityOnboardingData == null ? (
            <EmptyState
              title="No city onboarding yet"
              detail="Run the city onboarding script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat label="Run" value={formatLocalTime(latestCityOnboarding.run_at)} />
                <EvidenceStat
                  label="Requested"
                  value={cityOnboardingData.summary.requestedCities.join(", ") || "-"}
                />
                <EvidenceStat
                  label="Research only"
                  value={formatInteger(cityOnboardingData.summary.researchOnly)}
                />
                <EvidenceStat
                  label="Live release"
                  value={cityOnboardingData.summary.cannotApproveLive ? "Blocked" : "Review"}
                />
              </div>

              <Table>
                <THead>
                  <TR>
                    <TH>City</TH>
                    <TH>Class</TH>
                    <TH>Climate</TH>
                    <TH>Market</TH>
                    <TH>Metadata</TH>
                    <TH>Resolution</TH>
                  </TR>
                </THead>
                <TBody>
                  {cityOnboardingData.cities.map((city) => (
                    <TR key={city.citySlug}>
                      <TD>{city.citySlug}</TD>
                      <TD>
                        <Badge
                          tone={
                            city.classification === "live_eligible"
                              ? "success"
                              : city.classification === "research_only"
                                ? "warning"
                                : "danger"
                          }
                        >
                          {formatGateName(city.classification)}
                        </Badge>
                      </TD>
                      <TD>{formatCheck(city.climatePassed)}</TD>
                      <TD>{formatCheck(city.marketPassed)}</TD>
                      <TD>{formatCheck(city.metadataPassed)}</TD>
                      <TD>{formatCheck(city.resolutionPassed)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>City Research Audit</CardTitle>
          {latestCityResearchAudit == null ? null : (
            <Badge
              tone={
                latestCityResearchAudit.status === "READY_FOR_RESEARCH"
                  ? "success"
                  : "warning"
              }
            >
              {latestCityResearchAudit.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestCityResearchAudit == null || cityResearchAuditData == null ? (
            <EmptyState
              title="No city research audit yet"
              detail="Run the city research audit script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Run"
                  value={formatLocalTime(latestCityResearchAudit.run_at)}
                />
                <EvidenceStat
                  label="Live eligible"
                  value={formatInteger(cityResearchAuditData.summary.liveEligible)}
                />
                <EvidenceStat
                  label="Research only"
                  value={formatInteger(cityResearchAuditData.summary.researchOnly)}
                />
                <EvidenceStat
                  label="Live release"
                  value={cityResearchAuditData.summary.cannotApproveLive ? "Blocked" : "Review"}
                />
              </div>

              <Table>
                <THead>
                  <TR>
                    <TH>City</TH>
                    <TH>Class</TH>
                    <TH>Pairs</TH>
                    <TH>Resolved</TH>
                    <TH>Trades</TH>
                    <TH>Issues</TH>
                  </TR>
                </THead>
                <TBody>
                  {cityResearchAuditData.cities.slice(0, 12).map((city) => (
                    <TR key={city.citySlug}>
                      <TD>{city.citySlug}</TD>
                      <TD>
                        <Badge
                          tone={
                            city.classification === "live_eligible"
                              ? "success"
                              : city.classification === "research_only"
                                ? "warning"
                                : "danger"
                          }
                        >
                          {formatGateName(city.classification)}
                        </Badge>
                      </TD>
                      <TD>{formatInteger(city.forecastObservedPairs)}</TD>
                      <TD>{formatInteger(city.resolvedMarkets)}</TD>
                      <TD>{formatInteger(city.tradeHistoryPoints)}</TD>
                      <TD>{formatFailureCategories(city.failureCategories, city.reasons)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>City Edge Ranking</CardTitle>
          {latestCityEdgeRanking == null ? null : (
            <Badge
              tone={
                latestCityEdgeRanking.status === "READY_FOR_TARGETED_DISCOVERY"
                  ? "success"
                  : latestCityEdgeRanking.status === "DATA_REVIEW"
                    ? "danger"
                    : "warning"
              }
            >
              {latestCityEdgeRanking.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestCityEdgeRanking == null || cityEdgeRankingData == null ? (
            <EmptyState
              title="No city edge ranking yet"
              detail="Run the city edge ranking script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat label="Run" value={formatLocalTime(latestCityEdgeRanking.run_at)} />
                <EvidenceStat
                  label="Best live city"
                  value={cityEdgeRankingData.summary.bestLiveCity ?? "-"}
                />
                <EvidenceStat
                  label="Top live cities"
                  value={cityEdgeRankingData.summary.topLiveCities.join(", ") || "-"}
                />
                <EvidenceStat
                  label="Live candidates"
                  value={formatInteger(cityEdgeRankingData.summary.liveCandidateCount)}
                />
                <EvidenceStat
                  label="Research only"
                  value={formatInteger(cityEdgeRankingData.summary.researchOnlyCount)}
                />
                <EvidenceStat
                  label="Next action"
                  value={formatGateName(cityEdgeRankingData.summary.nextAction ?? "-")}
                />
                <EvidenceStat
                  label="Live release"
                  value={cityEdgeRankingData.summary.cannotApproveLive ? "Blocked" : "Review"}
                />
              </div>

              <Table>
                <THead>
                  <TR>
                    <TH>City</TH>
                    <TH>Status</TH>
                    <TH>Folds</TH>
                    <TH>Trades</TH>
                    <TH>Brier</TH>
                    <TH>PnL</TH>
                    <TH>Reason</TH>
                  </TR>
                </THead>
                <TBody>
                  {cityEdgeRankingData.cities.slice(0, 8).map((city) => (
                    <TR key={city.citySlug}>
                      <TD>{city.citySlug}</TD>
                      <TD>
                        <Badge
                          tone={city.eligibleForTargetedDiscovery ? "success" : "warning"}
                        >
                          {city.eligibleForTargetedDiscovery ? "Candidate" : "Review"}
                        </Badge>
                      </TD>
                      <TD>{formatInteger(city.validFolds)}</TD>
                      <TD>{formatInteger(city.profile.n_resolved_trades)}</TD>
                      <TD>{formatSignedNumber(city.profile.brier_delta, 4)}</TD>
                      <TD>{formatSignedMoney(city.profile.total_pnl)}</TD>
                      <TD>{city.rejectionReasons.map(formatGateName).join(", ") || "-"}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>

              <Table>
                <THead>
                  <TR>
                    <TH>Research city</TH>
                    <TH>Trades</TH>
                    <TH>Brier</TH>
                    <TH>PnL</TH>
                    <TH>Blocker</TH>
                  </TR>
                </THead>
                <TBody>
                  {cityEdgeRankingData.research.slice(0, 6).map((city) => (
                    <TR key={city.citySlug}>
                      <TD>{city.citySlug}</TD>
                      <TD>{formatInteger(city.profile.n_resolved_trades)}</TD>
                      <TD>{formatSignedNumber(city.profile.brier_delta, 4)}</TD>
                      <TD>{formatSignedMoney(city.profile.total_pnl)}</TD>
                      <TD>{city.rejectionReasons.map(formatGateName).join(", ") || "-"}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">Next Commands</h3>
                <div className="space-y-2">
                  {cityEdgeRankingData.summary.nextCommands.map((command) => (
                    <code
                      key={command}
                      className="block rounded border border-stone-200 bg-stone-50 px-3 py-2 text-xs text-stone-700"
                    >
                      {command}
                    </code>
                  ))}
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Strategy Discovery</CardTitle>
          {latestStrategyDiscovery == null ? null : (
            <Badge
              tone={
                latestStrategyDiscovery.status === "READY_FOR_SHADOW_PAPER"
                  ? "success"
                  : latestStrategyDiscovery.status === "DATA_REVIEW"
                    ? "danger"
                    : "warning"
              }
            >
              {latestStrategyDiscovery.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestStrategyDiscovery == null || strategyDiscoveryData == null ? (
            <EmptyState
              title="No strategy discovery yet"
              detail="Run the diagnostic strategy discovery script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Run"
                  value={formatLocalTime(latestStrategyDiscovery.run_at)}
                />
                <EvidenceStat label="Universe" value={latestStrategyDiscovery.universe} />
                <EvidenceStat
                  label="Version"
                  value={strategyDiscoveryData.summary.discoveryVersion ?? "v1"}
                />
                <EvidenceStat
                  label="Best family"
                  value={strategyDiscoveryData.summary.bestFamily ?? "-"}
                />
                <EvidenceStat
                  label="Live release"
                  value={strategyDiscoveryData.summary.cannotApproveLive ? "Blocked" : "Review"}
                />
                <EvidenceStat
                  label="Valid folds"
                  value={formatInteger(strategyDiscoveryData.summary.validFolds)}
                />
                <EvidenceStat
                  label="Brier delta"
                  value={formatSignedNumber(strategyDiscoveryData.profile.brier_delta, 4)}
                />
                <EvidenceStat
                  label="Proxy PnL"
                  value={formatSignedMoney(strategyDiscoveryData.profile.total_pnl)}
                />
                <EvidenceStat
                  label="Trades"
                  value={formatInteger(strategyDiscoveryData.profile.n_resolved_trades)}
                />
                <EvidenceStat
                  label="Live cities"
                  value={strategyDiscoveryData.summary.liveEligibleCities.join(", ") || "-"}
                />
                <EvidenceStat
                  label="Research cities"
                  value={strategyDiscoveryData.summary.researchOnlyCities.join(", ") || "-"}
                />
              </div>

              <Table>
                <THead>
                  <TR>
                    <TH>Gate</TH>
                    <TH>Status</TH>
                    <TH>Value</TH>
                    <TH>Required</TH>
                  </TR>
                </THead>
                <TBody>
                  {strategyDiscoveryData.gates.map((gate) => (
                    <TR key={gate.key}>
                      <TD>{formatGateName(gate.key)}</TD>
                      <TD>
                        <Badge tone={gate.passed ? "success" : "danger"}>
                          {gate.passed ? "Pass" : "Fail"}
                        </Badge>
                      </TD>
                      <TD>{formatUnknown(gate.value)}</TD>
                      <TD>{formatUnknown(gate.required)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>

              <Table>
                <THead>
                  <TR>
                    <TH>Fold</TH>
                    <TH>Family</TH>
                    <TH>Candidates</TH>
                    <TH>Trades</TH>
                    <TH>PnL</TH>
                    <TH>Brier</TH>
                  </TR>
                </THead>
                <TBody>
                  {strategyDiscoveryData.folds.slice(-8).map((fold) => (
                    <TR key={`${fold.index}-${fold.foldStart}`}>
                      <TD>{formatInteger(fold.index)}</TD>
                      <TD>{formatGateName(fold.selectedFamily ?? fold.reason ?? "-")}</TD>
                      <TD>{formatInteger(fold.nFoldCandidates)}</TD>
                      <TD>{formatInteger(fold.nOosTrades)}</TD>
                      <TD>{formatSignedMoney(fold.pnl)}</TD>
                      <TD>{formatSignedNumber(fold.brierDelta, 4)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Feature Discovery</CardTitle>
          {latestFeatureDiscovery == null ? null : (
            <Badge
              tone={
                latestFeatureDiscovery.status === "READY_FOR_REPAIR_V5"
                  ? "success"
                  : latestFeatureDiscovery.status === "DATA_REVIEW"
                    ? "danger"
                    : "warning"
              }
            >
              {latestFeatureDiscovery.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestFeatureDiscovery == null || featureDiscoveryData == null ? (
            <EmptyState
              title="No feature discovery yet"
              detail="Run the feature discovery diagnostic script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Run"
                  value={formatLocalTime(latestFeatureDiscovery.run_at)}
                />
                <EvidenceStat
                  label="Best family"
                  value={featureDiscoveryData.summary.bestFamily ?? "-"}
                />
                <EvidenceStat
                  label="Valid folds"
                  value={formatInteger(featureDiscoveryData.summary.validFolds)}
                />
                <EvidenceStat
                  label="Feature candidates"
                  value={formatInteger(featureDiscoveryData.summary.nFeatureCandidates)}
                />
                <EvidenceStat
                  label="Brier delta"
                  value={formatSignedNumber(featureDiscoveryData.profile.brier_delta, 4)}
                />
                <EvidenceStat
                  label="Proxy PnL"
                  value={formatSignedMoney(featureDiscoveryData.profile.total_pnl)}
                />
                <EvidenceStat
                  label="Trades"
                  value={formatInteger(featureDiscoveryData.profile.n_resolved_trades)}
                />
                <EvidenceStat
                  label="Live release"
                  value={featureDiscoveryData.summary.cannotApproveLive ? "Blocked" : "Review"}
                />
              </div>

              <div className="rounded border border-stone-200 bg-stone-50 p-3 text-xs text-stone-700">
                <span className="font-medium text-stone-900">Features:</span>{" "}
                {featureDiscoveryData.summary.features.join(", ") || "-"}
              </div>

              <Table>
                <THead>
                  <TR>
                    <TH>Gate</TH>
                    <TH>Status</TH>
                    <TH>Value</TH>
                    <TH>Required</TH>
                  </TR>
                </THead>
                <TBody>
                  {featureDiscoveryData.gates.map((gate) => (
                    <TR key={gate.key}>
                      <TD>{formatGateName(gate.key)}</TD>
                      <TD>
                        <Badge tone={gate.passed ? "success" : "danger"}>
                          {gate.passed ? "Pass" : "Fail"}
                        </Badge>
                      </TD>
                      <TD>{formatUnknown(gate.value)}</TD>
                      <TD>{formatUnknown(gate.required)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Feature Candidate Audit</CardTitle>
          {latestFeatureCandidateAudit == null ? null : (
            <Badge
              tone={
                latestFeatureCandidateAudit.status === "READY_FOR_REPAIR_V5"
                  ? "success"
                  : latestFeatureCandidateAudit.status === "DATA_REVIEW"
                    ? "danger"
                    : "warning"
              }
            >
              {latestFeatureCandidateAudit.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestFeatureCandidateAudit == null || featureCandidateAuditData == null ? (
            <EmptyState
              title="No feature candidate audit yet"
              detail="Run the feature candidate audit script after a Feature Candidate."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Run"
                  value={formatLocalTime(latestFeatureCandidateAudit.run_at)}
                />
                <EvidenceStat
                  label="Discovery run"
                  value={formatInteger(latestFeatureCandidateAudit.feature_discovery_run_id)}
                />
                <EvidenceStat
                  label="Best family"
                  value={featureCandidateAuditData.summary.bestFamily ?? "-"}
                />
                <EvidenceStat
                  label="Explanation"
                  value={featureCandidateAuditData.summary.explanation ?? "-"}
                />
                <EvidenceStat
                  label="Brier delta"
                  value={formatSignedNumber(featureCandidateAuditData.profile.brier_delta, 4)}
                />
                <EvidenceStat
                  label="Proxy PnL"
                  value={formatSignedMoney(featureCandidateAuditData.profile.total_pnl)}
                />
                <EvidenceStat
                  label="Trades"
                  value={formatInteger(featureCandidateAuditData.profile.n_resolved_trades)}
                />
                <EvidenceStat
                  label="Approved subset"
                  value={featureCandidateAuditData.summary.approvedSubsetKey ?? "-"}
                />
              </div>

              <Table>
                <THead>
                  <TR>
                    <TH>Gate</TH>
                    <TH>Status</TH>
                    <TH>Value</TH>
                    <TH>Required</TH>
                  </TR>
                </THead>
                <TBody>
                  {featureCandidateAuditData.gates.map((gate) => (
                    <TR key={gate.key}>
                      <TD>{formatGateName(gate.key)}</TD>
                      <TD>
                        <Badge tone={gate.passed ? "success" : "danger"}>
                          {gate.passed ? "Pass" : "Fail"}
                        </Badge>
                      </TD>
                      <TD>{formatUnknown(gate.value)}</TD>
                      <TD>{formatUnknown(gate.required)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>

              <Table>
                <THead>
                  <TR>
                    <TH>Segment</TH>
                    <TH>Trades</TH>
                    <TH>PnL</TH>
                    <TH>Brier</TH>
                    <TH>Folds</TH>
                  </TR>
                </THead>
                <TBody>
                  {featureCandidateAuditData.topSegments.map((segment) => (
                    <TR key={segment.key}>
                      <TD className="max-w-[320px] truncate">{segment.key}</TD>
                      <TD>{formatInteger(segment.nResolvedTrades)}</TD>
                      <TD>{formatSignedMoney(segment.totalPnl)}</TD>
                      <TD>{formatSignedNumber(segment.brierDelta, 4)}</TD>
                      <TD>{formatInteger(segment.foldCount)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>High Reward City Hunt</CardTitle>
          {latestHighRewardCityHunt == null ? null : (
            <Badge
              tone={
                latestHighRewardCityHunt.status === "READY_FOR_SHADOW_FAST_LANE"
                  ? "success"
                  : latestHighRewardCityHunt.status === "DATA_REVIEW"
                    ? "danger"
                    : latestHighRewardCityHunt.status === "HIGH_REWARD_CANDIDATE"
                      ? "warning"
                      : "neutral"
              }
            >
              {latestHighRewardCityHunt.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestHighRewardCityHunt == null || highRewardCityHuntData == null ? (
            <EmptyState
              title="No high reward city hunt yet"
              detail="Run the high-risk/high-reward city hunt script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat label="Run" value={formatLocalTime(latestHighRewardCityHunt.run_at)} />
                <EvidenceStat
                  label="Approved cities"
                  value={formatInteger(highRewardCityHuntData.summary.approvedCityCount)}
                />
                <EvidenceStat
                  label="Goal"
                  value={formatGateName(highRewardCityHuntData.summary.strategyGoal ?? "-")}
                />
                <EvidenceStat
                  label="Live release"
                  value={highRewardCityHuntData.summary.cannotApproveLive ? "Blocked" : "Review"}
                />
              </div>

              <div className="rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                Aggressive diagnostic only: low win rate is acceptable here only when the
                backend-calculated payoff ratio and PnL pass the city gates.
              </div>

              <Table>
                <THead>
                  <TR>
                    <TH>City</TH>
                    <TH>Side</TH>
                    <TH>Family</TH>
                    <TH>Trades</TH>
                    <TH>Win rate</TH>
                    <TH>Payoff</TH>
                    <TH>ROI</TH>
                    <TH>PnL</TH>
                    <TH>Status</TH>
                  </TR>
                </THead>
                <TBody>
                  {highRewardCityHuntData.bestCities.map((city) => (
                    <TR key={`${city.citySlug}-${city.variant}`}>
                      <TD>{city.citySlug}</TD>
                      <TD>{city.side}</TD>
                      <TD>{formatGateName(city.family)}</TD>
                      <TD>{formatInteger(city.nTrades)}</TD>
                      <TD>{formatPercentNumber(city.winRate)}</TD>
                      <TD>{formatRatio(city.payoffRatio)}</TD>
                      <TD>{formatPercentString(city.roi)}</TD>
                      <TD>{formatSignedMoney(city.totalPnl)}</TD>
                      <TD>
                        {city.passed ? (
                          <Badge tone="success">Candidate</Badge>
                        ) : (
                          <span className="text-stone-500">
                            {city.blockers.slice(0, 2).map(formatGateName).join(", ") || "-"}
                          </span>
                        )}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>

              <Table>
                <THead>
                  <TR>
                    <TH>Gate</TH>
                    <TH>Status</TH>
                    <TH>Value</TH>
                    <TH>Required</TH>
                  </TR>
                </THead>
                <TBody>
                  {highRewardCityHuntData.gates.map((gate) => (
                    <TR key={gate.key}>
                      <TD>{formatGateName(gate.key)}</TD>
                      <TD>
                        <Badge tone={gate.passed ? "success" : "danger"}>
                          {gate.passed ? "Pass" : "Fail"}
                        </Badge>
                      </TD>
                      <TD>{formatUnknown(gate.value)}</TD>
                      <TD>{formatUnknown(gate.required)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>High Reward Paper Status</CardTitle>
          {highRewardPaperStatusData == null ? null : (
            <Badge
              tone={
                highRewardPaperStatusData.status === "PAPER_READY_FOR_MEASUREMENT"
                  ? "success"
                  : highRewardPaperStatusData.status === "PAPER_FAILED"
                    ? "danger"
                    : highRewardPaperStatusData.status === "PAPER_RUNNING"
                      ? "warning"
                      : "neutral"
              }
            >
              {highRewardPaperStatusData.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {highRewardPaperStatusData == null ? (
            <EmptyState
              title="No high reward paper status yet"
              detail="The status endpoint will appear after the backend is available."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Run"
                  value={formatLocalTime(highRewardPaperStatusData.runAt)}
                />
                <EvidenceStat label="Policy" value={highRewardPaperStatusData.policyName} />
                <EvidenceStat
                  label="Active cities"
                  value={highRewardPaperStatusData.activeCities.join(", ") || "-"}
                />
                <EvidenceStat
                  label="Entry fills"
                  value={formatInteger(highRewardPaperStatusData.summary.entryFills)}
                />
                <EvidenceStat
                  label="Settlements"
                  value={formatInteger(highRewardPaperStatusData.summary.settlementFills)}
                />
                <EvidenceStat
                  label="Forward days"
                  value={formatNumber(highRewardPaperStatusData.summary.forwardDays, 2)}
                />
                <EvidenceStat
                  label="Days left"
                  value={formatNumber(
                    highRewardPaperStatusData.summary.remainingForwardDays,
                    2,
                  )}
                />
                <EvidenceStat
                  label="Resolved left"
                  value={formatInteger(
                    highRewardPaperStatusData.summary.remainingResolvedFills,
                  )}
                />
                <EvidenceStat
                  label="Missing coverage"
                  value={highRewardPaperStatusData.summary.missingCoverage.join(", ") || "-"}
                />
                <EvidenceStat
                  label="Paper PnL"
                  value={formatSignedMoney(highRewardPaperStatusData.summary.paperPnl)}
                />
                <EvidenceStat
                  label="Resolved PnL"
                  value={formatSignedMoney(highRewardPaperStatusData.summary.resolvedPnl)}
                />
                <EvidenceStat
                  label="Payoff"
                  value={formatRatio(highRewardPaperStatusData.summary.payoffRatio)}
                />
                <EvidenceStat
                  label="Sample gate"
                  value={highRewardPaperStatusData.summary.sampleGate ? "Passed" : "Waiting"}
                />
                <EvidenceStat
                  label="Coverage gate"
                  value={
                    highRewardPaperStatusData.summary.coverageGate ? "Passed" : "Waiting"
                  }
                />
                <EvidenceStat
                  label="Blockers"
                  value={
                    highRewardPaperStatusData.blockers.length > 0
                      ? highRewardPaperStatusData.blockers.map(formatGateName).join(", ")
                      : "-"
                  }
                />
              </div>

              <div className="rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                Paper fast lane only: this report validates the approved high-reward policy
                against paper fills. It does not approve live trading or create live orders.
              </div>

              {highRewardPaperStatusData.nextAction == null ? null : (
                <div className="rounded border border-sky-200 bg-sky-50 p-3 text-xs text-sky-950">
                  <div className="font-semibold">
                    Next action: {formatGateName(highRewardPaperStatusData.nextAction.code)}
                  </div>
                  <div>{highRewardPaperStatusData.nextAction.detail ?? "-"}</div>
                </div>
              )}

              {highRewardPaperStatusData.missingCoverageSamples.length > 0 ? (
                <div>
                  <h3 className="mb-2 text-sm font-semibold text-stone-900">
                    Missing Coverage Diagnostics
                  </h3>
                  <Table>
                    <THead>
                      <TR>
                        <TH>City</TH>
                        <TH>Side</TH>
                        <TH>Bucket</TH>
                        <TH>Reason</TH>
                        <TH>Price</TH>
                        <TH>Max Price</TH>
                        <TH>Price Gap</TH>
                        <TH>Prob Delta</TH>
                        <TH>Delta Gap</TH>
                        <TH>Hours</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {highRewardPaperStatusData.missingCoverageSamples.map((sample) => (
                        <TR key={sample.citySlug}>
                          <TD>{sample.citySlug}</TD>
                          <TD>{sample.side ?? "-"}</TD>
                          <TD>{sample.bucket ?? "-"}</TD>
                          <TD>{formatGateName(sample.reason ?? "-")}</TD>
                          <TD>{formatDecimalString(sample.marketPrice, 5)}</TD>
                          <TD>{formatDecimalString(sample.variantMaxPrice, 5)}</TD>
                          <TD>{formatSignedNumber(sample.priceToVariantMax, 5)}</TD>
                          <TD>{formatSignedNumber(sample.probabilityDelta, 5)}</TD>
                          <TD>{formatSignedNumber(sample.probabilityDeltaToMin, 5)}</TD>
                          <TD>{formatNumber(sample.hoursToClose, 2)}</TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </div>
              ) : null}

              {highRewardPaperStatusData.pendingTargets.length > 0 ? (
                <div>
                  <h3 className="mb-2 text-sm font-semibold text-stone-900">
                    Pending Paper Targets
                  </h3>
                  <Table>
                    <THead>
                      <TR>
                        <TH>City</TH>
                        <TH>Side</TH>
                        <TH>Target</TH>
                        <TH>Closed</TH>
                        <TH>Winner</TH>
                        <TH>Signals</TH>
                        <TH>Entry signals</TH>
                        <TH>Pending signals</TH>
                        <TH>Entry fills</TH>
                        <TH>Settlements</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {highRewardPaperStatusData.pendingTargets.map((target) => (
                        <TR
                          key={`${target.citySlug}-${target.side ?? "unknown"}-${target.targetDate ?? "unknown"}`}
                        >
                          <TD>{target.citySlug}</TD>
                          <TD>{target.side ?? "-"}</TD>
                          <TD>{target.targetDate ?? "-"}</TD>
                          <TD>{target.closed ? "Yes" : "No"}</TD>
                          <TD>
                            {target.winner == null ? "-" : target.winner ? "YES" : "NO"}
                          </TD>
                          <TD>{formatInteger(target.signals)}</TD>
                          <TD>{formatInteger(target.entrySignals)}</TD>
                          <TD>{formatInteger(target.pendingSignals)}</TD>
                          <TD>{formatInteger(target.entryFills)}</TD>
                          <TD>{formatInteger(target.settlementFills)}</TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </div>
              ) : null}

              <Table>
                <THead>
                  <TR>
                    <TH>City</TH>
                    <TH>Side</TH>
                    <TH>Signals</TH>
                    <TH>Entry fills</TH>
                    <TH>Settlements</TH>
                    <TH>Rejected</TH>
                    <TH>PnL</TH>
                    <TH>Resolved PnL</TH>
                    <TH>Payoff</TH>
                    <TH>Loss streak</TH>
                    <TH>Slippage</TH>
                  </TR>
                </THead>
                <TBody>
                  {highRewardPaperStatusData.cities.map((city) => (
                    <TR key={city.citySlug}>
                      <TD>{city.citySlug}</TD>
                      <TD>{city.side ?? "-"}</TD>
                      <TD>{formatInteger(city.signals)}</TD>
                      <TD>{formatInteger(city.entryFills)}</TD>
                      <TD>{formatInteger(city.settlementFills)}</TD>
                      <TD>{formatInteger(city.rejectedOrders)}</TD>
                      <TD>{formatSignedMoney(city.paperPnl)}</TD>
                      <TD>{formatSignedMoney(city.resolvedPnl)}</TD>
                      <TD>{formatRatio(city.payoffRatio)}</TD>
                      <TD>{formatInteger(city.maxLossStreak)}</TD>
                      <TD>{formatDecimalString(city.avgSlippage, 5)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Shadow Paper</CardTitle>
          <Badge tone={strategyShadowData.decisions.length > 0 ? "warning" : "neutral"}>
            Diagnostic only
          </Badge>
        </CardHeader>
        <CardContent>
          {strategyShadowData.decisions.length === 0 ? (
            <EmptyState
              title="No shadow decisions yet"
              detail="Run the strategy shadow diagnostic command after a Discovery candidate."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Decisions"
                  value={formatInteger(strategyShadowData.decisions.length)}
                />
                <EvidenceStat
                  label="Would trade"
                  value={formatInteger(strategyShadowData.wouldTrade)}
                />
                <EvidenceStat
                  label="Policies"
                  value={strategyShadowData.policyNames.join(", ") || "-"}
                />
                <EvidenceStat label="Live release" value="Blocked" />
              </div>
              <Table>
                <THead>
                  <TR>
                    <TH>Time</TH>
                    <TH>Policy</TH>
                    <TH>City</TH>
                    <TH>Side</TH>
                    <TH>Price</TH>
                    <TH>Calibrated</TH>
                    <TH>Edge</TH>
                    <TH>Decision</TH>
                  </TR>
                </THead>
                <TBody>
                  {strategyShadowData.decisions.slice(0, 10).map((decision) => (
                    <TR key={decision.id}>
                      <TD>{formatLocalTime(decision.ts)}</TD>
                      <TD>{decision.policyName}</TD>
                      <TD>{decision.citySlug}</TD>
                      <TD>{decision.side ?? "-"}</TD>
                      <TD>{formatProbability(decision.marketPrice ?? "0")}</TD>
                      <TD>{formatProbability(String(decision.calibratedProb ?? 0))}</TD>
                      <TD>{formatSignedMoney(decision.edgeNet)}</TD>
                      <TD>
                        {decision.wouldTrade ? (
                          <Badge tone="success">Would trade</Badge>
                        ) : (
                          <span className="text-stone-500">
                            {formatGateName(decision.reason ?? "blocked")}
                          </span>
                        )}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Discovery Candidate Audit</CardTitle>
          {latestDiscoveryCandidateAudit == null ? null : (
            <Badge
              tone={
                latestDiscoveryCandidateAudit.status === "READY_FOR_REPAIR_V5"
                  ? "success"
                  : latestDiscoveryCandidateAudit.status === "DATA_REVIEW"
                    ? "danger"
                    : "warning"
              }
            >
              {latestDiscoveryCandidateAudit.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestDiscoveryCandidateAudit == null || discoveryCandidateAuditData == null ? (
            <EmptyState
              title="No discovery candidate audit yet"
              detail="Run the discovery candidate audit script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Run"
                  value={formatLocalTime(latestDiscoveryCandidateAudit.run_at)}
                />
                <EvidenceStat
                  label="Discovery"
                  value={
                    latestDiscoveryCandidateAudit.discovery_run_id == null
                      ? "-"
                      : `#${latestDiscoveryCandidateAudit.discovery_run_id}`
                  }
                />
                <EvidenceStat
                  label="Next action"
                  value={formatGateName(discoveryCandidateAuditData.summary.nextAction ?? "-")}
                />
                <EvidenceStat
                  label="Best family"
                  value={discoveryCandidateAuditData.summary.bestFamily ?? "-"}
                />
                <EvidenceStat
                  label="Brier delta"
                  value={formatSignedNumber(discoveryCandidateAuditData.profile.brier_delta, 4)}
                />
                <EvidenceStat
                  label="Proxy PnL"
                  value={formatSignedMoney(discoveryCandidateAuditData.profile.total_pnl)}
                />
                <EvidenceStat
                  label="Trades"
                  value={formatInteger(discoveryCandidateAuditData.profile.n_resolved_trades)}
                />
                <EvidenceStat
                  label="Top city"
                  value={`${discoveryCandidateAuditData.concentration.topCity ?? "-"} ${formatPercentString(
                    discoveryCandidateAuditData.concentration.topCityShare,
                  )}`}
                />
                <EvidenceStat
                  label="Resolution"
                  value={discoveryCandidateAuditData.resolution.valid ? "Valid" : "Review"}
                />
                <EvidenceStat
                  label="Timing"
                  value={discoveryCandidateAuditData.timing.valid ? "Valid" : "Review"}
                />
                <EvidenceStat
                  label="Research traded"
                  value={
                    discoveryCandidateAuditData.concentration.researchOnlyTradedCities.join(
                      ", ",
                    ) || "-"
                  }
                />
                <EvidenceStat
                  label="Blockers"
                  value={formatBlockedCounts(discoveryCandidateAuditData.blockedCounts)}
                />
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">Gates</h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>Gate</TH>
                      <TH>Status</TH>
                      <TH>Value</TH>
                      <TH>Required</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {discoveryCandidateAuditData.gates.map((gate) => (
                      <TR key={gate.key}>
                        <TD>{formatGateName(gate.key)}</TD>
                        <TD>
                          <Badge tone={gate.passed ? "success" : "danger"}>
                            {gate.passed ? "Pass" : "Fail"}
                          </Badge>
                        </TD>
                        <TD>{formatUnknown(gate.value)}</TD>
                        <TD>{formatUnknown(gate.required)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">
                  Research City Resolution
                </h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>City</TH>
                      <TH>Station</TH>
                      <TH>Audited</TH>
                      <TH>Mismatches</TH>
                      <TH>Missing Obs</TH>
                      <TH>Status</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {discoveryCandidateAuditData.resolution.cities.map((city) => (
                      <TR key={city.citySlug}>
                        <TD>{city.citySlug}</TD>
                        <TD>{city.stationCode ?? "-"}</TD>
                        <TD>{formatInteger(city.auditedMarkets)}</TD>
                        <TD>{formatInteger(city.mismatches)}</TD>
                        <TD>{formatInteger(city.missingObservations)}</TD>
                        <TD>
                          <Badge
                            tone={
                              (city.mismatches ?? 0) === 0 &&
                              (city.missingObservations ?? 0) === 0
                                ? "success"
                                : "danger"
                            }
                          >
                            {(city.mismatches ?? 0) === 0 &&
                            (city.missingObservations ?? 0) === 0
                              ? "OK"
                              : "Review"}
                          </Badge>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">
                  Worst Segments
                </h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>Segment</TH>
                      <TH>Trades</TH>
                      <TH>PnL</TH>
                      <TH>Brier</TH>
                      <TH>Top 5 Share</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {discoveryCandidateAuditData.segments.slice(0, 8).map((segment) => (
                      <TR key={segment.segment}>
                        <TD>{segment.segment}</TD>
                        <TD>{formatInteger(segment.nTrades)}</TD>
                        <TD>{formatSignedMoney(segment.totalPnl)}</TD>
                        <TD>{formatSignedNumber(segment.brierDelta, 4)}</TD>
                        <TD>{formatPercentString(segment.top5Share)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Strategy Experiments</CardTitle>
          {latestStrategyExperiment == null ? null : (
            <Badge
              tone={
                latestStrategyExperiment.status === "READY_FOR_SHADOW_PAPER"
                  ? "success"
                  : latestStrategyExperiment.status === "REJECTED"
                    ? "danger"
                    : "warning"
              }
            >
              {latestStrategyExperiment.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestStrategyExperiment == null || strategyExperimentData == null ? (
            <EmptyState
              title="No strategy experiments yet"
              detail="Run the diagnostic strategy experiments script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat
                  label="Run"
                  value={formatLocalTime(latestStrategyExperiment.run_at)}
                />
                <EvidenceStat
                  label="Experiment"
                  value={latestStrategyExperiment.experiment_set}
                />
                <EvidenceStat
                  label="Best variant"
                  value={strategyExperimentData.summary.bestVariant ?? "-"}
                />
                <EvidenceStat
                  label="Live release"
                  value={strategyExperimentData.summary.cannotApproveLive ? "Blocked" : "Review"}
                />
                <EvidenceStat
                  label="Brier delta"
                  value={formatSignedNumber(
                    strategyExperimentData.modelValidation.brierDelta,
                    4,
                  )}
                />
                <EvidenceStat
                  label="Proxy PnL"
                  value={formatSignedMoney(strategyExperimentData.maxEdge.total_pnl)}
                />
                <EvidenceStat
                  label="Trades"
                  value={formatInteger(strategyExperimentData.maxEdge.n_resolved_trades)}
                />
                <EvidenceStat
                  label="Blocked"
                  value={formatBlockedCounts(strategyExperimentData.blockedCounts)}
                />
              </div>

              <Table>
                <THead>
                  <TR>
                    <TH>Gate</TH>
                    <TH>Status</TH>
                    <TH>Value</TH>
                    <TH>Required</TH>
                  </TR>
                </THead>
                <TBody>
                  {strategyExperimentData.gates.map((gate) => (
                    <TR key={gate.key}>
                      <TD>{formatGateName(gate.key)}</TD>
                      <TD>
                        <Badge tone={gate.passed ? "success" : "danger"}>
                          {gate.passed ? "Pass" : "Fail"}
                        </Badge>
                      </TD>
                      <TD>{formatUnknown(gate.value)}</TD>
                      <TD>{formatUnknown(gate.required)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-stone-900">
                  Shadow Sample
                </h3>
                <Table>
                  <THead>
                    <TR>
                      <TH>City</TH>
                      <TH>Market</TH>
                      <TH>Price</TH>
                      <TH>Raw</TH>
                      <TH>Calibrated</TH>
                      <TH>Edge</TH>
                      <TH>Would trade</TH>
                      <TH>Reason</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {strategyExperimentData.shadowSample.map((sample) => (
                      <TR key={`${sample.marketId}-${sample.ts}`}>
                        <TD>{sample.citySlug ?? "-"}</TD>
                        <TD>{sample.marketId ?? "-"}</TD>
                        <TD>{formatDecimalString(sample.marketPrice, 4)}</TD>
                        <TD>{formatProbability(sample.rawProb)}</TD>
                        <TD>{formatProbability(sample.calibratedProb)}</TD>
                        <TD>{formatDecimalString(sample.edgeNet, 5)}</TD>
                        <TD>{sample.wouldTrade ? "Yes" : "No"}</TD>
                        <TD>{formatGateName(sample.reason ?? "-")}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Live Readiness</CardTitle>
          {liveReadinessData == null ? null : (
            <Badge tone={liveReadinessData.ready_for_live_review ? "success" : "danger"}>
              {liveReadinessData.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {liveReadinessData == null ? (
            <EmptyState title="No readiness data" detail="The backend has not reported status." />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat label="Mode" value={liveReadinessData.mode} />
                <EvidenceStat
                  label="Geoblock"
                  value={formatUnknown(liveReadinessData.geoblock["status"])}
                />
                <EvidenceStat
                  label="Blockers"
                  value={
                    liveReadinessData.blockers.length > 0
                      ? liveReadinessData.blockers.map(formatGateName).join(", ")
                      : "None"
                  }
                />
                <EvidenceStat
                  label="Risk limits"
                  value={formatRiskLimits(liveReadinessData.risk_limits)}
                />
              </div>
              <Table>
                <THead>
                  <TR>
                    <TH>Check</TH>
                    <TH>Status</TH>
                    <TH>Value</TH>
                    <TH>Required</TH>
                  </TR>
                </THead>
                <TBody>
                  {parseReadinessChecks(liveReadinessData.checks).map((check) => (
                    <TR key={check.key}>
                      <TD>{formatGateName(check.key)}</TD>
                      <TD>
                        <Badge tone={check.passed ? "success" : "danger"}>
                          {check.passed ? "Pass" : "Fail"}
                        </Badge>
                      </TD>
                      <TD>{formatUnknown(check.value)}</TD>
                      <TD>{formatUnknown(check.required)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Measurement</CardTitle>
          {latestMeasurement == null ? null : (
            <Badge
              tone={latestMeasurement.status === "READY_FOR_LIVE_REVIEW" ? "success" : "warning"}
            >
              {latestMeasurement.status}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {latestMeasurement == null || measurementData == null ? (
            <EmptyState
              title="No measurement yet"
              detail="Run paper fills and the measurement script."
            />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <EvidenceStat label="Run" value={formatLocalTime(latestMeasurement.run_at)} />
                <EvidenceStat
                  label="Window"
                  value={`${formatDate(latestMeasurement.window_start)} - ${formatDate(
                    latestMeasurement.window_end,
                  )}`}
                />
                <EvidenceStat
                  label="Orders"
                  value={formatInteger(measurementData.summary.orders)}
                />
                <EvidenceStat
                  label="Entry fills"
                  value={formatInteger(measurementData.summary.entry_fills)}
                />
                <EvidenceStat
                  label="Policy"
                  value={measurementData.summary.policy_name ?? "-"}
                />
                <EvidenceStat
                  label="Paper PnL"
                  value={formatSignedMoney(measurementData.summary.paper_pnl)}
                />
                <EvidenceStat
                  label="Fee paid"
                  value={formatMoney(measurementData.metrics.total_fee_paid)}
                />
                <EvidenceStat
                  label="Avg slippage"
                  value={formatDecimalString(measurementData.metrics.avg_slippage, 5)}
                />
                <EvidenceStat
                  label="Paper vs replay"
                  value={formatSignedMoney(measurementData.metrics.paper_vs_replay_pnl_delta)}
                />
              </div>
              <Table>
                <THead>
                  <TR>
                    <TH>Check</TH>
                    <TH>Status</TH>
                    <TH>Value</TH>
                    <TH>Required</TH>
                  </TR>
                </THead>
                <TBody>
                  {measurementData.checks.map((check) => (
                    <TR key={check.key}>
                      <TD>{formatGateName(check.key)}</TD>
                      <TD>
                        <Badge tone={check.passed ? "success" : "danger"}>
                          {check.passed ? "Pass" : "Fail"}
                        </Badge>
                      </TD>
                      <TD>{formatUnknown(check.value)}</TD>
                      <TD>{formatUnknown(check.required)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Calibration</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <THead>
              <TR>
                <TH>City</TH>
                <TH>Model</TH>
                <TH>Lead</TH>
                <TH>Bias</TH>
                <TH>MAE</TH>
                <TH>Std</TH>
                <TH>N</TH>
              </TR>
            </THead>
            <TBody>
              {(calibration.data ?? []).map((row) => (
                <TR key={`${row.city_slug}-${row.model}-${row.lead_days}`}>
                  <TD>{row.city_slug}</TD>
                  <TD>{row.model}</TD>
                  <TD>{row.lead_days}d</TD>
                  <TD>{row.bias_c.toFixed(2)} C</TD>
                  <TD>{row.mae_c.toFixed(2)} C</TD>
                  <TD>{row.residual_std_c.toFixed(2)} C</TD>
                  <TD>{row.n_samples}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Backtests</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <THead>
              <TR>
                <TH>Run</TH>
                <TH>Profile</TH>
                <TH>Source</TH>
                <TH>Trades</TH>
                <TH>Win</TH>
                <TH>Staked</TH>
                <TH>PnL</TH>
                <TH>Brier M</TH>
                <TH>Brier Px</TH>
                <TH>PF</TH>
                <TH>Drawdown</TH>
              </TR>
            </THead>
            <TBody>
              {(backtests.data ?? []).map((row) => {
                const pnl = Number(row.total_pnl)
                const params = parseBacktestParams(row.params_json)
                return (
                  <TR key={`${row.run_at}-${row.profile}-${params.source ?? "unknown"}`}>
                    <TD>{formatLocalTime(row.run_at)}</TD>
                    <TD>
                      <Badge tone={row.profile === "longshot" ? "warning" : "neutral"}>
                        {row.profile}
                      </Badge>
                    </TD>
                    <TD>{formatSource(params.source)}</TD>
                    <TD>{row.n_trades}</TD>
                    <TD>{formatProbability(row.win_rate)}</TD>
                    <TD>{formatMoney(row.total_staked)}</TD>
                    <TD className={cn(pnl > 0 && "text-emerald-700", pnl < 0 && "text-rose-700")}>
                      {formatSignedMoney(row.total_pnl)}
                    </TD>
                    <TD>{formatBrier(params.brier_model)}</TD>
                    <TD>{formatBrier(params.brier_market)}</TD>
                    <TD>{row.profit_factor == null ? "-" : row.profit_factor.toFixed(2)}</TD>
                    <TD>{formatMoney(row.max_drawdown)}</TD>
                  </TR>
                )
              })}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

function EvidenceStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-stone-200 p-3">
      <div className="text-xs font-medium uppercase tracking-wide text-stone-500">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold text-stone-950" title={value}>
        {value}
      </div>
    </div>
  )
}

function formatCheck(passed: boolean): string {
  return passed ? "Pass" : "Review"
}

function formatFailureCategories(categories: Record<string, string[]>, fallback: string[]): string {
  const labels = Object.entries(categories)
    .filter(([, reasons]) => reasons.length > 0)
    .map(([category, reasons]) => `${formatGateName(category)}: ${reasons.map(formatGateName).join(", ")}`)
  if (labels.length > 0) return labels.join(" | ")
  return fallback.length > 0 ? fallback.map(formatGateName).join(", ") : "-"
}

interface BacktestParams {
  source?: string | undefined
  brier_model?: number | null | undefined
  brier_market?: number | null | undefined
}

interface MeasurementRunLike {
  summary_json: string
  metrics_json: string
  checks_json: string
}

interface ParsedMeasurement {
  summary: MeasurementSummary
  metrics: MeasurementMetrics
  checks: EvidenceGate[]
}

interface MeasurementSummary {
  orders: number | undefined
  entry_fills: number | undefined
  policy_name: string | undefined
  paper_pnl: string | undefined
}

interface MeasurementMetrics {
  total_fee_paid: string | undefined
  avg_slippage: string | undefined
  paper_vs_replay_pnl_delta: string | undefined
}

interface EvidenceRunLike {
  cities_json: string
  data_health_json: string
  model_health_json: string
  trading_json: string
  gates_json: string
}

interface HistoricalValidationRunLike {
  cities_json: string
  data_health_json: string
  model_health_json: string
  trading_json: string
  gates_json: string
}

interface HistoricalDiagnosticsRunLike {
  summary_json: string
  segments_json: string
  calibration_json: string
  recommendations_json: string
}

interface StrategyRepairRunLike {
  summary_json: string
  variants_json: string
  best_variant_json: string
  gates_json: string
}

interface StrategyHypothesisAuditRunLike {
  summary_json: string
  blockers_json: string
  timing_json: string
  bucket_audit_json: string
  stability_json: string
  segments_json: string
}

interface StrategyExperimentRunLike {
  summary_json: string
  best_variant_json: string
  gates_json: string
}

interface CityResearchAuditRunLike {
  summary_json: string
  cities_json: string
  gates_json: string
}

interface CityOnboardingRunLike {
  summary_json: string
  checks_json: string
  gates_json: string
}

interface StrategyDiscoveryRunLike {
  summary_json: string
  best_family_json: string
  folds_json: string
  gates_json: string
}

interface FeatureDiscoveryRunLike {
  summary_json: string
  best_family_json: string
  folds_json: string
  gates_json: string
}

interface FeatureCandidateAuditRunLike {
  summary_json: string
  profile_json: string
  segments_json: string
  gates_json: string
}

interface HighRewardCityHuntRunLike {
  summary_json: string
  rankings_json: string
  candidates_json: string
  gates_json: string
}

interface CityEdgeRankingRunLike {
  summary_json: string
  cities_json: string
  research_json: string
  gates_json: string
}

interface WeatherCityDiscoveryRunLike {
  summary_json: string
  cities_json: string
  gates_json: string
}

interface CityResolutionPromotionAuditRunLike {
  summary_json: string
  resolution_json: string
  gates_json: string
}

interface CityPromotionApplyRunLike {
  summary_json: string
  promoted_cities_json: string
  blocked_json: string
  gates_json: string
}

interface DiscoveryCandidateAuditRunLike {
  summary_json: string
  concentration_json: string
  city_resolution_json: string
  timing_json: string
  segments_json: string
  gates_json: string
}

interface ParsedEvidence {
  cities: string[]
  dataHealth: EvidenceDataHealth
  gates: EvidenceGate[]
  profiles: EvidenceProfile[]
  cityRows: EvidenceCityRow[]
}

interface ParsedHistoricalValidation {
  cities: string[]
  dataHealth: HistoricalDataHealth
  modelHealth: HistoricalModelHealth
  gates: EvidenceGate[]
  profiles: HistoricalProfile[]
  executionProxy: string | undefined
  priceSampling: string | undefined
  nRawPricePoints: number | undefined
  nSampledPricePoints: number | undefined
  priceSourceCounts: Record<string, number>
}

interface ParsedHistoricalDiagnostics {
  maxEdge: DiagnosticProfile
  nRawPricePoints: number | undefined
  nSampledPricePoints: number | undefined
  actions: DiagnosticAction[]
  worstSegments: DiagnosticSegment[]
  calibration: DiagnosticCalibrationRow[]
}

interface ParsedStrategyRepair {
  summary: StrategyRepairSummary
  variants: StrategyRepairVariant[]
  gates: EvidenceGate[]
  bestVariantName: string
  policyName: string | undefined
  executionProxy: string | undefined
  priceSampling: string | undefined
  nRawPricePoints: number | undefined
  nSampledPricePoints: number | undefined
}

interface ParsedStrategyHypothesisAudit {
  summary: HypothesisSummary
  blockers: string[]
  timing: HypothesisTiming
  bucketAudit: HypothesisBucketAudit
  stability: HypothesisStability
  segments: HypothesisSegments
}

interface ParsedStrategyExperiment {
  summary: StrategyExperimentSummary
  modelValidation: StrategyExperimentModelValidation
  maxEdge: HistoricalProfile
  blockedCounts: Record<string, number>
  gates: EvidenceGate[]
  shadowSample: StrategyExperimentSample[]
}

interface ParsedStrategyDiscovery {
  summary: StrategyDiscoverySummary
  profile: HistoricalProfile
  gates: EvidenceGate[]
  folds: DiscoveryFold[]
}

interface ParsedFeatureDiscovery {
  summary: FeatureDiscoverySummary
  profile: HistoricalProfile
  gates: EvidenceGate[]
  folds: DiscoveryFold[]
}

interface ParsedFeatureCandidateAudit {
  summary: FeatureCandidateAuditSummary
  profile: HistoricalProfile
  topSegments: FeatureAuditSegment[]
  gates: EvidenceGate[]
}

interface ParsedHighRewardCityHunt {
  summary: HighRewardCityHuntSummary
  bestCities: HighRewardCity[]
  gates: EvidenceGate[]
}

interface ParsedHighRewardPaperStatus {
  runAt: string
  status: string
  policyName: string
  activeCities: string[]
  summary: HighRewardPaperSummary
  cities: HighRewardPaperCity[]
  missingCoverageSamples: HighRewardMissingCoverageSample[]
  pendingTargets: HighRewardPendingTarget[]
  nextAction: HighRewardNextAction | null
  blockers: string[]
}

interface HighRewardPaperSummary {
  entryFills: number | undefined
  settlementFills: number | undefined
  resolvedFills: number | undefined
  forwardDays: number | undefined
  remainingForwardDays: number | undefined
  remainingResolvedFills: number | undefined
  sampleGate: boolean
  coverageGate: boolean
  missingCoverage: string[]
  paperPnl: string | undefined
  resolvedPnl: string | undefined
  payoffRatio: string | undefined
}

interface HighRewardPaperCity {
  citySlug: string
  side: string | undefined
  signals: number | undefined
  entryFills: number | undefined
  settlementFills: number | undefined
  rejectedOrders: number | undefined
  paperPnl: string | undefined
  resolvedPnl: string | undefined
  payoffRatio: string | undefined
  maxLossStreak: number | undefined
  avgSlippage: string | undefined
}

interface HighRewardMissingCoverageSample {
  citySlug: string
  reason: string | undefined
  side: string | undefined
  bucket: string | undefined
  marketPrice: string | undefined
  variantMaxPrice: string | undefined
  priceToVariantMax: number | undefined
  probabilityDelta: number | undefined
  probabilityDeltaToMin: number | undefined
  hoursToClose: number | undefined
}

interface HighRewardPendingTarget {
  citySlug: string
  side: string | undefined
  targetDate: string | undefined
  closed: boolean
  winner: boolean | undefined
  signals: number | undefined
  entrySignals: number | undefined
  pendingSignals: number | undefined
  entryFills: number | undefined
  settlementFills: number | undefined
}

interface HighRewardNextAction {
  code: string
  severity: string | undefined
  detail: string | undefined
}

interface ParsedStrategyShadow {
  decisions: StrategyShadowDecisionView[]
  policyNames: string[]
  wouldTrade: number
}

interface StrategyShadowDecisionView {
  id: number
  ts: string
  policyName: string
  citySlug: string
  side: string | undefined
  marketPrice: string | undefined
  calibratedProb: number | undefined
  edgeNet: string | undefined
  reason: string | undefined
  wouldTrade: boolean
}

interface ParsedCityEdgeRanking {
  summary: CityEdgeRankingSummary
  cities: CityEdgeRankingCity[]
  research: CityEdgeRankingCity[]
  gates: EvidenceGate[]
}

interface ParsedWeatherCityDiscovery {
  summary: WeatherCityDiscoverySummary
  cities: WeatherCityDiscoveryCity[]
  gates: EvidenceGate[]
}

interface ParsedCityResolutionPromotionAudit {
  summary: CityResolutionPromotionSummary
  cities: CityResolutionPromotionCity[]
  gates: EvidenceGate[]
}

interface ParsedCityPromotionApply {
  summary: CityPromotionApplySummary
  rows: CityPromotionApplyCity[]
  gates: EvidenceGate[]
}

interface ParsedDiscoveryCandidateAudit {
  summary: DiscoveryCandidateAuditSummary
  profile: HistoricalProfile
  concentration: DiscoveryCandidateConcentration
  resolution: DiscoveryCandidateResolution
  timing: DiscoveryCandidateTiming
  blockedCounts: Record<string, number>
  segments: DiscoveryCandidateSegment[]
  gates: EvidenceGate[]
}

interface ParsedCityResearchAudit {
  summary: CityResearchAuditSummary
  cities: CityResearchAuditCity[]
  gates: EvidenceGate[]
}

interface ParsedCityOnboarding {
  summary: CityOnboardingSummary
  cities: CityOnboardingCity[]
  gates: EvidenceGate[]
}

interface CityOnboardingSummary {
  requestedCities: string[]
  liveEligible: number | undefined
  researchOnly: number | undefined
  excluded: number | undefined
  cannotApproveLive: boolean
}

interface CityOnboardingCity {
  citySlug: string
  classification: string
  metadataPassed: boolean
  climatePassed: boolean
  marketPassed: boolean
  resolutionPassed: boolean
}

interface CityResearchAuditSummary {
  liveEligible: number | undefined
  researchOnly: number | undefined
  excluded: number | undefined
  cannotApproveLive: boolean
}

interface CityResearchAuditCity {
  citySlug: string
  classification: string
  forecastObservedPairs: number | undefined
  resolvedMarkets: number | undefined
  tradeHistoryPoints: number | undefined
  reasons: string[]
  failureCategories: Record<string, string[]>
}

interface CityEdgeRankingSummary {
  bestLiveCity: string | undefined
  topLiveCities: string[]
  liveCandidateCount: number | undefined
  researchOnlyCount: number | undefined
  nextAction: string | undefined
  nextCommands: string[]
  cannotApproveLive: boolean
}

interface CityEdgeRankingCity {
  citySlug: string
  classification: string
  validFolds: number | undefined
  bestFamily: string | undefined
  eligibleForTargetedDiscovery: boolean
  profile: HistoricalProfile
  rejectionReasons: string[]
}

interface WeatherCityDiscoverySummary {
  citiesSeen: number | undefined
  newCitiesRegistered: number | undefined
  nextAction: string | undefined
  cannotApproveLive: boolean
}

interface WeatherCityDiscoveryCity {
  citySlug: string
  stationCode: string | undefined
  resolutionSource: string | undefined
  metadataComplete: boolean
  registeredAsNeedsReview: boolean
}

interface CityResolutionPromotionSummary {
  promotableCities: string[]
  nextAction: string | undefined
  cannotApproveLive: boolean
}

interface CityResolutionPromotionCity {
  citySlug: string
  promotionStatus: string
  auditedMarkets: number | undefined
  mismatches: number | undefined
  mismatchRate: string | undefined
  canEnterShadow: boolean
}

interface CityPromotionApplySummary {
  requestedCities: string[]
  promotedCities: string[]
  blockedCities: string[]
  cannotApproveLive: boolean
}

interface CityPromotionApplyCity {
  citySlug: string
  result: string
  auditRunId: number | undefined
  mismatchRate: string | undefined
  resolutionSourceUsed: string | undefined
  blockers: string[]
}

interface StrategyDiscoverySummary {
  bestFamily: string | undefined
  discoveryVersion: string | undefined
  validFolds: number | undefined
  cannotApproveLive: boolean
  liveEligibleCities: string[]
  researchOnlyCities: string[]
}

interface FeatureDiscoverySummary {
  bestFamily: string | undefined
  validFolds: number | undefined
  nFeatureCandidates: number | undefined
  cannotApproveLive: boolean
  selectedCities: string[]
  features: string[]
}

interface FeatureCandidateAuditSummary {
  bestFamily: string | undefined
  explanation: string | undefined
  approvedSubsetKey: string | undefined
}

interface FeatureAuditSegment {
  key: string
  nResolvedTrades: number | undefined
  totalPnl: string | undefined
  brierDelta: number | undefined
  foldCount: number | undefined
}

interface HighRewardCityHuntSummary {
  approvedCityCount: number | undefined
  strategyGoal: string | undefined
  cannotApproveLive: boolean
}

interface HighRewardCity {
  citySlug: string
  family: string
  side: string
  variant: string
  nTrades: number | undefined
  winRate: number | undefined
  payoffRatio: string | undefined
  roi: string | undefined
  totalPnl: string | undefined
  passed: boolean
  blockers: string[]
}

interface DiscoveryFold {
  index: number | undefined
  foldStart: string | undefined
  selectedFamily: string | undefined
  reason: string | undefined
  nFoldCandidates: number | undefined
  nOosTrades: number | undefined
  pnl: string | undefined
  brierDelta: number | undefined
}

interface DiscoveryCandidateAuditSummary {
  bestFamily: string | undefined
  nextAction: string | undefined
}

interface DiscoveryCandidateConcentration {
  topCity: string | undefined
  topCityShare: string | undefined
  researchOnlyTradedCities: string[]
}

interface DiscoveryCandidateResolutionCity {
  citySlug: string
  stationCode: string | undefined
  auditedMarkets: number | undefined
  mismatches: number | undefined
  missingObservations: number | undefined
}

interface DiscoveryCandidateResolution {
  valid: boolean
  cities: DiscoveryCandidateResolutionCity[]
}

interface DiscoveryCandidateTiming {
  valid: boolean
  effectiveAfterClose: number | undefined
}

interface DiscoveryCandidateSegment {
  segment: string
  nTrades: number | undefined
  totalPnl: string | undefined
  brierDelta: number | undefined
  top5Share: string | undefined
}

interface StrategyExperimentSummary {
  bestVariant: string | undefined
  cannotApproveLive: boolean
}

interface StrategyExperimentModelValidation {
  brierDelta: number | undefined
}

interface StrategyExperimentSample {
  ts: string | undefined
  marketId: string | undefined
  citySlug: string | undefined
  marketPrice: string | undefined
  rawProb: number | undefined
  calibratedProb: number | undefined
  edgeNet: string | undefined
  reason: string | undefined
  wouldTrade: boolean
}

interface HypothesisSummary {
  nextAction: string | undefined
}

interface TimingSourceSummary {
  afterMarketClose: number | undefined
}

interface HypothesisTiming {
  valid: boolean
  dataApiTrades: TimingSourceSummary
  clobPricesHistory: TimingSourceSummary
}

interface HypothesisBucketAudit {
  valid: boolean
  issueCount: number | undefined
}

interface HypothesisStability {
  selectedPolicyName: string | undefined
  eligibleSegments: number | undefined
  oosCandidates: number | undefined
  oosTrades: number | undefined
  decisionTrace: HypothesisDecisionTrace
}

interface HypothesisDecisionTrace {
  actionableCandidates: number | undefined
  blockedCounts: Record<string, number>
  samples: HypothesisDecisionSample[]
}

interface HypothesisDecisionSample {
  ts: string | undefined
  marketId: string | undefined
  citySlug: string | undefined
  marketPrice: string | undefined
  rawProb: number | undefined
  calibratedProb: number | undefined
  edgeNet: string | undefined
  costPerShare: string | undefined
  hoursToClose: number | undefined
  reason: string | undefined
}

interface HypothesisSegment {
  group: string | undefined
  segment: string | undefined
  nTrades: number | undefined
  totalPnl: string | undefined
  brierDelta: number | undefined
}

interface HypothesisSegments {
  worstSegments: HypothesisSegment[]
}

interface StrategyRepairSummary {
  baseline_pnl: string | undefined
  best_variant_pnl: string | undefined
  baseline_brier_delta: number | undefined
  best_variant_brier_delta: number | undefined
  policy_version: string | undefined
  probability_cap: number | undefined
  min_calibration_samples: number | undefined
  alpha: number | undefined
  min_edge_net: string | undefined
  validation_scheme: string | undefined
  train_window: WindowRange | undefined
  holdout_window: WindowRange | undefined
  price_floor: string | undefined
  low_price_mode: string | undefined
  eligible_segments: number | undefined
  final_eligible_segments: number | undefined
  walk_forward_traded_segments: number | undefined
  traded_segments: number | undefined
  total_segments: number | undefined
  market_history_span: WindowRange | undefined
  fold_count: number | undefined
  fold_days: number | undefined
  min_train_days: number | undefined
  selection_train_size: number | undefined
  selected_policy_name: string | undefined
  insufficient_reason: string | undefined
  folds: RepairFold[]
}

interface RepairFold {
  index: number | undefined
  trainWindow: WindowRange | undefined
  foldWindow: WindowRange | undefined
  nTrain: number | undefined
  nFoldCandidates: number | undefined
  valid: boolean
  reason: string | undefined
  nOosTrades: number | undefined
  pnl: string | undefined
  brierDelta: number | undefined
}

interface StrategyRepairVariant {
  name: string
  policyVersion: string | undefined
  calibrate: boolean
  applySegmentFilters: boolean
  segmentScope: string | undefined
  validationSplit: string | undefined
  alpha: number | undefined
  probabilityCap: number | undefined
  priceFloor: string | undefined
  eligibleSegments: number | undefined
  finalEligibleSegments: number | undefined
  walkForwardTradedSegments: number | undefined
  tradedSegments: number | undefined
  totalSegments: number | undefined
  maxEdge: HistoricalProfile
  blockedCounts: Record<string, number>
}

interface WindowRange {
  start: string | undefined
  end: string | undefined
}

interface EvidenceDataHealth {
  forward_days: number | undefined
  price_snapshots: number | undefined
  book_snapshots: number | undefined
  ensemble_members: number | undefined
  resolved_markets: number | undefined
}

interface EvidenceGate {
  key: string
  passed: boolean
  value: unknown
  required: unknown
}

interface EvidenceProfile {
  profile: string
  source: string | undefined
  n_resolved_trades: number | undefined
  total_pnl: string | undefined
  roi: string | undefined
  brier_delta: number | undefined
  max_loss_streak: number | undefined
  avg_edge_net: string | undefined
}

interface EvidenceCityRow {
  city_slug: string
  price_snapshots: number | undefined
  ensemble_members: number | undefined
  resolutions: number | undefined
  reward_volatility_score: number | undefined
  needs_review: boolean
}

interface HistoricalDataHealth {
  market_price_history_points: number | undefined
  market_trade_history_points: number | undefined
}

interface HistoricalModelHealth {
  min_forecast_observed_pairs: number | undefined
}

interface HistoricalProfile {
  profile: string
  n_resolved_trades: number | undefined
  total_pnl: string | undefined
  roi: string | undefined
  brier_delta: number | undefined
  pnl_ci_low: string | undefined
  pnl_ci_high: string | undefined
  top_5_abs_pnl_share: string | undefined
}

interface DiagnosticProfile {
  n_trades: number | undefined
  total_pnl: string | undefined
  brier_delta: number | undefined
  observed_rate: number | undefined
}

interface DiagnosticAction {
  key: string
  priority: number | undefined
  reason: string
}

interface DiagnosticSegment {
  segment_group: string
  segment: string
  n_trades: number | undefined
  total_pnl: string | undefined
  win_rate: number | undefined
  avg_model_prob: number | undefined
  observed_rate: number | undefined
  brier_delta: number | undefined
}

interface DiagnosticCalibrationRow {
  bucket: string
  n_trades: number | undefined
  observed_rate: number | undefined
  avg_model_prob: number | undefined
  avg_market_price: string | undefined
  model_overconfidence: number | undefined
  total_pnl: string | undefined
}

function parseMeasurement(row: MeasurementRunLike): ParsedMeasurement {
  const summary = parseRecord(row.summary_json)
  const metrics = parseRecord(row.metrics_json)
  const checksRaw = parseRecord(row.checks_json)
  const checks = Object.entries(checksRaw).map(([key, value]) => {
    const check = isRecord(value) ? value : {}
    return {
      key,
      passed: check.passed === true,
      value: check.value,
      required: check.required,
    }
  })
  return {
    summary: {
      orders: numericParam(summary.orders) ?? undefined,
      entry_fills: numericParam(summary.entry_fills) ?? undefined,
      policy_name: stringParam(summary.policy_name),
      paper_pnl: moneyParam(summary.paper_pnl),
    },
    metrics: {
      total_fee_paid: moneyParam(metrics.total_fee_paid),
      avg_slippage: moneyParam(metrics.avg_slippage),
      paper_vs_replay_pnl_delta: moneyParam(metrics.paper_vs_replay_pnl_delta),
    },
    checks,
  }
}

function parseHistoricalValidation(
  row: HistoricalValidationRunLike,
): ParsedHistoricalValidation {
  const dataHealth = parseRecord(row.data_health_json)
  const modelHealth = parseRecord(row.model_health_json)
  const trading = parseRecord(row.trading_json)
  const gatesRaw = parseRecord(row.gates_json)
  const cities = parseStringList(row.cities_json)

  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })

  const profileRecords = isRecord(trading.profiles) ? trading.profiles : {}
  const profiles = Object.entries(profileRecords).flatMap(([profile, value]) => {
    if (!isRecord(value)) return []
    return [
      {
        profile,
        n_resolved_trades: numericParam(value.n_resolved_trades) ?? undefined,
        total_pnl: moneyParam(value.total_pnl),
        roi: moneyParam(value.roi),
        brier_delta: numericParam(value.brier_delta) ?? undefined,
        pnl_ci_low: moneyParam(value.pnl_ci_low),
        pnl_ci_high: moneyParam(value.pnl_ci_high),
        top_5_abs_pnl_share: moneyParam(value.top_5_abs_pnl_share),
      },
    ]
  })

  return {
    cities,
    dataHealth: {
      market_price_history_points:
        numericParam(dataHealth.market_price_history_points) ?? undefined,
      market_trade_history_points:
        numericParam(dataHealth.market_trade_history_points) ?? undefined,
    },
    modelHealth: {
      min_forecast_observed_pairs:
        numericParam(modelHealth.min_forecast_observed_pairs) ?? undefined,
    },
    gates,
    profiles,
    executionProxy: stringParam(trading.execution_proxy),
    priceSampling: stringParam(trading.price_sampling),
    nRawPricePoints: numericParam(trading.n_raw_price_points) ?? undefined,
    nSampledPricePoints: numericParam(trading.n_sampled_price_points) ?? undefined,
    priceSourceCounts: parseNumberRecord(trading.price_source_counts),
  }
}

function parseHistoricalDiagnostics(
  row: HistoricalDiagnosticsRunLike,
): ParsedHistoricalDiagnostics {
  const summary = parseRecord(row.summary_json)
  const calibrationRoot = parseRecord(row.calibration_json)
  const recommendations = parseRecord(row.recommendations_json)
  const profiles = isRecord(summary.profiles) ? summary.profiles : {}
  const maxEdgeRaw = isRecord(profiles.max_edge) ? profiles.max_edge : {}
  const actionsRaw = Array.isArray(recommendations.actions) ? recommendations.actions : []
  const worstSegmentsRaw = Array.isArray(recommendations.worst_segments)
    ? recommendations.worst_segments
    : []
  const calibrationRaw = Array.isArray(calibrationRoot.max_edge)
    ? calibrationRoot.max_edge
    : []

  return {
    maxEdge: {
      n_trades: numericParam(maxEdgeRaw.n_trades) ?? undefined,
      total_pnl: moneyParam(maxEdgeRaw.total_pnl),
      brier_delta: numericParam(maxEdgeRaw.brier_delta) ?? undefined,
      observed_rate: numericParam(maxEdgeRaw.observed_rate) ?? undefined,
    },
    nRawPricePoints: numericParam(summary.n_raw_price_points) ?? undefined,
    nSampledPricePoints: numericParam(summary.n_sampled_price_points) ?? undefined,
    actions: actionsRaw.flatMap((value) => {
      if (!isRecord(value)) return []
      return [
        {
          key: stringParam(value.key) ?? "unknown",
          priority: numericParam(value.priority) ?? undefined,
          reason: stringParam(value.reason) ?? "-",
        },
      ]
    }),
    worstSegments: worstSegmentsRaw.flatMap((value) => {
      if (!isRecord(value)) return []
      return [
        {
          segment_group: stringParam(value.segment_group) ?? "unknown",
          segment: stringParam(value.segment) ?? "-",
          n_trades: numericParam(value.n_trades) ?? undefined,
          total_pnl: moneyParam(value.total_pnl),
          win_rate: numericParam(value.win_rate) ?? undefined,
          avg_model_prob: numericParam(value.avg_model_prob) ?? undefined,
          observed_rate: numericParam(value.observed_rate) ?? undefined,
          brier_delta: numericParam(value.brier_delta) ?? undefined,
        },
      ]
    }),
    calibration: calibrationRaw.flatMap((value) => {
      if (!isRecord(value)) return []
      return [
        {
          bucket: stringParam(value.bucket) ?? "-",
          n_trades: numericParam(value.n_trades) ?? undefined,
          observed_rate: numericParam(value.observed_rate) ?? undefined,
          avg_model_prob: numericParam(value.avg_model_prob) ?? undefined,
          avg_market_price: moneyParam(value.avg_market_price),
          model_overconfidence: numericParam(value.model_overconfidence) ?? undefined,
          total_pnl: moneyParam(value.total_pnl),
        },
      ]
    }),
  }
}

function parseStrategyRepair(row: StrategyRepairRunLike): ParsedStrategyRepair {
  const summary = parseRecord(row.summary_json)
  const bestVariant = parseRecord(row.best_variant_json)
  const gatesRaw = parseRecord(row.gates_json)
  const variants = parseRecordList(row.variants_json).map(parseStrategyRepairVariant)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })

  return {
    summary: {
      baseline_pnl: moneyParam(summary.baseline_pnl),
      best_variant_pnl: moneyParam(summary.best_variant_pnl),
      baseline_brier_delta: numericParam(summary.baseline_brier_delta) ?? undefined,
      best_variant_brier_delta: numericParam(summary.best_variant_brier_delta) ?? undefined,
      policy_version: stringParam(summary.policy_version),
      probability_cap: numericParam(summary.probability_cap) ?? undefined,
      min_calibration_samples: numericParam(summary.min_calibration_samples) ?? undefined,
      alpha: numericParam(summary.alpha) ?? undefined,
      min_edge_net: moneyParam(summary.min_edge_net),
      validation_scheme: stringParam(summary.validation_scheme),
      train_window: parseWindowRange(summary.train_window),
      holdout_window: parseWindowRange(summary.holdout_window),
      price_floor: moneyParam(summary.price_floor),
      low_price_mode: stringParam(summary.low_price_mode),
      eligible_segments: numericParam(summary.eligible_segments) ?? undefined,
      final_eligible_segments: numericParam(summary.final_eligible_segments) ?? undefined,
      walk_forward_traded_segments:
        numericParam(summary.walk_forward_traded_segments) ?? undefined,
      traded_segments: numericParam(summary.traded_segments) ?? undefined,
      total_segments: numericParam(summary.total_segments) ?? undefined,
      market_history_span: parseWindowRange(summary.market_history_span),
      fold_count: numericParam(summary.fold_count) ?? undefined,
      fold_days: numericParam(summary.fold_days) ?? undefined,
      min_train_days: numericParam(summary.min_train_days) ?? undefined,
      selection_train_size: numericParam(summary.selection_train_size) ?? undefined,
      selected_policy_name: stringParam(summary.selected_policy_name),
      insufficient_reason: stringParam(summary.insufficient_reason),
      folds: recordListParam(summary.folds).map(parseRepairFold),
    },
    variants,
    gates,
    bestVariantName:
      stringParam(bestVariant.name) ?? stringParam(summary.best_variant) ?? "unknown",
    policyName: stringParam(bestVariant.policy_name) ?? stringParam(summary.policy_name),
    executionProxy: stringParam(bestVariant.execution_proxy),
    priceSampling: stringParam(bestVariant.price_sampling),
    nRawPricePoints: numericParam(bestVariant.n_raw_price_points) ?? undefined,
    nSampledPricePoints: numericParam(bestVariant.n_sampled_price_points) ?? undefined,
  }
}

function parseStrategyHypothesisAudit(
  row: StrategyHypothesisAuditRunLike,
): ParsedStrategyHypothesisAudit {
  const summary = parseRecord(row.summary_json)
  const timing = parseRecord(row.timing_json)
  const dataApiTrades = parseRecordParam(timing.data_api_trades)
  const clobPricesHistory = parseRecordParam(timing.clob_prices_history)
  const bucketAudit = parseRecord(row.bucket_audit_json)
  const stability = parseRecord(row.stability_json)
  const decisionTrace = parseRecordParam(stability.decision_trace)
  const segments = parseRecord(row.segments_json)
  return {
    summary: {
      nextAction: stringParam(summary.next_action),
    },
    blockers: parseStringList(row.blockers_json),
    timing: {
      valid: timing.valid === true,
      dataApiTrades: {
        afterMarketClose: numericParam(dataApiTrades.after_market_close) ?? undefined,
      },
      clobPricesHistory: {
        afterMarketClose: numericParam(clobPricesHistory.after_market_close) ?? undefined,
      },
    },
    bucketAudit: {
      valid: bucketAudit.valid === true,
      issueCount: numericParam(bucketAudit.issue_count) ?? undefined,
    },
    stability: {
      selectedPolicyName: stringParam(stability.selected_policy_name),
      eligibleSegments: numericParam(stability.eligible_segments) ?? undefined,
      oosCandidates:
        numericParam(stability.oos_candidates_in_eligible_segments) ?? undefined,
      oosTrades: numericParam(stability.oos_trades_in_selected_policy) ?? undefined,
      decisionTrace: {
        actionableCandidates:
          numericParam(decisionTrace.actionable_candidates) ?? undefined,
        blockedCounts: parseNumberRecord(decisionTrace.blocked_counts),
        samples: recordListParam(decisionTrace.samples).map((sample) => ({
          ts: stringParam(sample.ts),
          marketId: stringParam(sample.market_id),
          citySlug: stringParam(sample.city_slug),
          marketPrice: moneyParam(sample.market_price),
          rawProb: numericParam(sample.raw_prob) ?? undefined,
          calibratedProb: numericParam(sample.calibrated_prob) ?? undefined,
          edgeNet: moneyParam(sample.edge_net),
          costPerShare: moneyParam(sample.cost_per_share),
          hoursToClose: numericParam(sample.hours_to_close) ?? undefined,
          reason: stringParam(sample.reason),
        })),
      },
    },
    segments: {
      worstSegments: recordListParam(segments.worst_segments).map((segment) => ({
        group: stringParam(segment.segment_group ?? segment.group),
        segment: stringParam(segment.segment),
        nTrades: numericParam(segment.n_trades) ?? undefined,
        totalPnl: moneyParam(segment.total_pnl),
        brierDelta: numericParam(segment.brier_delta) ?? undefined,
      })),
    },
  }
}

function parseCityOnboarding(row: CityOnboardingRunLike): ParsedCityOnboarding {
  const summary = parseRecord(row.summary_json)
  const gatesRaw = parseRecord(row.gates_json)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })
  return {
    summary: {
      requestedCities: parseStringListParam(summary.requested_cities),
      liveEligible: numericParam(summary.live_eligible) ?? undefined,
      researchOnly: numericParam(summary.research_only) ?? undefined,
      excluded: numericParam(summary.excluded) ?? undefined,
      cannotApproveLive: summary.cannot_approve_live === true,
    },
    cities: parseRecordList(row.checks_json).map((city) => {
      const checks = parseRecordParam(city.checks)
      const metadata = parseRecordParam(checks.metadata)
      const climate = parseRecordParam(checks.climate)
      const market = parseRecordParam(checks.market)
      const resolution = parseRecordParam(checks.resolution)
      return {
        citySlug: stringParam(city.city_slug) ?? "unknown",
        classification: stringParam(city.classification) ?? "excluded",
        metadataPassed: metadata.passed === true,
        climatePassed: climate.passed === true,
        marketPassed: market.passed === true,
        resolutionPassed: resolution.passed === true,
      }
    }),
    gates,
  }
}

function parseCityResearchAudit(row: CityResearchAuditRunLike): ParsedCityResearchAudit {
  const summary = parseRecord(row.summary_json)
  const gatesRaw = parseRecord(row.gates_json)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })
  return {
    summary: {
      liveEligible: numericParam(summary.live_eligible) ?? undefined,
      researchOnly: numericParam(summary.research_only) ?? undefined,
      excluded: numericParam(summary.excluded) ?? undefined,
      cannotApproveLive: summary.cannot_approve_live === true,
    },
    cities: parseRecordList(row.cities_json).map((city) => ({
      citySlug: stringParam(city.city_slug) ?? "unknown",
      classification: stringParam(city.classification) ?? "excluded",
      forecastObservedPairs: numericParam(city.forecast_observed_pairs) ?? undefined,
      resolvedMarkets: numericParam(city.resolved_markets) ?? undefined,
      tradeHistoryPoints: numericParam(city.trade_history_points) ?? undefined,
      reasons: parseStringListParam(city.reasons),
      failureCategories: parseStringArrayRecord(city.failure_categories),
    })),
    gates,
  }
}

function parseCityEdgeRanking(row: CityEdgeRankingRunLike): ParsedCityEdgeRanking {
  const summary = parseRecord(row.summary_json)
  const gatesRaw = parseRecord(row.gates_json)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })
  return {
    summary: {
      bestLiveCity: stringParam(summary.best_live_city),
      topLiveCities: parseStringListParam(summary.top_live_cities),
      liveCandidateCount: numericParam(summary.live_candidate_count) ?? undefined,
      researchOnlyCount: numericParam(summary.research_only_count) ?? undefined,
      nextAction: stringParam(summary.next_action),
      nextCommands: parseStringListParam(summary.next_commands),
      cannotApproveLive: summary.cannot_approve_live === true,
    },
    cities: parseRecordList(row.cities_json).map(parseCityEdgeRankingCity),
    research: parseRecordList(row.research_json).map(parseCityEdgeRankingCity),
    gates,
  }
}

function parseWeatherCityDiscovery(
  row: WeatherCityDiscoveryRunLike,
): ParsedWeatherCityDiscovery {
  const summary = parseRecord(row.summary_json)
  const gatesRaw = parseRecord(row.gates_json)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })
  return {
    summary: {
      citiesSeen: numericParam(summary.cities_seen) ?? undefined,
      newCitiesRegistered: numericParam(summary.new_cities_registered) ?? undefined,
      nextAction: stringParam(summary.next_action),
      cannotApproveLive: summary.cannot_approve_live === true,
    },
    cities: parseRecordList(row.cities_json).map((city) => ({
      citySlug: stringParam(city.city_slug) ?? "unknown",
      stationCode: stringParam(city.station_code),
      resolutionSource: stringParam(city.resolution_source),
      metadataComplete: city.metadata_complete === true,
      registeredAsNeedsReview: city.registered_as_needs_review === true,
    })),
    gates,
  }
}

function parseCityResolutionPromotionAudit(
  row: CityResolutionPromotionAuditRunLike,
): ParsedCityResolutionPromotionAudit {
  const summary = parseRecord(row.summary_json)
  const resolution = parseRecord(row.resolution_json)
  const gatesRaw = parseRecord(row.gates_json)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })
  return {
    summary: {
      promotableCities: parseStringListParam(summary.promotable_cities),
      nextAction: stringParam(summary.next_action),
      cannotApproveLive: summary.cannot_approve_live === true,
    },
    cities: recordListParam(resolution.cities).map((city) => ({
      citySlug: stringParam(city.city_slug) ?? "unknown",
      promotionStatus: stringParam(city.promotion_status) ?? "DATA_REVIEW",
      auditedMarkets: numericParam(city.audited_markets) ?? undefined,
      mismatches: numericParam(city.mismatches) ?? undefined,
      mismatchRate: moneyParam(city.mismatch_rate),
      canEnterShadow: city.can_enter_shadow === true,
    })),
    gates,
  }
}

function parseCityPromotionApply(row: CityPromotionApplyRunLike): ParsedCityPromotionApply {
  const summary = parseRecord(row.summary_json)
  const promoted = parseRecordList(row.promoted_cities_json).map((city) => ({
    citySlug: stringParam(city.city_slug) ?? "unknown",
    result: "promoted",
    auditRunId: numericParam(city.audit_run_id) ?? undefined,
    mismatchRate: moneyParam(city.mismatch_rate),
    resolutionSourceUsed: stringParam(city.resolution_source_used),
    blockers: [],
  }))
  const blocked = parseRecordList(row.blocked_json).map((city) => ({
    citySlug: stringParam(city.city_slug) ?? "unknown",
    result: "blocked",
    auditRunId: undefined,
    mismatchRate: moneyParam(parseRecordParam(city.audit).mismatch_rate),
    resolutionSourceUsed: stringParam(parseRecordParam(city.audit).resolution_source_used),
    blockers: parseStringListParam(city.blockers),
  }))
  const gatesRaw = parseRecord(row.gates_json)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })

  return {
    summary: {
      requestedCities: parseStringListParam(summary.requested_cities),
      promotedCities: parseStringListParam(summary.promoted_cities),
      blockedCities: parseStringListParam(summary.blocked_cities),
      cannotApproveLive: summary.cannot_approve_live === true,
    },
    rows: [...promoted, ...blocked],
    gates,
  }
}

function parseCityEdgeRankingCity(city: Record<string, unknown>): CityEdgeRankingCity {
  const profileRaw = parseRecordParam(city.profile)
  return {
    citySlug: stringParam(city.city_slug) ?? "unknown",
    classification: stringParam(city.classification) ?? "excluded",
    validFolds: numericParam(city.valid_folds) ?? undefined,
    bestFamily: stringParam(city.best_family),
    eligibleForTargetedDiscovery: city.eligible_for_targeted_discovery === true,
    profile: {
      profile: "max_edge",
      n_resolved_trades: numericParam(profileRaw.n_resolved_trades) ?? undefined,
      total_pnl: moneyParam(profileRaw.total_pnl),
      roi: moneyParam(profileRaw.roi),
      brier_delta: numericParam(profileRaw.brier_delta) ?? undefined,
      pnl_ci_low: moneyParam(profileRaw.pnl_ci_low),
      pnl_ci_high: moneyParam(profileRaw.pnl_ci_high),
      top_5_abs_pnl_share: moneyParam(profileRaw.top_5_abs_pnl_share),
    },
    rejectionReasons: parseStringListParam(city.rejection_reasons),
  }
}

function parseDiscoveryCandidateAudit(
  row: DiscoveryCandidateAuditRunLike,
): ParsedDiscoveryCandidateAudit {
  const summary = parseRecord(row.summary_json)
  const concentration = parseRecord(row.concentration_json)
  const profileRaw = parseRecordParam(concentration.profile)
  const cityPnlShare = parseRecordParam(concentration.city_pnl_share)
  const resolution = parseRecord(row.city_resolution_json)
  const timing = parseRecord(row.timing_json)
  const segments = parseRecord(row.segments_json)
  const gatesRaw = parseRecord(row.gates_json)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })

  return {
    summary: {
      bestFamily: stringParam(summary.best_family),
      nextAction: stringParam(summary.next_action),
    },
    profile: {
      profile: "max_edge",
      n_resolved_trades: numericParam(profileRaw.n_resolved_trades) ?? undefined,
      total_pnl: moneyParam(profileRaw.total_pnl),
      roi: moneyParam(profileRaw.roi),
      brier_delta: numericParam(profileRaw.brier_delta) ?? undefined,
      pnl_ci_low: moneyParam(profileRaw.pnl_ci_low),
      pnl_ci_high: moneyParam(profileRaw.pnl_ci_high),
      top_5_abs_pnl_share: moneyParam(profileRaw.top_5_abs_pnl_share),
    },
    concentration: {
      topCity: stringParam(concentration.top_city ?? cityPnlShare.top_city),
      topCityShare: moneyParam(
        concentration.top_city_abs_pnl_share ?? cityPnlShare.top_city_abs_pnl_share,
      ),
      researchOnlyTradedCities: parseStringListParam(
        concentration.research_only_traded_cities,
      ),
    },
    resolution: {
      valid: resolution.valid === true,
      cities: recordListParam(resolution.cities).map((city) => ({
        citySlug: stringParam(city.city_slug) ?? "unknown",
        stationCode: stringParam(city.station_code),
        auditedMarkets: numericParam(city.audited_markets) ?? undefined,
        mismatches: numericParam(city.mismatches) ?? undefined,
        missingObservations: numericParam(city.missing_observations) ?? undefined,
      })),
    },
    timing: {
      valid: timing.valid === true,
      effectiveAfterClose: numericParam(timing.effective_after_close) ?? undefined,
    },
    blockedCounts: parseNumberRecord(segments.blocked_counts),
    segments: recordListParam(segments.by_segment).map((segment) => ({
      segment: stringParam(segment.segment) ?? "-",
      nTrades: numericParam(segment.n_resolved_trades) ?? undefined,
      totalPnl: moneyParam(segment.total_pnl),
      brierDelta: numericParam(segment.brier_delta) ?? undefined,
      top5Share: moneyParam(segment.top_5_abs_pnl_share),
    })),
    gates,
  }
}

function parseStrategyDiscovery(row: StrategyDiscoveryRunLike): ParsedStrategyDiscovery {
  const summary = parseRecord(row.summary_json)
  const bestFamily = parseRecord(row.best_family_json)
  const profileRaw = parseRecordParam(bestFamily.profile)
  const gatesRaw = parseRecord(row.gates_json)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })
  return {
    summary: {
      bestFamily: stringParam(summary.best_family),
      discoveryVersion: stringParam(summary.discovery_version),
      validFolds: numericParam(summary.valid_folds) ?? undefined,
      cannotApproveLive: summary.cannot_approve_live === true,
      liveEligibleCities: parseStringListParam(summary.live_eligible_cities),
      researchOnlyCities: parseStringListParam(summary.research_only_cities),
    },
    profile: {
      profile: "max_edge",
      n_resolved_trades: numericParam(profileRaw.n_resolved_trades) ?? undefined,
      total_pnl: moneyParam(profileRaw.total_pnl),
      roi: moneyParam(profileRaw.roi),
      brier_delta: numericParam(profileRaw.brier_delta) ?? undefined,
      pnl_ci_low: moneyParam(profileRaw.pnl_ci_low),
      pnl_ci_high: moneyParam(profileRaw.pnl_ci_high),
      top_5_abs_pnl_share: moneyParam(profileRaw.top_5_abs_pnl_share),
    },
    gates,
    folds: parseRecordList(row.folds_json).map((fold) => {
      const window = parseRecordParam(fold.fold_window)
      return {
        index: numericParam(fold.index) ?? undefined,
        foldStart: stringParam(window.start),
        selectedFamily: stringParam(fold.selected_family),
        reason: stringParam(fold.reason),
        nFoldCandidates: numericParam(fold.n_fold_candidates) ?? undefined,
        nOosTrades: numericParam(fold.n_oos_trades) ?? undefined,
        pnl: moneyParam(fold.pnl),
        brierDelta: numericParam(fold.brier_delta) ?? undefined,
      }
    }),
  }
}

function parseFeatureDiscovery(row: FeatureDiscoveryRunLike): ParsedFeatureDiscovery {
  const summary = parseRecord(row.summary_json)
  const bestFamily = parseRecord(row.best_family_json)
  const profileRaw = parseRecordParam(bestFamily.profile)
  const gatesRaw = parseRecord(row.gates_json)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })
  return {
    summary: {
      bestFamily: stringParam(summary.best_family),
      validFolds: numericParam(summary.valid_folds) ?? undefined,
      nFeatureCandidates: numericParam(summary.n_feature_candidates) ?? undefined,
      cannotApproveLive: summary.cannot_approve_live === true,
      selectedCities: parseStringListParam(summary.selected_cities),
      features: parseStringListParam(summary.features),
    },
    profile: {
      profile: "max_edge",
      n_resolved_trades: numericParam(profileRaw.n_resolved_trades) ?? undefined,
      total_pnl: moneyParam(profileRaw.total_pnl),
      roi: moneyParam(profileRaw.roi),
      brier_delta: numericParam(profileRaw.brier_delta) ?? undefined,
      pnl_ci_low: moneyParam(profileRaw.pnl_ci_low),
      pnl_ci_high: moneyParam(profileRaw.pnl_ci_high),
      top_5_abs_pnl_share: moneyParam(profileRaw.top_5_abs_pnl_share),
    },
    gates,
    folds: parseRecordList(row.folds_json).map((fold) => {
      const window = parseRecordParam(fold.fold_window)
      return {
        index: numericParam(fold.index) ?? undefined,
        foldStart: stringParam(window.start),
        selectedFamily: stringParam(fold.selected_family),
        reason: stringParam(fold.reason),
        nFoldCandidates: numericParam(fold.n_fold_candidates) ?? undefined,
        nOosTrades: numericParam(fold.n_oos_trades) ?? undefined,
        pnl: moneyParam(fold.pnl),
        brierDelta: numericParam(fold.brier_delta) ?? undefined,
      }
    }),
  }
}

function parseFeatureCandidateAudit(
  row: FeatureCandidateAuditRunLike,
): ParsedFeatureCandidateAudit {
  const summary = parseRecord(row.summary_json)
  const profileRaw = parseRecord(row.profile_json)
  const segments = parseRecord(row.segments_json)
  const gatesRaw = parseRecord(row.gates_json)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })
  return {
    summary: {
      bestFamily: stringParam(summary.best_family),
      explanation: stringParam(summary.explanation),
      approvedSubsetKey: stringParam(summary.approved_subset_key),
    },
    profile: {
      profile: "max_edge",
      n_resolved_trades: numericParam(profileRaw.n_resolved_trades) ?? undefined,
      total_pnl: moneyParam(profileRaw.total_pnl),
      roi: moneyParam(profileRaw.roi),
      brier_delta: numericParam(profileRaw.brier_delta) ?? undefined,
      pnl_ci_low: moneyParam(profileRaw.pnl_ci_low),
      pnl_ci_high: moneyParam(profileRaw.pnl_ci_high),
      top_5_abs_pnl_share: moneyParam(profileRaw.top_5_abs_pnl_share),
    },
    topSegments: recordListParam(segments.by_segment).map((segment) => ({
      key: stringParam(segment.key) ?? "-",
      nResolvedTrades: numericParam(segment.n_resolved_trades) ?? undefined,
      totalPnl: moneyParam(segment.total_pnl),
      brierDelta: numericParam(segment.brier_delta) ?? undefined,
      foldCount: numericParam(segment.fold_count) ?? undefined,
    })),
    gates,
  }
}

function parseHighRewardCityHunt(row: HighRewardCityHuntRunLike): ParsedHighRewardCityHunt {
  const summary = parseRecord(row.summary_json)
  const rankings = parseRecord(row.rankings_json)
  const gatesRaw = parseRecord(row.gates_json)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })
  return {
    summary: {
      approvedCityCount: numericParam(summary.approved_city_count) ?? undefined,
      strategyGoal: stringParam(summary.strategy_goal),
      cannotApproveLive: summary.cannot_approve_live === true,
    },
    bestCities: recordListParam(rankings.best_per_city).map((city) => ({
      citySlug: stringParam(city.city_slug) ?? "unknown",
      family: stringParam(city.family) ?? "-",
      side: stringParam(city.side) ?? "-",
      variant: stringParam(city.variant) ?? "-",
      nTrades: numericParam(city.n_trades) ?? undefined,
      winRate: numericParam(city.win_rate) ?? undefined,
      payoffRatio: moneyParam(city.payoff_ratio),
      roi: moneyParam(city.roi),
      totalPnl: moneyParam(city.total_pnl),
      passed: city.passed === true,
      blockers: parseStringListParam(city.blockers),
    })),
    gates,
  }
}

function parseHighRewardPaperStatus(row: {
  run_at: string
  status: string
  policy_name: string
  active_cities: string[]
  summary: Record<string, unknown>
  cities: Record<string, unknown>[]
  blockers: string[]
}): ParsedHighRewardPaperStatus {
  const gateProgress = parseRecordParam(row.summary.gate_progress)
  const missingCoverage = parseStringListParam(row.summary.missing_coverage)
  const cityRecords = recordListParam(row.cities)
  const missingCoverageSamples = cityRecords.flatMap((city) => {
    const citySlug = stringParam(city.city_slug) ?? "unknown"
    if (!missingCoverage.includes(citySlug)) return []
    const diagnostics = parseRecordParam(city.current_candidate_diagnostics)
    const sample = recordListParam(diagnostics.samples)[0]
    if (sample == null) return []
    return [
      {
        citySlug,
        reason: stringParam(sample.reason),
        side: stringParam(sample.side),
        bucket: stringParam(sample.bucket),
        marketPrice: moneyParam(sample.market_price),
        variantMaxPrice: moneyParam(sample.variant_max_price),
        priceToVariantMax: numericParam(sample.price_to_variant_max) ?? undefined,
        probabilityDelta: numericParam(sample.probability_delta) ?? undefined,
        probabilityDeltaToMin: numericParam(sample.probability_delta_to_min) ?? undefined,
        hoursToClose: numericParam(sample.hours_to_close) ?? undefined,
      },
    ]
  })
  const pendingTargets = recordListParam(row.summary.pending_targets).map((target) => ({
    citySlug: stringParam(target.city_slug) ?? "unknown",
    side: stringParam(target.side),
    targetDate: stringParam(target.target_date),
    closed: target.closed === true,
    winner: typeof target.winner === "boolean" ? target.winner : undefined,
    signals: numericParam(target.signals) ?? undefined,
    entrySignals: numericParam(target.entry_signals) ?? undefined,
    pendingSignals: numericParam(target.pending_signals) ?? undefined,
    entryFills: numericParam(target.entry_fills) ?? undefined,
    settlementFills: numericParam(target.settlement_fills) ?? undefined,
  }))
  const nextActionRaw = parseRecordParam(row.summary.next_action)
  const nextAction =
    Object.keys(nextActionRaw).length === 0
      ? null
      : {
          code: stringParam(nextActionRaw.code) ?? "unknown",
          severity: stringParam(nextActionRaw.severity),
          detail: stringParam(nextActionRaw.detail),
        }
  return {
    runAt: row.run_at,
    status: row.status,
    policyName: row.policy_name,
    activeCities: row.active_cities,
    summary: {
      entryFills: numericParam(row.summary.entry_fills) ?? undefined,
      settlementFills: numericParam(row.summary.settlement_fills) ?? undefined,
      resolvedFills: numericParam(gateProgress.resolved_fills) ?? undefined,
      forwardDays: numericParam(gateProgress.forward_days_elapsed) ?? undefined,
      remainingForwardDays: numericParam(gateProgress.remaining_forward_days) ?? undefined,
      remainingResolvedFills:
        numericParam(gateProgress.remaining_resolved_fills) ?? undefined,
      sampleGate: gateProgress.sample_gate_passed === true,
      coverageGate: gateProgress.coverage_gate_passed === true,
      missingCoverage,
      paperPnl: moneyParam(row.summary.paper_pnl),
      resolvedPnl: moneyParam(row.summary.resolved_pnl),
      payoffRatio: moneyParam(row.summary.payoff_ratio),
    },
    cities: cityRecords.map((city) => ({
      citySlug: stringParam(city.city_slug) ?? "unknown",
      side: stringParam(city.side),
      signals: numericParam(city.signals) ?? undefined,
      entryFills: numericParam(city.entry_fills) ?? undefined,
      settlementFills: numericParam(city.settlement_fills) ?? undefined,
      rejectedOrders: numericParam(city.rejected_orders) ?? undefined,
      paperPnl: moneyParam(city.paper_pnl),
      resolvedPnl: moneyParam(city.resolved_pnl),
      payoffRatio: moneyParam(city.payoff_ratio),
      maxLossStreak: numericParam(city.max_loss_streak) ?? undefined,
      avgSlippage: moneyParam(city.avg_slippage),
    })),
    missingCoverageSamples,
    pendingTargets,
    nextAction,
    blockers: row.blockers,
  }
}

function parseStrategyShadow(rows: unknown[]): ParsedStrategyShadow {
  const decisions = rows.map((row) => {
    const record = isRecord(row) ? row : {}
    return {
      id: numericParam(record.id) ?? 0,
      ts: stringParam(record.ts) ?? "",
      policyName: stringParam(record.policy_name) ?? "-",
      citySlug: stringParam(record.city_slug) ?? "-",
      side: parseShadowSide(stringParam(record.segment_key)),
      marketPrice: moneyParam(record.market_price),
      calibratedProb: numericParam(record.calibrated_prob) ?? undefined,
      edgeNet: moneyParam(record.edge_net),
      reason: stringParam(record.reason),
      wouldTrade: record.would_trade === true,
    }
  })
  return {
    decisions,
    policyNames: Array.from(new Set(decisions.map((decision) => decision.policyName))),
    wouldTrade: decisions.filter((decision) => decision.wouldTrade).length,
  }
}

function parseShadowSide(segmentKey: string | undefined): string | undefined {
  if (segmentKey == null) return undefined
  const parts = segmentKey.split("|")
  if (parts[0] === "high_reward" && parts.length >= 5) return parts[3]
  return undefined
}

function parseStrategyExperiment(row: StrategyExperimentRunLike): ParsedStrategyExperiment {
  const summary = parseRecord(row.summary_json)
  const bestVariant = parseRecord(row.best_variant_json)
  const modelValidation = parseRecordParam(bestVariant.model_validation)
  const profiles = parseRecordParam(bestVariant.profiles)
  const maxEdgeRaw = parseRecordParam(profiles.max_edge)
  const gatesRaw = parseRecord(row.gates_json)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })

  return {
    summary: {
      bestVariant: stringParam(summary.best_variant),
      cannotApproveLive: summary.cannot_approve_live === true,
    },
    modelValidation: {
      brierDelta: numericParam(modelValidation.brier_delta) ?? undefined,
    },
    maxEdge: {
      profile: "max_edge",
      n_resolved_trades: numericParam(maxEdgeRaw.n_resolved_trades) ?? undefined,
      total_pnl: moneyParam(maxEdgeRaw.total_pnl),
      roi: moneyParam(maxEdgeRaw.roi),
      brier_delta: numericParam(maxEdgeRaw.brier_delta) ?? undefined,
      pnl_ci_low: moneyParam(maxEdgeRaw.pnl_ci_low),
      pnl_ci_high: moneyParam(maxEdgeRaw.pnl_ci_high),
      top_5_abs_pnl_share: moneyParam(maxEdgeRaw.top_5_abs_pnl_share),
    },
    blockedCounts: parseNumberRecord(bestVariant.blocked_counts),
    gates,
    shadowSample: recordListParam(bestVariant.shadow_sample).map((sample) => ({
      ts: stringParam(sample.ts),
      marketId: stringParam(sample.market_id),
      citySlug: stringParam(sample.city_slug),
      marketPrice: moneyParam(sample.market_price),
      rawProb: numericParam(sample.raw_prob) ?? undefined,
      calibratedProb: numericParam(sample.calibrated_prob) ?? undefined,
      edgeNet: moneyParam(sample.edge_net),
      reason: stringParam(sample.reason),
      wouldTrade: sample.would_trade === true,
    })),
  }
}

function parseStrategyRepairVariant(value: Record<string, unknown>): StrategyRepairVariant {
  const profiles = isRecord(value.profiles) ? value.profiles : {}
  const maxEdgeRaw = isRecord(profiles.max_edge) ? profiles.max_edge : {}
  return {
    name: stringParam(value.name) ?? "unknown",
    policyVersion: stringParam(value.policy_version),
    calibrate: value.calibrate === true,
    applySegmentFilters: value.apply_segment_filters === true,
    segmentScope: stringParam(value.segment_scope),
    validationSplit: stringParam(value.validation_split),
    alpha: numericParam(value.alpha) ?? undefined,
    probabilityCap: numericParam(value.probability_cap) ?? undefined,
    priceFloor: moneyParam(value.price_floor),
    eligibleSegments: numericParam(value.eligible_segments) ?? undefined,
    finalEligibleSegments: numericParam(value.final_eligible_segments) ?? undefined,
    walkForwardTradedSegments:
      numericParam(value.walk_forward_traded_segments) ?? undefined,
    tradedSegments: numericParam(value.traded_segments) ?? undefined,
    totalSegments: numericParam(value.total_segments) ?? undefined,
    maxEdge: {
      profile: "max_edge",
      n_resolved_trades: numericParam(maxEdgeRaw.n_resolved_trades) ?? undefined,
      total_pnl: moneyParam(maxEdgeRaw.total_pnl),
      roi: moneyParam(maxEdgeRaw.roi),
      brier_delta: numericParam(maxEdgeRaw.brier_delta) ?? undefined,
      pnl_ci_low: moneyParam(maxEdgeRaw.pnl_ci_low),
      pnl_ci_high: moneyParam(maxEdgeRaw.pnl_ci_high),
      top_5_abs_pnl_share: moneyParam(maxEdgeRaw.top_5_abs_pnl_share),
    },
    blockedCounts: parseNumberRecord(value.blocked_counts),
  }
}

function parseRepairFold(value: Record<string, unknown>): RepairFold {
  return {
    index: numericParam(value.index) ?? undefined,
    trainWindow: parseWindowRange(value.train_window),
    foldWindow: parseWindowRange(value.fold_window),
    nTrain: numericParam(value.n_train) ?? undefined,
    nFoldCandidates: numericParam(value.n_fold_candidates) ?? undefined,
    valid: value.valid === true,
    reason: stringParam(value.reason),
    nOosTrades: numericParam(value.n_oos_trades) ?? undefined,
    pnl: moneyParam(value.pnl),
    brierDelta: numericParam(value.brier_delta) ?? undefined,
  }
}

function parseEvidence(row: EvidenceRunLike): ParsedEvidence {
  const dataHealth = parseRecord(row.data_health_json)
  const modelHealth = parseRecord(row.model_health_json)
  const trading = parseRecord(row.trading_json)
  const gatesRaw = parseRecord(row.gates_json)
  const coverage = isRecord(dataHealth.coverage_by_city) ? dataHealth.coverage_by_city : {}
  const qualityRows = Array.isArray(modelHealth.city_quality) ? modelHealth.city_quality : []
  const qualityByCity = new Map<string, Record<string, unknown>>()
  for (const item of qualityRows) {
    if (!isRecord(item)) continue
    const city = stringParam(item.city_slug)
    if (city != null) qualityByCity.set(city, item)
  }

  const cities = parseStringList(row.cities_json)
  const gates = Object.entries(gatesRaw).map(([key, value]) => {
    const gate = isRecord(value) ? value : {}
    return {
      key,
      passed: gate.passed === true,
      value: gate.value,
      required: gate.required,
    }
  })

  const profileRecords = isRecord(trading.profiles) ? trading.profiles : {}
  const profiles = Object.entries(profileRecords).flatMap(([profile, value]) => {
    if (!isRecord(value)) return []
    return [
      {
        profile,
        source: stringParam(value.source),
        n_resolved_trades: numericParam(value.n_resolved_trades) ?? undefined,
        total_pnl: moneyParam(value.total_pnl),
        roi: moneyParam(value.roi),
        brier_delta: numericParam(value.brier_delta) ?? undefined,
        max_loss_streak: numericParam(value.max_loss_streak) ?? undefined,
        avg_edge_net: moneyParam(value.avg_edge_net),
      },
    ]
  })

  const cityRows = cities.map((city) => {
    const cityCoverage = isRecord(coverage[city]) ? coverage[city] : {}
    const cityQuality = qualityByCity.get(city) ?? {}
    return {
      city_slug: city,
      price_snapshots: numericParam(cityCoverage.price_snapshots) ?? undefined,
      ensemble_members: numericParam(cityCoverage.ensemble_members) ?? undefined,
      resolutions: numericParam(cityCoverage.resolutions) ?? undefined,
      reward_volatility_score: numericParam(cityQuality.reward_volatility_score) ?? undefined,
      needs_review: cityQuality.needs_review === true || cityQuality.missing_registry === true,
    }
  })

  return {
    cities,
    dataHealth: {
      forward_days: numericParam(dataHealth.forward_days) ?? undefined,
      price_snapshots: numericParam(dataHealth.price_snapshots) ?? undefined,
      book_snapshots: numericParam(dataHealth.book_snapshots) ?? undefined,
      ensemble_members: numericParam(dataHealth.ensemble_members) ?? undefined,
      resolved_markets: numericParam(dataHealth.resolved_markets) ?? undefined,
    },
    gates,
    profiles,
    cityRows,
  }
}

function parseBacktestParams(raw: string): BacktestParams {
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!isRecord(parsed)) return {}
    return {
      source: typeof parsed.source === "string" ? parsed.source : undefined,
      brier_model: numericParam(parsed.brier_model),
      brier_market: numericParam(parsed.brier_market),
    }
  } catch {
    return {}
  }
}

function parseRecord(raw: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(raw)
    return isRecord(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

function parseRecordParam(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {}
}

function parseStringList(raw: string): string[] {
  try {
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : []
  } catch {
    return []
  }
}

function parseStringListParam(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : []
}

function parseRecordList(raw: string): Record<string, unknown>[] {
  try {
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter(isRecord) : []
  } catch {
    return []
  }
}

function recordListParam(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

function parseNumberRecord(value: unknown): Record<string, number> {
  if (!isRecord(value)) return {}
  const parsed: Record<string, number> = {}
  for (const [key, item] of Object.entries(value)) {
    const numeric = numericParam(item)
    if (numeric != null) parsed[key] = numeric
  }
  return parsed
}

function parseReadinessChecks(value: Record<string, unknown>): EvidenceGate[] {
  return Object.entries(value).map(([key, raw]) => {
    const check = isRecord(raw) ? raw : {}
    return {
      key,
      passed: check.passed === true,
      value: check.value,
      required: check.required,
    }
  })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function parseStringArrayRecord(value: unknown): Record<string, string[]> {
  if (!isRecord(value)) return {}
  const parsed: Record<string, string[]> = {}
  for (const [key, item] of Object.entries(value)) {
    parsed[key] = parseStringListParam(item)
  }
  return parsed
}

function numericParam(value: unknown): number | null | undefined {
  if (value == null) return value
  if (typeof value === "number" && Number.isFinite(value)) return value
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : undefined
  }
  return undefined
}

function stringParam(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined
}

function moneyParam(value: unknown): string | undefined {
  if (typeof value === "string") return value
  if (typeof value === "number" && Number.isFinite(value)) return String(value)
  return undefined
}

function parseWindowRange(value: unknown): WindowRange | undefined {
  if (!isRecord(value)) return undefined
  return {
    start: stringParam(value.start),
    end: stringParam(value.end),
  }
}

function formatBrier(value: number | null | undefined): string {
  return value == null ? "-" : value.toFixed(4)
}

function formatSource(value: string | undefined): string {
  if (value === "stored_signals_resolved_markets") return "stored"
  if (value === "replay_price_snapshots") return "replay"
  if (value === "historical_price_points") return "historical"
  if (value === "data_api_trades") return "trades"
  if (value === "clob_prices_history") return "prices"
  return value ?? "-"
}

function formatInteger(value: number | null | undefined): string {
  return value == null ? "-" : Math.round(value).toLocaleString("en-US")
}

function formatNumber(value: number | null | undefined, digits: number): string {
  return value == null || !Number.isFinite(value) ? "-" : value.toFixed(digits)
}

function formatSignedNumber(value: number | null | undefined, digits: number): string {
  if (value == null) return "-"
  const sign = value > 0 ? "+" : ""
  return `${sign}${value.toFixed(digits)}`
}

function formatPercentString(value: string | null | undefined): string {
  if (value == null) return "-"
  const numeric = Number(value)
  return Number.isFinite(numeric) ? `${(numeric * 100).toFixed(1)}%` : "-"
}

function formatPercentNumber(value: number | null | undefined): string {
  if (value == null) return "-"
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "-"
}

function formatRatio(value: string | null | undefined): string {
  if (value == null) return "-"
  const numeric = Number(value)
  return Number.isFinite(numeric) ? `${numeric.toFixed(2)}x` : "-"
}

function formatDecimalString(value: string | null | undefined, digits: number): string {
  if (value == null) return "-"
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : "-"
}

function formatWindow(value: WindowRange | null | undefined): string {
  if (value == null || value.start == null || value.end == null) return "-"
  return `${formatDate(value.start)} - ${formatDate(value.end)}`
}

function formatPriceSourceCounts(value: Record<string, number>): string {
  const entries = Object.entries(value)
  if (entries.length === 0) return "-"
  return entries.map(([key, count]) => `${formatSource(key)} ${formatInteger(count)}`).join(", ")
}

function formatBlockedCounts(value: Record<string, number>): string {
  const entries = Object.entries(value)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
  if (entries.length === 0) return "-"
  return entries.map(([key, count]) => `${formatGateName(key)} ${formatInteger(count)}`).join(", ")
}

function formatRiskLimits(value: Record<string, string>): string {
  const stake = value.max_stake_per_order ?? "-"
  const exposure = value.max_exposure_per_market ?? "-"
  const loss = value.max_daily_loss ?? "-"
  return `Stake ${stake}, Exposure ${exposure}, Loss ${loss}`
}

function formatMoneyRange(low: string | null | undefined, high: string | null | undefined): string {
  if (low == null || high == null) return "-"
  return `${formatSignedMoney(low)} / ${formatSignedMoney(high)}`
}

function formatGateName(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
}

function formatUnknown(value: unknown): string {
  if (value == null) return "-"
  if (typeof value === "string") return value
  if (typeof value === "number" || typeof value === "boolean") return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return "-"
  }
}
