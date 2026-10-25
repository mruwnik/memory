import { describe, it, expect, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'
import SettingsPage from './SettingsPage'
import type { SettingRow, SettingsState } from '@/hooks/useSettings'

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
  ...overrides,
})

const voyage = (overrides: Partial<SettingRow> = {}) =>
  row({ key: 'voyage_api_key', kind: 'secret', description: 'Voyage AI key', env_var: 'VOYAGE_API_KEY', value: null, ...overrides })

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

describe('SettingsPage env-set rows', () => {
  it('shows the operator label and offers no controls', async () => {
    install(state([
      voyage({ source: 'env', locked: true, set: true, key_hint: 'ab12' }),
      row({ source: 'env', locked: true, set: true, value: false }),
    ]))
    renderWithRouter(<SettingsPage />)

    expect(await screen.findAllByText('set by the operator')).toHaveLength(2)
    expect(screen.getByText(/Set by the operator in the environment \(VOYAGE_API_KEY\)/)).toBeInTheDocument()
    expect(screen.getByText(/Set by the operator in the environment \(ENABLE_RERANKING\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Replace key|Clear|Turn on|Turn off/ })).toBeNull()
    expect(screen.queryByLabelText('Voyage API key')).toBeNull()
  })
})

describe('SettingsPage secrets', () => {
  it('shows only the hint for a stored key and saves a replacement without echoing it', async () => {
    const calls = install(state([voyage({ source: 'ui', set: true, key_hint: 'wxyz' })]))
    const { user } = renderWithRouter(<SettingsPage />)

    expect(await screen.findByText('ends in wxyz')).toBeInTheDocument()
    expect(screen.queryByLabelText('Voyage API key')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Replace key' }))
    const input = screen.getByLabelText('Voyage API key') as HTMLInputElement
    expect(input.type).toBe('password')
    expect(input.value).toBe('')
    await user.type(input, 'pa-new-key')
    await user.click(screen.getByRole('button', { name: 'Save key' }))

    await waitFor(() => expect(calls.some(c => c.method === 'PATCH')).toBe(true))
    expect(calls.find(c => c.method === 'PATCH')?.body).toEqual({ values: { voyage_api_key: 'pa-new-key' } })
    await waitFor(() => expect(screen.queryByLabelText('Voyage API key')).toBeNull())
    expect(screen.queryByDisplayValue('pa-new-key')).toBeNull()
  })

  it('clears a stored key', async () => {
    const calls = install(state([voyage({ source: 'ui', set: true, key_hint: 'wxyz' })]))
    const { user } = renderWithRouter(<SettingsPage />)

    await user.click(await screen.findByRole('button', { name: 'Clear Voyage API key' }))
    await user.click(screen.getByRole('button', { name: 'Clear key' }))

    await waitFor(() => expect(calls.find(c => c.method === 'PATCH')?.body).toEqual({ values: { voyage_api_key: null } }))
  })

  it('switches to the input, focused, once clearing leaves no key set', async () => {
    install(state([voyage({ source: 'ui', set: true, key_hint: 'wxyz' })]), {}, state([voyage()]))
    const { user } = renderWithRouter(<SettingsPage />)

    await user.click(await screen.findByRole('button', { name: 'Clear Voyage API key' }))
    await user.click(screen.getByRole('button', { name: 'Clear key' }))

    const input = await screen.findByLabelText('Voyage API key')
    expect(input).toHaveFocus()
    expect(screen.queryByRole('button', { name: 'Replace key' })).toBeNull()
  })

  it('returns focus to Replace key on cancel', async () => {
    install(state([voyage({ source: 'ui', set: true, key_hint: 'wxyz' })]))
    const { user } = renderWithRouter(<SettingsPage />)

    await user.click(await screen.findByRole('button', { name: 'Replace key' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.getByRole('button', { name: 'Replace key' })).toHaveFocus()
  })

  it('shows why an unreadable key must be set again', async () => {
    install(state([voyage({ error: "can't be read; set it again" })]))
    renderWithRouter(<SettingsPage />)

    expect(await screen.findByText("can't be read; set it again")).toBeInTheDocument()
    expect(screen.getByLabelText('Voyage API key')).toBeInTheDocument()
  })
})

describe('SettingsPage Voyage check', () => {
  const open = async (check: () => Response) => {
    const calls = install(state([voyage()]), { '/settings/voyage_api_key/check': check })
    const rendered = renderWithRouter(<SettingsPage />)
    await rendered.user.type(await screen.findByLabelText('Voyage API key'), 'pa-test')
    return { calls, user: rendered.user }
  }

  it('is disabled until a key is typed', async () => {
    install(state([voyage()]))
    renderWithRouter(<SettingsPage />)

    expect(await screen.findByRole('button', { name: 'Check' })).toBeDisabled()
  })

  it('reports an accepted key without saving it', async () => {
    const { calls, user } = await open(() => mockResponse({ json: { ok: true, voyage_status: null } }))
    await user.click(screen.getByRole('button', { name: 'Check' }))

    expect(await screen.findByText('Voyage accepted the key.')).toBeInTheDocument()
    expect(calls.find(c => c.url.endsWith('/check'))?.body).toEqual({ value: 'pa-test' })
    expect(calls.some(c => c.method === 'PATCH')).toBe(false)
  })

  it('reports a rejected key with Voyage status', async () => {
    const { user } = await open(() => mockResponse({ json: { ok: false, voyage_status: 401 } }))
    await user.click(screen.getByRole('button', { name: 'Check' }))

    expect(await screen.findByText('Voyage rejected the key (HTTP 401).')).toBeInTheDocument()
  })

  it('says Voyage could not be reached on a connection failure', async () => {
    const { user } = await open(() => mockResponse({ json: { ok: false, voyage_status: 502 } }))
    await user.click(screen.getByRole('button', { name: 'Check' }))

    expect(await screen.findByText(/Couldn't reach Voyage/)).toBeInTheDocument()
  })

  it('shows the server reason when the check itself fails', async () => {
    const { user } = await open(() => mockResponse({ status: 400, json: { detail: 'the key is empty' } }))
    await user.click(screen.getByRole('button', { name: 'Check' }))

    expect(await screen.findByText('the key is empty')).toBeInTheDocument()
  })

  it('drops the verdict once the key is edited', async () => {
    const { user } = await open(() => mockResponse({ json: { ok: true, voyage_status: null } }))
    await user.click(screen.getByRole('button', { name: 'Check' }))
    await screen.findByText('Voyage accepted the key.')
    await user.type(screen.getByLabelText('Voyage API key'), 'x')

    expect(screen.queryByText('Voyage accepted the key.')).toBeNull()
  })

  it('shows the rejection when saving a bad key fails', async () => {
    mockFetch(async (_input, init) =>
      init?.method === 'PATCH'
        ? mockResponse({ status: 400, json: { detail: 'Voyage rejected the key (HTTP 401)' } })
        : mockResponse({ json: state([voyage()]) }))
    const { user } = renderWithRouter(<SettingsPage />)
    await user.type(await screen.findByLabelText('Voyage API key'), 'pa-bad')
    await user.click(screen.getByRole('button', { name: 'Save key' }))

    expect(await screen.findByText('Voyage rejected the key (HTTP 401)')).toBeInTheDocument()
    expect(screen.getByLabelText('Voyage API key')).toBeInTheDocument()
  })
})

describe('SettingsPage toggles', () => {
  it('turns a search ceiling off and shows the returned state', async () => {
    const calls = install(
      state([row({ value: true })]),
      {},
      state([row({ value: false, source: 'ui', set: true })]),
    )
    const { user } = renderWithRouter(<SettingsPage />)

    await user.click(await screen.findByRole('button', { name: 'Turn off: Rerank results' }))

    await waitFor(() => expect(calls.find(c => c.method === 'PATCH')?.body).toEqual({ values: { enable_reranking: false } }))
    expect(await screen.findByRole('button', { name: 'Turn on: Rerank results' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reset: Rerank results' })).toBeInTheDocument()
  })

  it('disables a server by sending the whole disabled list', async () => {
    const mcp = row({ key: 'disabled_mcp_servers', kind: 'list', description: 'Disabled MCP servers', env_var: 'DISABLED_MCP_SERVERS', value: ['forecast'], choices: ['core', 'forecast', 'github'] })
    const calls = install(state([mcp]))
    const { user } = renderWithRouter(<SettingsPage />)

    await screen.findByText('github')
    await user.click(screen.getByRole('button', { name: 'Disable github' }))

    await waitFor(() => expect(calls.find(c => c.method === 'PATCH')?.body).toEqual({ values: { disabled_mcp_servers: ['forecast', 'github'] } }))
  })

  it('files only the search ceilings under Search and warns when the MCP setting is missing', async () => {
    install(state([row({}), row({ key: 'enable_some_module', description: 'Some module', env_var: 'ENABLE_SOME_MODULE' })]))
    renderWithRouter(<SettingsPage />)

    expect(await screen.findByText('Rerank results')).toBeInTheDocument()
    expect(screen.queryByText('Some module')).toBeNull()
    expect(screen.getByText(/does not report the disabled_mcp_servers setting/)).toBeInTheDocument()
  })

  it('shows an error row and the server reason for a refused change', async () => {
    mockFetch(async (_input, init) =>
      init?.method === 'PATCH'
        ? mockResponse({ status: 409, json: { detail: 'ENABLE_RERANKING is set by the operator' } })
        : mockResponse({ json: state([row({ error: "can't be read; set it again" })]) }))
    const { user } = renderWithRouter(<SettingsPage />)

    expect(await screen.findByText("can't be read; set it again")).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Turn off: Rerank results' }))
    expect(await screen.findByText('ENABLE_RERANKING is set by the operator')).toBeInTheDocument()
  })
})

describe('SettingsPage restart banner', () => {
  const pending = row({ pending_restart: true, description: 'Module flags' })

  it('is absent when nothing is pending', async () => {
    install(state([row({})]))
    renderWithRouter(<SettingsPage />)

    await screen.findByText('Rerank results')
    expect(screen.queryByText(/Restart the api/)).toBeNull()
  })

  it('offers a restart button when the api can restart itself', async () => {
    const calls = install(state([pending], { restart_pending: true, self_restart: true }), {
      '/settings/restart': () => mockResponse({ status: 202, json: { status: 'restarting' } }),
      '/health': () => mockResponse({ json: { boot_id: String(Math.random()) } }),
    })
    const { user } = renderWithRouter(<SettingsPage />)

    expect(await screen.findByText(/Restart the api to apply: Module flags/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Restart now' }))
    expect(calls.some(c => c.url.endsWith('/settings/restart'))).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Restart' }))

    await waitFor(() => expect(calls.some(c => c.url.endsWith('/settings/restart') && c.method === 'POST')).toBe(true))
    expect(await screen.findByText('The api restarted.', {}, { timeout: 4000 })).toBeInTheDocument()
  })

  it('gives the ops command instead when the api cannot restart itself', async () => {
    install(state([pending], { restart_pending: true, self_restart: false }))
    renderWithRouter(<SettingsPage />)

    expect(await screen.findByText('tools/ops/restart.sh api')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Restart now' })).toBeNull()
  })
})
