import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useReports } from './useReports'
import {
  mockFetch,
  mockResponse,
  MockResponseInit,
  setAuthCookies,
  clearCookies,
} from '@/test/utils'

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

function mcpResult(...values: unknown[]): MockResponseInit {
  return {
    json: {
      jsonrpc: '2.0',
      id: 1,
      result: {
        content: values.map((v) => ({ type: 'text', text: JSON.stringify(v) })),
        isError: false,
      },
    },
  }
}

function routeMcp(resp: MockResponseInit) {
  return mockFetch(async (input) => {
    if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
    return mockResponse(resp)
  })
}

function mcpArgs(fetchMock: ReturnType<typeof mockFetch>, method: string) {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes(`/mcp/${method}`))
  return JSON.parse(call?.[1]?.body as string).params.arguments
}

const reportItem = {
  id: 1,
  title: 'R',
  filename: 'r.html',
  tags: [],
  inserted_at: null,
  modality: 'doc',
  mime_type: null,
  size: null,
  preview: null,
  metadata: { report_format: 'html' },
}
const nonReportItem = { ...reportItem, id: 2, metadata: { foo: 'bar' } }
const nullMetaItem = { ...reportItem, id: 3, metadata: null }

describe('useReports.listReports', () => {
  it('requests doc modality with metadata and filters to items having report_format', async () => {
    const fetchMock = routeMcp(mcpResult({ items: [reportItem, nonReportItem, nullMetaItem] }))
    const { result } = renderHook(() => useReports())
    const reports = await result.current.listReports()
    expect(reports).toEqual([reportItem])
    expect(mcpArgs(fetchMock, 'core_list_items')).toEqual({
      modalities: ['doc'],
      limit: 200,
      sort_by: 'inserted_at',
      sort_order: 'desc',
      include_metadata: true,
    })
  })

  it('returns an empty array when result is null', async () => {
    routeMcp(mcpResult(null))
    const { result } = renderHook(() => useReports())
    await expect(result.current.listReports()).resolves.toEqual([])
  })

  it('returns an empty array when items is missing', async () => {
    routeMcp(mcpResult({}))
    const { result } = renderHook(() => useReports())
    await expect(result.current.listReports()).resolves.toEqual([])
  })
})

describe('useReports.createReport', () => {
  it('forwards all arguments to reports_upsert and returns first result', async () => {
    const fetchMock = routeMcp(mcpResult({ id: 10 }))
    const { result } = renderHook(() => useReports())
    const got = await result.current.createReport('Title', 'body', ['t'], 'f.html', true, [
      'https://x.com',
    ])
    expect(got).toEqual({ id: 10 })
    expect(mcpArgs(fetchMock, 'reports_upsert')).toEqual({
      title: 'Title',
      content: 'body',
      tags: ['t'],
      filename: 'f.html',
      allow_scripts: true,
      allowed_connect_urls: ['https://x.com'],
    })
  })
})

describe('useReports.uploadReport', () => {
  it('builds FormData with optional fields and posts to /reports/upload', async () => {
    const fetchMock = mockFetch(async (input) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      return mockResponse({ json: { id: 1 } })
    })
    const { result } = renderHook(() => useReports())
    const file = new File(['x'], 'r.html')
    const got = await result.current.uploadReport(file, 'My Title', 'a,b', true, [
      'https://x.com',
      'https://y.com',
    ])
    expect(got).toEqual({ id: 1 })
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/reports/upload'))
    expect(call?.[1]?.method).toBe('POST')
    const fd = call?.[1]?.body as FormData
    expect(fd.get('title')).toBe('My Title')
    expect(fd.get('tags')).toBe('a,b')
    expect(fd.get('allow_scripts')).toBe('true')
    expect(fd.get('allowed_connect_urls')).toBe('https://x.com,https://y.com')
  })

  it('omits optional fields when not provided', async () => {
    const fetchMock = mockFetch(async (input) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      return mockResponse({ json: { id: 2 } })
    })
    const { result } = renderHook(() => useReports())
    await result.current.uploadReport(new File(['x'], 'r.html'))
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/reports/upload'))
    const fd = call?.[1]?.body as FormData
    expect(fd.get('title')).toBeNull()
    expect(fd.get('tags')).toBeNull()
    expect(fd.get('allow_scripts')).toBeNull()
    expect(fd.get('allowed_connect_urls')).toBeNull()
  })

  it('throws data.detail when upload fails', async () => {
    mockFetch(async (input) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      return mockResponse({ status: 400, json: { detail: 'bad file' } })
    })
    const { result } = renderHook(() => useReports())
    await expect(result.current.uploadReport(new File(['x'], 'r.html'))).rejects.toThrow('bad file')
  })

  it('throws default message when no detail', async () => {
    mockFetch(async (input) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      return mockResponse({ status: 500, json: {} })
    })
    const { result } = renderHook(() => useReports())
    await expect(result.current.uploadReport(new File(['x'], 'r.html'))).rejects.toThrow('Upload failed')
  })
})

describe('useReports.deleteReport', () => {
  it('calls reports_delete with report_id and returns the data', async () => {
    const fetchMock = routeMcp(mcpResult({ status: 'deleted' }))
    const { result } = renderHook(() => useReports())
    const got = await result.current.deleteReport(7)
    expect(got).toEqual({ status: 'deleted' })
    expect(mcpArgs(fetchMock, 'reports_delete')).toEqual({ report_id: 7 })
  })

  it('throws when the data payload carries an error field', async () => {
    routeMcp(mcpResult({ error: 'cannot delete' }))
    const { result } = renderHook(() => useReports())
    await expect(result.current.deleteReport(7)).rejects.toThrow('cannot delete')
  })
})
