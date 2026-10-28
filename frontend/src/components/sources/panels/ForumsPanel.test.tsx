import { describe, it, expect, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithUser, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { ForumsPanel } from './ForumsPanel'

const me = (scopes: string[]) => ({ json: { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes } })
const admin = me(['*'])

const SETTINGS = {
  active: true,
  check_interval: 1440,
  days_back: 30,
  ingest_delay: 480,
  max_items: 1000,
  min_karma: 10,
  af: false,
  comments_enabled: true,
  min_comment_karma: 5,
  comment_recheck_divisor: 10,
  comment_recheck_max_days: 30,
  comment_max_age_days: 365,
  max_comment_fetches: 200,
}

const summary = (overrides = {}) => ({
  json: {
    posts: 24123,
    comments: 8,
    newest_post: { title: 'A new post', url: 'https://www.lesswrong.com/posts/x/a-new-post', published_at: '2026-10-20T00:00:00Z' },
    last_sync_at: null,
    last_sync_result: { new_posts: 3, updated_posts: 40, comment_fetches: 7 },
    sync_error: null,
    settings: SETTINGS,
    ...overrides,
  },
})

const form = (name: string) => within(screen.getByRole('form', { name }))
const numberInput = (formName: string, labelText: string): HTMLInputElement =>
  form(formName).getByLabelText(labelText) as HTMLInputElement

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('ForumsPanel', () => {
  it('summarises what has been synced and how syncing is set up', async () => {
    mockFetchRoutes({ '/forums/summary': summary(), '/auth/me': admin, __default: { json: {} } })
    renderWithUser(<ForumsPanel />)

    expect(await screen.findByText((24123).toLocaleString())).toBeInTheDocument()
    expect(screen.getByText('8')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /A new post/ })).toHaveAttribute('href', 'https://www.lesswrong.com/posts/x/a-new-post')
    expect(screen.getByText('Never')).toBeInTheDocument()
    expect(screen.getByText(/3 new posts · 40 updated · comments fetched for 7 posts/)).toBeInTheDocument()
    expect(screen.getByText('every 24h')).toBeInTheDocument()
  })

  it('shows why the last sync failed', async () => {
    mockFetchRoutes({ '/forums/summary': summary({ sync_error: 'LW down' }), '/auth/me': admin, __default: { json: {} } })
    renderWithUser(<ForumsPanel />)
    expect(await screen.findByText('Last sync failed: LW down')).toBeInTheDocument()
  })

  it('saves edited sync settings', async () => {
    const mock = mockFetchRoutes({
      '/forums/settings': { json: { ...SETTINGS, min_comment_karma: 12, comments_enabled: true } },
      '/forums/summary': summary(),
      '/auth/me': admin,
      __default: { json: {} },
    })
    const { user } = renderWithUser(<ForumsPanel />)
    await screen.findByRole('form', { name: 'Scheduled sync settings' })

    await user.clear(numberInput('Scheduled sync settings', 'Min comment karma'))
    await user.type(numberInput('Scheduled sync settings', 'Min comment karma'), '12')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    await waitFor(() => expect(screen.getByText('Forum sync settings saved.')).toBeInTheDocument())
    const call = mock.mock.calls.find(([url]) => String(url).includes('/forums/settings'))!
    expect((call[1] as RequestInit).method).toBe('PUT')
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({ ...SETTINGS, min_comment_karma: 12 })
  })

  it('refuses a sync interval under an hour', async () => {
    mockFetchRoutes({ '/forums/summary': summary(), '/auth/me': admin, __default: { json: {} } })
    const { user } = renderWithUser(<ForumsPanel />)
    await screen.findByRole('form', { name: 'Scheduled sync settings' })

    const every = within(form('Scheduled sync settings').getByRole('group', { name: 'Sync every' }))
    await user.clear(every.getByLabelText('Hours'))
    await user.type(every.getByLabelText('Minutes'), '30')

    expect(screen.getByText('Sync at most once an hour.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled()
  })

  it('hides the settings and manual sync from non-admins', async () => {
    mockFetchRoutes({ '/forums/summary': summary(), '/auth/me': me(['read']), __default: { json: {} } })
    renderWithUser(<ForumsPanel />)
    await screen.findByText((24123).toLocaleString())
    await waitFor(() => expect(screen.queryByRole('form')).not.toBeInTheDocument())
  })

  it('posts a manual sync with the edited settings and shows the success banner', async () => {
    const mock = mockFetchRoutes({ '/forums/sync': { json: { task_id: 'abc', status: 'queued' } }, '/forums/summary': summary(), '/auth/me': admin, __default: { json: {} } })
    const { user } = renderWithUser(<ForumsPanel />)
    await screen.findByRole('form', { name: 'Sync now' })

    await user.clear(numberInput('Sync now', 'Min karma'))
    await user.type(numberInput('Sync now', 'Min karma'), '25')
    await user.click(form('Sync now').getByLabelText('Alignment Forum only'))
    await user.click(screen.getByRole('button', { name: 'Sync LessWrong' }))

    await waitFor(() => expect(screen.getByText(/LessWrong sync started/)).toBeInTheDocument())
    const call = mock.mock.calls.find(([url]) => String(url).includes('/forums/sync'))!
    const body = JSON.parse((call[1] as RequestInit).body as string)
    expect(body).toMatchObject({ min_karma: 25, af: true, limit: 50, max_items: 1000, tags: [] })
    expect(typeof body.since).toBe('string')
  })

  it('shows the server error detail when the manual sync fails', async () => {
    mockFetchRoutes({ '/forums/sync': { status: 500, json: { detail: 'rate limited' } }, '/forums/summary': summary(), '/auth/me': admin, __default: { json: {} } })
    const { user } = renderWithUser(<ForumsPanel />)
    await user.click(await screen.findByRole('button', { name: 'Sync LessWrong' }))
    await waitFor(() => expect(screen.getByText('rate limited')).toBeInTheDocument())
  })

  it('shows a retryable error when the summary fails to load', async () => {
    mockFetchRoutes({ '/forums/summary': { status: 500, json: { detail: 'db down' } }, '/auth/me': admin, __default: { json: {} } })
    renderWithUser(<ForumsPanel />)
    expect(await screen.findByText('db down')).toBeInTheDocument()
  })
})
