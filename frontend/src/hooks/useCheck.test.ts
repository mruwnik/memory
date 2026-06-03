import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useCheck } from './useCheck'
import {
  mockFetch,
  mockResponse,
  MockResponseInit,
  setAuthCookies,
  clearCookies,
} from '@/test/utils'
import { mcpResult } from './mcpEnvelope.testhelper'

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

function routeMcp(resp: MockResponseInit) {
  return mockFetch(async (input) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return mockResponse({ json: {} })
    return mockResponse(resp)
  })
}

function bodyArgs(fetchMock: ReturnType<typeof mockFetch>, methodSubstr: string) {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes(`/mcp/${methodSubstr}`))
  return JSON.parse(call?.[1]?.body as string).params.arguments
}

const job = {
  job_id: 'chk_abc',
  status: 'ok',
  mode: 'research',
  text: 'is the sky blue?',
  result: { answer: 'yes' },
  error: null,
  submitted_at: '2026-06-03T00:00:00+00:00',
  completed_at: '2026-06-03T00:01:00+00:00',
}

describe('useCheck.listJobs', () => {
  it('unwraps the jobs array and sends limit=200 by default', async () => {
    const fetchMock = routeMcp(mcpResult({ jobs: [job] }))
    const { result } = renderHook(() => useCheck())
    const jobs = await result.current.listJobs()
    expect(jobs).toEqual([job])
    expect(bodyArgs(fetchMock, 'check_list_jobs')).toEqual({ limit: 200 })
  })

  it('propagates MCP transport errors', async () => {
    const fetchMock = mockFetch(async (input) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      return mockResponse({ status: 500, text: 'boom' })
    })
    const { result } = renderHook(() => useCheck())
    await expect(result.current.listJobs()).rejects.toThrow('check_list_jobs failed')
    expect(fetchMock).toHaveBeenCalled()
  })
})

describe('useCheck.ask', () => {
  it('submits text with the default research mode', async () => {
    const fetchMock = routeMcp(mcpResult({ job_id: 'chk_new', status: 'queued' }))
    const { result } = renderHook(() => useCheck())
    const got = await result.current.ask({ text: 'why?' })
    expect(got).toEqual({ job_id: 'chk_new', status: 'queued' })
    expect(bodyArgs(fetchMock, 'check_ask')).toEqual({ text: 'why?', mode: 'research' })
  })

  it('forwards an explicit mode', async () => {
    const fetchMock = routeMcp(mcpResult({ job_id: 'chk_new', status: 'queued' }))
    const { result } = renderHook(() => useCheck())
    await result.current.ask({ text: 'check this', mode: 'verify' })
    expect(bodyArgs(fetchMock, 'check_ask')).toEqual({ text: 'check this', mode: 'verify' })
  })
})

describe('useCheck.deleteJob', () => {
  it('calls check_delete with the job id and resolves to undefined', async () => {
    const fetchMock = routeMcp(mcpResult({ deleted: true, job_id: 'chk_abc' }))
    const { result } = renderHook(() => useCheck())
    await expect(result.current.deleteJob('chk_abc')).resolves.toBeUndefined()
    expect(bodyArgs(fetchMock, 'check_delete')).toEqual({ job_id: 'chk_abc' })
  })
})
