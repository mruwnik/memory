import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useCelery } from './useCelery'
import { mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

const beatEntry = {
  key: 'k',
  name: 'task-a',
  task: 'memory.x',
  schedule_display: 'every 5m',
  last_run: null,
  last_status: null,
  last_duration_ms: null,
}

const activity = {
  hours: 24,
  by_task: [],
  totals: { total: 0, success: 0, failure: 0, avg_duration_ms: null },
  recent_failures: [],
}

describe('useCelery.getBeatSchedule', () => {
  it('returns the parsed schedule on success', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': { json: {} },
      '/api/celery/beat-schedule': { json: [beatEntry] },
    })
    const { result } = renderHook(() => useCelery())
    const schedule = await result.current.getBeatSchedule()
    expect(schedule).toEqual([beatEntry])
    expect(
      fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/api/celery/beat-schedule')),
    ).toBe(true)
  })

  it('throws on non-ok response', async () => {
    mockFetchRoutes({
      '/auth/me': { json: {} },
      '/api/celery/beat-schedule': { status: 500, json: {} },
    })
    const { result } = renderHook(() => useCelery())
    await expect(result.current.getBeatSchedule()).rejects.toThrow(
      'Failed to fetch beat schedule',
    )
  })
})

describe('useCelery.getTaskActivity', () => {
  it('defaults hours to 24 in the query string', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': { json: {} },
      '/api/celery/task-activity': { json: activity },
    })
    const { result } = renderHook(() => useCelery())
    const got = await result.current.getTaskActivity()
    expect(got).toEqual(activity)
    const call = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes('/api/celery/task-activity'),
    )
    expect(String(call?.[0])).toContain('hours=24')
  })

  it('passes a custom hours value', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': { json: {} },
      '/api/celery/task-activity': { json: activity },
    })
    const { result } = renderHook(() => useCelery())
    await result.current.getTaskActivity(72)
    const call = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes('/api/celery/task-activity'),
    )
    expect(String(call?.[0])).toContain('hours=72')
  })

  it('throws on non-ok response', async () => {
    mockFetchRoutes({
      '/auth/me': { json: {} },
      '/api/celery/task-activity': { status: 503, json: {} },
    })
    const { result } = renderHook(() => useCelery())
    await expect(result.current.getTaskActivity()).rejects.toThrow(
      'Failed to fetch task activity',
    )
  })
})
