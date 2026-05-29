import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useMetrics, timeRangeToHours, type TimeRange } from './useMetrics'
import { mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'

const setup = () => renderHook(() => useMetrics()).result.current

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

const JSON_HEADERS = { 'content-type': 'application/json' }

// Capture the URL of the metrics call (the one hitting /api/metrics).
const metricCall = (fetchMock: ReturnType<typeof mockFetch>) =>
  fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/metrics'))

describe('timeRangeToHours', () => {
  it.each([
    ['1h', 1],
    ['6h', 6],
    ['24h', 24],
    ['7d', 168],
  ])('maps %s to %i hours', (range, hours) => {
    expect(timeRangeToHours(range as TimeRange)).toBe(hours)
  })

  it('falls back to 24 for an unknown range', () => {
    expect(timeRangeToHours('99y' as TimeRange)).toBe(24)
  })
})

describe('useMetrics.getSummary', () => {
  it('requests the summary with default hours and parses JSON', async () => {
    const data = { period_hours: 24, since: 't', metrics: [] }
    const fetchMock = mockFetch(async () => mockResponse({ json: data, headers: JSON_HEADERS }))
    const { getSummary } = setup()

    const out = await getSummary()

    expect(out).toEqual(data)
    const url = String(metricCall(fetchMock)?.[0])
    expect(url).toContain('/api/metrics/summary')
    expect(url).toContain('hours=24')
  })

  it('includes metric_type and name params when supplied', async () => {
    const fetchMock = mockFetch(async () => mockResponse({ json: {}, headers: JSON_HEADERS }))
    const { getSummary } = setup()

    await getSummary(6, 'task', 'ingest')

    const url = String(metricCall(fetchMock)?.[0])
    expect(url).toContain('hours=6')
    expect(url).toContain('metric_type=task')
    expect(url).toContain('name=ingest')
  })

  it('throws with the status code on a non-OK response', async () => {
    mockFetch(async () => mockResponse({ status: 503, json: {}, headers: JSON_HEADERS }))
    const { getSummary } = setup()
    await expect(getSummary()).rejects.toThrow('Failed to fetch metrics summary: 503')
  })

  it('throws when the content-type is not JSON', async () => {
    mockFetch(async () => mockResponse({ status: 200, text: '<html>', headers: { 'content-type': 'text/html' } }))
    const { getSummary } = setup()
    await expect(getSummary()).rejects.toThrow(/Expected JSON response/)
  })
})

describe('useMetrics.getTaskMetrics', () => {
  it('sends hours, limit defaults and parses the response', async () => {
    const data = { period_hours: 24, count: 0, events: [] }
    const fetchMock = mockFetch(async () => mockResponse({ json: data, headers: JSON_HEADERS }))
    const { getTaskMetrics } = setup()

    const out = await getTaskMetrics()

    expect(out).toEqual(data)
    const url = String(metricCall(fetchMock)?.[0])
    expect(url).toContain('/api/metrics/tasks')
    expect(url).toContain('hours=24')
    expect(url).toContain('limit=100')
  })

  it('forwards custom hours, name and limit', async () => {
    const fetchMock = mockFetch(async () => mockResponse({ json: {}, headers: JSON_HEADERS }))
    const { getTaskMetrics } = setup()

    await getTaskMetrics(6, 'email', 25)

    const url = String(metricCall(fetchMock)?.[0])
    expect(url).toContain('hours=6')
    expect(url).toContain('limit=25')
    expect(url).toContain('name=email')
  })

  it('throws with status on failure', async () => {
    mockFetch(async () => mockResponse({ status: 500, json: {}, headers: JSON_HEADERS }))
    const { getTaskMetrics } = setup()
    await expect(getTaskMetrics()).rejects.toThrow('Failed to fetch task metrics: 500')
  })
})

describe('useMetrics.getMcpMetrics', () => {
  it('requests /api/metrics/mcp with defaults', async () => {
    const data = { period_hours: 24, count: 1, events: [] }
    const fetchMock = mockFetch(async () => mockResponse({ json: data, headers: JSON_HEADERS }))
    const { getMcpMetrics } = setup()

    const out = await getMcpMetrics()

    expect(out).toEqual(data)
    const url = String(metricCall(fetchMock)?.[0])
    expect(url).toContain('/api/metrics/mcp')
    expect(url).toContain('limit=100')
  })

  it('forwards name and limit', async () => {
    const fetchMock = mockFetch(async () => mockResponse({ json: {}, headers: JSON_HEADERS }))
    const { getMcpMetrics } = setup()
    await getMcpMetrics(1, 'core_search', 5)
    const url = String(metricCall(fetchMock)?.[0])
    expect(url).toContain('name=core_search')
    expect(url).toContain('limit=5')
  })

  it('throws with status on failure', async () => {
    mockFetch(async () => mockResponse({ status: 404, json: {}, headers: JSON_HEADERS }))
    const { getMcpMetrics } = setup()
    await expect(getMcpMetrics()).rejects.toThrow('Failed to fetch MCP metrics: 404')
  })
})

describe('useMetrics.getSystemMetrics', () => {
  it('requests /api/metrics/system with default hours=1 and no limit param', async () => {
    const data = { period_hours: 1, latest: {}, history: [] }
    const fetchMock = mockFetch(async () => mockResponse({ json: data, headers: JSON_HEADERS }))
    const { getSystemMetrics } = setup()

    const out = await getSystemMetrics()

    expect(out).toEqual(data)
    const url = String(metricCall(fetchMock)?.[0])
    expect(url).toContain('/api/metrics/system')
    expect(url).toContain('hours=1')
    expect(url).not.toContain('limit=')
  })

  it('forwards custom hours and name', async () => {
    const fetchMock = mockFetch(async () => mockResponse({ json: {}, headers: JSON_HEADERS }))
    const { getSystemMetrics } = setup()
    await getSystemMetrics(6, 'cpu')
    const url = String(metricCall(fetchMock)?.[0])
    expect(url).toContain('hours=6')
    expect(url).toContain('name=cpu')
  })

  it('throws with status on failure', async () => {
    mockFetch(async () => mockResponse({ status: 500, json: {}, headers: JSON_HEADERS }))
    const { getSystemMetrics } = setup()
    await expect(getSystemMetrics()).rejects.toThrow('Failed to fetch system metrics: 500')
  })
})
