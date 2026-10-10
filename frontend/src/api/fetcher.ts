// Used by the generated client: returns the parsed JSON body and throws on HTTP errors, so TanStack Query's
// error state works as expected.
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export const customFetch = async <T>(url: string, options?: RequestInit): Promise<T> => {
  const res = await fetch(url, options)
  const body = [204, 205, 304].includes(res.status) ? null : await res.text()
  if (!res.ok) {
    let detail = body ?? res.statusText
    try {
      detail = JSON.parse(body ?? '').detail ?? detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, String(detail))
  }
  return (body ? JSON.parse(body) : null) as T
}
