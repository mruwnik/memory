import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useJobs, JobFilters } from './useJobs'
import { mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

const JSON_HEADERS = { 'content-type': 'application/json' }

const job = {
  id: 7,
  job_type: 'email',
  external_id: null,
  status: 'complete',
  error_message: null,
  result_id: null,
  result_type: null,
  params: {},
  created_at: '2024-01-01',
  updated_at: '2024-01-01',
  completed_at: null,
  attempts: 1,
  user_id: 3,
}

function route(impl: (url: string, init?: RequestInit) => Response) {
  return mockFetch(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return mockResponse({ json: {} })
    return impl(url, init)
  })
}

describe('useJobs.listJobs', () => {
  it('returns parsed jobs with no query string for empty filters', async () => {
    const fetchMock = route(() => mockResponse({ json: [job], headers: JSON_HEADERS }))
    const { result } = renderHook(() => useJobs())
    const jobs = await result.current.listJobs()
    expect(jobs).toEqual([job])
    const url = String(fetchMock.mock.calls.find((c) => String(c[0]).includes('/jobs'))?.[0])
    expect(url).not.toContain('?')
  })

  it.each<[keyof JobFilters, JobFilters, string]>([
    ['status', { status: 'failed' }, 'status=failed'],
    ['job_type', { job_type: 'email' }, 'job_type=email'],
    ['limit', { limit: 10 }, 'limit=10'],
    ['offset', { offset: 5 }, 'offset=5'],
    ['source', { source: 'manual' }, 'source=manual'],
    ['created_after', { created_after: '2024-01-01' }, 'created_after=2024-01-01'],
    ['created_before', { created_before: '2024-02-01' }, 'created_before=2024-02-01'],
    ['userId', { userId: 42 }, 'user_id=42'],
  ])('serializes the %s filter into the query string', async (_label, filters, expected) => {
    const fetchMock = route(() => mockResponse({ json: [], headers: JSON_HEADERS }))
    const { result } = renderHook(() => useJobs())
    await result.current.listJobs(filters)
    const url = String(fetchMock.mock.calls.find((c) => String(c[0]).includes('/jobs'))?.[0])
    expect(url).toContain(expected)
  })

  it('includes user_id=0 since userId uses !== undefined check', async () => {
    const fetchMock = route(() => mockResponse({ json: [], headers: JSON_HEADERS }))
    const { result } = renderHook(() => useJobs())
    await result.current.listJobs({ userId: 0 })
    const url = String(fetchMock.mock.calls.find((c) => String(c[0]).includes('/jobs'))?.[0])
    expect(url).toContain('user_id=0')
  })

  it('throws on non-ok response', async () => {
    route(() => mockResponse({ status: 500, json: {} }))
    const { result } = renderHook(() => useJobs())
    await expect(result.current.listJobs()).rejects.toThrow('Failed to fetch jobs: 500')
  })
})

describe('useJobs.getJob', () => {
  it('fetches a single job by id', async () => {
    const fetchMock = route(() => mockResponse({ json: job, headers: JSON_HEADERS }))
    const { result } = renderHook(() => useJobs())
    const got = await result.current.getJob(7)
    expect(got).toEqual(job)
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/jobs/7'))).toBe(true)
  })

  it('throws on non-ok response', async () => {
    route(() => mockResponse({ status: 404, json: {} }))
    const { result } = renderHook(() => useJobs())
    await expect(result.current.getJob(7)).rejects.toThrow('Failed to fetch job: 404')
  })
})

describe('useJobs.retryJob', () => {
  it('POSTs to the retry endpoint and returns the job', async () => {
    const fetchMock = route(() => mockResponse({ json: job, headers: JSON_HEADERS }))
    const { result } = renderHook(() => useJobs())
    const got = await result.current.retryJob(7)
    expect(got).toEqual(job)
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/jobs/7/retry'))
    expect(call?.[1]?.method).toBe('POST')
  })

  it('throws error.detail on failure', async () => {
    route(() => mockResponse({ status: 409, json: { detail: 'cannot retry' } }))
    const { result } = renderHook(() => useJobs())
    await expect(result.current.retryJob(7)).rejects.toThrow('cannot retry')
  })
})

describe('useJobs.reingestJob', () => {
  it('POSTs to the reingest endpoint and returns the job', async () => {
    const fetchMock = route(() => mockResponse({ json: job, headers: JSON_HEADERS }))
    const { result } = renderHook(() => useJobs())
    const got = await result.current.reingestJob(7)
    expect(got).toEqual(job)
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/jobs/7/reingest'))
    expect(call?.[1]?.method).toBe('POST')
  })

  it('throws error.detail on failure', async () => {
    route(() => mockResponse({ status: 409, json: { detail: 'cannot reingest' } }))
    const { result } = renderHook(() => useJobs())
    await expect(result.current.reingestJob(7)).rejects.toThrow('cannot reingest')
  })
})

describe('useJobs.getJobTypes', () => {
  it('returns the parsed list on success', async () => {
    route(() => mockResponse({ json: ['email', 'blog'], headers: JSON_HEADERS }))
    const { result } = renderHook(() => useJobs())
    await expect(result.current.getJobTypes()).resolves.toEqual(['email', 'blog'])
  })

  it('returns an empty array (no throw) on non-ok response', async () => {
    route(() => mockResponse({ status: 500, json: {} }))
    const { result } = renderHook(() => useJobs())
    await expect(result.current.getJobTypes()).resolves.toEqual([])
  })
})

describe('useJobs.getUsersWithJobs', () => {
  it('returns the parsed users on success', async () => {
    const users = [{ id: 1, name: 'A', email: 'a@e.com' }]
    route(() => mockResponse({ json: users, headers: JSON_HEADERS }))
    const { result } = renderHook(() => useJobs())
    await expect(result.current.getUsersWithJobs()).resolves.toEqual(users)
  })

  it('throws on non-ok response', async () => {
    route(() => mockResponse({ status: 500, json: {} }))
    const { result } = renderHook(() => useJobs())
    await expect(result.current.getUsersWithJobs()).rejects.toThrow(
      'Failed to fetch job users: 500',
    )
  })
})
