import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'

const listTranscriptAccounts = vi.fn()
const createTranscriptAccount = vi.fn()
const updateTranscriptAccount = vi.fn()
const deleteTranscriptAccount = vi.fn()
const syncTranscriptAccount = vi.fn()
const rescanTranscriptAccount = vi.fn()
const listTranscriptProviders = vi.fn()
const generateTranscriptWebhookSecret = vi.fn()
const deleteTranscriptWebhookSecret = vi.fn()
const listProjects = vi.fn()

vi.mock('@/hooks/useSources', () => ({
  useSources: () => ({
    listTranscriptAccounts,
    createTranscriptAccount,
    updateTranscriptAccount,
    deleteTranscriptAccount,
    syncTranscriptAccount,
    rescanTranscriptAccount,
    listTranscriptProviders,
    generateTranscriptWebhookSecret,
    deleteTranscriptWebhookSecret,
  }),
}))

vi.mock('@/hooks/useProjects', () => ({
  useProjects: () => ({ listProjects }),
}))

vi.mock('../Sources', () => ({
  useSourcesContext: () => ({ userId: 7, selectedUser: { type: 'user', id: 7, name: 'X' } }),
}))

import { TranscriptsPanel } from './TranscriptsPanel'

const account = (over: Record<string, unknown> = {}) => ({
  llm_key: { choice: 'default', key_id: null, label: 'personal', provider: 'anthropic', paid_by_you: true, runs: true, can_choose_default: true },
  id: 1,
  name: 'My Fireflies',
  provider: 'fireflies',
  has_api_key: true,
  webhook_secret_hint: null,
  ingest_url: 'https://mem.example/transcripts/ingest/1',
  polls: true,
  tags: ['meetings'],
  last_sync_at: null,
  sync_error: null,
  sync_failing_since: null,
  active: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  project_id: null,
  sensitivity: 'basic',
  ...over,
})

const project = { id: 10, title: 'Proj', repo_path: 'org/repo' }

const passwordInputs = () =>
  Array.from(document.querySelectorAll('input[type="password"]')) as HTMLInputElement[]

beforeEach(() => {
  vi.clearAllMocks()
  listTranscriptAccounts.mockResolvedValue([])
  listTranscriptProviders.mockResolvedValue(['fireflies', 'generic'])
  listProjects.mockResolvedValue([project])
  createTranscriptAccount.mockResolvedValue(account())
  updateTranscriptAccount.mockResolvedValue(account())
  deleteTranscriptAccount.mockResolvedValue(undefined)
  syncTranscriptAccount.mockResolvedValue({ task_id: 't', status: 'queued' })
  rescanTranscriptAccount.mockResolvedValue({ task_id: 't', status: 'queued' })
  generateTranscriptWebhookSecret.mockResolvedValue({
    secret: 'fresh-secret-abcd',
    webhook_secret_hint: 'abcd',
    ingest_url: 'https://mem.example/transcripts/ingest/1',
  })
  deleteTranscriptWebhookSecret.mockResolvedValue(account())
})

describe('TranscriptsPanel - load states', () => {
  it('shows loading first', () => {
    listTranscriptAccounts.mockReturnValue(new Promise(() => {}))
    renderWithRouter(<TranscriptsPanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('passes the selected userId when listing accounts', async () => {
    renderWithRouter(<TranscriptsPanel />)
    await waitFor(() => expect(listTranscriptAccounts).toHaveBeenCalledWith(7))
  })

  it('shows empty state when no accounts', async () => {
    renderWithRouter(<TranscriptsPanel />)
    expect(await screen.findByText(/No transcript accounts configured/)).toBeInTheDocument()
  })

  it('shows error state and retries', async () => {
    listTranscriptAccounts.mockRejectedValueOnce(new Error('nope'))
    const { user } = renderWithRouter(<TranscriptsPanel />)
    expect(await screen.findByText('nope')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/No transcript accounts configured/)).toBeInTheDocument()
  })

  it('renders a populated account card with provider, tags and sensitivity', async () => {
    listTranscriptAccounts.mockResolvedValue([account()])
    renderWithRouter(<TranscriptsPanel />)
    expect(await screen.findByText('My Fireflies')).toBeInTheDocument()
    expect(screen.getByText('fireflies')).toBeInTheDocument()
    expect(screen.getByText('Tags: meetings')).toBeInTheDocument()
    expect(screen.getByText('Sensitivity: basic')).toBeInTheDocument()
  })

  it('warns when an account has no API key set', async () => {
    listTranscriptAccounts.mockResolvedValue([account({ has_api_key: false })])
    renderWithRouter(<TranscriptsPanel />)
    expect(await screen.findByText('No API key set')).toBeInTheDocument()
  })
})

describe('TranscriptsPanel - create flow', () => {
  it('creates an account sending provider + api_key', async () => {
    const { user } = renderWithRouter(<TranscriptsPanel />)
    await screen.findByText(/No transcript accounts configured/)

    await user.click(screen.getByRole('button', { name: 'Add account' }))
    await user.type(screen.getByPlaceholderText('My Fireflies'), 'Acct')
    // provider select defaults to first provider 'fireflies'
    await user.type(passwordInputs()[0], 'secret-key')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(createTranscriptAccount).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Acct',
          provider: 'fireflies',
          api_key: 'secret-key',
          sensitivity: 'basic',
        }),
      ),
    )
  })

  it('creates a push-only account without an API key', async () => {
    const { user } = renderWithRouter(<TranscriptsPanel />)
    await screen.findByText(/No transcript accounts configured/)

    await user.click(screen.getByRole('button', { name: 'Add account' }))
    await user.type(screen.getByPlaceholderText('My Fireflies'), 'Pushed')
    await user.selectOptions(screen.getByLabelText('Provider'), 'generic')
    expect(passwordInputs()).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(createTranscriptAccount).toHaveBeenCalledTimes(1))
    const payload = createTranscriptAccount.mock.calls[0][0]
    expect(payload).toMatchObject({ name: 'Pushed', provider: 'generic' })
    expect(payload).not.toHaveProperty('api_key')
  })

  it('shows the "no providers" guard when provider list is empty', async () => {
    listTranscriptProviders.mockResolvedValue([])
    const { user } = renderWithRouter(<TranscriptsPanel />)
    await screen.findByText(/No transcript accounts configured/)

    await user.click(screen.getByRole('button', { name: 'Add account' }))
    expect(await screen.findByText(/No transcript providers are available/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  })

  it('surfaces a create error', async () => {
    createTranscriptAccount.mockRejectedValueOnce(new Error('bad key'))
    const { user } = renderWithRouter(<TranscriptsPanel />)
    await screen.findByText(/No transcript accounts configured/)

    await user.click(screen.getByRole('button', { name: 'Add account' }))
    await user.type(screen.getByPlaceholderText('My Fireflies'), 'Acct')
    await user.type(passwordInputs()[0], 'k')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('bad key')).toBeInTheDocument()
  })
})

describe('TranscriptsPanel - edit flow', () => {
  beforeEach(() => listTranscriptAccounts.mockResolvedValue([account()]))

  it('disables provider and omits api_key when left blank', async () => {
    const { user } = renderWithRouter(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const providerSelect = screen
      .getAllByRole('combobox')
      .find(el => within(el).queryByRole('option', { name: 'fireflies' }))
    expect(providerSelect).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(updateTranscriptAccount).toHaveBeenCalledTimes(1))
    const payload = updateTranscriptAccount.mock.calls[0][1]
    expect(payload).not.toHaveProperty('api_key')
    expect(payload).toMatchObject({ name: 'My Fireflies' })
  })

  it('sends api_key when a new value is entered', async () => {
    const { user } = renderWithRouter(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.type(passwordInputs()[0], 'rotated')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(updateTranscriptAccount.mock.calls[0][1]).toMatchObject({ api_key: 'rotated' }),
    )
  })
})

describe('TranscriptsPanel - row actions', () => {
  beforeEach(() => listTranscriptAccounts.mockResolvedValue([account()]))

  it('pauses an account from its row', async () => {
    const { user } = renderWithRouter(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(updateTranscriptAccount).toHaveBeenCalledWith(1, { active: false }))
  })

  it('deletes via the confirm dialog', async () => {
    const { user } = renderWithRouter(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(deleteTranscriptAccount).toHaveBeenCalledWith(1))
  })

  it('triggers a sync', async () => {
    const { user } = renderWithRouter(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('button', { name: 'Sync' }))
    await waitFor(() => expect(syncTranscriptAccount).toHaveBeenCalledWith(1))
  })

  it('triggers a full rescan', async () => {
    const { user } = renderWithRouter(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('button', { name: 'Full rescan' }))
    await waitFor(() => expect(rescanTranscriptAccount).toHaveBeenCalledWith(1))
  })

  it('disables full rescan for inactive accounts', async () => {
    listTranscriptAccounts.mockResolvedValue([account({ active: false })])
    renderWithRouter(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')
    expect(screen.getByRole('button', { name: 'Full rescan' })).toBeDisabled()
  })
})

describe('TranscriptsPanel - push ingest', () => {
  it('hides sync and rescan for push-only accounts', async () => {
    listTranscriptAccounts.mockResolvedValue([account({ provider: 'generic', polls: false, has_api_key: false })])
    renderWithRouter(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    expect(screen.queryByRole('button', { name: 'Sync' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Full rescan' })).not.toBeInTheDocument()
    expect(screen.queryByText('No API key set')).not.toBeInTheDocument()
  })

  it('generates a secret and shows it once with the URL and an example curl', async () => {
    listTranscriptAccounts.mockResolvedValue([account()])
    const { user } = renderWithRouter(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('button', { name: 'Push' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('https://mem.example/transcripts/ingest/1')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Generate secret' }))

    await waitFor(() => expect(generateTranscriptWebhookSecret).toHaveBeenCalledWith(1))
    expect(await within(dialog).findByText('fresh-secret-abcd')).toBeInTheDocument()
    expect(within(dialog).getByText(/curl .*X-Hub-Signature/s)).toBeInTheDocument()
  })

  it('removes an existing secret', async () => {
    listTranscriptAccounts.mockResolvedValue([account({ webhook_secret_hint: 'wxyz' })])
    const { user } = renderWithRouter(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('button', { name: 'Push' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/wxyz/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Rotate secret' })).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Remove secret' }))
    await user.click(within(screen.getAllByRole('dialog').at(-1)!).getByRole('button', { name: 'Remove' }))

    await waitFor(() => expect(deleteTranscriptWebhookSecret).toHaveBeenCalledWith(1))
    expect(await within(dialog).findByRole('button', { name: 'Generate secret' })).toBeInTheDocument()
  })
})
