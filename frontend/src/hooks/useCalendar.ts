import { useCallback } from 'react'
import { useMCP } from './useMCP'

export interface CalendarEvent {
  id: number
  event_title: string
  start_time: string
  end_time: string | null
  all_day: boolean
  location: string | null
  calendar_name: string | null
  recurrence_rule: string | null
  calendar_account_id: number | null
  attendees: string[] | null
  meeting_link: string | null
}

export interface CalendarEventFilters {
  startDate?: string
  endDate?: string
  days?: number
  limit?: number
  userIds?: number[]
}

// Module-level cache: Map<"YYYY-MM-userIds", CalendarEvent[]>
// userIds are sorted and joined to create a stable cache key
const monthCache = new Map<string, CalendarEvent[]>()
const pendingFetches = new Map<string, Promise<CalendarEvent[]>>()

function getCacheKey(year: number, month: number, userIds?: number[]): string {
  const monthKey = `${year}-${String(month + 1).padStart(2, '0')}`
  if (!userIds || userIds.length === 0) {
    return monthKey
  }
  // Sort userIds for stable cache key
  const userKey = [...userIds].sort((a, b) => a - b).join(',')
  return `${monthKey}-users:${userKey}`
}

function getMonthRange(year: number, month: number): { start: Date; end: Date } {
  const start = new Date(year, month, 1)
  const end = new Date(year, month + 1, 0, 23, 59, 59, 999)
  return { start, end }
}

export const useCalendar = () => {
  const { mcpCall } = useMCP()

  const fetchMonthEvents = useCallback(async (
    year: number,
    month: number,
    userIds?: number[]
  ): Promise<CalendarEvent[]> => {
    const { start, end } = getMonthRange(year, month)
    const params: Record<string, unknown> = {
      start_date: start.toISOString(),
      end_date: end.toISOString(),
      limit: 500,
    }
    if (userIds && userIds.length > 0) {
      params.user_ids = userIds
    }
    const result = await mcpCall('organizer_upcoming', params) as CalendarEvent[][] | null
    return result?.[0] || []
  }, [mcpCall])

  const getMonthEvents = useCallback(async (
    year: number,
    month: number,
    userIds?: number[]
  ): Promise<CalendarEvent[]> => {
    const key = getCacheKey(year, month, userIds)

    // Return cached if available
    if (monthCache.has(key)) {
      return monthCache.get(key)!
    }

    // If already fetching this month, wait for it
    if (pendingFetches.has(key)) {
      return pendingFetches.get(key)!
    }

    // Fetch and cache
    const fetchPromise = fetchMonthEvents(year, month, userIds).then(events => {
      monthCache.set(key, events)
      pendingFetches.delete(key)
      return events
    })
    pendingFetches.set(key, fetchPromise)
    return fetchPromise
  }, [fetchMonthEvents])

  const getEventsForMonths = useCallback(async (
    year: number,
    month: number,
    userIds?: number[]
  ): Promise<CalendarEvent[]> => {
    // Fetch prev, current, and next month in parallel
    const months = [
      { year: month === 0 ? year - 1 : year, month: month === 0 ? 11 : month - 1 },
      { year, month },
      { year: month === 11 ? year + 1 : year, month: month === 11 ? 0 : month + 1 },
    ]

    const results = await Promise.all(
      months.map(m => getMonthEvents(m.year, m.month, userIds))
    )

    // Combine and dedupe by event id + start_time (for recurring events)
    const seen = new Set<string>()
    const combined: CalendarEvent[] = []
    for (const events of results) {
      for (const event of events) {
        const key = `${event.id}-${event.start_time}`
        if (!seen.has(key)) {
          seen.add(key)
          combined.push(event)
        }
      }
    }

    return combined.sort((a, b) =>
      new Date(a.start_time).getTime() - new Date(b.start_time).getTime()
    )
  }, [getMonthEvents])

  // Keep the original for backward compatibility with CalendarPanel
  const getUpcomingEvents = useCallback(async (filters: CalendarEventFilters = {}): Promise<CalendarEvent[]> => {
    const params: Record<string, unknown> = {
      start_date: filters.startDate,
      end_date: filters.endDate,
      days: filters.days ?? 7,
      limit: filters.limit ?? 50,
    }
    if (filters.userIds && filters.userIds.length > 0) {
      params.user_ids = filters.userIds
    }
    const result = await mcpCall('organizer_upcoming', params) as CalendarEvent[][] | null
    return result?.[0] || []
  }, [mcpCall])

  const clearCache = useCallback(() => {
    monthCache.clear()
    pendingFetches.clear()
  }, [])

  return {
    getUpcomingEvents,
    getEventsForMonths,
    getMonthEvents,
    clearCache,
  }
}
