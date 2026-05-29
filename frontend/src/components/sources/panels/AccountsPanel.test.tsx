import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithUser, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { AccountsPanel } from './AccountsPanel'

const authMe = { json: { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes: ['*'] } }

const githubAccount = {
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
const googleAccount = { id: 8, name: 'GAcc', email: 'g@gmail.com', active: true, last_sync_at: null, sync_error: null, folders: [] }

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
  vi.restoreAllMocks()
})

describe('AccountsPanel load states', () => {
  it('shows the OAuth config upload prompt when no config exists', async () => {
    mockFetchRoutes(routes({}))
    renderWithUser(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('OAuth Configuration Required')).toBeInTheDocument())
    // No "Connect Account" button without config
    expect(screen.queryByRole('button', { name: 'Connect Account' })).not.toBeInTheDocument()
  })

  it('shows the github empty state with no accounts', async () => {
    mockFetchRoutes(routes({}))
    renderWithUser(<AccountsPanel />)
    await waitFor(() =>
      expect(screen.getByText(/No GitHub accounts configured/)).toBeInTheDocument(),
    )
  })

  it('renders a github account card and connect button once config is present', async () => {
    mockFetchRoutes(routes({ github: [githubAccount], config: oauthConfig }))
    renderWithUser(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    expect(screen.getByText('Personal Access Token')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Connect Account' })).toBeInTheDocument()
    expect(screen.getByText('No Google accounts connected. Connect an account to use Gmail, Calendar, or Drive.')).toBeInTheDocument()
  })

  it('renders a connected google account with disconnect/reauthorize controls', async () => {
    mockFetchRoutes(routes({ google: [googleAccount], config: oauthConfig }))
    renderWithUser(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('GAcc')).toBeInTheDocument())
    expect(screen.getByText('g@gmail.com')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Re-authorize' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Disconnect' })).toBeInTheDocument()
  })

  it('shows an error state when github list fails', async () => {
    mockFetchRoutes({ ...routes({}), '/github/accounts': { status: 500, json: {} } })
    renderWithUser(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('Failed to fetch GitHub accounts')).toBeInTheDocument())
  })
})

describe('AccountsPanel github form', () => {
  it('creates a PAT account with the access token in the body', async () => {
    const mock = mockFetchRoutes(routes({ config: oauthConfig }))
    const { user } = renderWithUser(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText(/No GitHub accounts configured/)).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Account' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(field(dialog, 'Name'), 'CI Bot')
    await user.type(field(dialog, /Access Token/), 'ghp_secret')
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
    const { user } = renderWithUser(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText(/No GitHub accounts configured/)).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Account' }))
    const dialog = await screen.findByRole('dialog')
    await user.selectOptions(field(dialog, 'Authentication Type'), 'app')
    expect(within(dialog).getByText('App ID')).toBeInTheDocument()
    expect(within(dialog).getByText('Installation ID')).toBeInTheDocument()
    expect(within(dialog).getByText(/Private Key/)).toBeInTheDocument()
  })

  it('disables the auth type select when editing', async () => {
    mockFetchRoutes(routes({ github: [githubAccount], config: oauthConfig }))
    const { user } = renderWithUser(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const dialog = await screen.findByRole('dialog')
    expect(field(dialog, 'Authentication Type')).toBeDisabled()
  })
})

describe('AccountsPanel github actions', () => {
  it('toggles active with a PATCH', async () => {
    const mock = mockFetchRoutes(routes({ github: [githubAccount], config: oauthConfig }))
    const { user } = renderWithUser(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('switch'))
    await waitFor(() => {
      const patch = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/github/accounts/4') && (init as RequestInit)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ active: false })
    })
  })

  it('alerts with the validation message on Validate', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    // validate route must precede the broader /github/accounts list route
    mockFetchRoutes({
      '/github/accounts/4/validate': { json: { status: 'ok', message: 'credentials valid' } },
      ...routes({ github: [githubAccount], config: oauthConfig }),
    })
    const { user } = renderWithUser(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Validate' }))
    await waitFor(() => expect(alertSpy).toHaveBeenCalledWith('credentials valid'))
  })

  it('deletes after confirming the dialog', async () => {
    const mock = mockFetchRoutes(routes({ github: [githubAccount], config: oauthConfig }))
    const { user } = renderWithUser(<AccountsPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
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
    const { user } = renderWithUser(<AccountsPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Connect Account' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Connect Account' }))
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
    const { user } = renderWithUser(<AccountsPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Connect Account' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Connect Account' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Connect' }))
    await waitFor(() =>
      expect(openSpy).toHaveBeenCalledWith('https://accounts.google.com/o/oauth2', '_blank', expect.any(String)),
    )
  })
})
