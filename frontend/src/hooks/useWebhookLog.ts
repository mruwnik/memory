import { useCallback } from 'react'
import { useAuth } from './useAuth'
import { apiError } from './apiError'

export type WebhookSource = 'github' | 'slack' | 'discord' | 'transcripts'
export type WebhookOutcome = 'stored' | 'not_collected' | 'no_item_type' | 'rejected'

export interface WebhookAccount {
  source: WebhookSource
  account_id: number
  name: string
  last_event_id: number | null
  last_event_at: string | null
}

export interface TriggeredDelivery {
  subscription_id: number
  subscription: string
  item_id: number
  status: string
  run_id: string | null
}

export interface WebhookEvent {
  id: number
  created_at: string
  outcome: WebhookOutcome
  // Null for a delivery from an untracked channel, which shows only that it arrived.
  type: string | null
  container: string | null
  item_ids: number[]
  triggered: TriggeredDelivery[]
}

export const useWebhookLog = () => {
  const { apiCall } = useAuth()

  const listAccounts = useCallback(async (): Promise<WebhookAccount[]> => {
    const response = await apiCall('/webhook-events')
    if (!response.ok) throw await apiError(response, 'Failed to load webhook accounts')
    return response.json()
  }, [apiCall])

  const listEvents = useCallback(
    async (source: WebhookSource, accountId: number, before?: number, limit = 50): Promise<WebhookEvent[]> => {
      const params = new URLSearchParams({ limit: String(limit) })
      if (before !== undefined) params.set('before', String(before))
      const response = await apiCall(`/webhook-events/${source}/${accountId}?${params}`)
      if (!response.ok) throw await apiError(response, 'Failed to load the webhook log')
      return response.json()
    },
    [apiCall],
  )

  return { listAccounts, listEvents }
}
