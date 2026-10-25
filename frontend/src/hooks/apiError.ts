interface ValidationError { loc?: (string | number)[]; msg?: string }

/** A failed request's reason, plus the reasons that name a request field, keyed by the field's name. */
export class ApiError extends Error {
  constructor(message: string, readonly fields: Record<string, string> = Object.create(null)) {
    super(message)
  }
}

// FastAPI puts the reason in `detail`: a string, or a list of validation errors for a 422
// (and for the 400s that point at one field).
const serverDetail = (body: unknown): { message: string; fields: Record<string, string> } => {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return { message: detail, fields: Object.create(null) }
  if (!Array.isArray(detail)) return { message: '', fields: Object.create(null) }
  const errors = detail as ValidationError[]
  // No prototype, so a field named like an Object method can't collide.
  const fields: Record<string, string> = Object.create(null)
  for (const { loc, msg } of errors) {
    // loc[0] is where the value came from (body, query, path); the rest is its path within it.
    const field = loc?.slice(1).join('.')
    if (!field || !msg) continue
    fields[field] = fields[field] ? `${fields[field]}; ${msg}` : msg
  }
  return { message: errors.map(d => d?.msg ?? String(d)).join('; '), fields }
}

/** An Error for a failed response that keeps the server's reason, so users can tell "not configured" from "denied" from "down". */
export const apiError = async (response: Response, fallback: string): Promise<ApiError> => {
  const { message, fields } = serverDetail(await response.json().catch(() => null))
  return new ApiError(message || `${fallback} (${response.status})`, fields)
}
