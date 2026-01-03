import { useState, useEffect, useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useSources, CalendarEvent } from '@/hooks/useSources'
import './Calendar.css'

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
  const { getUpcomingEvents } = useSources()
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
    <div className="calendar-view">
      <div className="calendar-header">
        <Link to="/ui/dashboard" className="back-btn">Back</Link>
        <h1>{MONTH_NAMES[currentDate.getMonth()]} {currentDate.getFullYear()}</h1>
        <div className="calendar-nav">
          <button onClick={goToPreviousMonth} className="nav-btn">&lt;</button>
          <button onClick={goToToday} className="today-btn">Today</button>
          <button onClick={goToNextMonth} className="nav-btn">&gt;</button>
        </div>
      </div>

      {error && (
        <div className="calendar-error">
          <p>{error}</p>
          <button onClick={() => loadEvents(currentDate)}>Retry</button>
        </div>
      )}

      <div className="calendar-grid">
        {/* Day headers */}
        {DAYS_OF_WEEK.map(day => (
          <div key={day} className="calendar-day-header">{day}</div>
        ))}

        {/* Calendar cells */}
        {calendarDays.map((day, index) => (
          <div
            key={index}
            className={`calendar-cell ${!day.isCurrentMonth ? 'other-month' : ''} ${day.isToday ? 'today' : ''}`}
          >
            <div className="cell-date">{day.date.getDate()}</div>
            <div className="cell-events">
              {day.events.slice(0, 4).map((event, eventIndex) => (
                <div
                  key={`${event.id}-${event.start_time}`}
                  className={`event-item ${event.all_day ? 'all-day' : ''}`}
                  title={`${event.event_title}${event.location ? ` - ${event.location}` : ''}`}
                  onClick={(e) => {
                    e.stopPropagation()
                    setSelectedEvent(event)
                  }}
                >
                  {!event.all_day && (
                    <span className="event-time">{formatEventTime(event)}</span>
                  )}
                  <span className="event-title">{event.event_title}</span>
                </div>
              ))}
              {day.events.length > 4 && (
                <div className="more-events">+{day.events.length - 4} more</div>
              )}
            </div>
          </div>
        ))}
      </div>

      {loading && <div className="loading-overlay">Loading events...</div>}

      {/* Event Detail Modal */}
      {selectedEvent && (
        <div className="event-modal-overlay" onClick={() => setSelectedEvent(null)}>
          <div className="event-modal" onClick={(e) => e.stopPropagation()}>
            <div className="event-modal-header">
              <h2>{selectedEvent.event_title}</h2>
              <button className="modal-close" onClick={() => setSelectedEvent(null)}>&times;</button>
            </div>
            <div className="event-modal-content">
              <div className="event-detail">
                <span className="detail-label">Date</span>
                <span className="detail-value">
                  {new Date(selectedEvent.start_time).toLocaleDateString('en-US', {
                    weekday: 'long',
                    year: 'numeric',
                    month: 'long',
                    day: 'numeric'
                  })}
                </span>
              </div>

              <div className="event-detail">
                <span className="detail-label">Time</span>
                <span className="detail-value">
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
                <div className="event-detail">
                  <span className="detail-label">Location</span>
                  <span className="detail-value">{selectedEvent.location}</span>
                </div>
              )}

              {selectedEvent.calendar_name && (
                <div className="event-detail">
                  <span className="detail-label">Calendar</span>
                  <span className="detail-value">{selectedEvent.calendar_name}</span>
                </div>
              )}

              {selectedEvent.recurrence_rule && (
                <div className="event-detail">
                  <span className="detail-label">Repeats</span>
                  <span className="detail-value recurring-badge">Recurring event</span>
                </div>
              )}

              {selectedEvent.meeting_link && (
                <div className="event-detail">
                  <span className="detail-label">Meeting</span>
                  <span className="detail-value">
                    <a href={selectedEvent.meeting_link} target="_blank" rel="noopener noreferrer" className="meeting-link">
                      Join Meeting
                    </a>
                  </span>
                </div>
              )}

              {selectedEvent.attendees && selectedEvent.attendees.length > 0 && (
                <div className="event-detail">
                  <span className="detail-label">Attendees</span>
                  <span className="detail-value attendees-list">
                    {selectedEvent.attendees.map((email, i) => (
                      <span key={i} className="attendee">{email}</span>
                    ))}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="calendar-footer">
        <Link to="/ui/sources" className="config-link">Configure calendar accounts</Link>
      </div>
    </div>
  )
}

function getEventsForDate(date: Date, events: CalendarEvent[]): CalendarEvent[] {
  const dateStr = date.toISOString().split('T')[0]
  return events.filter(event => {
    const eventDate = new Date(event.start_time).toISOString().split('T')[0]
    return eventDate === dateStr
  }).sort((a, b) => {
    // All-day events first, then by time
    if (a.all_day && !b.all_day) return -1
    if (!a.all_day && b.all_day) return 1
    return new Date(a.start_time).getTime() - new Date(b.start_time).getTime()
  })
}

export default Calendar
