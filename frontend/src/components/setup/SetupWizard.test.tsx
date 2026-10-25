import { useState } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderWithRouter, screen, waitFor, within } from '@/test/utils'
import type { SetupStatus } from '@/hooks/useSetup'
import type { AuthUser } from '@/hooks/useAuth'
import type { SettingRow, SettingsState } from '@/hooks/useSettings'
import { SetupWizard } from './SetupWizard'
import { POLL_MS } from './ClientStep'

const getNeedsAccount = vi.fn()
const createAccount = vi.fn()
const getStatus = vi.fn()
const storeSession = vi.fn()
const listKeys = vi.fn()
const upsertKey = vi.fn()
const listSystemAgents = vi.fn()
const updateUser = vi.fn()
const getSettings = vi.fn()
const updateSettings = vi.fn()
const restartApi = vi.fn()

vi.mock('@/hooks/useSetup', () => ({
  getNeedsAccount: () => getNeedsAccount(),
  createAccount: (body: unknown) => createAccount(body),
  useSetup: () => ({ getStatus }),
}))
vi.mock('@/hooks/useAuth', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAuth')>()),
  storeSession: (token: string) => storeSession(token),
}))
vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => ({ listKeys, upsertKey, listSystemAgents }),
}))
vi.mock('@/hooks/useUsers', () => ({ useUsers: () => ({ updateUser }) }))
vi.mock('@/hooks/useSettings', () => ({
  useSettings: () => ({ getSettings, updateSettings, restartApi, checkVoyageKey: vi.fn() }),
}))
vi.mock('@/components/sources/panels/EmailPanel', () => ({ EmailPanel: () => <div>EMAIL_PANEL</div> }))

const ADA: AuthUser = { id: 7, name: 'Ada', email: 'ada@example.com', user_type: 'human', scopes: ['*'], timezone: 'UTC', modules: [] }

const status = (overrides: Partial<SetupStatus> = {}): SetupStatus => ({
  needs_account: false,
  sources: { email: 0, google: 0, github: 0, slack: 0, discord: 0, feeds: 0, notes: 0, transcripts: 0 },
  mcp_clients: [],
  mcp_url: 'https://memory.example.com/mcp',
  ...overrides,
})

const checkAuth = vi.fn()

const Harness = ({ signedIn = false, scopes = ADA.scopes }: { signedIn?: boolean; scopes?: string[] }) => {
  const [authed, setAuthed] = useState(signedIn)
  return <SetupWizard isAuthenticated={authed} user={authed ? { ...ADA, scopes } : null} checkAuth={async () => {
    checkAuth()
    setAuthed(true)
  }} />
}

const setting = (overrides: Partial<SettingRow>): SettingRow => ({
  key: 'module_teams',
  kind: 'bool',
  applies: 'restart',
  description: 'Teams and projects',
  env_var: 'ENABLE_MODULES',
  source: 'default',
  locked: false,
  set: false,
  value: false,
  key_hint: null,
  choices: [],
  pending_restart: false,
  error: null,
  module: 'teams',
  mcp_servers: ['projects', 'teams'],
  ...overrides,
})

const settings = (rows: SettingRow[], overrides: Partial<SettingsState> = {}): SettingsState => ({
  settings: rows,
  restart_pending: false,
  self_restart: true,
  ...overrides,
})

const atStep = (step: string) => ({ initialEntries: [{ pathname: '/ui/setup', state: { step } }] })

beforeEach(() => {
  getNeedsAccount.mockResolvedValue(true)
  getStatus.mockResolvedValue(status())
  listKeys.mockResolvedValue({ providers: { anthropic: [], openai: [] } })
  listSystemAgents.mockResolvedValue([])
})

afterEach(() => {
  vi.useRealTimers()
})

describe('SetupWizard', () => {
  it('creates the first account with the setup token, signs in, and moves on to keys', async () => {
    createAccount.mockResolvedValue('session-1')
    const { user } = renderWithRouter(<Harness />, { initialEntries: ['/ui/setup'] })

    await user.type(await screen.findByLabelText('Name'), 'Ada')
    await user.type(screen.getByLabelText('Email'), 'ada@example.com')
    await user.type(screen.getByLabelText('New password'), 'longenough1')
    await user.type(screen.getByLabelText('Confirm new password'), 'longenough1')
    await user.type(screen.getByLabelText('Setup token'), 'tok')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(createAccount).toHaveBeenCalledWith({ name: 'Ada', email: 'ada@example.com', password: 'longenough1', token: 'tok' })
    expect(storeSession).toHaveBeenCalledWith('session-1')
    expect(await screen.findByRole('heading', { name: 'LLM keys' })).toBeInTheDocument()
  })

  it('shows why the server refused the account', async () => {
    createAccount.mockRejectedValue(new Error('That setup token is not the one in the server log'))
    const { user } = renderWithRouter(<Harness />, { initialEntries: ['/ui/setup'] })

    await user.type(await screen.findByLabelText('Name'), 'Ada')
    await user.type(screen.getByLabelText('Email'), 'ada@example.com')
    await user.type(screen.getByLabelText('New password'), 'longenough1')
    await user.type(screen.getByLabelText('Confirm new password'), 'longenough1')
    await user.type(screen.getByLabelText('Setup token'), 'nope')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('That setup token is not the one in the server log')).toBeInTheDocument()
    expect(storeSession).not.toHaveBeenCalled()
  })

  it('offers only a login once the server has an account', async () => {
    getNeedsAccount.mockResolvedValue(false)
    renderWithRouter(<Harness />, { initialEntries: ['/ui/setup'] })

    expect(await screen.findByText(/already set up/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Setup token')).not.toBeInTheDocument()
  })

  it("can't be reopened by a signed-in user who isn't in the middle of it", () => {
    renderWithRouter(<Harness signedIn />, { initialEntries: ['/ui/setup'] })
    expect(screen.queryByRole('heading', { name: 'Set up Memory' })).not.toBeInTheDocument()
  })

  it.each([
    [[], false],
    [[{ id: 1, provider: 'anthropic', label: 'personal', key_hint: 'ab12', is_default: true, status: 'ok' }], true],
  ])('shows the system agents only once a key exists (keys: %j)', async (anthropic, shown) => {
    listKeys.mockResolvedValue({ providers: { anthropic, openai: [] } })
    listSystemAgents.mockResolvedValue([
      { slug: 'system:summarise', title: 'Summaries', description: 'd', use: 'ingest', model: 'anthropic/claude-haiku-5-5', provider: 'anthropic', has_key: true, problem: null },
    ])
    renderWithRouter(<Harness signedIn />, atStep('keys'))

    await waitFor(() => expect(listSystemAgents).toHaveBeenCalled())
    expect(await screen.findByText(shown ? 'Summaries' : 'Add a key')).toBeInTheDocument()
    expect(screen.queryByText('Summaries') !== null).toBe(shown)
  })

  it('adds a key from the keys step', async () => {
    upsertKey.mockResolvedValue(undefined)
    const { user } = renderWithRouter(<Harness signedIn />, atStep('keys'))

    // The form remounts once the keys load, which would clear what was typed.
    await waitFor(() => expect(screen.queryByText('Loading keys...')).toBeNull())
    await user.type(screen.getByLabelText('Label'), 'personal')
    await user.type(screen.getByLabelText('API key'), 'sk-ant-x')
    await user.click(screen.getByRole('button', { name: 'Add key' }))

    expect(upsertKey).toHaveBeenCalledWith({ provider: 'anthropic', label: 'personal', key: 'sk-ant-x' })
    await waitFor(() => expect(listKeys).toHaveBeenCalledTimes(2))
  })

  it('lists all eight sources, marks connected ones, and opens the existing panel', async () => {
    getStatus.mockResolvedValue(status({ sources: { ...status().sources, feeds: 2 } }))
    const { user } = renderWithRouter(<Harness signedIn />, atStep('sources'))

    const rows = within(await screen.findByRole('list', { name: 'Sources' })).getAllByRole('listitem')
    expect(rows).toHaveLength(8)
    const feeds = rows.find(row => within(row).queryByText('RSS feeds'))!
    expect(within(feeds).getByText('Connected')).toBeInTheDocument()

    const email = rows.find(row => within(row).queryByText('Email'))!
    await user.click(within(email).getByRole('button', { name: 'Set up Email' }))
    expect(screen.getByText('EMAIL_PANEL')).toBeInTheDocument()
  })

  it('waits for an AI client to connect, then names it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    getStatus.mockResolvedValueOnce(status()).mockResolvedValue(status({ mcp_clients: ['Claude Code'] }))
    renderWithRouter(<Harness signedIn />, atStep('client'))

    expect(await screen.findByText('claude mcp add --transport http memory https://memory.example.com/mcp')).toBeInTheDocument()
    expect(screen.getByText(/Waiting for your client/)).toBeInTheDocument()
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS))
    expect(await screen.findByText(/Connected: Claude Code/)).toBeInTheDocument()
  })

  it('saves the timezone from the browser and finishes for good', async () => {
    updateUser.mockResolvedValue({})
    const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone
    const { user } = renderWithRouter(<Harness signedIn />, atStep('timezone'))

    await user.click(await screen.findByRole('button', { name: 'Save and finish' }))

    expect(updateUser).toHaveBeenCalledWith(ADA.id, { timezone: browserZone })
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Set up Memory' })).not.toBeInTheDocument())
  })

  it('steps forward and back without doing anything on a step, moving focus to the new step', async () => {
    const { user } = renderWithRouter(<Harness signedIn />, atStep('keys'))

    await user.click(await screen.findByRole('button', { name: 'Next' }))
    expect(await screen.findByRole('heading', { name: 'Embeddings' })).toBeInTheDocument()
    expect(document.activeElement).toHaveTextContent('Embeddings')
    await user.click(screen.getByRole('button', { name: 'Back' }))
    expect(await screen.findByRole('heading', { name: 'LLM keys' })).toBeInTheDocument()
  })

  it.each([
    [['*'], ['LLM keys', 'Embeddings', 'Modules', 'Connect a source']],
    [['read'], ['LLM keys', 'Connect a source']],
  ])('offers the instance-wide steps only to admins (scopes %j)', (scopes, first) => {
    renderWithRouter(<Harness signedIn scopes={scopes} />, atStep('keys'))

    const steps = within(screen.getByRole('list', { name: 'Setup steps' })).getAllByRole('listitem')
    expect(steps.slice(0, first.length).map(s => s.textContent?.replace(/^\d+\. /, ''))).toEqual(first)
  })

  it('sets the Voyage key on its step', async () => {
    getSettings.mockResolvedValue(settings([setting({ key: 'voyage_api_key', kind: 'secret', applies: 'live', env_var: 'VOYAGE_API_KEY', value: null, module: null, mcp_servers: [] })]))
    updateSettings.mockResolvedValue(settings([setting({ key: 'voyage_api_key', kind: 'secret', source: 'ui', set: true, key_hint: '9876', value: null, module: null, mcp_servers: [] })]))
    const { user } = renderWithRouter(<Harness signedIn />, atStep('voyage'))

    await user.type(await screen.findByLabelText('Voyage API key'), 'pa-key-9876')
    await user.click(screen.getByRole('button', { name: 'Save key' }))

    expect(updateSettings).toHaveBeenCalledWith({ voyage_api_key: 'pa-key-9876' })
    expect(await screen.findByText('ends in 9876')).toBeInTheDocument()
  })

  it('turns a module on, shows env-set ones locked, and restarts the api to apply it', async () => {
    const locked = setting({ key: 'module_multi_user', module: 'multi_user', description: 'More than one human user', source: 'env', locked: true, set: true, value: true, mcp_servers: [] })
    getSettings.mockResolvedValue(settings([setting({}), locked]))
    updateSettings.mockResolvedValue(settings([setting({ source: 'ui', set: true, value: true, pending_restart: true }), locked], { restart_pending: true }))
    restartApi.mockResolvedValue(undefined)
    const { user } = renderWithRouter(<Harness signedIn />, atStep('modules'))

    expect(await screen.findByText(/Set by the operator in the environment \(ENABLE_MODULES\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Turn off: module multi_user' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Turn on: module teams' }))
    expect(updateSettings).toHaveBeenCalledWith({ module_teams: true })

    expect(await screen.findByText('Restart the api to apply: module teams.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Restart now' }))
    await user.click(await screen.findByRole('button', { name: 'Restart' }))

    await waitFor(() => expect(checkAuth).toHaveBeenCalled())
    expect(restartApi).toHaveBeenCalled()
  })
})
