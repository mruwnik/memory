import { useCallback } from 'react'
import { useAuth } from './useAuth'
import { apiError } from './apiError'

export type WebhookSource = 'github' | 'slack' | 'discord' | 'transcripts'
export type WebhookOutcome = 'stored' | 'not_collected' | 'no_item_type' | 'rejected'
export const WEBHOOK_SOURCE_NAMES: Record<WebhookSource, string> = {
  github: 'GitHub', slack: 'Slack', discord: 'Discord', transcripts: 'Transcripts',
}

export interface WebhookAccount {
  source: WebhookSource
  account_id: number
  name: string
  last_event_id: number | null
  last_event_at: string | null
  // The caller's own; an admin also sees everyone else's.
  own: boolean
}

export interface TriggeredDelivery {
  subscription_id: number
  subscription: string
  // Null for a webhook subscription's delivery of the event itself.
  item_id: number | null
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
  // The repo's full name or the channel's name, where the viewer may see it.
  container_name: string | null
  // Why the delivery's repo fails to sync, on a delivery that stored nothing since it began.
  container_error: string | null
  // Whether its ingest links the items it writes to it.
  links_items: boolean
  item_ids: number[]
  triggered: TriggeredDelivery[]
  // Only sent on request; null once expired, and for deliveries that keep none.
  payload: unknown
  payload_withheld: boolean
}

export interface WebhookEventQuery {
  before?: number
  limit?: number
  outcome?: WebhookOutcome
  // A prefix: "pull_request" finds every pull_request action.
  type?: string
  payload?: boolean
}

export const useWebhookLog = () => {
  const { apiCall } = useAuth()

  const listAccounts = useCallback(async (): Promise<WebhookAccount[]> => {
    const response = await apiCall('/webhook-events')
    if (!response.ok) throw await apiError(response, 'Failed to load webhook accounts')
    return response.json()
  }, [apiCall])

  const listEvents = useCallback(
    async (source: WebhookSource, accountId: number, query: WebhookEventQuery = {}): Promise<WebhookEvent[]> => {
      const params = new URLSearchParams(
        Object.entries(query).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)]),
      )
      const response = await apiCall(`/webhook-events/${source}/${accountId}?${params}`)
      if (!response.ok) throw await apiError(response, 'Failed to load the webhook log')
      return response.json()
    },
    [apiCall],
  )

  // One delivery with its payload: the page of one starting at it.
  const getEvent = useCallback(
    async (source: WebhookSource, accountId: number, id: number): Promise<WebhookEvent | undefined> => {
      const [event] = await listEvents(source, accountId, { before: id + 1, limit: 1, payload: true })
      return event?.id === id ? event : undefined
    },
    [listEvents],
  )

  return { listAccounts, listEvents, getEvent }
}
