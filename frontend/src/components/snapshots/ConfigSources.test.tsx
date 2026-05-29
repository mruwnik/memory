import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithRouter, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import ConfigSources from './ConfigSources'

const authMe = { json: { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes: ['*'] } }

const environment = {
  id: 100,
  name: 'Dev Env',
  volume_name: 'vol-dev',
  description: 'My dev volume',
  initialized_from_snapshot_id: null,
  cloned_from_environment_id: null,
  size_bytes: 2 * 1024 * 1024,
  last_used_at: null,
  created_at: '2030-01-01T00:00:00Z',
  session_count: 3,
}

const snapshot = {
  id: 200,
  name: 'Base Snap',
  content_hash: 'abcdef0123456789ffffffff',
  claude_account_email: 'snap@example.com',
  subscription_type: 'max',
  summary: JSON.stringify({ skills: ['s1'], agents: [], plugins: [], hooks: [], commands: [], mcp_servers: ['m1', 'm2'] }),
  filename: 'snap.tar',
  size: 1500,
  created_at: '2030-01-02T00:00:00Z',
}

const routes = (envs: unknown[] = [], snaps: unknown[] = []) => ({
  '/claude/environments/list': { json: envs },
  '/claude/snapshots/list': { json: snaps },
  '/auth/me': authMe,
  __default: { json: {} },
})

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ConfigSources load + tabs', () => {
  it('shows loading then the empty environments state', async () => {
    mockFetchRoutes(routes([], []))
    renderWithRouter(<ConfigSources />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No environments yet')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /Environments \(0\)/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Snapshots \(0\)/ })).toBeInTheDocument()
  })

  it('renders an environment with its formatted size and session count', async () => {
    mockFetchRoutes(routes([environment], []))
    renderWithRouter(<ConfigSources />)
    await waitFor(() => expect(screen.getByText('Dev Env')).toBeInTheDocument())
    expect(screen.getByText('My dev volume')).toBeInTheDocument()
    expect(screen.getByText('2.0 MB')).toBeInTheDocument()
    expect(screen.getByText('3 sessions')).toBeInTheDocument()
    expect(screen.getByText('Volume: vol-dev')).toBeInTheDocument()
  })

  it('switches to the snapshots tab and renders snapshot summary counts', async () => {
    mockFetchRoutes(routes([], [snapshot]))
    const { user } = renderWithRouter(<ConfigSources />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Snapshots \(1\)/ })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Snapshots \(1\)/ }))
    expect(await screen.findByText('Base Snap')).toBeInTheDocument()
    expect(screen.getByText('max')).toBeInTheDocument()
    expect(screen.getByText('snap@example.com')).toBeInTheDocument()
    expect(screen.getByText('2 MCP servers')).toBeInTheDocument()
    expect(screen.getByText('1 skill')).toBeInTheDocument()
  })

  it('shows the load error when a fetch fails', async () => {
    mockFetchRoutes({ ...routes([], []), '/claude/environments/list': { status: 500, json: { detail: 'boom' } } })
    renderWithRouter(<ConfigSources />)
    await waitFor(() => expect(screen.getByText(/Failed to/)).toBeInTheDocument())
  })

  it('dismisses the error banner when Dismiss is clicked', async () => {
    mockFetchRoutes({ ...routes([], []), '/claude/environments/list': { status: 500, json: { detail: 'boom' } } })
    const { user } = renderWithRouter(<ConfigSources />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Dismiss' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument()
  })
})

describe('ConfigSources create environment', () => {
  it('disables Create until a name is entered, then posts the request', async () => {
    const mock = mockFetchRoutes({ ...routes([], []), '/claude/environments/create': { json: { ...environment, id: 101 } } })
    const { user } = renderWithRouter(<ConfigSources />)
    await waitFor(() => expect(screen.getByText('No environments yet')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '+ New Environment' }))
    const heading = await screen.findByText('Create Environment')
    const modal = heading.closest('div')!.parentElement as HTMLElement
    const createBtn = within(modal).getByRole('button', { name: 'Create' })
    expect(createBtn).toBeDisabled()
    await user.type(within(modal).getByPlaceholderText('My Development Environment'), 'New Env')
    expect(createBtn).not.toBeDisabled()
    await user.click(createBtn)
    await waitFor(() => {
      const post = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/claude/environments/create') && (init as RequestInit)?.method === 'POST',
      )
      expect(post).toBeTruthy()
      expect(JSON.parse((post![1] as RequestInit).body as string)).toMatchObject({ name: 'New Env' })
    })
  })
})

describe('ConfigSources delete flows', () => {
  it('deletes an environment after confirm() returns true', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const mock = mockFetchRoutes(routes([environment], []))
    const { user } = renderWithRouter(<ConfigSources />)
    await waitFor(() => expect(screen.getByText('Dev Env')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => {
      const del = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/claude/environments/100') && (init as RequestInit)?.method === 'DELETE',
      )
      expect(del).toBeTruthy()
    })
  })

  it('does not delete an environment when confirm() returns false', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const mock = mockFetchRoutes(routes([environment], []))
    const { user } = renderWithRouter(<ConfigSources />)
    await waitFor(() => expect(screen.getByText('Dev Env')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    const del = mock.mock.calls.find(
      ([url, init]) => String(url).includes('/claude/environments/100') && (init as RequestInit)?.method === 'DELETE',
    )
    expect(del).toBeFalsy()
  })

  it('deletes a snapshot via the raw fetch endpoint after confirm', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const mock = mockFetchRoutes(routes([], [snapshot]))
    const { user } = renderWithRouter(<ConfigSources />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Snapshots \(1\)/ })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Snapshots \(1\)/ }))
    await screen.findByText('Base Snap')
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => {
      const del = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/claude/snapshots/200') && (init as RequestInit)?.method === 'DELETE',
      )
      expect(del).toBeTruthy()
    })
  })
})

describe('ConfigSources reset environment', () => {
  it('opens the reset modal and posts a reset after confirm', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const mock = mockFetchRoutes({ ...routes([environment], [snapshot]), '/claude/environments/100/reset': { json: environment } })
    const { user } = renderWithRouter(<ConfigSources />)
    await waitFor(() => expect(screen.getByText('Dev Env')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect(await screen.findByRole('heading', { name: 'Reset Environment' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reset Environment' }))
    await waitFor(() => {
      const post = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/claude/environments/100/reset') && (init as RequestInit)?.method === 'POST',
      )
      expect(post).toBeTruthy()
    })
  })
})
