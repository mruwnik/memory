import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useCalendar, CalendarEvent } from './useCalendar'
import {
  mcpToolFromRequest,
  mockFetch,
  mockResponse,
  setAuthCookies,
  clearCookies,
} from '@/test/utils'
import { mcpResult } from './mcpEnvelope.testhelper'

// Each /mcp call gets a fresh response built from `events` so cache misses fetch.
function routeMcpDynamic(makeEvents: () => CalendarEvent[]) {
  return mockFetch(async (input) => {
    if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
    return mockResponse(mcpResult(makeEvents()))
  })
}

function upcomingCalls(fetchMock: ReturnType<typeof mockFetch>) {
  return fetchMock.mock.calls.filter((c) => mcpToolFromRequest(c[0], c[1]) === 'organizer_upcoming')
}

function mcpArgs(fetchMock: ReturnType<typeof mockFetch>, idx = 0) {
  return JSON.parse(upcomingCalls(fetchMock)[idx]?.[1]?.body as string).params.arguments
}

const ev = (id: number, start: string): CalendarEvent => ({
  id,
  event_title: `e${id}`,
  start_time: start,
  end_time: null,
  all_day: false,
  location: null,
  calendar_name: null,
  recurrence_rule: null,
  calendar_account_id: null,
  attendees: null,
  meeting_link: null,
})

beforeEach(() => {
  clearCookies()
  setAuthCookies()
  // Clear the module-level cache so each test starts cold.
  mockFetch(async () => mockResponse({ json: {} }))
  const { result } = renderHook(() => useCalendar())
  result.current.clearCache()
})

describe('useCalendar.getUpcomingEvents', () => {
  it('uses default days=7 and limit=50 and unwraps result[0]', async () => {
    const fetchMock = routeMcpDynamic(() => [ev(1, '2024-01-01T00:00:00Z')])
    const { result } = renderHook(() => useCalendar())
    const events = await result.current.getUpcomingEvents()
    expect(events).toEqual([ev(1, '2024-01-01T00:00:00Z')])
    const a = mcpArgs(fetchMock)
    expect(a.days).toBe(7)
    expect(a.limit).toBe(50)
    expect(a.user_ids).toBeUndefined()
  })

  it('forwards explicit filters including user_ids', async () => {
    const fetchMock = routeMcpDynamic(() => [])
    const { result } = renderHook(() => useCalendar())
    await result.current.getUpcomingEvents({
      startDate: '2024-01-01',
      endDate: '2024-01-31',
      days: 14,
      limit: 5,
      userIds: [2, 1],
    })
    const a = mcpArgs(fetchMock)
    expect(a).toMatchObject({
      start_date: '2024-01-01',
      end_date: '2024-01-31',
      days: 14,
      limit: 5,
      user_ids: [2, 1],
    })
  })

  it('returns empty array when result is null', async () => {
    mockFetch(async (input) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      return mockResponse(mcpResult(null))
    })
    const { result } = renderHook(() => useCalendar())
    await expect(result.current.getUpcomingEvents()).resolves.toEqual([])
  })
})

describe('useCalendar.getMonthEvents (caching)', () => {
  it('requests the full month range with limit 500', async () => {
    const fetchMock = routeMcpDynamic(() => [ev(1, '2024-03-15T00:00:00Z')])
    const { result } = renderHook(() => useCalendar())
    await result.current.getMonthEvents(2024, 2) // March (0-indexed)
    const a = mcpArgs(fetchMock)
    expect(a.limit).toBe(500)
    expect(a.start_date).toBe(new Date(2024, 2, 1).toISOString())
    expect(a.end_date).toBe(new Date(2024, 3, 0, 23, 59, 59, 999).toISOString())
  })

  it('caches by month so a second call does not re-fetch', async () => {
    const fetchMock = routeMcpDynamic(() => [ev(1, '2024-05-10T00:00:00Z')])
    const { result } = renderHook(() => useCalendar())
    const first = await result.current.getMonthEvents(2024, 4)
    const second = await result.current.getMonthEvents(2024, 4)
    expect(second).toBe(first)
    expect(upcomingCalls(fetchMock)).toHaveLength(1)
  })

  it('uses distinct cache keys for different user id sets', async () => {
    const fetchMock = routeMcpDynamic(() => [ev(1, '2024-06-01T00:00:00Z')])
    const { result } = renderHook(() => useCalendar())
    await result.current.getMonthEvents(2024, 5, [1])
    await result.current.getMonthEvents(2024, 5, [2])
    expect(upcomingCalls(fetchMock)).toHaveLength(2)
  })

  it('clearCache forces a re-fetch', async () => {
    const fetchMock = routeMcpDynamic(() => [ev(1, '2024-07-01T00:00:00Z')])
    const { result } = renderHook(() => useCalendar())
    await result.current.getMonthEvents(2024, 6)
    result.current.clearCache()
    await result.current.getMonthEvents(2024, 6)
    expect(upcomingCalls(fetchMock)).toHaveLength(2)
  })
})

describe('useCalendar.getEventsForMonths', () => {
  it('fetches prev/current/next months, dedupes by id+start_time, and sorts by start', async () => {
    // Each month returns the same recurring event id=1 plus a unique one.
    let n = 0
    const fetchMock = mockFetch(async (input) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      n += 1
      const unique = ev(100 + n, `2024-02-${String(10 + n).padStart(2, '0')}T00:00:00Z`)
      const dup = ev(1, '2024-02-01T00:00:00Z')
      return mockResponse(mcpResult([unique, dup]))
    })
    const { result } = renderHook(() => useCalendar())
    const events = await result.current.getEventsForMonths(2024, 1) // Feb
    expect(upcomingCalls(fetchMock)).toHaveLength(3)
    // dup (id 1) appears once; 3 unique => 4 total
    expect(events).toHaveLength(4)
    const sorted = [...events].sort(
      (a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime(),
    )
    expect(events).toEqual(sorted)
  })

  it('wraps year boundaries: January pulls previous December and next February', async () => {
    const seen: Array<{ start: string }> = []
    const fetchMock = mockFetch(async (input, init) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      const a = JSON.parse(init?.body as string).params.arguments
      seen.push({ start: a.start_date })
      return mockResponse(mcpResult([]))
    })
    const { result } = renderHook(() => useCalendar())
    await result.current.getEventsForMonths(2024, 0) // January
    const starts = seen.map((s) => s.start).sort()
    expect(starts).toContain(new Date(2023, 11, 1).toISOString()) // Dec 2023
    expect(starts).toContain(new Date(2024, 0, 1).toISOString()) // Jan 2024
    expect(starts).toContain(new Date(2024, 1, 1).toISOString()) // Feb 2024
    expect(fetchMock).toHaveBeenCalled()
  })
})
