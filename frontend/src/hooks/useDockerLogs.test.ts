import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useDockerLogs } from './useDockerLogs'
import { mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

const JSON_HEADERS = { 'content-type': 'application/json' }

const container = { name: 'api', status: 'running', started_at: '2024-01-01' }
const logsResponse = {
  container: 'api',
  logs: 'line1\nline2',
  since: null,
  until: null,
  lines: 2,
}

// Helper: route /auth/me to {} and delegate everything else to `impl`.
function route(impl: (url: string, init?: RequestInit) => Response) {
  return mockFetch(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return mockResponse({ json: {} })
    return impl(url, init)
  })
}

describe('useDockerLogs.listContainers', () => {
  it('returns parsed containers when JSON content-type present', async () => {
    route(() => mockResponse({ json: [container], headers: JSON_HEADERS }))
    const { result } = renderHook(() => useDockerLogs())
    const containers = await result.current.listContainers()
    expect(containers).toEqual([container])
  })

  it('throws error.detail when the response is not ok', async () => {
    route(() => mockResponse({ status: 500, json: { detail: 'docker down' } }))
    const { result } = renderHook(() => useDockerLogs())
    await expect(result.current.listContainers()).rejects.toThrow('docker down')
  })

  it('falls back to status message when error body is not JSON', async () => {
    route(() =>
      mockResponse({
        status: 502,
        ok: false,
        text: 'gateway',
        // no json -> json() throws -> .catch fallback {detail:'Unknown error'}
      }),
    )
    // Make json() reject to exercise the .catch branch.
    const fetchMock = mockFetch(async (input) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      const bad = mockResponse({ status: 502 })
      ;(bad as unknown as { json: () => Promise<unknown> }).json = async () => {
        throw new Error('not json')
      }
      return bad
    })
    const { result } = renderHook(() => useDockerLogs())
    await expect(result.current.listContainers()).rejects.toThrow('Unknown error')
    expect(fetchMock).toHaveBeenCalled()
  })

  it('throws when ok but content-type is not JSON', async () => {
    route(() =>
      mockResponse({ status: 200, text: '<html>oops</html>', headers: { 'content-type': 'text/html' } }),
    )
    const { result } = renderHook(() => useDockerLogs())
    await expect(result.current.listContainers()).rejects.toThrow('Expected JSON response')
  })
})

describe('useDockerLogs.getLogs', () => {
  it('encodes the container name and omits query when no params', async () => {
    const fetchMock = route(() => mockResponse({ json: logsResponse, headers: JSON_HEADERS }))
    const { result } = renderHook(() => useDockerLogs())
    const got = await result.current.getLogs('my/container')
    expect(got).toEqual(logsResponse)
    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/docker/logs/'))
    expect(String(call?.[0])).toContain('/api/docker/logs/my%2Fcontainer')
    expect(String(call?.[0])).not.toContain('?')
  })

  it('builds the query string from all provided params', async () => {
    const fetchMock = route(() => mockResponse({ json: logsResponse, headers: JSON_HEADERS }))
    const { result } = renderHook(() => useDockerLogs())
    const since = new Date('2024-01-01T00:00:00.000Z')
    const until = new Date('2024-01-02T00:00:00.000Z')
    await result.current.getLogs('api', {
      since,
      until,
      tail: 100,
      filter_text: 'error',
      timestamps: false,
    })
    const url = String(
      fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/docker/logs/'))?.[0],
    )
    expect(url).toContain(`since=${encodeURIComponent(since.toISOString())}`)
    expect(url).toContain(`until=${encodeURIComponent(until.toISOString())}`)
    expect(url).toContain('tail=100')
    expect(url).toContain('filter_text=error')
    expect(url).toContain('timestamps=false')
  })

  it('includes timestamps=true when explicitly set true', async () => {
    const fetchMock = route(() => mockResponse({ json: logsResponse, headers: JSON_HEADERS }))
    const { result } = renderHook(() => useDockerLogs())
    await result.current.getLogs('api', { timestamps: true })
    const url = String(
      fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/docker/logs/'))?.[0],
    )
    expect(url).toContain('timestamps=true')
  })

  it('throws error.detail on non-ok response', async () => {
    route(() => mockResponse({ status: 404, json: { detail: 'no such container' } }))
    const { result } = renderHook(() => useDockerLogs())
    await expect(result.current.getLogs('ghost')).rejects.toThrow('no such container')
  })
})
