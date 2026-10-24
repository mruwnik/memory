// FastAPI puts the reason in `detail`: a string, or a list of validation errors for a 422.
const serverDetail = (body: unknown): string => {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(d => d?.msg ?? String(d)).join('; ')
  return ''
}

/** An Error for a failed response that keeps the server's reason, so users can tell "not configured" from "denied" from "down". */
export const apiError = async (response: Response, fallback: string): Promise<Error> => {
  const detail = serverDetail(await response.json().catch(() => null))
  return new Error(detail || `${fallback} (${response.status})`)
}
