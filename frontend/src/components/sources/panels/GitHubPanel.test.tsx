import { describe, it, expect, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithUser, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { GitHubPanel } from './GitHubPanel'

const authMe = { json: { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes: ['*'] } }

const repo = {
  id: 50,
  account_id: 4,
  owner: 'octocat',
  name: 'hello',
  repo_path: 'octocat/hello',
  track_issues: true,
  track_prs: true,
  track_comments: false,
  track_project_fields: false,
  labels_filter: [],
  state_filter: null,
  tags: [],
  check_interval: 60,
  full_sync_interval: 1440,
  last_sync_at: null,
  last_full_sync_at: null,
  active: true,
  created_at: '',
}

const account = (overrides = {}) => ({
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
  ...overrides,
})

const routes = (accounts: unknown[] = [], projects: unknown[] = []) => ({
  // account-scoped projects route must precede the broader /github/accounts list
  '/github/accounts/4/projects': { json: projects },
  '/github/accounts': { json: accounts },
  '/auth/me': authMe,
  __default: { json: {} },
})

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('GitHubPanel load states', () => {
  it('prompts to add an account in the Accounts tab when none exist', async () => {
    mockFetchRoutes(routes([]))
    renderWithUser(<GitHubPanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByText(/No GitHub accounts configured. Add a GitHub account in the Accounts tab first./)).toBeInTheDocument(),
    )
  })

  it('renders an account with no repos/projects tracked', async () => {
    mockFetchRoutes(routes([account()]))
    renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    expect(screen.getByText('No repositories tracked')).toBeInTheDocument()
    expect(screen.getByText('No projects tracked')).toBeInTheDocument()
    expect(screen.getByText('Repositories (0)')).toBeInTheDocument()
  })

  it('renders a tracked repo with its tracking badges', async () => {
    mockFetchRoutes(routes([account({ repos: [repo] })]))
    renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('octocat/hello')).toBeInTheDocument())
    expect(screen.getByText('Issues')).toBeInTheDocument()
    expect(screen.getByText('PRs')).toBeInTheDocument()
    expect(screen.queryByText('Comments')).not.toBeInTheDocument()
    expect(screen.getByText('Repositories (1)')).toBeInTheDocument()
  })

  it('shows an error state when the accounts fetch fails', async () => {
    mockFetchRoutes({ ...routes([]), '/github/accounts': { status: 500, json: {} } })
    renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument())
  })
})

describe('GitHubPanel repo actions', () => {
  it('toggles a repo enabled state with a PATCH', async () => {
    const mock = mockFetchRoutes(routes([account({ repos: [repo] })]))
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('octocat/hello')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Disable' }))
    await waitFor(() => {
      const patch = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/repos/50') && (init as RequestInit)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ active: false })
    })
  })

  it('removes a repo with a DELETE', async () => {
    const mock = mockFetchRoutes(routes([account({ repos: [repo] })]))
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('octocat/hello')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => {
      const del = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/repos/50') && (init as RequestInit)?.method === 'DELETE',
      )
      expect(del).toBeTruthy()
    })
  })

  it('syncs a repo with a POST', async () => {
    const mock = mockFetchRoutes(routes([account({ repos: [repo] })]))
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('octocat/hello')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Sync' }))
    await waitFor(() => {
      const sync = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/repos/50/sync') && (init as RequestInit)?.method === 'POST',
      )
      expect(sync).toBeTruthy()
    })
  })

  it('disables the Add Repo button when the account is inactive', async () => {
    mockFetchRoutes(routes([account({ active: false })]))
    renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Add Repo' })).toBeDisabled()
  })
})

describe('GitHubPanel add repo form', () => {
  it('lists available repos and submits add for the newly checked repo', async () => {
    // available-repos route must precede the broader /github/accounts list
    const mock = mockFetchRoutes({
      '/github/accounts/4/available-repos': {
        json: [
          { owner: 'octocat', name: 'hello', full_name: 'octocat/hello', description: 'demo', private: false, html_url: null },
        ],
      },
      ...routes([account()]),
    })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Repo' }))
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByText('octocat/hello')
    await user.click(within(dialog).getByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: /Add 1 Repository/ }))
    await waitFor(() => {
      const post = mock.mock.calls.find(
        ([url, init]) => String(url).endsWith('/accounts/4/repos') && (init as RequestInit)?.method === 'POST',
      )
      expect(post).toBeTruthy()
      expect(JSON.parse((post![1] as RequestInit).body as string)).toMatchObject({ owner: 'octocat', name: 'hello' })
    })
  })
})

describe('GitHubPanel project actions', () => {
  it('renders a tracked project with its item count', async () => {
    const project = {
      id: 70,
      account_id: 4,
      node_id: 'n1',
      number: 12,
      owner_type: 'org',
      owner_login: 'octocat',
      title: 'Roadmap',
      short_description: null,
      readme: null,
      url: 'https://github.com/orgs/octocat/projects/12',
      public: true,
      closed: false,
      fields: [],
      items_total_count: 5,
      github_created_at: null,
      github_updated_at: null,
      last_sync_at: null,
      created_at: '',
    }
    mockFetchRoutes(routes([account()], [project]))
    renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText(/Roadmap/)).toBeInTheDocument())
    expect(screen.getByText('5 items')).toBeInTheDocument()
    expect(screen.getByText('Projects (1)')).toBeInTheDocument()
  })

  it('removes a project with a DELETE', async () => {
    const project = {
      id: 70, account_id: 4, node_id: 'n1', number: 12, owner_type: 'org', owner_login: 'octocat',
      title: 'Roadmap', short_description: null, readme: null, url: 'https://x', public: true, closed: false,
      fields: [], items_total_count: 0, github_created_at: null, github_updated_at: null, last_sync_at: null, created_at: '',
    }
    const mock = mockFetchRoutes({
      '/github/projects/70': { json: {} },
      ...routes([account()], [project]),
    })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText(/Roadmap/)).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => {
      const del = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/github/projects/70') && (init as RequestInit)?.method === 'DELETE',
      )
      expect(del).toBeTruthy()
    })
  })

  it('renders a project with >3 fields showing a "+N" overflow badge', async () => {
    const project = {
      id: 71, account_id: 4, node_id: 'n2', number: 13, owner_type: 'org', owner_login: 'octocat',
      title: 'BigBoard', short_description: null, readme: null, url: 'https://x', public: true, closed: false,
      fields: [
        { id: 'f1', name: 'Status' }, { id: 'f2', name: 'Priority' },
        { id: 'f3', name: 'Size' }, { id: 'f4', name: 'Sprint' }, { id: 'f5', name: 'Owner' },
      ],
      items_total_count: 9, github_created_at: null, github_updated_at: null, last_sync_at: null, created_at: '',
    }
    mockFetchRoutes(routes([account()], [project]))
    renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText(/BigBoard/)).toBeInTheDocument())
    expect(screen.getByText('Status')).toBeInTheDocument()
    expect(screen.getByText('Size')).toBeInTheDocument()
    expect(screen.queryByText('Sprint')).not.toBeInTheDocument()
    expect(screen.getByText('+2')).toBeInTheDocument()
  })
})

const availProjectsRoute = (projects: unknown[]) => ({
  '/github/accounts/4/available-projects': { json: projects },
})

const availProject = (o = {}) => ({
  number: 5,
  title: 'Roadmap 2030',
  short_description: 'planning board',
  items_total_count: 12,
  closed: false,
  ...o,
})

describe('GitHubPanel add project form', () => {
  it('loads available projects after entering an owner and submits the selected one', async () => {
    const mock = mockFetchRoutes({
      ...availProjectsRoute([availProject(), availProject({ number: 6, title: 'Other', short_description: null })]),
      '/github/accounts/4/projects': { json: {} },
      ...routes([account()]),
    })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Project' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByPlaceholderText('e.g., my-organization'), 'octocat')
    const radioLabel = (await within(dialog).findByText(/Roadmap 2030/)).closest('label')!
    await user.click(within(radioLabel).getByRole('radio'))
    await user.click(within(dialog).getByRole('button', { name: 'Add Project' }))
    await waitFor(() => {
      const post = mock.mock.calls.find(
        ([url, init]) => String(url).endsWith('/accounts/4/projects') && (init as RequestInit)?.method === 'POST',
      )
      expect(post).toBeTruthy()
      expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({
        owner: 'octocat', project_number: 5, is_org: true,
      })
    })
  })

  it('shows a validation error when submitting without selecting a project', async () => {
    mockFetchRoutes({ ...availProjectsRoute([availProject()]), ...routes([account()]) })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Project' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByPlaceholderText('e.g., my-organization'), 'octocat')
    await within(dialog).findByText(/Roadmap 2030/)
    // The submit button is disabled until a project is selected.
    expect(within(dialog).getByRole('button', { name: 'Add Project' })).toBeDisabled()
  })

  it('filters available projects by the search box and shows no-match text', async () => {
    mockFetchRoutes({
      ...availProjectsRoute([availProject({ number: 5, title: 'Alpha' }), availProject({ number: 6, title: 'Beta', short_description: null })]),
      ...routes([account()]),
    })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Project' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByPlaceholderText('e.g., my-organization'), 'octocat')
    await within(dialog).findByText(/Alpha/)
    await user.type(within(dialog).getByPlaceholderText('Filter projects...'), 'Beta')
    await waitFor(() => expect(within(dialog).queryByText(/Alpha/)).not.toBeInTheDocument())
    expect(within(dialog).getByText(/Beta/)).toBeInTheDocument()
    await user.clear(within(dialog).getByPlaceholderText('Filter projects...'))
    await user.type(within(dialog).getByPlaceholderText('Filter projects...'), 'zzz')
    expect(await within(dialog).findByText('No matching projects')).toBeInTheDocument()
  })

  it('marks already-added projects as disabled and labels closed ones', async () => {
    const existing = {
      id: 70, account_id: 4, node_id: 'n1', number: 5, owner_type: 'org', owner_login: 'octocat',
      title: 'Roadmap 2030', short_description: null, readme: null, url: 'https://x', public: true, closed: false,
      fields: [], items_total_count: 0, github_created_at: null, github_updated_at: null, last_sync_at: null, created_at: '',
    }
    mockFetchRoutes({
      ...availProjectsRoute([availProject({ number: 5, title: 'Roadmap 2030' }), availProject({ number: 9, title: 'Archived', closed: true })]),
      ...routes([account()], [existing]),
    })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Project' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByPlaceholderText('e.g., my-organization'), 'octocat')
    const addedLabel = (await within(dialog).findByText('(already added)')).closest('label')!
    expect(within(dialog).getByText('(closed)')).toBeInTheDocument()
    expect(within(addedLabel).getByRole('radio')).toBeDisabled()
  })

  it('shows the empty-projects message when the owner has none', async () => {
    mockFetchRoutes({ ...availProjectsRoute([]), ...routes([account()]) })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Project' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByPlaceholderText('e.g., my-organization'), 'nobody')
    expect(await within(dialog).findByText('No projects found for nobody')).toBeInTheDocument()
  })

  it('shows an error when loading available projects fails', async () => {
    mockFetchRoutes({
      '/github/accounts/4/available-projects': { status: 500, json: { detail: 'boom proj' } },
      ...routes([account()]),
    })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Project' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByPlaceholderText('e.g., my-organization'), 'octocat')
    expect(await within(dialog).findByText('boom proj')).toBeInTheDocument()
  })

  it('reloads available projects when the owner type switches to User', async () => {
    const mock = mockFetchRoutes({ ...availProjectsRoute([availProject()]), ...routes([account()]) })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Project' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByPlaceholderText('e.g., my-organization'), 'octocat')
    await within(dialog).findByText(/Roadmap 2030/)
    await user.click(within(dialog).getByLabelText('User'))
    await waitFor(() => {
      const userCall = mock.mock.calls.find(
        ([url]) => String(url).includes('available-projects') && String(url).includes('is_org=false'),
      )
      expect(userCall).toBeTruthy()
    })
  })

  it('cancels the add-project form', async () => {
    mockFetchRoutes({ ...availProjectsRoute([availProject()]), ...routes([account()]) })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Project' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})

const availRepo = (o = {}) => ({
  owner: 'octocat', name: 'hello', full_name: 'octocat/hello',
  description: 'demo', private: false, html_url: null, ...o,
})

describe('GitHubPanel repo form selection helpers', () => {
  it('uses All/None to bulk-toggle and updates the monitored count', async () => {
    mockFetchRoutes({
      '/github/accounts/4/available-repos': {
        json: [availRepo(), availRepo({ name: 'world', full_name: 'octocat/world', private: true })],
      },
      ...routes([account()]),
    })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Repo' }))
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByText('octocat/hello')
    expect(within(dialog).getByText('private')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'All' }))
    expect(within(dialog).getByText('2 monitored of 2')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'None' }))
    expect(within(dialog).getByText('0 monitored of 2')).toBeInTheDocument()
  })

  it('removes a deselected existing repo via the manage form', async () => {
    const mock = mockFetchRoutes({
      '/github/accounts/4/available-repos': { json: [availRepo()] },
      '/github/accounts/4/repos/50': { json: {} },
      ...routes([account({ repos: [repo] })]),
    })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('octocat/hello')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Repo' }))
    const dialog = await screen.findByRole('dialog')
    // The existing repo is pre-checked; uncheck it to mark for removal.
    await user.click(within(dialog).getByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: /Remove 1 Repository/ }))
    await waitFor(() => {
      const del = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/repos/50') && (init as RequestInit)?.method === 'DELETE',
      )
      expect(del).toBeTruthy()
    })
  })

  it('filters repos and shows the no-match message', async () => {
    mockFetchRoutes({
      '/github/accounts/4/available-repos': {
        json: [availRepo({ name: 'alpha', full_name: 'octocat/alpha' }), availRepo({ name: 'beta', full_name: 'octocat/beta' })],
      },
      ...routes([account()]),
    })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Repo' }))
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByText('octocat/alpha')
    await user.type(within(dialog).getByPlaceholderText('Filter repositories...'), 'zzz')
    expect(await within(dialog).findByText('No matching repositories')).toBeInTheDocument()
  })

  it('shows an error when loading available repos fails', async () => {
    mockFetchRoutes({
      '/github/accounts/4/available-repos': { status: 500, json: {} },
      ...routes([account()]),
    })
    const { user } = renderWithUser(<GitHubPanel />)
    await waitFor(() => expect(screen.getByText('My GH')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Repo' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('Failed to fetch available repos')).toBeInTheDocument()
  })
})
