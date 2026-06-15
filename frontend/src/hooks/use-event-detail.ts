import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import type { EventDetail } from "@/types/api"

export const eventKeys = {
  detail: (idOrSlug: string) => ["events", idOrSlug] as const,
}

export function useEventDetail(idOrSlug: string | null) {
  return useQuery({
    queryKey: eventKeys.detail(idOrSlug ?? ""),
    queryFn: () => api<EventDetail>(`/events/${encodeURIComponent(idOrSlug ?? "")}`),
    enabled: idOrSlug != null && idOrSlug.length > 0,
    refetchInterval: 10_000,
  })
}
