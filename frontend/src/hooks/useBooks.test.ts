import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useBooks } from './useBooks'
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

const book = {
  id: 1,
  title: 'T',
  author: 'A',
  publisher: null,
  published: null,
  language: null,
  total_pages: null,
  tags: [],
  section_count: 0,
  file_path: null,
}

describe('useBooks.listBooks', () => {
  it('unwraps the first element and applies defaults', async () => {
    const fetchMock = routeMcp(mcpResult([book]))
    const { result } = renderHook(() => useBooks())
    const books = await result.current.listBooks()
    expect(books).toEqual([book])
    expect(mcpArgs(fetchMock, 'books_list_books')).toEqual({
      title: undefined,
      author: undefined,
      tags: undefined,
      sections: false,
      limit: 50,
      offset: 0,
    })
  })

  it('forwards provided filters', async () => {
    const fetchMock = routeMcp(mcpResult([book]))
    const { result } = renderHook(() => useBooks())
    await result.current.listBooks({
      title: 'q',
      author: 'me',
      tags: ['t'],
      sections: true,
      limit: 10,
      offset: 20,
    })
    expect(mcpArgs(fetchMock, 'books_list_books')).toEqual({
      title: 'q',
      author: 'me',
      tags: ['t'],
      sections: true,
      limit: 10,
      offset: 20,
    })
  })

  it('returns an empty array when the result is null/empty', async () => {
    const fetchMock = routeMcp(mcpResult(null))
    const { result } = renderHook(() => useBooks())
    await expect(result.current.listBooks()).resolves.toEqual([])
    expect(fetchMock).toHaveBeenCalled()
  })
})

describe('useBooks.readBook', () => {
  it('passes book_id and defaults sections to empty array', async () => {
    const fetchMock = routeMcp(mcpResult([{ id: 1, title: 'S', content: 'c', order: 0, parent_section_id: null }]))
    const { result } = renderHook(() => useBooks())
    const sections = await result.current.readBook(3)
    expect(sections).toHaveLength(1)
    expect(mcpArgs(fetchMock, 'books_read_book')).toEqual({ book_id: 3, sections: [] })
  })

  it('forwards explicit section ids', async () => {
    const fetchMock = routeMcp(mcpResult([]))
    const { result } = renderHook(() => useBooks())
    await result.current.readBook(3, [1, 2])
    expect(mcpArgs(fetchMock, 'books_read_book')).toEqual({ book_id: 3, sections: [1, 2] })
  })

  it('returns an empty array when result missing', async () => {
    routeMcp(mcpResult(undefined))
    const { result } = renderHook(() => useBooks())
    await expect(result.current.readBook(3)).resolves.toEqual([])
  })
})

describe('useBooks.uploadBook', () => {
  it('POSTs FormData to /books/upload and returns the json', async () => {
    const fetchMock = mockFetch(async (input) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      return mockResponse({ json: { task_id: 'abc', status: 'queued' } })
    })
    const { result } = renderHook(() => useBooks())
    const file = new File(['data'], 'book.epub')
    const got = await result.current.uploadBook(file)
    expect(got).toEqual({ task_id: 'abc', status: 'queued' })
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/books/upload'))
    expect(call?.[1]?.method).toBe('POST')
    expect(call?.[1]?.body).toBeInstanceOf(FormData)
  })

  it('throws data.detail when upload fails', async () => {
    mockFetch(async (input) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      return mockResponse({ status: 413, json: { detail: 'too big' } })
    })
    const { result } = renderHook(() => useBooks())
    await expect(result.current.uploadBook(new File(['x'], 'b.epub'))).rejects.toThrow('too big')
  })

  it('throws a default message when no detail present', async () => {
    mockFetch(async (input) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      return mockResponse({ status: 500, json: {} })
    })
    const { result } = renderHook(() => useBooks())
    await expect(result.current.uploadBook(new File(['x'], 'b.epub'))).rejects.toThrow('Upload failed')
  })
})
