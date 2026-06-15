import { ArrowLeft } from "lucide-react"
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { EmptyState } from "@/components/EmptyState"
import { LoadingPanel } from "@/components/LoadingPanel"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useEventDetail } from "@/hooks/use-event-detail"
import { formatLocalTime, formatMoney } from "@/lib/format"

interface EventDetailPageProps {
  eventIdOrSlug: string | null
  onBack: () => void
}

export function EventDetailPage({ eventIdOrSlug, onBack }: EventDetailPageProps) {
  const detail = useEventDetail(eventIdOrSlug)

  if (eventIdOrSlug == null) {
    return <EmptyState title="No event selected" detail="Open an event from the markets view." />
  }
  if (detail.isLoading) {
    return <LoadingPanel />
  }
  if (!detail.data) {
    return <EmptyState title="Event unavailable" detail="The selected event was not found." />
  }

  const forecastData = detail.data.forecasts.map((point) => ({
    fetched_at: point.fetched_at,
    label: formatLocalTime(point.fetched_at),
    tmax: point.tmax_c,
    p10: point.p10,
    p50: point.p50,
    p90: point.p90,
  }))
  const priceData = detail.data.prices.map((point) => ({
    ts: point.ts,
    label: formatLocalTime(point.ts),
    labelName: point.label,
    mid: point.mid == null ? null : Number(point.mid),
  }))
  const observationData = detail.data.observations.map((point) => ({
    observed_at: point.observed_at,
    label: formatLocalTime(point.observed_at),
    temp_c: point.temp_c,
  }))

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <Button variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            Back
          </Button>
          <h2 className="mt-3 text-lg font-semibold">{detail.data.event.title}</h2>
          <div className="mt-2 flex flex-wrap gap-2">
            <Badge>{detail.data.event.city_slug}</Badge>
            <Badge tone="warning">{detail.data.event.target_date}</Badge>
          </div>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Forecast</CardTitle>
          </CardHeader>
          <CardContent className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={forecastData}>
                <CartesianGrid stroke="#e7e5e4" />
                <XAxis dataKey="label" hide />
                <YAxis width={42} />
                <Tooltip labelFormatter={(value) => String(value)} />
                <Legend />
                <Area type="monotone" dataKey="p90" stroke="#a16207" fill="#fde68a" dot={false} />
                <Area type="monotone" dataKey="p10" stroke="#0f766e" fill="#ccfbf1" dot={false} />
                <Line type="monotone" dataKey="p50" stroke="#0f766e" dot={false} />
                <Line type="monotone" dataKey="tmax" stroke="#334155" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Market Price</CardTitle>
          </CardHeader>
          <CardContent className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={priceData}>
                <CartesianGrid stroke="#e7e5e4" />
                <XAxis dataKey="label" hide />
                <YAxis width={42} domain={[0, 1]} tickFormatter={(v) => formatMoney(String(v), 2)} />
                <Tooltip formatter={(value) => formatMoney(String(value), 3)} />
                <Legend />
                <Line type="monotone" dataKey="mid" stroke="#7c3aed" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>METAR Observations</CardTitle>
        </CardHeader>
        <CardContent className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={observationData}>
              <CartesianGrid stroke="#e7e5e4" />
              <XAxis dataKey="label" hide />
              <YAxis width={42} />
              <Tooltip />
              <Line type="monotone" dataKey="temp_c" stroke="#b91c1c" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>
    </div>
  )
}
