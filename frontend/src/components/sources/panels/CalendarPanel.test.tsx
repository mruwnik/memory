import { describe, it, expect, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithUser, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { CalendarPanel } from './CalendarPanel'

const authMe = { json: { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes: ['*'] } }

const caldavAccount = {
  id: 2,
  name: 'My CalDAV',
  calendar_type: 'caldav',
  caldav_url: 'https://dav.example.com/cal/',
  caldav_username: 'me',
  google_account_id: null,
  google_account: null,
  calendar_ids: [],
  tags: [],
  check_interval: 15,
  sync_past_days: 30,
  sync_future_days: 90,
  last_sync_at: null,
  sync_error: null,
  active: true,
  created_at: '',
  updated_at: '',
  project_id: null,
  sensitivity: 'basic',
}

const gmailAccount = { id: 9, name: 'GA', email: 'cal@gmail.com', active: true, last_sync_at: null, sync_error: null, folders: [] }

// mcp content maps via JSON.parse then hooks take [0]
const mcp = (payload: unknown) => ({
  json: { jsonrpc: '2.0', id: 1, result: { content: [{ text: JSON.stringify(payload) }] } },
})

const routes = (accounts: unknown[] = [], events: unknown[] = []) => ({
  '/calendar-accounts': { json: accounts },
  '/google-drive/accounts': { json: [gmailAccount] },
  '/mcp/projects_list_all': mcp({ projects: [], count: 0 }),
  '/mcp/organizer_upcoming': mcp(events),
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

describe('CalendarPanel load states', () => {
  it('shows the empty state when no calendar accounts exist', async () => {
    mockFetchRoutes(routes([]))
    renderWithUser(<CalendarPanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No calendar accounts configured')).toBeInTheDocument())
  })

  it('renders a CalDAV account with its server url', async () => {
    mockFetchRoutes(routes([caldavAccount]))
    renderWithUser(<CalendarPanel />)
    await waitFor(() => expect(screen.getByText('My CalDAV')).toBeInTheDocument())
    expect(screen.getByText('CalDAV: https://dav.example.com/cal/')).toBeInTheDocument()
    expect(screen.getByText('No events synced yet')).toBeInTheDocument()
  })

  it('renders an error state when the list fetch fails', async () => {
    mockFetchRoutes({ ...routes([]), '/calendar-accounts': { status: 500, json: {} } })
    renderWithUser(<CalendarPanel />)
    await waitFor(() => expect(screen.getByText('Failed to fetch calendar accounts')).toBeInTheDocument())
  })

  it('groups events under a collapsible calendar and expands on click', async () => {
    const event = {
      id: 11,
      event_title: 'Standup',
      start_time: '2030-01-01T09:00:00Z',
      end_time: null,
      all_day: false,
      location: 'Zoom',
      calendar_name: 'Work',
      recurrence_rule: null,
      calendar_account_id: 2,
      attendees: null,
      meeting_link: null,
    }
    mockFetchRoutes(routes([caldavAccount], [event]))
    const { user } = renderWithUser(<CalendarPanel />)
    await waitFor(() => expect(screen.getByText('My CalDAV')).toBeInTheDocument())
    const calendarToggle = await screen.findByText('Work')
    expect(screen.queryByText('Standup')).not.toBeInTheDocument()
    await user.click(calendarToggle)
    await waitFor(() => expect(screen.getByText('Standup')).toBeInTheDocument())
    expect(screen.getByText('Zoom')).toBeInTheDocument()
  })
})

describe('CalendarPanel create flow', () => {
  it('submits a caldav create with credentials', async () => {
    const mock = mockFetchRoutes(routes([]))
    const { user } = renderWithUser(<CalendarPanel />)
    await waitFor(() => expect(screen.getByText('No calendar accounts configured')).toBeInTheDocument())
    await user.click(screen.getAllByRole('button', { name: 'Add Calendar' })[0])
    const dialog = await screen.findByRole('dialog')
    await user.type(field(dialog, 'Name'), 'Home')
    await user.type(field(dialog, 'CalDAV Server URL'), 'https://dav.home/cal/')
    await user.type(field(dialog, 'Username'), 'homeuser')
    await user.type(field(dialog, /Password/), 'pw')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      const post = mock.mock.calls.find(
        ([url, init]) => String(url).endsWith('/calendar-accounts') && (init as RequestInit)?.method === 'POST',
      )
      expect(post).toBeTruthy()
    })
    const post = mock.mock.calls.find(
      ([url, init]) => String(url).endsWith('/calendar-accounts') && (init as RequestInit)?.method === 'POST',
    )!
    expect(JSON.parse((post[1] as RequestInit).body as string)).toMatchObject({
      name: 'Home',
      calendar_type: 'caldav',
      caldav_url: 'https://dav.home/cal/',
      caldav_username: 'homeuser',
      caldav_password: 'pw',
    })
  })

  it('shows the google account selector and requires a choice for google type', async () => {
    mockFetchRoutes(routes([]))
    const { user } = renderWithUser(<CalendarPanel />)
    await waitFor(() => expect(screen.getByText('No calendar accounts configured')).toBeInTheDocument())
    await user.click(screen.getAllByRole('button', { name: 'Add Calendar' })[0])
    const dialog = await screen.findByRole('dialog')
    await user.selectOptions(field(dialog, 'Calendar Type'), 'google')
    expect(within(dialog).getByText('cal@gmail.com')).toBeInTheDocument()
  })
})

describe('CalendarPanel mutate flows', () => {
  it('toggles active via the status switch', async () => {
    const mock = mockFetchRoutes(routes([caldavAccount]))
    const { user } = renderWithUser(<CalendarPanel />)
    await waitFor(() => expect(screen.getByText('My CalDAV')).toBeInTheDocument())
    await user.click(screen.getByRole('switch'))
    await waitFor(() => {
      const patch = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/calendar-accounts/2') && (init as RequestInit)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ active: false })
    })
  })

  it('deletes the account immediately when Delete is clicked', async () => {
    const mock = mockFetchRoutes(routes([caldavAccount]))
    const { user } = renderWithUser(<CalendarPanel />)
    await waitFor(() => expect(screen.getByText('My CalDAV')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => {
      const del = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/calendar-accounts/2') && (init as RequestInit)?.method === 'DELETE',
      )
      expect(del).toBeTruthy()
    })
  })

  it('triggers a sync POST', async () => {
    const mock = mockFetchRoutes(routes([caldavAccount]))
    const { user } = renderWithUser(<CalendarPanel />)
    await waitFor(() => expect(screen.getByText('My CalDAV')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Sync' }))
    await waitFor(() => {
      const sync = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/calendar-accounts/2/sync') && (init as RequestInit)?.method === 'POST',
      )
      expect(sync).toBeTruthy()
    })
  })
})
