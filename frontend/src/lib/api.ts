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
const apiTimeoutMs = 15_000

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), apiTimeoutMs)
  const callerSignal = init?.signal

  if (callerSignal != null) {
    if (callerSignal.aborted) {
      controller.abort()
    } else {
      callerSignal.addEventListener("abort", () => controller.abort(), { once: true })
    }
  }

  try {
    const res = await fetch(`${apiBase}/api${path}`, {
      ...init,
      signal: controller.signal,
    })
    if (!res.ok) {
      const text = await res.text()
      throw new ApiError(res.status, text || `Request failed with status ${res.status}`)
    }
    return res.json() as Promise<T>
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(408, "Request timed out while waiting for the backend API.")
    }
    throw error
  } finally {
    window.clearTimeout(timeoutId)
  }
}
