import { useState, useEffect, useLayoutEffect, useCallback, useMemo, useRef, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useCalendar, CalendarEvent } from '@/hooks/useCalendar'
import { useAuth } from '@/hooks/useAuth'
import { useUsers, User } from '@/hooks/useUsers'
import { usePeople, Person } from '@/hooks/usePeople'
import { Badge, Button, Card, Checkbox, DetailList, LoadingState, Modal, Notice, Page, RefreshButton, buttonClass, NewTabLink } from '@/components/shared/ui'

const DAYS_OF_WEEK = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'
]

interface DayCell {
  date: Date
  isCurrentMonth: boolean
  isToday: boolean
  events: CalendarEvent[]
}

const VIEWPORT_GUTTER = 8

interface FilterMenuProps<T> {
  label: string
  options: { value: T; label: ReactNode }[]
  selected: Set<T>
  onToggle: (value: T) => void
  onAll: () => void
  onNone: () => void
}

/** A button that opens a checklist of filter values. Escape, an outside click or tabbing away closes it. */
function FilterMenu<T extends string | number>({ label, options, selected, onToggle, onAll, onNone }: FilterMenuProps<T>) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  // The menu hangs off the button's right edge, which on a phone can push it past the left of the screen.
  useLayoutEffect(() => {
    const menu = menuRef.current
    if (!menu) return
    const { left, right } = menu.getBoundingClientRect()
    const shift = Math.max(VIEWPORT_GUTTER - left, Math.min(0, window.innerWidth - VIEWPORT_GUTTER - right))
    menu.style.transform = shift ? `translateX(${shift}px)` : ''
  }, [open])

  useEffect(() => {
    if (!open) return
    menuRef.current?.querySelector<HTMLElement>('button, input')?.focus()
    const closeOutside = (e: Event) => !rootRef.current?.contains(e.target as Node) && setOpen(false)
    document.addEventListener('mousedown', closeOutside)
    document.addEventListener('focusin', closeOutside)
    return () => {
      document.removeEventListener('mousedown', closeOutside)
      document.removeEventListener('focusin', closeOutside)
    }
  }, [open])

  return (
    <div ref={rootRef} className="relative" onKeyDown={e => {
      if (e.key !== 'Escape' || !open) return
      setOpen(false)
      buttonRef.current?.focus()
    }}>
      <button ref={buttonRef} type="button" className={buttonClass('secondary')} aria-expanded={open}
        disabled={!options.length} onClick={() => setOpen(o => !o)}>
        <span>{label}</span>
        <span className="text-xs text-slate-500">({selected.size}/{options.length})</span>
      </button>
      {open && (
        <div ref={menuRef} role="group" aria-label={label}
          className="absolute right-0 top-full mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 w-72 max-w-[calc(100vw-1rem)] max-h-80 overflow-auto">
          <div className="p-2 border-b border-slate-100 flex gap-4">
            <Button variant="link" onClick={onAll}>Select all</Button>
            <Button variant="link" onClick={onNone}>Select none</Button>
          </div>
          <div className="p-2 space-y-1">
            {options.map(o => (
              <Checkbox key={o.value} checked={selected.has(o.value)} onChange={() => onToggle(o.value)}
                label={<span className="truncate">{o.label}</span>}
                className="flex w-full px-2 py-1.5 rounded hover:bg-slate-50 cursor-pointer" />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

const Calendar = () => {
  const { getEventsForMonths, clearCache } = useCalendar()
  const { hasScope, user: currentUser, isLoading: authLoading } = useAuth()
  const { listUsers } = useUsers()
  const { listPeople } = usePeople()

  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [currentDate, setCurrentDate] = useState(new Date())
  const [selectedEvent, setSelectedEvent] = useState<CalendarEvent | null>(null)
  const [selectedDayEvents, setSelectedDayEvents] = useState<{ date: Date; events: CalendarEvent[] } | null>(null)
  const [enabledCalendars, setEnabledCalendars] = useState<Set<string>>(() => {
    // Load saved selection from localStorage
    const saved = localStorage.getItem('calendar-enabled-calendars')
    if (saved) {
      try {
        const parsed = JSON.parse(saved)
        if (Array.isArray(parsed)) {
          return new Set(parsed)
        }
      } catch {
        // Ignore parse errors
      }
    }
    return new Set()
  })
  const [hasLoadedInitialCalendars, setHasLoadedInitialCalendars] = useState(false)

  // Attendee popup state
  const [selectedAttendee, setSelectedAttendee] = useState<{ email: string; person: Person | null; loading: boolean } | null>(null)

  // User filtering (admin only)
  const isAdmin = hasScope('admin') || hasScope('*')
  const [users, setUsers] = useState<User[]>([])
  const [enabledUsers, setEnabledUsers] = useState<Set<number>>(new Set())
  const [usersLoaded, setUsersLoaded] = useState(false)

  // Load users for admin
  useEffect(() => {
    if (!isAdmin) return

    const loadUsers = async () => {
      if (currentUser) setEnabledUsers(new Set([currentUser.id]))
      try {
        const userList = await listUsers()
        setUsers(userList.filter(u => u.user_type === 'human'))
      } catch {
        // Without the user list there is no Users filter; events still load for the current user.
      } finally {
        setUsersLoaded(true)
      }
    }

    loadUsers()
  }, [isAdmin, listUsers, currentUser])

  // Get userIds to filter by (undefined = all users for non-admins)
  const selectedUserIds = useMemo(() => {
    if (!isAdmin || !usersLoaded) return undefined
    // If all users are selected, don't filter (better performance)
    if (enabledUsers.size === users.length) return undefined
    return Array.from(enabledUsers)
  }, [isAdmin, usersLoaded, enabledUsers, users.length])

  const loadEvents = useCallback(async (date: Date, userIds?: number[]) => {
    setLoading(true)
    setError(null)
    try {
      const data = await getEventsForMonths(date.getFullYear(), date.getMonth(), userIds)
      setEvents(data)
      // Handle calendar selection
      const newCalendarNames = new Set(data.map(e => e.calendar_name || 'Unknown').filter(Boolean))
      setEnabledCalendars(prev => {
        // First load ever (no saved selection): enable all calendars
        if (!hasLoadedInitialCalendars && prev.size === 0 && !localStorage.getItem('calendar-enabled-calendars')) {
          return newCalendarNames
        }
        // Otherwise, just add any new calendars that weren't in the previous set
        // (preserves user's deselections while showing newly added calendars)
        const updated = new Set(prev)
        newCalendarNames.forEach(name => updated.add(name))
        return updated
      })
      setHasLoadedInitialCalendars(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load events')
    } finally {
      setLoading(false)
    }
  }, [getEventsForMonths, hasLoadedInitialCalendars])

  useEffect(() => {
    // Wait for auth to load before fetching - we need to know if user is admin
    if (authLoading) return
    // For admins, wait until user list is loaded before fetching events
    // to avoid fetching all users' events then re-fetching filtered
    if (isAdmin && !usersLoaded) return
    loadEvents(currentDate, selectedUserIds)
  }, [loadEvents, currentDate, selectedUserIds, isAdmin, usersLoaded, authLoading])

  // Persist calendar selection to localStorage
  useEffect(() => {
    if (hasLoadedInitialCalendars) {
      localStorage.setItem('calendar-enabled-calendars', JSON.stringify(Array.from(enabledCalendars)))
    }
  }, [enabledCalendars, hasLoadedInitialCalendars])

  // Get unique calendar names for filter
  const calendarNames = useMemo(() => {
    const names = new Set(events.map(e => e.calendar_name || 'Unknown'))
    return Array.from(names).sort()
  }, [events])

  // Filter events by enabled calendars
  const filteredEvents = useMemo(() => {
    return events.filter(e => enabledCalendars.has(e.calendar_name || 'Unknown'))
  }, [events, enabledCalendars])

  // Generate calendar grid for current month view
  const calendarDays = useMemo((): DayCell[] => {
    const year = currentDate.getFullYear()
    const month = currentDate.getMonth()

    // First day of the month
    const firstDay = new Date(year, month, 1)
    // Last day of the month
    const lastDay = new Date(year, month + 1, 0)

    // Get the day of week for first day (0 = Sunday, convert to Monday start)
    let startDayOfWeek = firstDay.getDay()
    startDayOfWeek = startDayOfWeek === 0 ? 6 : startDayOfWeek - 1 // Convert to Monday = 0

    const days: DayCell[] = []
    const today = new Date()
    today.setHours(0, 0, 0, 0)

    // Add days from previous month to fill the first week
    const prevMonth = new Date(year, month, 0)
    for (let i = startDayOfWeek - 1; i >= 0; i--) {
      const date = new Date(year, month - 1, prevMonth.getDate() - i)
      days.push({
        date,
        isCurrentMonth: false,
        isToday: date.getTime() === today.getTime(),
        events: getEventsForDate(date, filteredEvents),
      })
    }

    // Add days of current month
    for (let day = 1; day <= lastDay.getDate(); day++) {
      const date = new Date(year, month, day)
      days.push({
        date,
        isCurrentMonth: true,
        isToday: date.getTime() === today.getTime(),
        events: getEventsForDate(date, filteredEvents),
      })
    }

    // Add days from next month to complete the grid (6 rows)
    const remainingDays = 42 - days.length // 6 weeks * 7 days
    for (let day = 1; day <= remainingDays; day++) {
      const date = new Date(year, month + 1, day)
      days.push({
        date,
        isCurrentMonth: false,
        isToday: date.getTime() === today.getTime(),
        events: getEventsForDate(date, filteredEvents),
      })
    }

    return days
  }, [currentDate, filteredEvents])

  const goToPreviousMonth = () => {
    setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() - 1, 1))
  }

  const goToNextMonth = () => {
    setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() + 1, 1))
  }

  const goToToday = () => {
    setCurrentDate(new Date())
  }

  const toggleCalendar = (name: string) => {
    setEnabledCalendars(prev => {
      const next = new Set(prev)
      if (next.has(name)) {
        next.delete(name)
      } else {
        next.add(name)
      }
      return next
    })
  }

  const selectAllCalendars = () => setEnabledCalendars(new Set(calendarNames))
  const selectNoCalendars = () => setEnabledCalendars(new Set())

  const toggleUser = (userId: number) => {
    setEnabledUsers(prev => {
      const next = new Set(prev)
      if (next.has(userId)) {
        next.delete(userId)
      } else {
        next.add(userId)
      }
      return next
    })
    // Clear cache when user selection changes
    clearCache()
  }

  const selectAllUsers = () => {
    setEnabledUsers(new Set(users.map(u => u.id)))
    clearCache()
  }
  const selectNoUsers = () => {
    setEnabledUsers(new Set())
    clearCache()
  }

  const refreshEvents = () => {
    clearCache()
    void loadEvents(currentDate, selectedUserIds)
  }

  const formatEventTime = (event: CalendarEvent) => {
    if (event.all_day) return ''
    const date = new Date(event.start_time)
    return date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  }

  const formatDateHeader = (date: Date) => {
    return date.toLocaleDateString(undefined, {
      weekday: 'long',
      year: 'numeric',
      month: 'long',
      day: 'numeric'
    })
  }

  const handleAttendeeClick = async (email: string) => {
    setSelectedAttendee({ email, person: null, loading: true })
    try {
      // Search for person by email (matches against aliases and contact_info)
      const people = await listPeople({ search: email, limit: 1 })
      setSelectedAttendee({ email, person: people[0] || null, loading: false })
    } catch {
      setSelectedAttendee({ email, person: null, loading: false })
    }
  }

  return (
    <Page
      title="Calendar"
      description={`${MONTH_NAMES[currentDate.getMonth()]} ${currentDate.getFullYear()}`}
      actions={<>
        <div className="flex flex-wrap gap-2">
          {isAdmin && users.length > 0 && (
            <FilterMenu label="Users" selected={enabledUsers} onToggle={toggleUser} onAll={selectAllUsers} onNone={selectNoUsers}
              options={users.map(user => ({
                value: user.id,
                label: <>{user.name}{user.id === currentUser?.id && <span className="text-slate-500 ml-1">(you)</span>}</>,
              }))} />
          )}
          <FilterMenu label="Calendars" selected={enabledCalendars} onToggle={toggleCalendar}
            onAll={selectAllCalendars} onNone={selectNoCalendars}
            options={calendarNames.map(name => ({ value: name, label: name }))} />
          <div className="flex gap-2">
            <Button size="icon" onClick={goToPreviousMonth} aria-label="Previous month">&lt;</Button>
            <Button onClick={goToToday}>Today</Button>
            <RefreshButton onClick={refreshEvents} loading={loading} />
            <Button size="icon" onClick={goToNextMonth} aria-label="Next month">&gt;</Button>
          </div>
        </div>
      </>}
      width="wide"
      error={error}
      onRetry={refreshEvents}
    >
      {!loading && !error && (
        selectedUserIds?.length === 0 ? <Notice tone="info">No users selected.</Notice>
        : events.length > 0 && filteredEvents.length === 0 ? <Notice tone="info">No calendars selected.</Notice>
        : events.length === 0 && (
          <Notice tone="info">
            No events this month. <Link to="/ui/sources?tab=calendar" className="underline">Configure calendar accounts</Link>
          </Notice>
        )
      )}
      <Card flush className="relative grid grid-cols-7 overflow-hidden">
        {/* Day headers */}
        {DAYS_OF_WEEK.map(day => (
          <div key={day} aria-hidden="true" className="py-3 text-center text-sm font-semibold text-slate-600 bg-slate-50 border-b border-slate-200">
            {day}
          </div>
        ))}

        {/* Calendar cells */}
        {calendarDays.map((day, index) => (
          <div
            key={index}
            role="group"
            aria-label={day.date.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}
            className={`min-h-28 p-2 border-b border-r border-slate-100 ${
              !day.isCurrentMonth ? 'bg-slate-50/50' : 'bg-white'
            } ${day.isToday ? 'bg-primary/10' : ''}`}
          >
            <div className="mb-1">
              <span className={`inline-flex items-center justify-center w-7 h-7 text-sm font-medium rounded-full ${
                day.isToday
                  ? 'bg-primary text-white'
                  : !day.isCurrentMonth
                    ? 'text-slate-500'
                    : 'text-slate-700'
              }`}>
                {day.date.getDate()}
              </span>
            </div>
            <div className="space-y-1">
              {day.events.slice(0, 4).map((event) => (
                <button
                  type="button"
                  key={`${event.id}-${event.start_time}`}
                  className={`block w-full text-left text-xs p-1 rounded truncate focus-visible:outline-2 focus-visible:outline-primary ${
                    event.all_day
                      ? 'bg-primary/20 text-primary-dark'
                      : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                  }`}
                  title={`${event.event_title}${event.location ? ` - ${event.location}` : ''}`}
                  onClick={() => setSelectedEvent(event)}
                >
                  {!event.all_day && (
                    <span className="font-medium text-primary-dark mr-1">{formatEventTime(event)}</span>
                  )}
                  <span>{event.event_title}</span>
                </button>
              ))}
              {day.events.length > 4 && (
                <Button variant="link"
                  className="pl-1"
                  onClick={() => setSelectedDayEvents({ date: day.date, events: day.events })}
                >
                  +{day.events.length - 4} more
                </Button>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="absolute inset-0 bg-white/80 flex items-center justify-center z-10">
            <LoadingState message="Loading events..." />
          </div>
        )}
      </Card>

      {/* Day Events Modal (for +N more) */}
      {selectedDayEvents && (
        <Modal title={formatDateHeader(selectedDayEvents.date)} onClose={() => setSelectedDayEvents(null)}>
          <div className="space-y-2">
              {selectedDayEvents.events.map((event) => (
                <button
                  type="button"
                  key={`${event.id}-${event.start_time}`}
                  className={`block w-full text-left p-3 rounded-lg transition-colors focus-visible:outline-2 focus-visible:outline-primary ${
                    event.all_day
                      ? 'bg-primary/10 hover:bg-primary/20'
                      : 'bg-slate-50 hover:bg-slate-100'
                  }`}
                  onClick={() => {
                    setSelectedDayEvents(null)
                    setSelectedEvent(event)
                  }}
                >
                  <span className="flex items-start gap-3">
                    <span className="block w-16 shrink-0 text-sm">
                      {event.all_day ? (
                        <span className="text-primary-dark font-medium">All day</span>
                      ) : (
                        <span className="text-slate-600">{formatEventTime(event)}</span>
                      )}
                    </span>
                    <span className="block flex-1 min-w-0">
                      <span className="block font-medium text-slate-800">{event.event_title}</span>
                      {event.location && (
                        <span className="block text-xs text-slate-500 truncate">{event.location}</span>
                      )}
                      {event.calendar_name && (
                        <span className="block text-xs text-slate-500 mt-1">{event.calendar_name}</span>
                      )}
                    </span>
                  </span>
                </button>
              ))}
            </div>
        </Modal>
      )}

      {/* Event Detail Modal */}
      {selectedEvent && (
        <Modal title={selectedEvent.event_title} onClose={() => setSelectedEvent(null)} size="md">
          <DetailList items={[
            ['Date', formatDateHeader(eventDate(selectedEvent))],
            ['Time', selectedEvent.all_day ? 'All day' : <>
              {formatEventTime(selectedEvent)}
              {selectedEvent.end_time && <> – {formatEventTime({ ...selectedEvent, start_time: selectedEvent.end_time })}</>}
            </>],
            ['Location', selectedEvent.location],
            ['Calendar', selectedEvent.calendar_name],
            ['Repeats', selectedEvent.recurrence_rule && <Badge tone="primary">Recurring event</Badge>],
            ['Meeting', selectedEvent.meeting_link && (
              <NewTabLink href={selectedEvent.meeting_link} className="text-primary hover:underline">Join meeting</NewTabLink>
            )],
            ['Attendees', selectedEvent.attendees && selectedEvent.attendees.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {selectedEvent.attendees.map((email, i) => (
                  <Button size="sm" key={i} onClick={() => handleAttendeeClick(email)}>{email}</Button>
                ))}
              </div>
            )],
          ]} />
        </Modal>
      )}

      {/* Attendee Person Popup */}
      {selectedAttendee && (
        <Modal title={selectedAttendee.email} onClose={() => setSelectedAttendee(null)} size="sm">
          <div>
              {selectedAttendee.loading ? (
                <div className="text-center py-4 text-slate-500">Loading...</div>
              ) : selectedAttendee.person ? (
                <div className="space-y-3">
                  <div>
                    <div className="text-lg font-medium text-slate-800">{selectedAttendee.person.display_name}</div>
                    {selectedAttendee.person.identifier && (
                      <div className="text-xs text-slate-500">@{selectedAttendee.person.identifier}</div>
                    )}
                  </div>

                  {selectedAttendee.person.aliases && selectedAttendee.person.aliases.length > 0 && (
                    <div>
                      <div className="text-xs text-slate-500 mb-1">Also known as</div>
                      <div className="flex flex-wrap gap-1">
                        {selectedAttendee.person.aliases.map((alias, i) => (
                          <Badge key={i}>{alias}</Badge>
                        ))}
                      </div>
                    </div>
                  )}

                  {selectedAttendee.person.contact_info && Object.keys(selectedAttendee.person.contact_info).length > 0 && (
                    <div>
                      <div className="text-xs text-slate-500 mb-1">Contact</div>
                      <div className="space-y-1">
                        {Object.entries(selectedAttendee.person.contact_info).map(([key, value]) => {
                          // Skip complex nested objects (e.g., slack workspace data)
                          if (typeof value !== 'string') return null
                          return (
                            <div key={key} className="text-sm">
                              <span className="text-slate-500 capitalize">{key}: </span>
                              <span className="text-slate-800">{value}</span>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}

                  {selectedAttendee.person.tags && selectedAttendee.person.tags.length > 0 && (
                    <div>
                      <div className="text-xs text-slate-500 mb-1">Tags</div>
                      <div className="flex flex-wrap gap-1">
                        {selectedAttendee.person.tags.map((tag, i) => (
                          <Badge key={i} tone="primary">{tag}</Badge>
                        ))}
                      </div>
                    </div>
                  )}

                </div>
              ) : (
                <div className="text-center py-4">
                  <div className="text-slate-500 mb-2">No profile found for this person</div>
                  <Link to="/ui/people" className="text-sm text-primary hover:underline">Add them in People</Link>
                </div>
              )}
            </div>
        </Modal>
      )}

      {events.length > 0 && (
        <footer className="text-center">
          <Link to="/ui/sources?tab=calendar" className="inline-block py-2 text-primary text-sm hover:underline">
            Configure calendar accounts
          </Link>
        </footer>
      )}
    </Page>
  )
}

/** The local date an event falls on; an all-day event's stored UTC midnight names its date in UTC. */
function eventDate(event: CalendarEvent): Date {
  const start = new Date(event.start_time)
  return event.all_day ? new Date(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()) : start
}

/** Dedupe key: normalize title + date for comparison */
function getDedupeKey(event: CalendarEvent): string {
  const title = event.event_title.toLowerCase().trim()
  // For all-day events, just use the date; for timed events, include time
  if (event.all_day) return `${title}|${new Date(event.start_time).toISOString().slice(0, 10)}`
  return `${title}|${event.start_time}`
}

function getEventsForDate(date: Date, events: CalendarEvent[]): CalendarEvent[] {
  const year = date.getFullYear()
  const month = date.getMonth()
  const day = date.getDate()

  const localDay = [new Date(year, month, day).getTime(), new Date(year, month, day + 1).getTime()]
  // All-day events are stored as UTC midnight to UTC midnight, so they belong to the cell's date in UTC.
  const utcDay = [Date.UTC(year, month, day), Date.UTC(year, month, day + 1)]
  const dayEvents = events.filter(event => {
    if (!event.start_time) return false
    const [dayStart, dayEnd] = event.all_day ? utcDay : localDay
    const start = new Date(event.start_time).getTime()
    // End is exclusive; an event with no (or a zero-length) end still shows on its start day.
    const end = Math.max(event.end_time ? new Date(event.end_time).getTime() : start, start + 1)
    return start < dayEnd && end > dayStart
  })

  // Deduplicate events with same title at same time (e.g., holidays in multiple calendars)
  const seen = new Set<string>()
  const deduped = dayEvents.filter(event => {
    const key = getDedupeKey(event)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })

  return deduped.sort((a, b) => {
    // All-day events first, then by time
    if (a.all_day && !b.all_day) return -1
    if (!a.all_day && b.all_day) return 1
    return new Date(a.start_time).getTime() - new Date(b.start_time).getTime()
  })
}

export default Calendar
