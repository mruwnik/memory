import { describe, it, expect, beforeEach, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithRouter, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'

// Stub every child panel so the container test exercises only tab/URL/context
// logic, not the panels' own fetching (those are covered in their own files).
// vi.mock factories are hoisted, so each must be fully self-contained.
vi.mock('./panels/AccountsPanel', () => ({ AccountsPanel: () => <div data-testid="panel-accounts" /> }))
vi.mock('./panels/EmailPanel', () => ({ EmailPanel: () => <div data-testid="panel-email" /> }))
vi.mock('./panels/FeedsPanel', () => ({ FeedsPanel: () => <div data-testid="panel-feeds" /> }))
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
    await user.click(screen.getByRole('button', { name: 'RSS Feeds' }))
    expect(screen.getByTestId('panel-feeds')).toBeInTheDocument()
    expect(screen.queryByTestId('panel-accounts')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Books' }))
    expect(screen.getByTestId('panel-books')).toBeInTheDocument()
  })

  it('hides the user selector for a non-admin user', async () => {
    mockFetchRoutes({ '/auth/me': meWith(['teams']), __default: { json: {} } })
    renderWithRouter(<Sources />)
    await waitFor(() => expect(screen.getByText('Manage Sources')).toBeInTheDocument())
    expect(screen.queryByTestId('user-selector')).not.toBeInTheDocument()
  })

  it('shows the user selector for an admin user', async () => {
    mockFetchRoutes({ '/auth/me': meWith(['*']), __default: { json: {} } })
    renderWithRouter(<Sources />)
    await waitFor(() => expect(screen.getByTestId('user-selector')).toBeInTheDocument())
  })
})
