import type { LucideIcon } from "lucide-react"
import {
  BarChart3,
  Brain,
  CloudSun,
  Database,
  GitBranch,
  Percent,
  Rocket,
  ShieldCheck,
  Target,
  ThermometerSun,
  TrendingUp,
} from "lucide-react"

import { EmptyState } from "@/components/EmptyState"
import { LoadingPanel } from "@/components/LoadingPanel"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table"
import { useCityVolatility } from "@/hooks/use-analysis"
import { formatLocalTime, formatProbability } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { CityVolatilityMetric } from "@/types/api"

type StrategyStatus = "implemented" | "partial" | "planned"
type Accent = "emerald" | "sky" | "violet" | "amber" | "rose" | "indigo" | "slate"

interface StrategyNode {
  title: string
  group: string
  description: string
  status: StrategyStatus
  icon: LucideIcon
  accent: Accent
  bullets: string[]
}

const statusMeta: Record<
  StrategyStatus,
  { label: string; tone: "success" | "warning" | "neutral" }
> = {
  implemented: { label: "Implementado", tone: "success" },
  partial: { label: "Parcial", tone: "warning" },
  planned: { label: "Planejado", tone: "neutral" },
}

const accentClasses: Record<Accent, string> = {
  emerald: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  sky: "bg-sky-50 text-sky-700 ring-sky-200",
  violet: "bg-violet-50 text-violet-700 ring-violet-200",
  amber: "bg-amber-50 text-amber-700 ring-amber-200",
  rose: "bg-rose-50 text-rose-700 ring-rose-200",
  indigo: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  slate: "bg-slate-50 text-slate-700 ring-slate-200",
}

const strategyNodes: StrategyNode[] = [
  {
    title: "Dados",
    group: "Mercado e clima",
    description: "Mercados, books, previsões, observações e resoluções alimentam o SQLite.",
    status: "partial",
    icon: Database,
    accent: "sky",
    bullets: [
      "Markets/books implementados",
      "Histórico de clima em construção",
      "Estações ainda precisam revisão",
    ],
  },
  {
    title: "Probabilidade",
    group: "Previsão para bucket",
    description: "Membros do ensemble viram probabilidades calibradas para cada bucket.",
    status: "partial",
    icon: Brain,
    accent: "violet",
    bullets: [
      "Contagem por ensemble existe",
      "Ajustes de viés/spread existem",
      "Calibração ainda precisa dados",
    ],
  },
  {
    title: "Edge de mercado",
    group: "Modelo vs preço",
    description: "O bot compara a probabilidade do modelo com o melhor ask após a fee de clima.",
    status: "implemented",
    icon: Percent,
    accent: "emerald",
    bullets: ["Edge bruto e líquido", "Fee taker de 5% incluída", "Backend preserva Decimal"],
  },
  {
    title: "Perfis",
    group: "Estilos de sinal",
    description: "Dois perfis paper varrem os mesmos mercados com formatos diferentes de oportunidade.",
    status: "implemented",
    icon: Target,
    accent: "amber",
    bullets: ["max_edge para EV amplo", "longshot para caudas baratas", "Sem ordens reais"],
  },
  {
    title: "Risco",
    group: "Sizing e limites",
    description: "Kelly fracionário é limitado por stake, bankroll e exposição configurados.",
    status: "partial",
    icon: ShieldCheck,
    accent: "rose",
    bullets: ["Kelly sizing implementado", "Apenas sinais paper", "Ledger de execução fica para depois"],
  },
  {
    title: "Backtest",
    group: "Ciclo de evidência",
    description: "Sinais, snapshots locais e preços históricos podem comparar modelo contra mercado.",
    status: "partial",
    icon: BarChart3,
    accent: "indigo",
    bullets: [
      "Backtest de sinais existe",
      "Validação histórica por prices-history",
      "Slippage depende de book forward",
    ],
  },
  {
    title: "Roadmap",
    group: "Próximos upgrades",
    description: "A estratégia deve evoluir com calibração, replay, slippage e filtros de confirmação.",
    status: "planned",
    icon: Rocket,
    accent: "slate",
    bullets: [
      "Backfill de previsões",
      "Replay de sinais históricos",
      "Slippage por profundidade do book",
    ],
  },
]

const qualityLabels: Record<string, string> = {
  low_samples: "poucas amostras",
  missing_station: "sem estação",
  needs_review: "revisar estação",
  no_forecast_pairs: "sem pares forecast",
  no_intraday: "sem intradiário",
  ok: "ok",
}

export function StrategyPage() {
  const cityVolatility = useCityVolatility()
  const ranking = cityVolatility.data ?? []

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold">Estratégia</h2>
          <p className="text-sm text-stone-600">
            Tese alto risco / alta recompensa baseada em surpresa meteorológica por cidade
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge tone="success">Somente paper</Badge>
          <Badge tone="warning">Fee de 5% incluída</Badge>
          <Badge>STRATEGY.md</Badge>
        </div>
      </div>

      <section className="border-y border-stone-200 py-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h3 className="text-sm font-semibold text-stone-950">Tese central</h3>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-stone-600">
              Priorizar cidades onde a máxima realizada historicamente foge mais da previsão,
              então cruzar essa instabilidade com preço, fee, liquidez e perfil de sinal.
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-md border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-700">
            <GitBranch className="h-4 w-4 text-stone-500" aria-hidden="true" />
            Surpresa histórica para edge para sinal
          </div>
        </div>
      </section>

      <CityVolatilityRanking
        ranking={ranking}
        isLoading={cityVolatility.isLoading}
        isError={cityVolatility.isError}
      />

      <section className="relative">
        <div className="absolute bottom-0 left-5 top-0 border-l border-dashed border-stone-300 lg:hidden" />
        <div className="absolute left-12 right-12 top-9 hidden border-t border-dashed border-stone-300 lg:block" />
        <div className="relative grid gap-4 lg:grid-cols-7">
          {strategyNodes.map((node) => (
            <StrategyNodeCard key={node.title} node={node} />
          ))}
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        <StrategyPrinciple
          title="Regra de decisão"
          detail="Sinais existem apenas quando probabilidade, preço, fee, janela de tempo e limites de risco concordam."
        />
        <StrategyPrinciple
          title="Fonte de verdade"
          detail="STRATEGY.md guarda a narrativa da estratégia; o dashboard apenas espelha isso como mapa visual."
        />
        <StrategyPrinciple
          title="Próxima evidência"
          detail="O próximo salto útil é replay histórico com calibração e slippage conservador."
        />
      </section>
    </div>
  )
}

interface CityVolatilityRankingProps {
  ranking: CityVolatilityMetric[]
  isLoading: boolean
  isError: boolean
}

function CityVolatilityRanking({ ranking, isLoading, isError }: CityVolatilityRankingProps) {
  const bestCity = ranking[0]
  const maeLeader = maxBy(ranking, (row) => row.forecast_mae_c)
  const tailLeader = maxBy(ranking, (row) => row.tail_miss_rate_3c)

  return (
    <section className="space-y-4">
      <div>
        <h3 className="text-base font-semibold text-stone-950">
          Ranking Alto Risco / Alta Recompensa
        </h3>
        <p className="mt-1 text-sm text-stone-600">
          Último ranking salvo pelo comando de análise, ordenado por score de surpresa.
        </p>
      </div>

      {isLoading ? <LoadingPanel /> : null}
      {isError ? (
        <EmptyState
          title="Erro ao carregar ranking"
          detail="Verifique se a API FastAPI está rodando e tente atualizar a página."
        />
      ) : null}
      {!isLoading && !isError && ranking.length === 0 ? (
        <EmptyState
          title="Ranking ainda não gerado"
          detail="Run: cd backend; uv run python -m analysis.city_volatility --days 730 --json"
        />
      ) : null}

      {ranking.length > 0 ? (
        <>
          <div className="grid gap-4 md:grid-cols-3">
            <RankingSummaryCard
              icon={TrendingUp}
              title="Maior score"
              city={bestCity?.city_slug}
              value={bestCity == null ? "—" : bestCity.reward_volatility_score.toFixed(1)}
              detail={
                bestCity == null
                  ? "Sem ranking salvo"
                  : `${bestCity.n_samples} amostras · ${formatLocalTime(bestCity.computed_at)}`
              }
            />
            <RankingSummaryCard
              icon={ThermometerSun}
              title="Maior erro médio"
              city={maeLeader?.city_slug}
              value={maeLeader == null ? "—" : formatTemperature(maeLeader.forecast_mae_c)}
              detail="MAE da máxima diária"
            />
            <RankingSummaryCard
              icon={CloudSun}
              title="Maior tail miss 3 °C"
              city={tailLeader?.city_slug}
              value={
                tailLeader == null ? "—" : formatProbability(tailLeader.tail_miss_rate_3c)
              }
              detail="Frequência de erro grande"
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Cidades ranqueadas</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <THead>
                  <TR>
                    <TH>Cidade</TH>
                    <TH>Estação</TH>
                    <TH>Score</TH>
                    <TH>MAE</TH>
                    <TH>Tail 3 °C</TH>
                    <TH>P90 intradiário</TH>
                    <TH>Amostras</TH>
                    <TH>Qualidade</TH>
                  </TR>
                </THead>
                <TBody>
                  {ranking.map((row) => (
                    <TR key={`${row.computed_at}-${row.city_slug}`}>
                      <TD className="font-medium">{row.city_slug}</TD>
                      <TD>{row.station_code ?? "—"}</TD>
                      <TD>{row.reward_volatility_score.toFixed(1)}</TD>
                      <TD>{formatTemperature(row.forecast_mae_c)}</TD>
                      <TD>{formatProbability(row.tail_miss_rate_3c)}</TD>
                      <TD>{formatTemperature(row.p90_intraday_range_c)}</TD>
                      <TD>{row.n_samples}</TD>
                      <TD>
                        <div className="flex flex-wrap gap-1">
                          {qualityTokens(row.data_quality).map((quality) => (
                            <Badge key={quality} tone={qualityTone(quality)}>
                              {qualityLabels[quality] ?? quality}
                            </Badge>
                          ))}
                        </div>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </CardContent>
          </Card>
        </>
      ) : null}
    </section>
  )
}

interface RankingSummaryCardProps {
  icon: LucideIcon
  title: string
  city: string | undefined
  value: string
  detail: string
}

function RankingSummaryCard({ icon: Icon, title, city, value, detail }: RankingSummaryCardProps) {
  return (
    <Card>
      <CardContent className="flex h-full flex-col gap-3 p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200">
            <Icon className="h-5 w-5" aria-hidden="true" />
          </div>
          <Badge>{city ?? "—"}</Badge>
        </div>
        <div>
          <p className="text-xs font-medium uppercase text-stone-500">{title}</p>
          <p className="mt-1 text-2xl font-semibold text-stone-950">{value}</p>
          <p className="mt-1 text-sm text-stone-600">{detail}</p>
        </div>
      </CardContent>
    </Card>
  )
}

interface StrategyNodeCardProps {
  node: StrategyNode
}

function StrategyNodeCard({ node }: StrategyNodeCardProps) {
  const Icon = node.icon
  const status = statusMeta[node.status]

  return (
    <Card className="relative min-h-72 overflow-hidden">
      <CardContent className="flex h-full flex-col gap-4 p-4">
        <div className="flex items-start justify-between gap-3">
          <div
            className={cn(
              "flex h-10 w-10 shrink-0 items-center justify-center rounded-md ring-1",
              accentClasses[node.accent],
            )}
          >
            <Icon className="h-5 w-5" aria-hidden="true" />
          </div>
          <Badge tone={status.tone}>{status.label}</Badge>
        </div>

        <div>
          <p className="text-xs font-medium uppercase text-stone-500">{node.group}</p>
          <h3 className="mt-1 text-base font-semibold text-stone-950">{node.title}</h3>
          <p className="mt-2 text-sm leading-6 text-stone-600">{node.description}</p>
        </div>

        <ul className="mt-auto space-y-2 text-sm text-stone-700">
          {node.bullets.map((bullet) => (
            <li key={bullet} className="flex gap-2">
              <CloudSun className="mt-0.5 h-4 w-4 shrink-0 text-stone-400" aria-hidden="true" />
              <span>{bullet}</span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

interface StrategyPrincipleProps {
  title: string
  detail: string
}

function StrategyPrinciple({ title, detail }: StrategyPrincipleProps) {
  return (
    <div className="rounded-lg border border-stone-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-stone-950">{title}</h3>
      <p className="mt-2 text-sm leading-6 text-stone-600">{detail}</p>
    </div>
  )
}

function maxBy<T>(items: T[], score: (item: T) => number): T | undefined {
  return items.reduce<T | undefined>((best, item) => {
    if (best == null) return item
    return score(item) > score(best) ? item : best
  }, undefined)
}

function formatTemperature(value: number): string {
  return `${value.toFixed(2)} °C`
}

function qualityTokens(value: string): string[] {
  return value.split(",").map((token) => token.trim()).filter(Boolean)
}

function qualityTone(quality: string): "neutral" | "success" | "warning" | "danger" {
  if (quality === "ok") return "success"
  if (quality === "missing_station" || quality === "no_forecast_pairs") return "danger"
  if (quality === "low_samples" || quality === "needs_review" || quality === "no_intraday") {
    return "warning"
  }
  return "neutral"
}
