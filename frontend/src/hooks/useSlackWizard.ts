// Multi-tenant Slack-app wizard hook (slack-changes.md §3.4).
//
// Hits the per-app wizard endpoints. The flow is:
//
//   1. createApp({name, client_id})              POST /slack/apps
//   2. setClientSecret(app_id, secret)           POST /slack/apps/{id}/client-secret
//   3. issueWizardNonce(app_id)                  POST /slack/apps/{id}/wizard-nonce
//      → returns {nonce, callback_url, events_url} for the user to paste.
//   4. Open OAuth URL in popup; callback stores credentials.
//   5. setSigningSecret(app_id, secret)          POST /slack/apps/{id}/signing-secret
//   6. User pastes events_url + ticks events in Slack; Slack POSTs
//      url_verification, server advances setup_state to 'signing_verified'.
//   7. beginTestMessage(app_id, token)           POST /slack/apps/{id}/test-message
//      User posts the token in any channel; events handler matches and
//      flips setup_state to 'live'.
//
// Frontend polls /apps/{id}/wizard-status to know when async transitions
// (4, 6, 7) have completed.
import { useCallback } from 'react'
import { useAuth } from './useAuth'

export type SlackAppSetupState =
  | 'draft'
  | 'signing_verified'
  | 'live'
  | 'degraded'

export interface SlackAppResponse {
  id: number
  client_id: string
  name: string
  setup_state: SlackAppSetupState
  is_active: boolean
  is_owner: boolean
  is_authorized: boolean
  client_secret_configured: boolean
  signing_secret_configured: boolean
  created_by_user_id: number | null
  authorized_user_ids: number[]
  created_at: string | null
  updated_at: string | null
}

export interface SlackAppCreate {
  name: string
  client_id: string
}

export interface SlackAppNonceResponse {
  nonce: string
  callback_url: string
  events_url: string
}

export interface SlackWizardStatus {
  setup_state: SlackAppSetupState
  has_credentials: boolean
  test_message_pending: boolean
}

export interface SlackTestMessageStatus {
  status: 'waiting' | 'matched' | 'expired'
}

const apiOk = async (response: Response, fallback: string) => {
  if (response.ok) return response.json()
  let detail = fallback
  try {
    const body = await response.json()
    if (body?.detail) detail = body.detail
  } catch {
    /* ignore — non-JSON body */
  }
  throw new Error(detail)
}

export const useSlackWizard = () => {
  const { apiCall } = useAuth()

  const listApps = useCallback(async (): Promise<SlackAppResponse[]> => {
    const r = await apiCall('/slack/apps')
    return apiOk(r, 'Failed to list Slack apps')
  }, [apiCall])

  const getApp = useCallback(
    async (appId: number): Promise<SlackAppResponse> => {
      const r = await apiCall(`/slack/apps/${appId}`)
      return apiOk(r, 'Failed to fetch Slack app')
    },
    [apiCall],
  )

  const createApp = useCallback(
    async (data: SlackAppCreate): Promise<SlackAppResponse> => {
      const r = await apiCall('/slack/apps', {
        method: 'POST',
        body: JSON.stringify(data),
      })
      return apiOk(r, 'Failed to create Slack app')
    },
    [apiCall],
  )

  const deleteApp = useCallback(
    async (appId: number): Promise<void> => {
      const r = await apiCall(`/slack/apps/${appId}`, { method: 'DELETE' })
      if (!r.ok) await apiOk(r, 'Failed to delete Slack app')
    },
    [apiCall],
  )

  const setClientSecret = useCallback(
    async (appId: number, secret: string): Promise<SlackAppResponse> => {
      const r = await apiCall(`/slack/apps/${appId}/client-secret`, {
        method: 'POST',
        body: JSON.stringify({ secret }),
      })
      return apiOk(r, 'Failed to set client secret')
    },
    [apiCall],
  )

  const setSigningSecret = useCallback(
    async (appId: number, secret: string): Promise<SlackAppResponse> => {
      const r = await apiCall(`/slack/apps/${appId}/signing-secret`, {
        method: 'POST',
        body: JSON.stringify({ secret }),
      })
      return apiOk(r, 'Failed to set signing secret')
    },
    [apiCall],
  )

  const issueWizardNonce = useCallback(
    async (appId: number): Promise<SlackAppNonceResponse> => {
      const r = await apiCall(`/slack/apps/${appId}/wizard-nonce`, {
        method: 'POST',
      })
      return apiOk(r, 'Failed to issue wizard nonce')
    },
    [apiCall],
  )

  const getWizardStatus = useCallback(
    async (appId: number): Promise<SlackWizardStatus> => {
      const r = await apiCall(`/slack/apps/${appId}/wizard-status`)
      return apiOk(r, 'Failed to fetch wizard status')
    },
    [apiCall],
  )

  const beginTestMessage = useCallback(
    async (
      appId: number,
      token: string,
    ): Promise<SlackTestMessageStatus> => {
      const r = await apiCall(`/slack/apps/${appId}/test-message`, {
        method: 'POST',
        body: JSON.stringify({ token }),
      })
      return apiOk(r, 'Failed to begin test message')
    },
    [apiCall],
  )

  const pollTestMessage = useCallback(
    async (appId: number): Promise<SlackTestMessageStatus> => {
      const r = await apiCall(`/slack/apps/${appId}/test-message`)
      return apiOk(r, 'Failed to poll test message')
    },
    [apiCall],
  )

  return {
    listApps,
    getApp,
    createApp,
    deleteApp,
    setClientSecret,
    setSigningSecret,
    issueWizardNonce,
    getWizardStatus,
    beginTestMessage,
    pollTestMessage,
  }
}
