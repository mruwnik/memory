import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithRouter, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { AccountsPanel } from './AccountsPanel'

vi.mock('../context', () => {
  const refreshCounts = () => {}
  return { useSourcesContext: () => ({ userId: 3, selectedUser: { type: 'user', id: 3, name: 'Other' }, refreshCounts }) }
})

const authMe = { json: { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes: ['*'] } }

const githubAccount = {
  llm_key: { choice: 'default', key_id: null, label: 'personal', provider: 'anthropic', paid_by_you: true, runs: true, can_choose_default: true },
  id: 4,
  name: 'My GH',
  verified_login: 'octocat',
  auth_type: 'pat',
  has_access_token: true,
  has_private_key: false,
  app_id: null,
  installation_id: null,
  active: true,
  last_sync_at: null,
  created_at: '',
  updated_at: '',
  repos: [],
}

const oauthConfig = { id: 1, name: 'cfg', client_id: 'abcdefghijklmnopqrstuvwxyz', project_id: 'my-proj', redirect_uris: [], created_at: '' }
const googleAccount = { id: 8, name: 'GAcc', email: 'g@gmail.com', active: true, last_sync_at: null, sync_error: null, sync_failing_since: null, folders: [] }

const routes = (opts: { github?: unknown[]; google?: unknown[]; config?: unknown } = {}) => ({
  '/github/accounts': { json: opts.github ?? [] },
  '/google-drive/accounts': { json: opts.google ?? [] },
  '/google-drive/config': opts.config === undefined
    ? { status: 404, json: { detail: 'no config' } }
    : { json: opts.config },
  '/auth/me': authMe,
  __default: { json: {} },
})

const field = (container: HTMLElement, labelText: string | RegExp): HTMLElement => {
  const label = within(container).getByText(labelText, { selector: 'label' })
  const control = (label.parentElement as HTMLElement).querySelector('input, select, textarea')
  if (!control) throw new Error(`No control for ${labelText}`)
  return control as HTMLElement
}

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('AccountsPanel load states', () => {
  it('shows the OAuth config upload prompt when no config exists', async () => {
    mockFetchRoutes(routes({}))
    renderWithRouter(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('OAuth configuration required')).toBeInTheDocument())
    // No "Connect account" button without config
    expect(screen.queryByRole('button', { name: 'Connect account' })).not.toBeInTheDocument()
  })

  it('shows the github empty state with no accounts', async () => {
    mockFetchRoutes(routes({}))
    renderWithRouter(<AccountsPanel />)
    await waitFor(() =>
      expect(screen.getByText(/No GitHub accounts configured/)).toBeInTheDocument(),
    )
  })

  it('renders a github account card and connect button once config is present', async () => {
    mockFetchRoutes(routes({ github: [githubAccount], config: oauthConfig }))
    renderWithRouter(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    expect(screen.getByText('Personal access token')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Connect account' })).toBeInTheDocument()
    expect(screen.getByText('No Google accounts connected. Connect an account to use Gmail, Calendar, or Drive.')).toBeInTheDocument()
  })

  it('renders a connected google account with disconnect/reauthorize controls', async () => {
    mockFetchRoutes(routes({ google: [googleAccount], config: oauthConfig }))
    renderWithRouter(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('GAcc')).toBeInTheDocument())
    expect(screen.getByText('g@gmail.com')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Re-authorize' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Disconnect' })).toBeInTheDocument()
  })

  it.each([
    ['github', 'GitHub accounts', 'Google accounts'],
    ['google', 'Google accounts', 'GitHub accounts'],
  ] as const)('shows only %s accounts when given that provider', async (provider, shown, hidden) => {
    mockFetchRoutes(routes({ config: oauthConfig }))
    renderWithRouter(<AccountsPanel provider={provider} />)
    expect(await screen.findByRole('heading', { name: shown })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: hidden })).not.toBeInTheDocument()
  })

  it('shows an error state when github list fails', async () => {
    mockFetchRoutes({ ...routes({}), '/github/accounts': { status: 500, json: {} } })
    renderWithRouter(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText(/Failed to fetch GitHub accounts/)).toBeInTheDocument())
  })
})

describe('AccountsPanel github form', () => {
  it('creates a PAT account with the access token in the body', async () => {
    const mock = mockFetchRoutes(routes({ config: oauthConfig }))
    const { user } = renderWithRouter(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText(/No GitHub accounts configured/)).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add account' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(field(dialog, 'Name'), 'CI Bot')
    await user.type(field(dialog, /Access token/), 'ghp_secret')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      const post = mock.mock.calls.find(
        ([url, init]) => String(url).endsWith('/github/accounts') && (init as RequestInit)?.method === 'POST',
      )
      expect(post).toBeTruthy()
      expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({
        name: 'CI Bot',
        auth_type: 'pat',
        access_token: 'ghp_secret',
      })
    })
  })

  it('reveals app fields when GitHub App auth type is selected', async () => {
    mockFetchRoutes(routes({ config: oauthConfig }))
    const { user } = renderWithRouter(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText(/No GitHub accounts configured/)).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add account' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getAllByText('Instructions')).toHaveLength(1)
    expect(within(dialog).getByText('GitHub personal access token settings')).toBeInTheDocument()
    expect(within(dialog).queryByText('GitHub Apps settings')).not.toBeInTheDocument()
    await user.selectOptions(field(dialog, 'Authentication type'), 'app')
    expect(within(dialog).getAllByText('Instructions')).toHaveLength(1)
    expect(within(dialog).queryByText('GitHub personal access token settings')).not.toBeInTheDocument()
    expect(within(dialog).getByText('GitHub Apps settings')).toBeInTheDocument()
    expect(within(dialog).getByText(/Issues → Read-only and Pull requests → Read-only/)).toBeInTheDocument()

    expect(within(dialog).getByText('App ID')).toBeInTheDocument()
    expect(within(dialog).getByText('Installation ID')).toBeInTheDocument()
    expect(within(dialog).getByText(/Private key/)).toBeInTheDocument()
  })

  it('disables the auth type select when editing', async () => {
    mockFetchRoutes(routes({ github: [githubAccount], config: oauthConfig }))
    const { user } = renderWithRouter(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const dialog = await screen.findByRole('dialog')
    expect(field(dialog, 'Authentication type')).toBeDisabled()
  })
})

describe('AccountsPanel github actions', () => {
  it('toggles active with a PATCH', async () => {
    const mock = mockFetchRoutes(routes({ github: [githubAccount], config: oauthConfig }))
    const { user } = renderWithRouter(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() => {
      const patch = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/github/accounts/4') && (init as RequestInit)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ active: false })
    })
  })

  it.each([
    [{ status: 'success', message: 'Authenticated as octo' }, 'status'],
    [{ status: 'error', message: 'Bad credentials' }, 'alert'],
  ])('shows the Validate result %j on the account row', async (result, role) => {
    // validate route must precede the broader /github/accounts list route
    mockFetchRoutes({
      '/github/accounts/4/validate': { json: result },
      ...routes({ github: [githubAccount], config: oauthConfig }),
    })
    const { user } = renderWithRouter(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Validate' }))
    expect(await screen.findByRole(role)).toHaveTextContent(result.message)
  })

  it('deletes after confirming the dialog', async () => {
    const mock = mockFetchRoutes(routes({ github: [githubAccount], config: oauthConfig }))
    const { user } = renderWithRouter(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    await waitFor(() => {
      const del = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/github/accounts/4') && (init as RequestInit)?.method === 'DELETE',
      )
      expect(del).toBeTruthy()
    })
  })
})

describe('AccountsPanel google connect', () => {
  it('opens the scope modal listing available scopes when connecting', async () => {
    mockFetchRoutes({
      ...routes({ config: oauthConfig }),
      '/google-drive/available-scopes': {
        json: {
          scopes: {
            drive: { scope: 's1', label: 'Drive', description: 'Read drive' },
            calendar: { scope: 's2', label: 'Calendar', description: 'Read calendar' },
          },
        },
      },
    })
    const { user } = renderWithRouter(<AccountsPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Connect account' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Connect account' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Drive')).toBeInTheDocument()
    expect(within(dialog).getByText('Read calendar')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Connect' })).toBeInTheDocument()
  })

  it('opens auth url and confirms with selected scopes', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    mockFetchRoutes({
      ...routes({ config: oauthConfig }),
      '/google-drive/available-scopes': {
        json: { scopes: { drive: { scope: 's1', label: 'Drive', description: 'd' } } },
      },
      '/google-drive/authorize': { json: { authorization_url: 'https://accounts.google.com/o/oauth2' } },
    })
    const { user } = renderWithRouter(<AccountsPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Connect account' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Connect account' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Connect' }))
    expect(openSpy).toHaveBeenCalledWith('', '_blank', expect.any(String))
    expect(await screen.findByRole('link', { name: 'Open Google sign-in (opens in a new tab)' })).toHaveAttribute('href', 'https://accounts.google.com/o/oauth2')
  })

  const googleRoutes = {
    '/google-drive/accounts/8/reauthorize': { json: { authorization_url: 'https://accounts.google.com/reauth' } },
    ...routes({ config: oauthConfig, google: [googleAccount] }),
    '/google-drive/available-scopes': { json: { scopes: { drive: { scope: 's1', label: 'Drive', description: 'd' } } } },
    '/google-drive/authorize': { json: { authorization_url: 'https://accounts.google.com/new' } },
  }

  it.each([
    ['Connect account', 'Connect', 'https://accounts.google.com/new'],
    ['Re-authorize', 'Update permissions', 'https://accounts.google.com/reauth'],
  ])('%s waits for the callback page\'s message, then reloads the selected user\'s accounts', async (open, confirm, authUrl) => {
    // closed: true is what a COOP-severed handle reads mid-sign-in, so it must not end the wait.
    const popup = { location: { href: '' }, closed: false, close: vi.fn() }
    vi.spyOn(window, 'open').mockImplementation(() => popup as unknown as Window)
    const mock = mockFetchRoutes(googleRoutes)
    const { user } = renderWithRouter(<AccountsPanel />)
    await user.click(await screen.findByRole('button', { name: open }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: confirm }))

    await waitFor(() => expect(popup.location.href).toBe(authUrl))
    popup.closed = true
    expect(screen.getByText(/Waiting for Google/)).toBeInTheDocument()
    const accountLoads = () => mock.mock.calls.filter(([url]) => String(url).endsWith('/google-drive/accounts?user_id=3')).length
    const before = accountLoads()

    const channel = new BroadcastChannel('google-oauth-1')
    channel.postMessage({ type: 'oauth-complete' })
    channel.close()
    await waitFor(() => expect(screen.queryByText(/Waiting for Google/)).not.toBeInTheDocument())
    await waitFor(() => expect(accountLoads()).toBe(before + 1))
  })

  it('says when it stops waiting for Google', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const popup = { location: { href: '' }, closed: false, close: vi.fn() }
    vi.spyOn(window, 'open').mockImplementation(() => popup as unknown as Window)
    mockFetchRoutes(googleRoutes)
    const { user } = renderWithRouter(<AccountsPanel />)
    await user.click(await screen.findByRole('button', { name: 'Connect account' }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Connect' }))
    await screen.findByText(/Waiting for Google/)

    await vi.advanceTimersByTimeAsync(5 * 60_000)
    expect(await screen.findByText(/Stopped waiting for Google/)).toBeInTheDocument()
  })
})
