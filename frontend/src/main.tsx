import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Toaster, toast } from "sonner"

import App from "@/App"
import { ApiError } from "@/lib/api"
import "@/index.css"

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error) => {
      const message = error instanceof ApiError ? error.message : "Unable to load data"
      toast.error(message)
    },
  }),
  defaultOptions: {
    queries: { staleTime: 5_000, retry: 2 },
  },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <Toaster richColors position="top-right" />
    </QueryClientProvider>
  </StrictMode>,
)
