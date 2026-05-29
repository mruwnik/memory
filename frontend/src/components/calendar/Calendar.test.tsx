import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import userEvent from '@testing-library/user-event'
import type { CalendarEvent } from '@/hooks/useCalendar'
import Calendar from './Calendar'

const getEventsForMonths = vi.fn()
const clearCache = vi.fn()
const listUsers = vi.fn()
const listPeople = vi.fn()
const hasScope = vi.fn()

vi.mock('@/hooks/useCalendar', () => ({
  useCalendar: () => ({ getEventsForMonths, clearCache }),
}))
const mockAuthUser = { id: 1, name: 'Me', email: 'me@x.com', user_type: 'human', scopes: [] }
vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({ hasScope, user: mockAuthUser, isLoading: false }),
}))
vi.mock('@/hooks/useUsers', () => ({ useUsers: () => ({ listUsers }) }))
vi.mock('@/hooks/usePeople', () => ({ usePeople: () => ({ listPeople }) }))

// Build a timed event on a given day of the *current* month so it lands in the grid.
const onDay = (day: number, o: Partial<CalendarEvent> = {}): CalendarEvent => {
  const now = new Date()
  const d = new Date(now.getFullYear(), now.getMonth(), day, 10, 0, 0)
  return {
    id: day,
    event_title: `Event ${day}`,
    start_time: d.toISOString(),
    end_time: null,
    all_day: false,
    location: null,
    calendar_name: 'Work',
    recurrence_rule: null,
    calendar_account_id: 1,
    attendees: null,
    meeting_link: null,
    ...o,
  }
}

beforeEach(() => {
  localStorage.clear()
  getEventsForMonths.mockReset().mockResolvedValue([])
  clearCache.mockReset()
  listUsers.mockReset().mockResolvedValue([])
  listPeople.mockReset().mockResolvedValue([])
  hasScope.mockReset().mockReturnValue(false)
})

describe('Calendar', () => {
  it('renders the current month/year header and weekday labels', async () => {
    renderWithRouter(<Calendar />)
    const now = new Date()
    const monthNames = ['January','February','March','April','May','June','July','August','September','October','November','December']
    await waitFor(() => expect(getEventsForMonths).toHaveBeenCalled())
    expect(screen.getByText(`${monthNames[now.getMonth()]} ${now.getFullYear()}`)).toBeInTheDocument()
    expect(screen.getByText('Mon')).toBeInTheDocument()
    expect(screen.getByText('Sun')).toBeInTheDocument()
  })

  it('shows an error banner with retry on load failure', async () => {
    getEventsForMonths.mockRejectedValueOnce(new Error('cal down'))
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(screen.getByText('cal down')).toBeInTheDocument())
    getEventsForMonths.mockResolvedValue([])
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByText('cal down')).not.toBeInTheDocument())
  })

  it('renders events into the grid', async () => {
    getEventsForMonths.mockResolvedValue([onDay(15, { event_title: 'Standup' })])
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(screen.getByText('Standup')).toBeInTheDocument())
  })

  it('navigates to the previous and next month, refetching', async () => {
    const user = userEvent.setup()
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(getEventsForMonths).toHaveBeenCalled())
    const before = getEventsForMonths.mock.calls.length
    await user.click(screen.getByRole('button', { name: '<' }))
    await waitFor(() => expect(getEventsForMonths.mock.calls.length).toBeGreaterThan(before))
  })

  it('opens an event detail modal on click', async () => {
    getEventsForMonths.mockResolvedValue([
      onDay(10, { event_title: 'Lunch', location: 'Cafe', calendar_name: 'Personal' }),
    ])
    const user = userEvent.setup()
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(screen.getByText('Lunch')).toBeInTheDocument())
    await user.click(screen.getByText('Lunch'))
    await waitFor(() => expect(screen.getByText('Location')).toBeInTheDocument())
    expect(screen.getByText('Cafe')).toBeInTheDocument()
  })

  it('shows a "+N more" button when a day has many events and opens the day modal', async () => {
    const events = Array.from({ length: 6 }, (_, i) => onDay(12, { id: 100 + i, event_title: `E${i}`, start_time: new Date(new Date().getFullYear(), new Date().getMonth(), 12, 9 + i).toISOString() }))
    getEventsForMonths.mockResolvedValue(events)
    const user = userEvent.setup()
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(screen.getByText('+2 more')).toBeInTheDocument())
    await user.click(screen.getByText('+2 more'))
    await waitFor(() => expect(screen.getByText('E5')).toBeInTheDocument())
  })

  it('filters events by calendar via the Calendars dropdown', async () => {
    getEventsForMonths.mockResolvedValue([
      onDay(8, { id: 1, event_title: 'WorkEvt', calendar_name: 'Work' }),
      onDay(9, { id: 2, event_title: 'HomeEvt', calendar_name: 'Home' }),
    ])
    const user = userEvent.setup()
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(screen.getByText('WorkEvt')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Calendars/ }))
    // Uncheck "Home"
    const homeRow = screen.getByText('Home').closest('label')!
    await user.click(within(homeRow).getByRole('checkbox'))
    await waitFor(() => expect(screen.queryByText('HomeEvt')).not.toBeInTheDocument())
    expect(screen.getByText('WorkEvt')).toBeInTheDocument()
  })

  it('shows an attendee popup with person details on attendee click', async () => {
    getEventsForMonths.mockResolvedValue([
      onDay(5, { event_title: 'Meeting', attendees: ['bob@x.com'] }),
    ])
    listPeople.mockResolvedValue([
      { id: 1, identifier: 'bob', display_name: 'Bob Smith', aliases: [], contact_info: {}, tags: ['friend'], created_at: null },
    ])
    const user = userEvent.setup()
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(screen.getByText('Meeting')).toBeInTheDocument())
    await user.click(screen.getByText('Meeting'))
    await user.click(await screen.findByRole('button', { name: 'bob@x.com' }))
    await waitFor(() => expect(screen.getByText('Bob Smith')).toBeInTheDocument())
  })

  it('shows "No profile found" when no person matches the attendee', async () => {
    getEventsForMonths.mockResolvedValue([
      onDay(5, { event_title: 'Meeting', attendees: ['ghost@x.com'] }),
    ])
    listPeople.mockResolvedValue([])
    const user = userEvent.setup()
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(screen.getByText('Meeting')).toBeInTheDocument())
    await user.click(screen.getByText('Meeting'))
    await user.click(await screen.findByRole('button', { name: 'ghost@x.com' }))
    await waitFor(() => expect(screen.getByText('No profile found for this person')).toBeInTheDocument())
  })

  it('navigates to today, refetching', async () => {
    const user = userEvent.setup()
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(getEventsForMonths).toHaveBeenCalled())
    // Move away first, then click Today.
    await user.click(screen.getByRole('button', { name: '>' }))
    const before = getEventsForMonths.mock.calls.length
    await user.click(screen.getByRole('button', { name: 'Today' }))
    await waitFor(() => expect(getEventsForMonths.mock.calls.length).toBeGreaterThan(before))
  })

  it('selects none then all calendars via the dropdown controls', async () => {
    getEventsForMonths.mockResolvedValue([
      onDay(8, { id: 1, event_title: 'WorkEvt', calendar_name: 'Work' }),
      onDay(9, { id: 2, event_title: 'HomeEvt', calendar_name: 'Home' }),
    ])
    const user = userEvent.setup()
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(screen.getByText('WorkEvt')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Calendars/ }))
    await user.click(screen.getByRole('button', { name: 'Select none' }))
    await waitFor(() => expect(screen.queryByText('WorkEvt')).not.toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    await waitFor(() => expect(screen.getByText('WorkEvt')).toBeInTheDocument())
    expect(screen.getByText('HomeEvt')).toBeInTheDocument()
  })

  it('persists the calendar selection to localStorage when deselecting', async () => {
    getEventsForMonths.mockResolvedValue([
      onDay(8, { id: 1, event_title: 'WorkEvt', calendar_name: 'Work' }),
      onDay(9, { id: 2, event_title: 'HomeEvt', calendar_name: 'Home' }),
    ])
    const user = userEvent.setup()
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(screen.getByText('WorkEvt')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Calendars/ }))
    const homeRow = screen.getByText('Home').closest('label')!
    await user.click(within(homeRow).getByRole('checkbox'))
    await waitFor(() => expect(screen.queryByText('HomeEvt')).not.toBeInTheDocument())
    const saved = JSON.parse(localStorage.getItem('calendar-enabled-calendars')!)
    expect(saved).toContain('Work')
    expect(saved).not.toContain('Home')
  })

  it('ignores a corrupt localStorage value', async () => {
    localStorage.setItem('calendar-enabled-calendars', 'not json{')
    getEventsForMonths.mockResolvedValue([onDay(8, { event_title: 'WorkEvt', calendar_name: 'Work' })])
    renderWithRouter(<Calendar />)
    // A non-array/garbage value is ignored; new calendars still get enabled.
    await waitFor(() => expect(screen.getByText('WorkEvt')).toBeInTheDocument())
  })

  it('renders an all-day event and its detail modal', async () => {
    getEventsForMonths.mockResolvedValue([
      onDay(11, {
        event_title: 'Holiday', all_day: true,
        start_time: new Date(new Date().getFullYear(), new Date().getMonth(), 11).toISOString(),
        meeting_link: 'https://meet.example/x', recurrence_rule: 'FREQ=YEARLY',
      }),
    ])
    const user = userEvent.setup()
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(screen.getByText('Holiday')).toBeInTheDocument())
    await user.click(screen.getByText('Holiday'))
    await waitFor(() => expect(screen.getByText('All day')).toBeInTheDocument())
    expect(screen.getByText('Recurring event')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Join Meeting' })).toHaveAttribute('href', 'https://meet.example/x')
  })

  it('renders a timed event detail modal with end time', async () => {
    const y = new Date().getFullYear()
    const m = new Date().getMonth()
    getEventsForMonths.mockResolvedValue([
      onDay(14, {
        event_title: 'Sync',
        start_time: new Date(y, m, 14, 13, 0).toISOString(),
        end_time: new Date(y, m, 14, 14, 0).toISOString(),
      }),
    ])
    const user = userEvent.setup()
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(screen.getByText('Sync')).toBeInTheDocument())
    await user.click(screen.getByText('Sync'))
    await waitFor(() => expect(screen.getByText('Time')).toBeInTheDocument())
    // Em-dash range between start and end time is rendered.
    expect(screen.getByText(/–/)).toBeInTheDocument()
  })

  it('deduplicates same-title all-day events across calendars', async () => {
    const day = new Date(new Date().getFullYear(), new Date().getMonth(), 20).toISOString()
    getEventsForMonths.mockResolvedValue([
      onDay(20, { id: 1, event_title: 'NewYear', all_day: true, start_time: day, calendar_name: 'A' }),
      onDay(20, { id: 2, event_title: 'NewYear', all_day: true, start_time: day, calendar_name: 'B' }),
    ])
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(screen.getAllByText('NewYear')).toHaveLength(1))
  })

  it('shows person aliases, contact info and identifier in the attendee popup', async () => {
    getEventsForMonths.mockResolvedValue([
      onDay(6, { event_title: 'Review', attendees: ['carol@x.com'] }),
    ])
    listPeople.mockResolvedValue([
      {
        id: 2, identifier: 'carol', display_name: 'Carol Jones',
        aliases: ['CJ', 'C.Jones'],
        contact_info: { email: 'carol@x.com', slack: { workspace: 'nested' } },
        tags: ['team'], created_at: null,
      },
    ])
    const user = userEvent.setup()
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(screen.getByText('Review')).toBeInTheDocument())
    await user.click(screen.getByText('Review'))
    await user.click(await screen.findByRole('button', { name: 'carol@x.com' }))
    await waitFor(() => expect(screen.getByText('Carol Jones')).toBeInTheDocument())
    expect(screen.getByText('@carol')).toBeInTheDocument()
    expect(screen.getByText('CJ')).toBeInTheDocument()
    expect(screen.getByText('Also known as')).toBeInTheDocument()
    expect(screen.getByText('team')).toBeInTheDocument()
    // string contact value rendered, nested object skipped
    expect(screen.getByText('carol@x.com', { selector: 'span' })).toBeInTheDocument()
  })
})

describe('Calendar admin user filter', () => {
  const adminUsers = [
    { id: 1, name: 'Me', email: 'me@x.com', user_type: 'human' },
    { id: 2, name: 'Other', email: 'other@x.com', user_type: 'human' },
    { id: 5, name: 'Third', email: 'third@x.com', user_type: 'human' },
    { id: 3, name: 'A Bot', email: 'bot@x.com', user_type: 'bot' },
  ]

  beforeEach(() => {
    hasScope.mockImplementation((s: string) => s === 'admin' || s === '*')
    listUsers.mockResolvedValue(adminUsers)
  })

  it('shows a Users filter listing only human users for admins', async () => {
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(listUsers).toHaveBeenCalled())
    const usersBtn = await screen.findByRole('button', { name: /Users/ })
    await userEvent.setup().click(usersBtn)
    expect(screen.getByText('Other')).toBeInTheDocument()
    expect(screen.queryByText('A Bot')).not.toBeInTheDocument()
    // current user is annotated
    expect(screen.getByText('(you)')).toBeInTheDocument()
  })

  it('defaults to only the current user as the events filter', async () => {
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(getEventsForMonths).toHaveBeenCalled())
    // Default selection passes [currentUser.id] as the filter.
    await waitFor(() => {
      const last = getEventsForMonths.mock.calls.at(-1)
      expect(last?.[2]).toEqual([1])
    })
    // The Users button reflects 1 of 3 humans enabled by default.
    expect(screen.getByRole('button', { name: /Users/ })).toHaveTextContent('(1/3)')
  })

  it('clears the cache and updates the count when toggling a user', async () => {
    const user = userEvent.setup()
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(getEventsForMonths).toHaveBeenCalled())
    await user.click(screen.getByRole('button', { name: /Users/ }))
    const otherRow = screen.getByText('Other').closest('label')!
    await user.click(within(otherRow).getByRole('checkbox'))
    expect(clearCache).toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /Users/ })).toHaveTextContent('(2/3)')
  })

  it('selects none then all users via the dropdown controls', async () => {
    renderWithRouter(<Calendar />)
    await waitFor(() => expect(getEventsForMonths).toHaveBeenCalled())
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Users/ }))
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    await user.click(screen.getByRole('button', { name: 'Select none' }))
    expect(clearCache).toHaveBeenCalled()
  })
})
