import { useCallback } from 'react'
import { useAuth } from './useAuth'
import { apiError } from './apiError'

export interface AuditEvent {
  id: number
  timestamp: string
  actor_id: number | null
  /** Kept after the actor's user is deleted. */
  actor_email: string | null
  action: string
  target: string | null
  attributes: Record<string, unknown>
}

export interface AuditResponse {
  total: number
  offset: number
  limit: number
  events: AuditEvent[]
}

export const useAudit = () => {
  const { apiCall } = useAuth()

  const listAuditEvents = useCallback(async (
    options: { action?: string; actorId?: number; limit?: number; offset?: number } = {}
  ): Promise<AuditResponse> => {
    const params = new URLSearchParams()
    if (options.action) params.set('action', options.action)
    if (options.actorId !== undefined) params.set('actor_id', options.actorId.toString())
    if (options.limit) params.set('limit', options.limit.toString())
    if (options.offset) params.set('offset', options.offset.toString())

    const response = await apiCall(`/audit?${params}`)
    if (!response.ok) throw await apiError(response, 'Failed to fetch audit events')
    return response.json()
  }, [apiCall])

  return { listAuditEvents }
}
