import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useMCP } from './useMCP'
import { mockFetch, mockResponse, clearCookies } from '@/test/utils'

beforeEach(() => {
  clearCookies()
  document.cookie = 'access_token=tok;path=/'
  vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})

/** Build a JSON (non-SSE) MCP response with a tool result. */
const jsonRpcResult = (content: unknown[], isError = false) =>
  mockResponse({
    json: { jsonrpc: '2.0', id: 1, result: { content, isError } },
    headers: { 'content-type': 'application/json' },
  })

/** Build an SSE (text/event-stream) response that streams the given lines. */
const sseResponse = (raw: string) => {
  const encoder = new TextEncoder()
  let sent = false
  const stream = {
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { done: true, value: undefined }
          sent = true
          return { done: false, value: encoder.encode(raw) }
        },
        releaseLock: () => {},
      }),
    },
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'text/event-stream' }),
    text: async () => raw,
  }
  return stream as unknown as Response
}

describe('mcpCall', () => {
  it('sends a tools/call JSON-RPC envelope to /mcp/<method>', async () => {
    const fetchMock = mockFetch(async () =>
      jsonRpcResult([{ text: '{"ok":true}' }]),
    )
    const { result } = renderHook(() => useMCP())
    await act(async () => {
      await result.current.mcpCall('some_tool', { a: 1 })
    })
    const call = fetchMock.mock.calls.find(([u]) =>
      u.toString().includes('/mcp/some_tool'),
    )!
    const body = JSON.parse((call[1] as RequestInit).body as string)
    expect(body.method).toBe('tools/call')
    expect(body.params).toEqual({ name: 'some_tool', arguments: { a: 1 } })
  })

  it('parses each content item as JSON when possible', async () => {
    mockFetch(async () =>
      jsonRpcResult([{ text: '{"x":1}' }, { text: '[2,3]' }]),
    )
    const { result } = renderHook(() => useMCP())
    let out!: unknown[]
    await act(async () => {
      out = await result.current.mcpCall('t')
    })
    expect(out).toEqual([{ x: 1 }, [2, 3]])
  })

  it('falls back to raw text for non-JSON content items', async () => {
    mockFetch(async () => jsonRpcResult([{ text: 'plain text' }]))
    const { result } = renderHook(() => useMCP())
    let out!: unknown[]
    await act(async () => {
      out = await result.current.mcpCall('t')
    })
    expect(out).toEqual(['plain text'])
  })

  it('throws with status and body detail on a non-ok HTTP response', async () => {
    mockFetch(async () =>
      mockResponse({ status: 500, text: 'server boom' }),
    )
    const { result } = renderHook(() => useMCP())
    await expect(result.current.mcpCall('boom')).rejects.toThrow(
      /MCP call boom failed \(500\).*server boom/,
    )
  })

  it('throws on a JSON-RPC error envelope', async () => {
    mockFetch(async () =>
      mockResponse({
        json: {
          jsonrpc: '2.0',
          id: 1,
          error: { code: -32601, message: 'method not found' },
        },
        headers: { 'content-type': 'application/json' },
      }),
    )
    const { result } = renderHook(() => useMCP())
    await expect(result.current.mcpCall('t')).rejects.toThrow(
      'MCP t error -32601: method not found',
    )
  })

  it('throws when result.content is not an array', async () => {
    mockFetch(async () =>
      mockResponse({
        json: { jsonrpc: '2.0', id: 1, result: {} },
        headers: { 'content-type': 'application/json' },
      }),
    )
    const { result } = renderHook(() => useMCP())
    await expect(result.current.mcpCall('t')).rejects.toThrow(
      /malformed response/,
    )
  })

  it('throws a tool error using the first content item text', async () => {
    mockFetch(async () =>
      jsonRpcResult([{ text: 'tool exploded' }], true),
    )
    const { result } = renderHook(() => useMCP())
    await expect(result.current.mcpCall('t')).rejects.toThrow(
      'MCP t tool error: tool exploded',
    )
  })

  it('throws on an empty (non-SSE) body', async () => {
    mockFetch(async () =>
      mockResponse({ text: '', headers: { 'content-type': 'application/json' } }),
    )
    const { result } = renderHook(() => useMCP())
    await expect(result.current.mcpCall('t')).rejects.toThrow(
      'MCP server returned an empty response',
    )
  })

  it('throws on a non-JSON (non-SSE) body', async () => {
    mockFetch(async () =>
      mockResponse({
        text: '<html>oops</html>',
        headers: { 'content-type': 'application/json' },
      }),
    )
    const { result } = renderHook(() => useMCP())
    await expect(result.current.mcpCall('t')).rejects.toThrow(
      /non-JSON response/,
    )
  })

  it('parses a server-sent-event stream and returns the last event', async () => {
    const raw =
      'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"content":[{"text":"{\\"v\\":9}"}]}}\n\n'
    mockFetch(async () => sseResponse(raw))
    const { result } = renderHook(() => useMCP())
    let out!: unknown[]
    await act(async () => {
      out = await result.current.mcpCall('t')
    })
    expect(out).toEqual([{ v: 9 }])
  })

  it('throws when an SSE stream yields no valid events', async () => {
    mockFetch(async () => sseResponse(': just a comment\n\n'))
    const { result } = renderHook(() => useMCP())
    await expect(result.current.mcpCall('t')).rejects.toThrow(
      'No valid SSE events received',
    )
  })
})

describe('convenience wrappers', () => {
  it('listNotes calls notes_note_files with the path', async () => {
    const fetchMock = mockFetch(async () => jsonRpcResult([{ text: '[]' }]))
    const { result } = renderHook(() => useMCP())
    await act(async () => {
      await result.current.listNotes('/sub')
    })
    const body = JSON.parse(
      (fetchMock.mock.calls.find(([u]) =>
        u.toString().includes('/mcp/notes_note_files'),
      )![1] as RequestInit).body as string,
    )
    expect(body.params.arguments).toEqual({ path: '/sub' })
  })

  it('fetchFile calls core_fetch_file with the filename', async () => {
    const fetchMock = mockFetch(async () => jsonRpcResult([{ text: '"x"' }]))
    const { result } = renderHook(() => useMCP())
    await act(async () => {
      await result.current.fetchFile('a.md')
    })
    const body = JSON.parse(
      (fetchMock.mock.calls.find(([u]) =>
        u.toString().includes('/mcp/core_fetch_file'),
      )![1] as RequestInit).body as string,
    )
    expect(body.params.arguments).toEqual({ filename: 'a.md' })
  })

  it('saveNote derives a subject from the filename when none given', async () => {
    const fetchMock = mockFetch(async () => jsonRpcResult([{ text: '"ok"' }]))
    const { result } = renderHook(() => useMCP())
    await act(async () => {
      await result.current.saveNote('dir/MyNote.md', 'body')
    })
    const body = JSON.parse(
      (fetchMock.mock.calls.find(([u]) =>
        u.toString().includes('/mcp/notes_upsert'),
      )![1] as RequestInit).body as string,
    )
    expect(body.params.arguments).toEqual({
      filename: 'dir/MyNote.md',
      content: 'body',
      subject: 'MyNote',
    })
  })

  it('saveNote uses an explicit subject when provided', async () => {
    const fetchMock = mockFetch(async () => jsonRpcResult([{ text: '"ok"' }]))
    const { result } = renderHook(() => useMCP())
    await act(async () => {
      await result.current.saveNote('n.md', 'body', 'Custom')
    })
    const body = JSON.parse(
      (fetchMock.mock.calls.find(([u]) =>
        u.toString().includes('/mcp/notes_upsert'),
      )![1] as RequestInit).body as string,
    )
    expect(body.params.arguments.subject).toBe('Custom')
  })

  it.each([
    ['getTags'],
    ['getSubjects'],
    ['getObservationTypes'],
  ] as const)('%s returns an empty wrapped array without an MCP call', async (name) => {
    const fetchMock = mockFetch()
    const { result } = renderHook(() => useMCP())
    let out!: unknown
    await act(async () => {
      out = await (result.current as any)[name]()
    })
    expect(out).toEqual([[]])
    // The hook's effect hits /auth/me, but no /mcp/ endpoint should be called.
    const mcpCalls = fetchMock.mock.calls.filter(([u]) =>
      u.toString().includes('/mcp/'),
    )
    expect(mcpCalls).toHaveLength(0)
  })

  it('getMetadataSchemas unwraps the first content item', async () => {
    mockFetch(async () =>
      jsonRpcResult([{ text: '{"schema":{}}' }]),
    )
    const { result } = renderHook(() => useMCP())
    let out!: unknown
    await act(async () => {
      out = await result.current.getMetadataSchemas()
    })
    expect(out).toEqual({ schema: {} })
  })

  it('searchKnowledgeBase passes query and applies config defaults', async () => {
    const fetchMock = mockFetch(async () => jsonRpcResult([{ text: '[]' }]))
    const { result } = renderHook(() => useMCP())
    await act(async () => {
      await result.current.searchKnowledgeBase('hello')
    })
    const body = JSON.parse(
      (fetchMock.mock.calls.find(([u]) =>
        u.toString().includes('/mcp/core_search'),
      )![1] as RequestInit).body as string,
    )
    expect(body.params.arguments).toEqual({
      query: 'hello',
      filters: {},
      modalities: [],
      limit: 20,
      previews: false,
      use_scores: false,
    })
  })

  it('searchKnowledgeBase honours explicit config and modalities', async () => {
    const fetchMock = mockFetch(async () => jsonRpcResult([{ text: '[]' }]))
    const { result } = renderHook(() => useMCP())
    await act(async () => {
      await result.current.searchKnowledgeBase(
        'q',
        ['text', 'photo'],
        { tag: 'x' },
        { limit: 5, previews: true, useScores: true },
      )
    })
    const body = JSON.parse(
      (fetchMock.mock.calls.find(([u]) =>
        u.toString().includes('/mcp/core_search'),
      )![1] as RequestInit).body as string,
    )
    expect(body.params.arguments).toEqual({
      query: 'q',
      filters: { tag: 'x' },
      modalities: ['text', 'photo'],
      limit: 5,
      previews: true,
      use_scores: true,
    })
  })
})
