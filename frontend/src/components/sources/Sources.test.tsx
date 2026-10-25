import { describe, it, expect, beforeEach, vi } from 'vitest'
import { act, screen, waitFor } from '@testing-library/react'
import { Link, useNavigate } from 'react-router-dom'
import { renderWithRouter, mockFetch, mockFetchRoutes, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'

// Stub every child panel so the container test exercises only tab/URL/context
// logic, not the panels' own fetching (those are covered in their own files).
// vi.mock factories are hoisted, so each must be fully self-contained.
vi.mock('./panels/AccountsPanel', () => ({ AccountsPanel: () => <div data-testid="panel-accounts" /> }))
vi.mock('./panels/EmailPanel', () => ({ EmailPanel: () => <div data-testid="panel-email" /> }))
vi.mock('./panels/FeedsPanel', async () => {
  const { useSourcesContext } = await import('./context')
  return {
    FeedsPanel: () => <div data-testid="panel-feeds"><button onClick={useSourcesContext().refreshCounts}>reload feeds</button></div>,
  }
})
vi.mock('./panels/GitHubPanel', () => ({ GitHubPanel: () => <div data-testid="panel-github" /> }))
vi.mock('./panels/GoogleDrivePanel', () => ({ GoogleDrivePanel: () => <div data-testid="panel-drive" /> }))
vi.mock('./panels/CalendarPanel', () => ({ CalendarPanel: () => <div data-testid="panel-calendar" /> }))
vi.mock('./panels/TranscriptsPanel', () => ({ TranscriptsPanel: () => <div data-testid="panel-transcripts" /> }))
vi.mock('./panels/BooksPanel', () => ({ BooksPanel: () => <div data-testid="panel-books" /> }))
vi.mock('./panels/ForumsPanel', () => ({ ForumsPanel: () => <div data-testid="panel-forums" /> }))
vi.mock('./panels/PhotosPanel', () => ({ PhotosPanel: () => <div data-testid="panel-photos" /> }))
vi.mock('./panels/SecretsPanel', () => ({ SecretsPanel: () => <div data-testid="panel-secrets" /> }))
vi.mock('./panels/DiscordPanel', () => ({ DiscordPanel: () => <div data-testid="panel-discord" /> }))
vi.mock('./panels/SlackPanel', () => ({ SlackPanel: () => <div data-testid="panel-slack" /> }))

// Stub the admin user selector so its own data-fetching does not interfere.
vi.mock('../common/UserSelector', async () => {
  const actual = await vi.importActual<typeof import('../common/UserSelector')>('../common/UserSelector')
  return {
    ...actual,
    default: () => <div data-testid="user-selector">user selector</div>,
  }
})

import Sources from './Sources'

const Back = () => {
  const navigate = useNavigate()
  return <button onClick={() => navigate(-1)}>back</button>
}

const meWith = (scopes: string[]) => ({
  json: { user_id: 1, name: 'Admin', email: 'a@b.c', user_type: 'human', scopes },
})

beforeEach(() => {
  clearCookies()
  setAuthCookies()
  localStorage.clear()
})

describe('Sources container', () => {
  it('renders the Accounts tab by default', async () => {
    mockFetchRoutes({ '/auth/me': meWith(['teams']), __default: { json: {} } })
    renderWithRouter(<Sources />)
    expect(screen.getByTestId('panel-accounts')).toBeInTheDocument()
    expect(screen.queryByTestId('panel-email')).not.toBeInTheDocument()
  })

  it('honours the ?tab= query param for the initial tab', async () => {
    mockFetchRoutes({ '/auth/me': meWith(['teams']), __default: { json: {} } })
    renderWithRouter(<Sources />, { initialEntries: ['/?tab=discord'] })
    expect(screen.getByTestId('panel-discord')).toBeInTheDocument()
  })

  it('falls back to Accounts for an invalid tab param', async () => {
    mockFetchRoutes({ '/auth/me': meWith(['teams']), __default: { json: {} } })
    renderWithRouter(<Sources />, { initialEntries: ['/?tab=bogus'] })
    expect(screen.getByTestId('panel-accounts')).toBeInTheDocument()
  })

  it('switches panels when a tab button is clicked', async () => {
    mockFetchRoutes({ '/auth/me': meWith(['teams']), __default: { json: {} } })
    const { user } = renderWithRouter(<Sources />)
    await user.click(screen.getByRole('tab', { name: 'RSS feeds' }))
    expect(screen.getByTestId('panel-feeds')).toBeInTheDocument()
    expect(screen.queryByTestId('panel-accounts')).not.toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Books' }))
    expect(screen.getByTestId('panel-books')).toBeInTheDocument()
  })

  it('hides the user selector for a non-admin user', async () => {
    mockFetchRoutes({ '/auth/me': meWith(['teams']), __default: { json: {} } })
    renderWithRouter(<Sources />)
    await waitFor(() => expect(screen.getByText('Sources')).toBeInTheDocument())
    expect(screen.queryByTestId('user-selector')).not.toBeInTheDocument()
  })

  it('shows the user selector for an admin user', async () => {
    mockFetchRoutes({ '/auth/me': meWith(['*']), __default: { json: {} } })
    renderWithRouter(<Sources />)
    await waitFor(() => expect(screen.getByTestId('user-selector')).toBeInTheDocument())
  })

  it('follows the URL when a link elsewhere or Back changes ?tab=', async () => {
    mockFetchRoutes({ '/auth/me': meWith(['teams']), __default: { json: {} } })
    const { user } = renderWithRouter(<><Link to="/?tab=secrets">Secrets link</Link><Back /><Sources /></>)
    await user.click(screen.getByRole('link', { name: 'Secrets link' }))
    expect(screen.getByTestId('panel-secrets')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Secrets' })).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Email' }))
    expect(screen.getByTestId('panel-email')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'back' }))
    expect(screen.getByTestId('panel-secrets')).toBeInTheDocument()
  })

  it('badges each tab with its count and flags failing ones', async () => {
    mockFetchRoutes({
      '/auth/me': meWith(['teams']),
      '/source-tabs': { json: { drive: { count: 4, failing: 2 }, email: { count: 0, failing: 0 }, books: { count: 7, failing: 0 } } },
      __default: { json: {} },
    })
    renderWithRouter(<Sources />)
    const drive = await screen.findByRole('tab', { name: 'Drive (4), has problems' })
    expect(drive).toHaveAttribute('title', '2 failing to sync')
    expect(screen.getByRole('tab', { name: 'Books (7)' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Email' })).toBeInTheDocument()
  })

  it('updates the badges when the open panel reloads after a change', async () => {
    let failing = 1
    mockFetch(async input => {
      const url = String(input)
      if (url.includes('/auth/me')) return mockResponse(meWith(['teams']))
      if (url.includes('/source-tabs')) return mockResponse({ json: { feeds: { count: failing, failing } } })
      return mockResponse({ json: {} })
    })
    const { user } = renderWithRouter(<Sources />, { initialEntries: ['/?tab=feeds'] })
    await screen.findByRole('tab', { name: 'RSS feeds (1), has problems' })
    failing = 0
    await user.click(screen.getByRole('button', { name: 'reload feeds' }))
    expect(await screen.findByRole('tab', { name: 'RSS feeds' })).toBeInTheDocument()
  })

  it('keeps the badges up when a refresh fails', async () => {
    let status = 200
    let failed = 0
    mockFetch(async input => {
      const url = String(input)
      if (url.includes('/auth/me')) return mockResponse(meWith(['teams']))
      if (url.includes('/source-tabs')) {
        if (status === 500) failed++
        return mockResponse({ status, json: { feeds: { count: 1, failing: 1 } } })
      }
      return mockResponse({ json: {} })
    })
    const { user } = renderWithRouter(<Sources />, { initialEntries: ['/?tab=feeds'] })
    await screen.findByRole('tab', { name: 'RSS feeds (1), has problems' })
    status = 500
    await user.click(screen.getByRole('button', { name: 'reload feeds' }))
    await waitFor(() => expect(failed).toBeGreaterThan(0))
    await act(async () => {})
    expect(screen.getByRole('tab', { name: 'RSS feeds (1), has problems' })).toBeInTheDocument()
  })
})
