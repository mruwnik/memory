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
      apiKeyId: 4,
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
    expect(url).toContain('api_key_id=4')
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
      apiKeyId: 4,
      userId: 3,
    })
    const { url } = callTo(fetchMock, '/telemetry/metrics')
    expect(url).toContain('metric=token.usage')
    expect(url).toContain('granularity=60')
    expect(url).toContain('source=cli')
    expect(url).toContain('group_by=session_id')
    expect(url).toContain('group_by=tool_name')
    expect(url).toContain('api_key_id=4')
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

describe('useTelemetry getSessionStats', () => {
  it('fetches server-aggregated sessions with every filter', async () => {
    const payload = { from_time: 'a', to_time: 'b', sessions: [] }
    const fetchMock = mockJson(payload)
    const r = await setup().getSessionStats({ apiKeyId: 4, userId: 9 })
    expect(r).toEqual(payload)
    const { url } = callTo(fetchMock, '/telemetry/sessions')
    expect(url).toContain('api_key_id=4')
    expect(url).toContain('user_id=9')
  })

  it('throws a status-bearing error on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().getSessionStats()).rejects.toThrow('Failed to fetch session stats: 500')
  })
})

describe('useTelemetry getApiKeysWithTelemetry', () => {
  it('returns the keys for the selected user', async () => {
    const keys = [{ id: 4, name: 'laptop', key_type: 'internal', user_id: 9, revoked: false }]
    const fetchMock = mockJson(keys)
    const r = await setup().getApiKeysWithTelemetry(9)
    expect(r).toEqual(keys)
    expect(callTo(fetchMock, '/telemetry/api-keys').url).toContain('user_id=9')
  })
})
