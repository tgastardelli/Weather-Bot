import { BarChart3, GitBranch, LineChart, Radio, Signal, TableProperties } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { AnalysisPage } from "@/pages/Analysis"
import { EventDetailPage } from "@/pages/EventDetail"
import { MarketsPage } from "@/pages/Markets"
import { SignalsPage } from "@/pages/Signals"
import { StrategyPage } from "@/pages/Strategy"

type Page = "markets" | "event" | "analysis" | "signals" | "strategy"

export default function App() {
  const [page, setPage] = useState<Page>("markets")
  const [selectedEvent, setSelectedEvent] = useState<string | null>(null)

  function openEvent(idOrSlug: string) {
    setSelectedEvent(idOrSlug)
    setPage("event")
  }

  return (
    <div className="min-h-screen bg-stone-50 text-stone-950">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Radio className="h-5 w-5 text-emerald-700" aria-hidden="true" />
              <h1 className="text-xl font-semibold">Weather Bot</h1>
            </div>
            <p className="mt-1 text-sm text-stone-600">Polymarket weather research console</p>
          </div>
          <nav className="flex flex-wrap gap-2">
            <Button
              variant={page === "markets" ? "primary" : "ghost"}
              size="sm"
              onClick={() => setPage("markets")}
            >
              <TableProperties className="h-4 w-4" aria-hidden="true" />
              Markets
            </Button>
            <Button
              variant={page === "event" ? "primary" : "ghost"}
              size="sm"
              onClick={() => setPage("event")}
              disabled={selectedEvent == null}
            >
              <LineChart className="h-4 w-4" aria-hidden="true" />
              Event
            </Button>
            <Button
              variant={page === "analysis" ? "primary" : "ghost"}
              size="sm"
              onClick={() => setPage("analysis")}
            >
              <BarChart3 className="h-4 w-4" aria-hidden="true" />
              Analysis
            </Button>
            <Button
              variant={page === "signals" ? "primary" : "ghost"}
              size="sm"
              onClick={() => setPage("signals")}
            >
              <Signal className="h-4 w-4" aria-hidden="true" />
              Signals
            </Button>
            <Button
              variant={page === "strategy" ? "primary" : "ghost"}
              size="sm"
              onClick={() => setPage("strategy")}
            >
              <GitBranch className="h-4 w-4" aria-hidden="true" />
              Estratégia
            </Button>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6">
        {page === "markets" && <MarketsPage onOpenEvent={openEvent} />}
        {page === "event" && (
          <EventDetailPage eventIdOrSlug={selectedEvent} onBack={() => setPage("markets")} />
        )}
        {page === "analysis" && <AnalysisPage />}
        {page === "signals" && <SignalsPage />}
        {page === "strategy" && <StrategyPage />}
      </main>
    </div>
  )
}
