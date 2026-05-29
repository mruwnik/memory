import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useTelemetry } from './useTelemetry'
import { setAuthCookies, clearCookies, mockFetch, mockResponse } from '@/test/utils'

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

function setup() {
  return renderHook(() => useTelemetry()).result.current
}

function callTo(fetchMock: ReturnType<typeof mockFetch>, substr: string) {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes(substr))
  return { url: String(call?.[0]), init: call?.[1] }
}

// parseJsonResponse requires a content-type: application/json header.
const JSON_HEADERS = { 'content-type': 'application/json' }

function mockJson(json: unknown, status = 200) {
  return mockFetch(async () => mockResponse({ status, json, headers: JSON_HEADERS }))
}

describe('useTelemetry getRawEvents', () => {
  it('returns the parsed raw response with no filters', async () => {
    const payload = {
      total: 0,
      offset: 0,
      limit: 100,
      from: 'a',
      to: 'b',
      events: [],
    }
    const fetchMock = mockJson(payload)
    const r = await setup().getRawEvents()
    expect(r).toEqual(payload)
    const { url } = callTo(fetchMock, '/telemetry/raw')
    expect(url).not.toContain('event_type')
  })

  it('serializes every supplied filter into query params', async () => {
    const fetchMock = mockJson({ events: [] })
    const from = new Date('2024-01-01T00:00:00.000Z')
    const to = new Date('2024-01-02T00:00:00.000Z')
    await setup().getRawEvents({
      eventType: 'tool',
      name: 'Bash',
      sessionId: 's1',
      source: 'cli',
      from,
      to,
      limit: 50,
      offset: 10,
      userId: 7,
    })
    const { url } = callTo(fetchMock, '/telemetry/raw')
    expect(url).toContain('event_type=tool')
    expect(url).toContain('name=Bash')
    expect(url).toContain('session_id=s1')
    expect(url).toContain('source=cli')
    expect(url).toContain(`from=${encodeURIComponent(from.toISOString())}`)
    expect(url).toContain(`to=${encodeURIComponent(to.toISOString())}`)
    expect(url).toContain('limit=50')
    expect(url).toContain('offset=10')
    expect(url).toContain('user_id=7')
  })

  it('includes user_id=0 because the check is undefined, not falsy', async () => {
    const fetchMock = mockJson({ events: [] })
    await setup().getRawEvents({ userId: 0 })
    expect(callTo(fetchMock, '/telemetry/raw').url).toContain('user_id=0')
  })

  it('throws a status-bearing error on non-ok', async () => {
    mockJson({}, 503)
    await expect(setup().getRawEvents()).rejects.toThrow('Failed to fetch telemetry events: 503')
  })

  it('throws when the content-type is not JSON', async () => {
    mockFetch(async () =>
      mockResponse({ status: 200, text: '<html>oops</html>', headers: { 'content-type': 'text/html' } }),
    )
    await expect(setup().getRawEvents()).rejects.toThrow(/Expected JSON response but got text\/html/)
  })
})

describe('useTelemetry getMetrics', () => {
  it('always sets the metric param and appends each group_by', async () => {
    const fetchMock = mockJson({ metric: 'token.usage', data: [] })
    await setup().getMetrics('token.usage', {
      granularity: 60,
      source: 'cli',
      groupBy: ['session_id', 'tool_name'],
      userId: 3,
    })
    const { url } = callTo(fetchMock, '/telemetry/metrics')
    expect(url).toContain('metric=token.usage')
    expect(url).toContain('granularity=60')
    expect(url).toContain('source=cli')
    expect(url).toContain('group_by=session_id')
    expect(url).toContain('group_by=tool_name')
    expect(url).toContain('user_id=3')
  })

  it('omits optional params when not supplied', async () => {
    const fetchMock = mockJson({ data: [] })
    await setup().getMetrics('cost.usage')
    const { url } = callTo(fetchMock, '/telemetry/metrics')
    expect(url).toContain('metric=cost.usage')
    expect(url).not.toContain('granularity')
    expect(url).not.toContain('group_by')
  })

  it('throws a status-bearing error on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().getMetrics('x')).rejects.toThrow('Failed to fetch telemetry metrics: 500')
  })
})

describe('useTelemetry getToolUsage', () => {
  it('hits the tool-usage endpoint and returns the parsed response', async () => {
    const payload = { from_time: 'a', to_time: 'b', session_count: 2, tools: [] }
    const fetchMock = mockJson(payload)
    const r = await setup().getToolUsage({ userId: 9 })
    expect(r).toEqual(payload)
    const { url } = callTo(fetchMock, '/sessions/stats/tool-usage')
    expect(url).toContain('user_id=9')
  })

  it('throws a status-bearing error on non-ok', async () => {
    mockJson({}, 502)
    await expect(setup().getToolUsage()).rejects.toThrow('Failed to fetch tool usage: 502')
  })
})

describe('useTelemetry getUsersWithTelemetry', () => {
  it('returns the user list', async () => {
    const users = [{ id: 1, name: 'A', email: 'a@x' }]
    const fetchMock = mockJson(users)
    const r = await setup().getUsersWithTelemetry()
    expect(r).toEqual(users)
    expect(callTo(fetchMock, '/telemetry/users').url).toContain('/telemetry/users')
  })

  it('throws a status-bearing error on non-ok', async () => {
    mockJson({}, 403)
    await expect(setup().getUsersWithTelemetry()).rejects.toThrow(
      'Failed to fetch telemetry users: 403',
    )
  })
})

describe('useTelemetry getSessionStats aggregation', () => {
  // getSessionStats issues two getMetrics calls (token.usage, cost.usage) in
  // parallel and merges them by session_id.
  function routeMetrics(tokenData: unknown[], costData: unknown[]) {
    return mockFetch(async (input, init) => {
      const url = String(input)
      if (url.includes('/telemetry/metrics')) {
        const body = String(init?.body ?? '') // GET has no body; disambiguate via URL
        const isCost = url.includes('metric=cost.usage')
        const data = isCost ? costData : tokenData
        void body
        return mockResponse({
          json: { metric: isCost ? 'cost.usage' : 'token.usage', from: 'F', to: 'T', data },
          headers: JSON_HEADERS,
        })
      }
      return mockResponse({ json: {}, headers: JSON_HEADERS })
    })
  }

  it('aggregates tokens and cost per session and sorts by total_tokens desc', async () => {
    routeMetrics(
      [
        { timestamp: '2024-01-01', count: 2, sum: 100, session_id: 's1' },
        { timestamp: '2024-01-02', count: 3, sum: 50, session_id: 's1' },
        { timestamp: '2024-01-01', count: 1, sum: 500, session_id: 's2' },
      ],
      [
        { timestamp: '2024-01-01', count: 2, sum: 1.5, session_id: 's1' },
        { timestamp: '2024-01-01', count: 1, sum: 9.0, session_id: 's2' },
      ],
    )
    const r = await setup().getSessionStats()
    expect(r.from).toBe('F')
    expect(r.to).toBe('T')
    expect(r.sessions.map((s) => s.session_id)).toEqual(['s2', 's1'])

    const s1 = r.sessions.find((s) => s.session_id === 's1')!
    expect(s1.total_tokens).toBe(150)
    expect(s1.event_count).toBe(5)
    expect(s1.total_cost).toBe(1.5)
    expect(s1.first_seen).toBe('2024-01-01')
    expect(s1.last_seen).toBe('2024-01-02')

    const s2 = r.sessions.find((s) => s.session_id === 's2')!
    expect(s2.total_tokens).toBe(500)
    expect(s2.total_cost).toBe(9.0)
  })

  it('skips data points with no session_id', async () => {
    routeMetrics(
      [
        { timestamp: 't', count: 1, sum: 10, session_id: null },
        { timestamp: 't', count: 1, sum: 20, session_id: 's1' },
      ],
      [],
    )
    const r = await setup().getSessionStats()
    expect(r.sessions).toHaveLength(1)
    expect(r.sessions[0].session_id).toBe('s1')
  })

  it('treats a null sum as zero when aggregating', async () => {
    routeMetrics([{ timestamp: 't', count: 1, sum: null, session_id: 's1' }], [])
    const r = await setup().getSessionStats()
    expect(r.sessions[0].total_tokens).toBe(0)
  })

  it('ignores cost data for sessions absent from token data', async () => {
    routeMetrics(
      [{ timestamp: 't', count: 1, sum: 5, session_id: 's1' }],
      [{ timestamp: 't', count: 1, sum: 99, session_id: 'orphan' }],
    )
    const r = await setup().getSessionStats()
    expect(r.sessions).toHaveLength(1)
    expect(r.sessions[0].session_id).toBe('s1')
    expect(r.sessions[0].total_cost).toBe(0)
  })

  it('returns an empty session list when there is no data', async () => {
    routeMetrics([], [])
    const r = await setup().getSessionStats()
    expect(r.sessions).toEqual([])
  })
})
