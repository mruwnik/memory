import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithUser, screen, waitFor, within } from '@/test/utils'

const listTranscriptAccounts = vi.fn()
const createTranscriptAccount = vi.fn()
const updateTranscriptAccount = vi.fn()
const deleteTranscriptAccount = vi.fn()
const syncTranscriptAccount = vi.fn()
const rescanTranscriptAccount = vi.fn()
const listTranscriptProviders = vi.fn()
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
  id: 1,
  name: 'My Fireflies',
  provider: 'fireflies',
  has_api_key: true,
  has_webhook_secret: false,
  tags: ['meetings'],
  last_sync_at: null,
  sync_error: null,
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
  listTranscriptProviders.mockResolvedValue(['fireflies', 'otter'])
  listProjects.mockResolvedValue([project])
  createTranscriptAccount.mockResolvedValue(account())
  updateTranscriptAccount.mockResolvedValue(account())
  deleteTranscriptAccount.mockResolvedValue(undefined)
  syncTranscriptAccount.mockResolvedValue({ task_id: 't', status: 'queued' })
  rescanTranscriptAccount.mockResolvedValue({ task_id: 't', status: 'queued' })
})

describe('TranscriptsPanel - load states', () => {
  it('shows loading first', () => {
    listTranscriptAccounts.mockReturnValue(new Promise(() => {}))
    renderWithUser(<TranscriptsPanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('passes the selected userId when listing accounts', async () => {
    renderWithUser(<TranscriptsPanel />)
    await waitFor(() => expect(listTranscriptAccounts).toHaveBeenCalledWith(7))
  })

  it('shows empty state when no accounts', async () => {
    renderWithUser(<TranscriptsPanel />)
    expect(await screen.findByText(/No transcript accounts configured/)).toBeInTheDocument()
  })

  it('shows error state and retries', async () => {
    listTranscriptAccounts.mockRejectedValueOnce(new Error('nope'))
    const { user } = renderWithUser(<TranscriptsPanel />)
    expect(await screen.findByText('nope')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/No transcript accounts configured/)).toBeInTheDocument()
  })

  it('renders a populated account card with provider, tags and sensitivity', async () => {
    listTranscriptAccounts.mockResolvedValue([account()])
    renderWithUser(<TranscriptsPanel />)
    expect(await screen.findByText('My Fireflies')).toBeInTheDocument()
    expect(screen.getByText('Provider: fireflies')).toBeInTheDocument()
    expect(screen.getByText('Tags: meetings')).toBeInTheDocument()
    expect(screen.getByText('Sensitivity: basic')).toBeInTheDocument()
  })

  it('warns when an account has no API key set', async () => {
    listTranscriptAccounts.mockResolvedValue([account({ has_api_key: false })])
    renderWithUser(<TranscriptsPanel />)
    expect(await screen.findByText('No API key set')).toBeInTheDocument()
  })
})

describe('TranscriptsPanel - create flow', () => {
  it('creates an account sending provider + api_key + optional webhook secret', async () => {
    const { user } = renderWithUser(<TranscriptsPanel />)
    await screen.findByText(/No transcript accounts configured/)

    await user.click(screen.getByRole('button', { name: 'Add Account' }))
    await user.type(screen.getByPlaceholderText('My Fireflies'), 'Acct')
    // provider select defaults to first provider 'fireflies'
    const [apiKey, webhook] = passwordInputs()
    await user.type(apiKey, 'secret-key')
    await user.type(webhook, 'hook')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(createTranscriptAccount).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Acct',
          provider: 'fireflies',
          api_key: 'secret-key',
          webhook_secret: 'hook',
          sensitivity: 'basic',
        }),
      ),
    )
  })

  it('shows the "no providers" guard when provider list is empty', async () => {
    listTranscriptProviders.mockResolvedValue([])
    const { user } = renderWithUser(<TranscriptsPanel />)
    await screen.findByText(/No transcript accounts configured/)

    await user.click(screen.getByRole('button', { name: 'Add Account' }))
    expect(await screen.findByText(/No transcript providers are available/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  })

  it('surfaces a create error', async () => {
    createTranscriptAccount.mockRejectedValueOnce(new Error('bad key'))
    const { user } = renderWithUser(<TranscriptsPanel />)
    await screen.findByText(/No transcript accounts configured/)

    await user.click(screen.getByRole('button', { name: 'Add Account' }))
    await user.type(screen.getByPlaceholderText('My Fireflies'), 'Acct')
    await user.type(passwordInputs()[0], 'k')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('bad key')).toBeInTheDocument()
  })
})

describe('TranscriptsPanel - edit flow', () => {
  beforeEach(() => listTranscriptAccounts.mockResolvedValue([account({ has_webhook_secret: true })]))

  it('disables provider and omits api_key/webhook when left blank', async () => {
    const { user } = renderWithUser(<TranscriptsPanel />)
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
    expect(payload).not.toHaveProperty('webhook_secret')
    expect(payload).toMatchObject({ name: 'My Fireflies' })
  })

  it('sends api_key when a new value is entered', async () => {
    const { user } = renderWithUser(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.type(passwordInputs()[0], 'rotated')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(updateTranscriptAccount.mock.calls[0][1]).toMatchObject({ api_key: 'rotated' }),
    )
  })

  it('clears the webhook secret when the remove checkbox is ticked', async () => {
    const { user } = renderWithUser(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.click(screen.getByLabelText('Remove existing webhook secret'))
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(updateTranscriptAccount.mock.calls[0][1]).toMatchObject({ webhook_secret: '' }),
    )
  })
})

describe('TranscriptsPanel - row actions', () => {
  beforeEach(() => listTranscriptAccounts.mockResolvedValue([account()]))

  it('toggles active state via SourceCard status badge', async () => {
    const { user } = renderWithUser(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('switch'))
    await waitFor(() => expect(updateTranscriptAccount).toHaveBeenCalledWith(1, { active: false }))
  })

  it('deletes via the confirm dialog', async () => {
    const { user } = renderWithUser(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(deleteTranscriptAccount).toHaveBeenCalledWith(1))
  })

  it('triggers a sync', async () => {
    const { user } = renderWithUser(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('button', { name: 'Sync' }))
    await waitFor(() => expect(syncTranscriptAccount).toHaveBeenCalledWith(1))
  })

  it('triggers a full rescan', async () => {
    const { user } = renderWithUser(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')

    await user.click(screen.getByRole('button', { name: 'Full rescan' }))
    await waitFor(() => expect(rescanTranscriptAccount).toHaveBeenCalledWith(1))
  })

  it('disables full rescan for inactive accounts', async () => {
    listTranscriptAccounts.mockResolvedValue([account({ active: false })])
    renderWithUser(<TranscriptsPanel />)
    await screen.findByText('My Fireflies')
    expect(screen.getByRole('button', { name: 'Full rescan' })).toBeDisabled()
  })
})
