export function formatMoney(value: string | null | undefined, digits: 2 | 3 = 2): string {
  if (value == null) return "—"
  return Number(value).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function formatProbability(value: string | number | null | undefined): string {
  if (value == null) return "—"
  return `${(Number(value) * 100).toFixed(1)}%`
}

export function formatSignedMoney(value: string | null | undefined): string {
  if (value == null) return "—"
  const numeric = Number(value)
  const sign = numeric > 0 ? "+" : ""
  return `${sign}${formatMoney(value)}`
}

export function formatLocalTime(isoUtc: string | null | undefined): string {
  if (!isoUtc) return "—"
  return new Date(isoUtc).toLocaleString()
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—"
  return value
}
