import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import type { City } from "@/types/api"

export const cityKeys = {
  all: ["cities"] as const,
}

export function useCities() {
  return useQuery({
    queryKey: cityKeys.all,
    queryFn: () => api<City[]>("/cities"),
    staleTime: 60_000,
  })
}
