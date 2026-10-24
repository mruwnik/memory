import { useState, useEffect, useLayoutEffect, useCallback, useMemo, useRef, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useCalendar, CalendarEvent } from '@/hooks/useCalendar'
import { useAuth } from '@/hooks/useAuth'
import { useUsers, User } from '@/hooks/useUsers'
import { usePeople, Person } from '@/hooks/usePeople'
import { Button, Card, Checkbox, LoadingState, Modal, Notice, Page, buttonClass } from '@/components/shared/ui'

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
      try {
        const userList = await listUsers()
        // Only show human users
        const humanUsers = userList.filter(u => u.user_type === 'human')
        setUsers(humanUsers)
        // Default: only current user enabled
        if (currentUser) {
          setEnabledUsers(new Set([currentUser.id]))
        }
        setUsersLoaded(true)
      } catch {
        // Silently fail - user filter just won't appear
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

  const formatEventTime = (event: CalendarEvent) => {
    if (event.all_day) return ''
    const date = new Date(event.start_time)
    return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }).replace(' ', '')
  }

  const formatDateHeader = (date: Date) => {
    return date.toLocaleDateString('en-US', {
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
            <Button size="icon" onClick={goToNextMonth} aria-label="Next month">&gt;</Button>
          </div>
        </div>
      </>}
      width="wide"
      error={error}
      onRetry={() => loadEvents(currentDate, selectedUserIds)}
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
          <div key={day} className="py-3 text-center text-sm font-semibold text-slate-600 bg-slate-50 border-b border-slate-200">
            {day}
          </div>
        ))}

        {/* Calendar cells */}
        {calendarDays.map((day, index) => (
          <div
            key={index}
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
                <div
                  key={`${event.id}-${event.start_time}`}
                  className={`text-xs p-1 rounded truncate cursor-pointer ${
                    event.all_day
                      ? 'bg-primary/20 text-primary'
                      : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                  }`}
                  title={`${event.event_title}${event.location ? ` - ${event.location}` : ''}`}
                  onClick={(e) => {
                    e.stopPropagation()
                    setSelectedEvent(event)
                  }}
                >
                  {!event.all_day && (
                    <span className="font-medium text-primary mr-1">{formatEventTime(event)}</span>
                  )}
                  <span>{event.event_title}</span>
                </div>
              ))}
              {day.events.length > 4 && (
                <Button variant="link"
                  className="pl-1"
                  onClick={(e) => {
                    e.stopPropagation()
                    setSelectedDayEvents({ date: day.date, events: day.events })
                  }}
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
                <div
                  key={`${event.id}-${event.start_time}`}
                  className={`p-3 rounded-lg cursor-pointer transition-colors ${
                    event.all_day
                      ? 'bg-primary/10 hover:bg-primary/20'
                      : 'bg-slate-50 hover:bg-slate-100'
                  }`}
                  onClick={() => {
                    setSelectedDayEvents(null)
                    setSelectedEvent(event)
                  }}
                >
                  <div className="flex items-start gap-3">
                    <div className="w-16 shrink-0 text-sm">
                      {event.all_day ? (
                        <span className="text-primary font-medium">All day</span>
                      ) : (
                        <span className="text-slate-600">{formatEventTime(event)}</span>
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-slate-800">{event.event_title}</div>
                      {event.location && (
                        <div className="text-xs text-slate-500 truncate">{event.location}</div>
                      )}
                      {event.calendar_name && (
                        <div className="text-xs text-slate-500 mt-1">{event.calendar_name}</div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
        </Modal>
      )}

      {/* Event Detail Modal */}
      {selectedEvent && (
        <Modal title={selectedEvent.event_title} onClose={() => setSelectedEvent(null)} size="md">
          <div className="space-y-4">
              <div className="flex">
                <span className="w-24 text-sm text-slate-500 shrink-0">Date</span>
                <span className="text-sm text-slate-800">
                  {new Date(selectedEvent.start_time).toLocaleDateString('en-US', {
                    weekday: 'long',
                    year: 'numeric',
                    month: 'long',
                    day: 'numeric'
                  })}
                </span>
              </div>

              <div className="flex">
                <span className="w-24 text-sm text-slate-500 shrink-0">Time</span>
                <span className="text-sm text-slate-800">
                  {selectedEvent.all_day ? 'All day' : (
                    <>
                      {new Date(selectedEvent.start_time).toLocaleTimeString('en-US', {
                        hour: 'numeric',
                        minute: '2-digit'
                      })}
                      {selectedEvent.end_time && (
                        <> – {new Date(selectedEvent.end_time).toLocaleTimeString('en-US', {
                          hour: 'numeric',
                          minute: '2-digit'
                        })}</>
                      )}
                    </>
                  )}
                </span>
              </div>

              {selectedEvent.location && (
                <div className="flex">
                  <span className="w-24 text-sm text-slate-500 shrink-0">Location</span>
                  <span className="text-sm text-slate-800">{selectedEvent.location}</span>
                </div>
              )}

              {selectedEvent.calendar_name && (
                <div className="flex">
                  <span className="w-24 text-sm text-slate-500 shrink-0">Calendar</span>
                  <span className="text-sm text-slate-800">{selectedEvent.calendar_name}</span>
                </div>
              )}

              {selectedEvent.recurrence_rule && (
                <div className="flex">
                  <span className="w-24 text-sm text-slate-500 shrink-0">Repeats</span>
                  <span className="text-xs bg-primary/10 text-primary px-2 py-1 rounded">Recurring event</span>
                </div>
              )}

              {selectedEvent.meeting_link && (
                <div className="flex">
                  <span className="w-24 text-sm text-slate-500 shrink-0">Meeting</span>
                  <a
                    href={selectedEvent.meeting_link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm text-primary hover:underline"
                  >
                    Join meeting
                  </a>
                </div>
              )}

              {selectedEvent.attendees && selectedEvent.attendees.length > 0 && (
                <div className="flex">
                  <span className="w-24 text-sm text-slate-500 shrink-0">Attendees</span>
                  <div className="flex flex-wrap gap-1">
                    {selectedEvent.attendees.map((email, i) => (
                      <Button size="sm"
                        key={i}
                        onClick={() => handleAttendeeClick(email)}
                      >
                        {email}
                      </Button>
                    ))}
                  </div>
                </div>
              )}
            </div>
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
                          <span key={i} className="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded">
                            {alias}
                          </span>
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
                          <span key={i} className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded">
                            {tag}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                </div>
              ) : (
                <div className="text-center py-4">
                  <div className="text-slate-500 mb-2">No profile found for this person</div>
                  <div className="text-xs text-slate-500">You can add them to your contacts via the People section</div>
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

/** Dedupe key: normalize title + date for comparison */
function getDedupeKey(event: CalendarEvent): string {
  const title = event.event_title.toLowerCase().trim()
  const eventDate = new Date(event.start_time)
  // For all-day events, just use the date; for timed events, include time
  if (event.all_day) {
    return `${title}|${eventDate.getFullYear()}-${eventDate.getMonth()}-${eventDate.getDate()}`
  }
  return `${title}|${event.start_time}`
}

function getEventsForDate(date: Date, events: CalendarEvent[]): CalendarEvent[] {
  const year = date.getFullYear()
  const month = date.getMonth()
  const day = date.getDate()

  const dayEvents = events.filter(event => {
    if (!event.start_time) return false
    try {
      const eventDate = new Date(event.start_time)
      return eventDate.getFullYear() === year &&
             eventDate.getMonth() === month &&
             eventDate.getDate() === day
    } catch {
      return false
    }
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
