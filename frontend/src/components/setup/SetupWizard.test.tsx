import { useState } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderWithRouter, screen, waitFor, within } from '@/test/utils'
import type { SetupStatus } from '@/hooks/useSetup'
import type { AuthUser } from '@/hooks/useAuth'
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
vi.mock('@/components/sources/panels/EmailPanel', () => ({ EmailPanel: () => <div>EMAIL_PANEL</div> }))

const ADA: AuthUser = { id: 7, name: 'Ada', email: 'ada@example.com', user_type: 'human', scopes: ['*'], timezone: 'UTC' }

const status = (overrides: Partial<SetupStatus> = {}): SetupStatus => ({
  needs_account: false,
  sources: { email: 0, google: 0, github: 0, slack: 0, discord: 0, feeds: 0, notes: 0, transcripts: 0 },
  mcp_clients: [],
  mcp_url: 'https://memory.example.com/mcp',
  ...overrides,
})

const Harness = ({ signedIn = false }: { signedIn?: boolean }) => {
  const [authed, setAuthed] = useState(signedIn)
  return <SetupWizard isAuthenticated={authed} user={authed ? ADA : null} checkAuth={async () => setAuthed(true)} />
}

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

    expect(await screen.findByText(/Voyage key/)).toBeInTheDocument()
    await waitFor(() => expect(listSystemAgents).toHaveBeenCalled())
    expect(await screen.findByText(shown ? 'Summaries' : 'Add a key')).toBeInTheDocument()
    expect(screen.queryByText('Summaries') !== null).toBe(shown)
  })

  it('adds a key from the keys step and says where the Voyage key lives', async () => {
    upsertKey.mockResolvedValue(undefined)
    const { user } = renderWithRouter(<Harness signedIn />, atStep('keys'))

    expect(await screen.findByText(/Voyage key/)).toBeInTheDocument()
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
    expect(await screen.findByRole('heading', { name: 'Connect a source' })).toBeInTheDocument()
    expect(document.activeElement).toHaveTextContent('Connect a source')
    await user.click(screen.getByRole('button', { name: 'Back' }))
    expect(await screen.findByRole('heading', { name: 'LLM keys' })).toBeInTheDocument()
  })
})
