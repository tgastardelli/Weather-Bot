export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

const apiBase = import.meta.env.VITE_API_URL ?? ""

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase}/api${path}`, init)
  if (!res.ok) {
    const text = await res.text()
    throw new ApiError(res.status, text || `Request failed with status ${res.status}`)
  }
  return res.json() as Promise<T>
}
