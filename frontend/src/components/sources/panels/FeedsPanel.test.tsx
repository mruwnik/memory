import { describe, it, expect, beforeEach, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithRouter, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { FeedsPanel } from './FeedsPanel'
import { SourcesContext } from '../context'

const authMe = { json: { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes: ['*'] } }

const feed = {
  llm_key: { choice: 'default', key_id: null, label: 'personal', provider: 'anthropic', paid_by_you: true, runs: true, can_choose_default: true },
  id: 7,
  url: 'https://blog.example.com/feed.xml',
  title: 'Example Blog',
  description: 'A blog',
  tags: [],
  check_interval: 1440,
  ingest_delay: 0,
  last_sync_at: null,
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
    renderWithRouter(<FeedsPanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No RSS feeds configured')).toBeInTheDocument())
  })

  it('renders the feed title, url and description', async () => {
    mockFetchRoutes({ '/article-feeds': { json: [feed] }, '/auth/me': authMe, __default: { json: {} } })
    renderWithRouter(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('Example Blog')).toBeInTheDocument())
    expect(screen.getByText('A blog')).toBeInTheDocument()
    expect(screen.getByText(/Last checked:/)).toBeInTheDocument()
  })

  it('falls back to the url as the title when title is empty', async () => {
    mockFetchRoutes({ '/article-feeds': { json: [{ ...feed, title: null }] }, '/auth/me': authMe, __default: { json: {} } })
    renderWithRouter(<FeedsPanel />)
    await waitFor(() => expect(screen.getAllByText(feed.url).length).toBeGreaterThan(0))
  })

  it('shows an error state with retry on list failure', async () => {
    mockFetchRoutes({ '/article-feeds': { status: 500, json: {} }, '/auth/me': authMe, __default: { json: {} } })
    renderWithRouter(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText(/Failed to fetch article feeds/)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

describe('FeedsPanel create flow', () => {
  it('submits a create with the entered url, default interval and ingest delay', async () => {
    const mock = mockFetchRoutes({ '/article-feeds': { json: [] }, '/auth/me': authMe, __default: { json: {} } })
    const { user } = renderWithRouter(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('No RSS feeds configured')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add feed' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(field(dialog, 'Feed URL'), 'https://new.example.com/rss')
    const delayHours = within(within(dialog).getByRole('group', { name: 'Wait before ingesting' })).getByLabelText('Hours')
    await user.clear(delayHours)
    await user.type(delayHours, '72')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(postBody(mock, '/article-feeds', 'POST')).toBeTruthy())
    expect(postBody(mock, '/article-feeds', 'POST')).toMatchObject({
      url: 'https://new.example.com/rss',
      check_interval: 1440,
      ingest_delay: 72 * 60,
      active: true,
    })
  })

  const urlError = (msg: string) => [{ loc: ['body', 'url'], msg, type: 'value_error' }]
  it.each([
    ['on the Feed URL field when the server names it', urlError('URL is not allowed: non-public IP'), 'URL is not allowed: non-public IP', true],
    ['on the Feed URL field for a duplicate', urlError('Feed with this URL already exists'), 'Feed with this URL already exists', true],
    ['in a banner when it names no field, even if it mentions the URL', 'Could not parse feed from URL', 'Could not parse feed from URL', false],
  ])('shows a create failure inside the open form, %s', async (_, detail, message, onField) => {
    const mock = mockFetchRoutes({ '/article-feeds': { json: [] }, '/auth/me': authMe, __default: { json: {} } })
    mock.mockImplementation(async (input, init) => {
      const url = String(input)
      const method = (init as RequestInit)?.method ?? 'GET'
      if (url.includes('/article-feeds') && method === 'POST') {
        return { ok: false, status: 422, json: async () => ({ detail }) } as unknown as Response
      }
      return { ok: true, status: 200, json: async () => [] } as unknown as Response
    })
    const { user } = renderWithRouter(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('No RSS feeds configured')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add feed' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(field(dialog, 'Feed URL'), 'https://bad')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(await within(dialog).findByText(message)).toBeInTheDocument()
    expect(field(dialog, 'Feed URL')).toHaveValue('https://bad')
    expect(field(dialog, 'Feed URL')).toHaveAccessibleDescription(onField ? message : '')
    expect(within(dialog).queryAllByRole('alert').map(a => a.textContent)).toEqual(onField ? [] : [message])
    // Focus moves to the field so a screen reader reads the error with it.
    expect(field(dialog, 'Feed URL') === document.activeElement).toBe(onField)
  })
})

describe('FeedsPanel form labels', () => {
  it('labels the tags input, tag remove buttons and the interval fields', async () => {
    mockFetchRoutes({ '/article-feeds': { json: [] }, '/auth/me': authMe, __default: { json: {} } })
    const { user } = renderWithRouter(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('No RSS feeds configured')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add feed' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Tags'), 'news{Enter}')
    expect(within(dialog).getByRole('button', { name: 'Remove tag news' })).toBeInTheDocument()
    const interval = within(dialog).getByRole('group', { name: 'Sync every' })
    expect(within(interval).getByLabelText('Hours')).toHaveValue(24)
    expect(within(interval).getByLabelText('Minutes')).toHaveValue(0)
  })
})

describe('FeedsPanel mutate flows', () => {
  it('keeps the list on screen while it refetches after an action', async () => {
    const mock = mockFetchRoutes({ '/article-feeds': { json: [feed] }, '/auth/me': authMe, __default: { json: {} } })
    let gets = 0
    mock.mockImplementation(async (input, init) => {
      const method = (init as RequestInit)?.method ?? 'GET'
      if (String(input).includes('/article-feeds') && method === 'GET' && ++gets > 1) return new Promise<Response>(() => {})
      return { ok: true, status: 200, json: async () => String(input).includes('/article-feeds') ? [feed] : {} } as unknown as Response
    })
    const { user } = renderWithRouter(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('Example Blog')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(gets).toBe(2))
    expect(screen.getByText('Example Blog')).toBeInTheDocument()
    expect(screen.queryByText('Loading...')).not.toBeInTheDocument()
  })

  it('shows a delete failure inline and keeps the list', async () => {
    const mock = mockFetchRoutes({ '/article-feeds': { json: [feed] }, '/auth/me': authMe, __default: { json: {} } })
    mock.mockImplementation(async (input, init) => {
      if ((init as RequestInit)?.method === 'DELETE') {
        return { ok: false, status: 500, json: async () => ({ detail: 'delete broke' }) } as unknown as Response
      }
      return { ok: true, status: 200, json: async () => String(input).includes('/article-feeds') ? [feed] : {} } as unknown as Response
    })
    const { user } = renderWithRouter(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('Example Blog')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    expect(await screen.findByText(/delete broke/)).toBeInTheDocument()
    expect(screen.getByText('Example Blog')).toBeInTheDocument()
  })

  it('toggles active with a PATCH', async () => {
    const mock = mockFetchRoutes({ '/article-feeds': { json: [feed] }, '/auth/me': authMe, __default: { json: {} } })
    const { user } = renderWithRouter(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('Example Blog')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(postBody(mock, '/article-feeds/7', 'PATCH')).toEqual({ active: false }))
  })

  it('deletes after confirmation and refreshes the tab badges', async () => {
    const mock = mockFetchRoutes({ '/article-feeds': { json: [feed] }, '/auth/me': authMe, __default: { json: {} } })
    const refreshCounts = vi.fn()
    const { user } = renderWithRouter(
      <SourcesContext.Provider value={{ selectedUser: { type: 'user', id: 0, name: '' }, userId: undefined, refreshCounts }}>
        <FeedsPanel />
      </SourcesContext.Provider>,
    )
    await waitFor(() => expect(screen.getByText('Example Blog')).toBeInTheDocument())
    expect(refreshCounts).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    await waitFor(() => {
      const del = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/article-feeds/7') && (init as RequestInit)?.method === 'DELETE',
      )
      expect(del).toBeTruthy()
    })
    await waitFor(() => expect(refreshCounts).toHaveBeenCalledTimes(2))
  })

  it('disables the feed url field when editing', async () => {
    mockFetchRoutes({ '/article-feeds': { json: [feed] }, '/auth/me': authMe, __default: { json: {} } })
    const { user } = renderWithRouter(<FeedsPanel />)
    await waitFor(() => expect(screen.getByText('Example Blog')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const dialog = await screen.findByRole('dialog')
    expect(field(dialog, 'Feed URL')).toBeDisabled()
  })
})
