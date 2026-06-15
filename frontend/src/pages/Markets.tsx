import { ExternalLink, RefreshCw } from "lucide-react"

import { EmptyState } from "@/components/EmptyState"
import { LoadingPanel } from "@/components/LoadingPanel"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table"
import { useCities } from "@/hooks/use-cities"
import { useMarkets } from "@/hooks/use-markets"
import { formatDate, formatMoney, formatProbability } from "@/lib/format"
import { cn } from "@/lib/utils"

interface MarketsPageProps {
  onOpenEvent: (idOrSlug: string) => void
}

export function MarketsPage({ onOpenEvent }: MarketsPageProps) {
  const cities = useCities()
  const selectedCity = cities.data?.find((city) => city.active)?.slug ?? null
  const markets = useMarkets(selectedCity)
  const events = markets.data ?? []

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold">Markets</h2>
          <p className="text-sm text-stone-600">Active highest-temperature events by city</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void markets.refetch()}>
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          Refresh
        </Button>
      </div>

      {cities.isLoading || markets.isLoading ? <LoadingPanel /> : null}

      {!cities.isLoading && !markets.isLoading && events.length === 0 ? (
        <EmptyState
          title="No active events"
          detail={
            "Run: cd backend; uv run python -m app.collectors.run_once markets --json"
          }
        />
      ) : null}

      <div className="grid gap-4">
        {events.map((event) => (
          <Card key={event.id}>
            <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <CardTitle>{event.title}</CardTitle>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Badge>{event.city_slug}</Badge>
                  <Badge tone="warning">{formatDate(event.target_date)}</Badge>
                  {event.closed ? <Badge tone="danger">Closed</Badge> : <Badge tone="success">Active</Badge>}
                </div>
              </div>
              <Button variant="outline" size="sm" onClick={() => onOpenEvent(event.slug)}>
                <ExternalLink className="h-4 w-4" aria-hidden="true" />
                Open
              </Button>
            </CardHeader>
            <CardContent>
              <Table>
                <THead>
                  <TR>
                    <TH>Bucket</TH>
                    <TH>Bid</TH>
                    <TH>Ask</TH>
                    <TH>Model</TH>
                    <TH>Net Edge</TH>
                    <TH>Status</TH>
                  </TR>
                </THead>
                <TBody>
                  {event.buckets.map((bucket) => {
                    const edgeNumber = bucket.edge_net == null ? null : Number(bucket.edge_net)
                    return (
                      <TR key={bucket.market_id}>
                        <TD className="font-medium">{bucket.label}</TD>
                        <TD>{formatMoney(bucket.best_bid, 3)}</TD>
                        <TD>{formatMoney(bucket.best_ask, 3)}</TD>
                        <TD>{formatProbability(bucket.model_prob)}</TD>
                        <TD
                          className={cn(
                            edgeNumber != null && edgeNumber > 0 && "text-emerald-700",
                            edgeNumber != null && edgeNumber < 0 && "text-rose-700",
                          )}
                        >
                          {formatMoney(bucket.edge_net, 3)}
                        </TD>
                        <TD>
                          {bucket.winner == null ? (
                            <Badge>Open</Badge>
                          ) : bucket.winner ? (
                            <Badge tone="success">Winner</Badge>
                          ) : (
                            <Badge tone="danger">Lost</Badge>
                          )}
                        </TD>
                      </TR>
                    )
                  })}
                </TBody>
              </Table>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
