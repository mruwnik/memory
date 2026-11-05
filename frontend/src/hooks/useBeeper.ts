import { useCallback } from 'react'
import { useAuth } from './useAuth'
import type { LLMKeyChoice, LLMKeyView } from './useSources'
import { apiError } from './apiError'

type Sensitivity = 'public' | 'basic' | 'internal' | 'confidential'

export interface BeeperServer {
  url: string
  // Another account in memory already uses this Server
  in_use: boolean
}

export type BeeperSetupStep = 'email' | 'code' | 'verify' | 'done'

export interface BeeperAccount {
  id: number
  name: string | null
  server_url: string
  email: string | null
  beeper_user_id: string | null
  signed_in: boolean
  verified: boolean
  // The Server's own sign-in state: needs-login, needs-verification, ready, ...
  setup_state: string
  step: BeeperSetupStep
  collect_by_default: boolean
  is_active: boolean
  server_version: string | null
  version_matches: boolean
  // Whether the collector's WebSocket to the Server is up
  connected: boolean
  last_reconcile_at: string | null
  last_event_at: string | null
  created_at: string | null
}

export interface BeeperAccountCreate {
  server_url: string
  name?: string
  collect_by_default: boolean
}

export interface BeeperAccountUpdate {
  name?: string
  collect_by_default?: boolean
  is_active?: boolean
}

export interface BeeperVerification {
  id: string
  // requested, ready, sas_ready, done, cancelled, error, ...
  state: string
  direction: string
  methods: string[]
  available_actions: string[]
  other_device_name: string | null
  // Space-separated emoji to compare with the other device
  sas_emojis: string | null
  sas_decimals: string | null
  error_code: string | null
}

export interface BeeperSetup {
  account: BeeperAccount
  verification: BeeperVerification | null
}

export type BeeperVerificationAction = 'accept' | 'cancel' | 'sas-start' | 'sas-confirm'

export interface BeeperNetwork {
  id: number
  account_id: number
  network_account_id: string
  network: string
  bridge_type: string | null
  bridge_provider: string | null
  display_name: string | null
  // From Beeper, plus "removed" once it disappears from Beeper and "backfilling" while history loads
  status: string | null
  collect_messages: boolean
  project_id: number | null
  sensitivity: Sensitivity
  llm_key: LLMKeyView
}

export interface BeeperNetworkUpdate {
  collect_messages?: boolean
  project_id?: number | null
  sensitivity?: Sensitivity
  llm_key?: LLMKeyChoice
}

export interface BeeperChat {
  id: number
  network_id: number
  chat_id: string
  title: string | null
  chat_type: string | null
  is_merged: boolean
  merged_into: string | null
  message_expiry_seconds: number | null
  // null inherits the network's setting
  collect_messages: boolean | null
  effective_collect: boolean
  project_id: number | null
  // null inherits the network's sensitivity
  sensitivity: Sensitivity | null
  last_activity_at: string | null
  backfill_done: boolean
  // Server kept answering with no older history, so the backfill gave up
  backfill_stopped: boolean
}

export interface BeeperChatUpdate {
  collect_messages?: boolean | null
  project_id?: number | null
  sensitivity?: Sensitivity | null
}

export const useBeeper = () => {
  const { apiCall } = useAuth()

  const call = useCallback(async <T>(path: string, fallback: string, method = 'GET', body?: unknown): Promise<T> => {
    const response = await apiCall(`/beeper${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) })
    if (!response.ok) throw await apiError(response, fallback)
    return response.json()
  }, [apiCall])

  const listServers = useCallback(() => call<BeeperServer[]>('/servers', 'Failed to fetch Beeper Servers'), [call])

  const listAccounts = useCallback((userId?: number) =>
    call<BeeperAccount[]>(`/accounts${userId !== undefined ? `?user_id=${userId}` : ''}`, 'Failed to fetch Beeper accounts'), [call])

  const getAccount = useCallback((id: number) => call<BeeperAccount>(`/accounts/${id}`, 'Failed to fetch Beeper account'), [call])

  const createAccount = useCallback((data: BeeperAccountCreate) =>
    call<BeeperAccount>('/accounts', 'Failed to add Beeper account', 'POST', data), [call])

  const updateAccount = useCallback((id: number, data: BeeperAccountUpdate) =>
    call<BeeperAccount>(`/accounts/${id}`, 'Failed to update Beeper account', 'PATCH', data), [call])

  const deleteAccount = useCallback((id: number) =>
    call<{ signed_out: boolean; note: string }>(`/accounts/${id}`, 'Failed to delete Beeper account', 'DELETE'), [call])

  const signOut = useCallback((id: number) =>
    call<{ account: BeeperAccount; note: string }>(`/accounts/${id}/sign-out`, 'Failed to sign out of Beeper', 'POST'), [call])

  const sendEmail = useCallback((id: number, email: string) =>
    call<BeeperSetup>(`/accounts/${id}/email`, 'Failed to send the sign-in code', 'POST', { email }), [call])

  const sendCode = useCallback((id: number, code: string) =>
    call<BeeperSetup>(`/accounts/${id}/code`, 'Failed to sign in', 'POST', { code }), [call])

  const getSetup = useCallback((id: number) => call<BeeperSetup>(`/accounts/${id}/setup`, 'Failed to check the sign-in'), [call])

  const startVerification = useCallback((id: number) =>
    call<BeeperSetup>(`/accounts/${id}/verifications`, 'Failed to start verification', 'POST'), [call])

  const verificationAction = useCallback((id: number, verificationId: string, action: BeeperVerificationAction) =>
    call<BeeperSetup>(`/accounts/${id}/verifications/${encodeURIComponent(verificationId)}/${action}`, 'Failed to update verification', 'POST'), [call])

  const sendRecoveryKey = useCallback((id: number, recoveryKey: string) =>
    call<BeeperSetup>(`/accounts/${id}/recovery-key`, 'Failed to verify with the recovery key', 'POST', { recovery_key: recoveryKey }), [call])

  const refreshNetworks = useCallback((id: number) =>
    call<BeeperNetwork[]>(`/accounts/${id}/refresh`, 'Failed to refresh networks', 'POST'), [call])

  const listNetworks = useCallback((id: number) =>
    call<BeeperNetwork[]>(`/accounts/${id}/networks`, 'Failed to fetch Beeper networks'), [call])

  const updateNetwork = useCallback((id: number, data: BeeperNetworkUpdate) =>
    call<BeeperNetwork>(`/networks/${id}`, 'Failed to update Beeper network', 'PATCH', data), [call])

  const listChats = useCallback((networkId: number) =>
    call<BeeperChat[]>(`/networks/${networkId}/chats`, 'Failed to fetch Beeper chats'), [call])

  const updateChat = useCallback((id: number, data: BeeperChatUpdate) =>
    call<BeeperChat>(`/chats/${id}`, 'Failed to update Beeper chat', 'PATCH', data), [call])

  return {
    listServers,
    listAccounts,
    getAccount,
    createAccount,
    updateAccount,
    deleteAccount,
    signOut,
    sendEmail,
    sendCode,
    getSetup,
    startVerification,
    verificationAction,
    sendRecoveryKey,
    refreshNetworks,
    listNetworks,
    updateNetwork,
    listChats,
    updateChat,
  }
}
