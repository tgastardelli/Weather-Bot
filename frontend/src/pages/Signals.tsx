import { EmptyState } from "@/components/EmptyState"
import { LoadingPanel } from "@/components/LoadingPanel"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table"
import { useSignals } from "@/hooks/use-signals"
import { formatLocalTime, formatMoney, formatProbability } from "@/lib/format"
import { cn } from "@/lib/utils"

export function SignalsPage() {
  const signals = useSignals()
  const rows = signals.data ?? []

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">Signals</h2>
        <p className="text-sm text-stone-600">Strategy engine output</p>
      </div>

      {signals.isLoading ? <LoadingPanel /> : null}
      {!signals.isLoading && rows.length === 0 ? (
        <EmptyState title="No signals" detail="Collect markets and forecasts, then run the scan." />
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Signal Log</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <THead>
              <TR>
                <TH>Time</TH>
                <TH>City</TH>
                <TH>Bucket</TH>
                <TH>Profile</TH>
                <TH>Prob</TH>
                <TH>Price</TH>
                <TH>Edge</TH>
                <TH>Stake</TH>
                <TH>Status</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((row) => {
                const edge = Number(row.edge_net)
                return (
                  <TR key={row.id}>
                    <TD>{formatLocalTime(row.ts)}</TD>
                    <TD>{row.city_slug}</TD>
                    <TD className="font-medium">{row.bucket_label}</TD>
                    <TD>{row.profile}</TD>
                    <TD>{formatProbability(row.model_prob)}</TD>
                    <TD>{formatMoney(row.market_price, 3)}</TD>
                    <TD className={cn(edge > 0 && "text-emerald-700", edge < 0 && "text-rose-700")}>
                      {formatMoney(row.edge_net, 3)}
                    </TD>
                    <TD>{formatMoney(row.stake)}</TD>
                    <TD>
                      <Badge tone={row.status === "PROPOSED" ? "success" : "warning"}>
                        {row.status}
                      </Badge>
                    </TD>
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
