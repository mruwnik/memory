import { useCallback } from 'react'
import { SERVER_URL, useAuth } from './useAuth'
import { apiError } from './apiError'

export type SettingSource = 'env' | 'ui' | 'default'

export interface SettingRow {
  key: string
  kind: 'bool' | 'secret' | 'list'
  applies: 'live' | 'restart'
  description: string
  /** Null for a setting no env var can set. */
  env_var: string | null
  source: SettingSource
  locked: boolean
  set: boolean
  /** Never present for secrets. */
  value: boolean | string[] | null
  key_hint: string | null
  choices: string[]
  pending_restart: boolean
  /** Why the stored UI value is being ignored. */
  error: string | null
  /** Module flags only: the module, and the MCP servers that go with it. */
  module: string | null
  mcp_servers: string[]
}

export interface SettingsState {
  settings: SettingRow[]
  restart_pending: boolean
  self_restart: boolean
}

export interface KeyCheckResult {
  ok: boolean
  voyage_status: number | null
}

const RESTART_POLL_MS = 1000
const RESTART_POLL_ATTEMPTS = 90

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

const bootId = async (): Promise<string | null> => {
  try {
    const response = await fetch(`${SERVER_URL}/health`)
    return (await response.json()).boot_id ?? null
  } catch {
    return null
  }
}

export const useSettings = () => {
  const { apiCall } = useAuth()

  const getSettings = useCallback(async (): Promise<SettingsState> => {
    const response = await apiCall('/settings')
    if (!response.ok) throw await apiError(response, 'Failed to load settings')
    return response.json()
  }, [apiCall])

  /** A null value clears the UI value, back to the default. */
  const updateSettings = useCallback(async (values: Record<string, unknown>): Promise<SettingsState> => {
    const response = await apiCall('/settings', { method: 'PATCH', body: JSON.stringify({ values }) })
    if (!response.ok) throw await apiError(response, 'Failed to save settings')
    return response.json()
  }, [apiCall])

  const checkVoyageKey = useCallback(async (value: string): Promise<KeyCheckResult> => {
    const response = await apiCall('/settings/voyage_api_key/check', { method: 'POST', body: JSON.stringify({ value }) })
    if (!response.ok) throw await apiError(response, 'Failed to check the key')
    return response.json()
  }, [apiCall])

  /** Asks the api to restart, then resolves once /health reports a different boot id. */
  const restartApi = useCallback(async (): Promise<void> => {
    const before = await bootId()
    const response = await apiCall('/settings/restart', { method: 'POST' })
    if (!response.ok) throw await apiError(response, 'Failed to restart the api')
    // Without a pre-restart id, only an id seen after the api went down counts as the new process.
    let sawDown = false
    for (let attempt = 0; attempt < RESTART_POLL_ATTEMPTS; attempt++) {
      await sleep(RESTART_POLL_MS)
      const now = await bootId()
      if (!now) sawDown = true
      else if (before ? now !== before : sawDown) return
    }
    throw new Error('The api did not come back within 90 seconds')
  }, [apiCall])

  return { getSettings, updateSettings, checkVoyageKey, restartApi }
}
