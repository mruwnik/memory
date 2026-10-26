import { describe, it, expect, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'
import type { SettingRow, SettingsState } from '@/hooks/useSettings'
import { NotesSyncSettings } from './NotesSyncSettings'

const row = (overrides: Partial<SettingRow>): SettingRow => ({
  key: 'enable_reranking',
  kind: 'bool',
  applies: 'live',
  description: 'Rerank results',
  env_var: 'ENABLE_RERANKING',
  source: 'default',
  locked: false,
  set: false,
  value: true,
  key_hint: null,
  choices: [],
  pending_restart: false,
  error: null,
  module: null,
  mcp_servers: [],
  ...overrides,
})

const state = (settings: SettingRow[], overrides: Partial<SettingsState> = {}): SettingsState => ({
  settings,
  restart_pending: false,
  self_restart: false,
  ...overrides,
})

interface Call { url: string; method: string; body: unknown }

// Serves GET /settings from `current`; PATCH replaces it with `afterPatch`.
const install = (current: SettingsState, routes: Record<string, () => Response> = {}, afterPatch?: SettingsState) => {
  const calls: Call[] = []
  mockFetch(async (input, init) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    for (const [suffix, respond] of Object.entries(routes)) if (url.endsWith(suffix)) return respond()
    if (url.endsWith('/settings') && method === 'PATCH') return mockResponse({ json: afterPatch ?? current })
    if (url.endsWith('/settings')) return mockResponse({ json: current })
    return mockResponse({ json: {} })
  })
  return calls
}

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('Notes source sync interval', () => {
  const interval = (overrides: Partial<SettingRow> = {}) =>
    row({ key: 'notes_sync_interval', kind: 'int', description: 'Seconds between notes syncs', env_var: 'NOTES_SYNC_INTERVAL', value: 900, ...overrides })

  it('saves the interval in seconds from minutes', async () => {
    const calls = install(state([interval()]), {}, state([interval({ source: 'ui', set: true, value: 1800 })]))
    const { user } = renderWithRouter(<NotesSyncSettings />)

    const minutes = await screen.findByLabelText('Minutes between syncs')
    expect(minutes).toHaveValue(15)
    await user.clear(minutes)
    await user.type(minutes, '30')
    await user.click(screen.getByRole('button', { name: 'Save interval' }))

    await waitFor(() => expect(calls.find(c => c.method === 'PATCH')?.body).toEqual({ values: { notes_sync_interval: 1800 } }))
    expect(await screen.findByText('set here')).toBeInTheDocument()
  })

  it('shows an env-set interval locked', async () => {
    install(state([interval({ source: 'env', locked: true, set: true, value: 600 })]))
    renderWithRouter(<NotesSyncSettings />)

    expect(await screen.findByText(/Every 10 minutes/)).toBeInTheDocument()
    expect(screen.getByText(/Set by the operator in the environment \(NOTES_SYNC_INTERVAL\)/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Minutes between syncs')).toBeNull()
  })
})
