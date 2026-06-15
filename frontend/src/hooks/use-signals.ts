import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import type { Signal } from "@/types/api"

export const signalKeys = {
  all: ["signals"] as const,
}

export function useSignals() {
  return useQuery({
    queryKey: signalKeys.all,
    queryFn: () => api<Signal[]>("/signals"),
    refetchInterval: 10_000,
  })
}
