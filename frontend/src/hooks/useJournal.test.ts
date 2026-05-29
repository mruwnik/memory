import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useJournal } from './useJournal'
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
    if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
    return mockResponse(resp)
  })
}

function args(fetchMock: ReturnType<typeof mockFetch>) {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/mcp/journal_add'))
  return JSON.parse(call?.[1]?.body as string).params.arguments
}

const entry = { status: 'ok', entry: { id: 1, content: 'hi' } }

describe('useJournal.addJournalEntry', () => {
  it('applies default target_type and private flag', async () => {
    const fetchMock = routeMcp(mcpResult(entry))
    const { result } = renderHook(() => useJournal())
    const got = await result.current.addJournalEntry(5, 'hello')
    expect(got).toEqual(entry)
    expect(args(fetchMock)).toEqual({
      target_id: 5,
      content: 'hello',
      target_type: 'source_item',
      private: false,
    })
  })

  it.each([
    ['project', true],
    ['team', false],
    ['poll', true],
  ] as const)('forwards target_type=%s and private=%s', async (targetType, isPrivate) => {
    const fetchMock = routeMcp(mcpResult(entry))
    const { result } = renderHook(() => useJournal())
    await result.current.addJournalEntry(9, 'body', targetType, isPrivate)
    expect(args(fetchMock)).toEqual({
      target_id: 9,
      content: 'body',
      target_type: targetType,
      private: isPrivate,
    })
  })

  it('propagates MCP tool errors', async () => {
    const fetchMock = mockFetch(async (input) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      return mockResponse({
        json: {
          jsonrpc: '2.0',
          id: 1,
          result: { content: [{ type: 'text', text: 'nope' }], isError: true },
        },
      })
    })
    const { result } = renderHook(() => useJournal())
    await expect(result.current.addJournalEntry(1, 'x')).rejects.toThrow('tool error')
    expect(fetchMock).toHaveBeenCalled()
  })
})
