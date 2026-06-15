import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import type { Event } from "@/types/api"

export const marketKeys = {
  all: ["markets"] as const,
  list: (city: string | null) => ["markets", city ?? "all"] as const,
}

export function useMarkets(city: string | null) {
  return useQuery({
    queryKey: marketKeys.list(city),
    queryFn: () =>
      api<Event[]>(city ? `/markets?city=${encodeURIComponent(city)}` : "/markets"),
    refetchInterval: 10_000,
  })
}
