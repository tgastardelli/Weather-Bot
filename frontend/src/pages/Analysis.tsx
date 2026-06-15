import { EmptyState } from "@/components/EmptyState"
import { LoadingPanel } from "@/components/LoadingPanel"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table"
import {
  useBacktests,
  useCalibration,
  useEvidence,
  useHistoryBackfill,
  useHistoricalValidation,
  useLiveReadiness,
  useMeasurement,
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
  const evidence = useEvidence()
  const measurement = useMeasurement()
  const historicalValidation = useHistoricalValidation()
  const historyBackfill = useHistoryBackfill()
  const liveReadiness = useLiveReadiness()
  const latestEvidence = evidence.data?.latest ?? null
  const evidenceData = latestEvidence == null ? null : parseEvidence(latestEvidence)
  const latestMeasurement = measurement.data?.latest ?? null
  const measurementData =
    latestMeasurement == null ? null : parseMeasurement(latestMeasurement)
  const latestHistorical = historicalValidation.data?.latest ?? null
  const historicalData =
    latestHistorical == null ? null : parseHistoricalValidation(latestHistorical)
  const latestHistoryBackfill = historyBackfill.data?.latest ?? null
  const liveReadinessData = liveReadiness.data ?? null
  const loading =
    calibration.isLoading ||
    backtests.isLoading ||
    evidence.isLoading ||
    measurement.isLoading ||
    historicalValidation.isLoading ||
    historyBackfill.isLoading ||
    liveReadiness.isLoading
  const hasData =
    (calibration.data?.length ?? 0) > 0 ||
    (backtests.data?.length ?? 0) > 0 ||
    latestEvidence != null ||
    latestMeasurement != null ||
    latestHistorical != null ||
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
  priceSourceCounts: Record<string, number>
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
    priceSourceCounts: parseNumberRecord(trading.price_source_counts),
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

function formatDecimalString(value: string | null | undefined, digits: number): string {
  if (value == null) return "-"
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : "-"
}

function formatPriceSourceCounts(value: Record<string, number>): string {
  const entries = Object.entries(value)
  if (entries.length === 0) return "-"
  return entries.map(([key, count]) => `${formatSource(key)} ${formatInteger(count)}`).join(", ")
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
