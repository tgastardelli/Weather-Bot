import { CircleDashed } from "lucide-react"

interface EmptyStateProps {
  title: string
  detail: string
}

export function EmptyState({ title, detail }: EmptyStateProps) {
  return (
    <div className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-stone-300 bg-stone-50 p-6 text-center">
      <CircleDashed className="h-5 w-5 text-stone-500" aria-hidden="true" />
      <p className="text-sm font-medium text-stone-900">{title}</p>
      <p className="max-w-md text-sm text-stone-600">{detail}</p>
    </div>
  )
}
