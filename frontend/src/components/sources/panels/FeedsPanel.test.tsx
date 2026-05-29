import { describe, it, expect, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithUser, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { FeedsPanel } from './FeedsPanel'

const authMe = { json: { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes: ['*'] } }

const feed = {
  id: 7,
  url: 'https://blog.example.com/feed.xml',
  title: 'Example Blog',
  description: 'A blog',
  tags: [],
  check_interval: 1440,
  last_checked_at: null,
  active: true,
  created_at: '',
  updated_at: '',
}

const field = (container: HTMLElement, labelText: string | RegExp): HTMLElement => {
  const label = within(container).getByText(labelText, { selector: 'label' })
  const control = (label.parentElement as HTMLElement).querySelector('input, select, textarea')
  if (!control) throw new Error(`No control for ${labelText}`)
  return control as HTMLElement
}

const postBody = (mock: ReturnType<typeof mockFetchRoutes>, urlPart: string, method: string) => {
  const call = mock.mock.calls.find(
    ([url, init]) => String(url).includes(urlPart) && (init as RequestInit)?.method === method,
  )
  return call ? JSON.parse((call[1] as RequestInit).body as string) : null
}

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('FeedsPanel load states', () => {
  it('shows empty state when there are no feeds', async () => {
    mockFetchRoutes({ '/article-feeds': { json: [] }, '/auth/me': authMe, __default: { json: {} } })
    renderWithUser(<FeedsPanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No RSS feeds configured')).toBeInTheDocument())
  })

  it('renders the feed title, url and description', async () => {
    mockFetchRoutes({ '/article-feeds': { json: [feed] }, '/auth/me': authMe, __default: { json: {} } })
    renderWithUser(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('Example Blog')).toBeInTheDocument())
    expect(screen.getByText('A blog')).toBeInTheDocument()
  })

  it('falls back to the url as the title when title is empty', async () => {
    mockFetchRoutes({ '/article-feeds': { json: [{ ...feed, title: null }] }, '/auth/me': authMe, __default: { json: {} } })
    renderWithUser(<FeedsPanel />)
    await waitFor(() => expect(screen.getAllByText(feed.url).length).toBeGreaterThan(0))
  })

  it('shows an error state with retry on list failure', async () => {
    mockFetchRoutes({ '/article-feeds': { status: 500, json: {} }, '/auth/me': authMe, __default: { json: {} } })
    renderWithUser(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('Failed to fetch article feeds')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

describe('FeedsPanel create flow', () => {
  it('submits a create with the entered url and default interval', async () => {
    const mock = mockFetchRoutes({ '/article-feeds': { json: [] }, '/auth/me': authMe, __default: { json: {} } })
    const { user } = renderWithUser(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('No RSS feeds configured')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Feed' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(field(dialog, 'Feed URL'), 'https://new.example.com/rss')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(postBody(mock, '/article-feeds', 'POST')).toBeTruthy())
    expect(postBody(mock, '/article-feeds', 'POST')).toMatchObject({
      url: 'https://new.example.com/rss',
      check_interval: 1440,
      active: true,
    })
  })

  it('switches the panel to its error state on create failure', async () => {
    const mock = mockFetchRoutes({ '/article-feeds': { json: [] }, '/auth/me': authMe, __default: { json: {} } })
    mock.mockImplementation(async (input, init) => {
      const url = String(input)
      const method = (init as RequestInit)?.method ?? 'GET'
      if (url.includes('/article-feeds') && method === 'POST') {
        return { ok: false, status: 422, json: async () => ({ detail: 'bad feed url' }) } as unknown as Response
      }
      return { ok: true, status: 200, json: async () => [] } as unknown as Response
    })
    const { user } = renderWithUser(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('No RSS feeds configured')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Feed' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(field(dialog, 'Feed URL'), 'https://bad')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    // handleCreate catches the error and flips the whole panel to ErrorState.
    expect(await screen.findByText('bad feed url')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

describe('FeedsPanel mutate flows', () => {
  it('toggles active with a PATCH', async () => {
    const mock = mockFetchRoutes({ '/article-feeds': { json: [feed] }, '/auth/me': authMe, __default: { json: {} } })
    const { user } = renderWithUser(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('Example Blog')).toBeInTheDocument())
    await user.click(screen.getByRole('switch'))
    await waitFor(() => expect(postBody(mock, '/article-feeds/7', 'PATCH')).toEqual({ active: false }))
  })

  it('deletes after confirmation', async () => {
    const mock = mockFetchRoutes({ '/article-feeds': { json: [feed] }, '/auth/me': authMe, __default: { json: {} } })
    const { user } = renderWithUser(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('Example Blog')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => {
      const del = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/article-feeds/7') && (init as RequestInit)?.method === 'DELETE',
      )
      expect(del).toBeTruthy()
    })
  })

  it('disables the feed url field when editing', async () => {
    mockFetchRoutes({ '/article-feeds': { json: [feed] }, '/auth/me': authMe, __default: { json: {} } })
    const { user } = renderWithUser(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('Example Blog')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const dialog = await screen.findByRole('dialog')
    expect(field(dialog, 'Feed URL')).toBeDisabled()
  })
})
