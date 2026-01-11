import { useState, useEffect, useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useCalendar, CalendarEvent } from '@/hooks/useCalendar'

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

const Calendar = () => {
  const { getUpcomingEvents } = useCalendar()
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [currentDate, setCurrentDate] = useState(new Date())
  const [selectedEvent, setSelectedEvent] = useState<CalendarEvent | null>(null)

  const loadEvents = useCallback(async (date: Date) => {
    setLoading(true)
    setError(null)
    try {
      // Calculate range for the month view (include overflow days)
      const year = date.getFullYear()
      const month = date.getMonth()
      // Start from first day of previous month (for overflow)
      const startDate = new Date(year, month - 1, 1)
      // End at last day of next month (for overflow)
      const endDate = new Date(year, month + 2, 0)

      const data = await getUpcomingEvents({
        startDate: startDate.toISOString(),
        endDate: endDate.toISOString(),
        limit: 200,
      })
      setEvents(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load events')
    } finally {
      setLoading(false)
    }
  }, [getUpcomingEvents])

  useEffect(() => {
    loadEvents(currentDate)
  }, [loadEvents, currentDate])

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
        events: getEventsForDate(date, events),
      })
    }

    // Add days of current month
    for (let day = 1; day <= lastDay.getDate(); day++) {
      const date = new Date(year, month, day)
      days.push({
        date,
        isCurrentMonth: true,
        isToday: date.getTime() === today.getTime(),
        events: getEventsForDate(date, events),
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
        events: getEventsForDate(date, events),
      })
    }

    return days
  }, [currentDate, events])

  const goToPreviousMonth = () => {
    setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() - 1, 1))
  }

  const goToNextMonth = () => {
    setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() + 1, 1))
  }

  const goToToday = () => {
    setCurrentDate(new Date())
  }

  const formatEventTime = (event: CalendarEvent) => {
    if (event.all_day) return ''
    const date = new Date(event.start_time)
    return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }).replace(' ', '')
  }

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <header className="flex items-center gap-4 mb-6 pb-4 border-b border-slate-200">
        <Link to="/ui/dashboard" className="bg-slate-50 text-slate-600 border border-slate-200 py-2 px-4 rounded-md text-sm hover:bg-slate-100">
          Back
        </Link>
        <h1 className="text-2xl font-semibold text-slate-800 flex-1">
          {MONTH_NAMES[currentDate.getMonth()]} {currentDate.getFullYear()}
        </h1>
        <div className="flex gap-2">
          <button onClick={goToPreviousMonth} className="w-9 h-9 bg-white border border-slate-200 rounded-md hover:bg-slate-50">&lt;</button>
          <button onClick={goToToday} className="px-4 h-9 bg-primary text-white rounded-md hover:bg-primary-dark">Today</button>
          <button onClick={goToNextMonth} className="w-9 h-9 bg-white border border-slate-200 rounded-md hover:bg-slate-50">&gt;</button>
        </div>
      </header>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg mb-4 flex justify-between items-center">
          <p>{error}</p>
          <button onClick={() => loadEvents(currentDate)} className="text-primary hover:underline">Retry</button>
        </div>
      )}

      <div className="grid grid-cols-7 bg-white rounded-xl shadow-md overflow-hidden">
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
                    ? 'text-slate-400'
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
                <div className="text-xs text-slate-500 pl-1">+{day.events.length - 4} more</div>
              )}
            </div>
          </div>
        ))}
      </div>

      {loading && (
        <div className="fixed inset-0 bg-white/80 flex items-center justify-center z-10">
          <div className="text-slate-600">Loading events...</div>
        </div>
      )}

      {/* Event Detail Modal */}
      {selectedEvent && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
          onClick={() => setSelectedEvent(null)}
        >
          <div
            className="bg-white rounded-xl shadow-xl max-w-md w-full max-h-[80vh] overflow-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between p-6 border-b border-slate-100">
              <h2 className="text-xl font-semibold text-slate-800 pr-4">{selectedEvent.event_title}</h2>
              <button
                className="text-slate-400 hover:text-slate-600 text-2xl leading-none"
                onClick={() => setSelectedEvent(null)}
              >
                &times;
              </button>
            </div>
            <div className="p-6 space-y-4">
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
                    Join Meeting
                  </a>
                </div>
              )}

              {selectedEvent.attendees && selectedEvent.attendees.length > 0 && (
                <div className="flex">
                  <span className="w-24 text-sm text-slate-500 shrink-0">Attendees</span>
                  <div className="flex flex-wrap gap-1">
                    {selectedEvent.attendees.map((email, i) => (
                      <span key={i} className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded">
                        {email}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <footer className="mt-6 text-center">
        <Link to="/ui/sources" className="text-primary text-sm hover:underline">
          Configure calendar accounts
        </Link>
      </footer>
    </div>
  )
}

function getEventsForDate(date: Date, events: CalendarEvent[]): CalendarEvent[] {
  const dateStr = date.toISOString().split('T')[0]
  return events.filter(event => {
    if (!event.start_time) return false
    try {
      const eventDate = new Date(event.start_time).toISOString().split('T')[0]
      return eventDate === dateStr
    } catch {
      return false
    }
  }).sort((a, b) => {
    // All-day events first, then by time
    if (a.all_day && !b.all_day) return -1
    if (!a.all_day && b.all_day) return 1
    return new Date(a.start_time).getTime() - new Date(b.start_time).getTime()
  })
}

export default Calendar
