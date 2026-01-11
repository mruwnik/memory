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
}

export const useCalendar = () => {
  const { mcpCall } = useMCP()

  const getUpcomingEvents = useCallback(async (filters: CalendarEventFilters = {}): Promise<CalendarEvent[]> => {
    const result = await mcpCall<CalendarEvent[][]>('organizer_get_upcoming_events', {
      start_date: filters.startDate,
      end_date: filters.endDate,
      days: filters.days ?? 7,
      limit: filters.limit ?? 50,
    })
    // mcpCall returns array from .map(), unwrap the first element
    return result?.[0] || []
  }, [mcpCall])

  return {
    getUpcomingEvents,
  }
}
