import type { MockResponseInit } from '@/test/utils'

/**
 * Build a MockResponseInit whose body is a JSON-RPC envelope wrapping the given
 * content payloads. Each payload becomes one `content` item whose `text` is the
 * JSON-stringified value, mirroring how the MCP server streams tool results.
 *
 * The hooks' `mcpCall` reads the response via `response.text()` (no
 * `text/event-stream` content-type) then `JSON.parse`s it, and finally
 * `JSON.parse`s each `content[i].text`. So the hook receives the parsed
 * payloads as its result array.
 */
export function mcpResult(...payloads: unknown[]): MockResponseInit {
  return {
    status: 200,
    json: {
      jsonrpc: '2.0',
      id: 1,
      result: {
        content: payloads.map((p) => ({
          type: 'text',
          text: typeof p === 'string' ? p : JSON.stringify(p),
        })),
      },
    },
  }
}

/** Build a JSON-RPC tool-error envelope (result.isError = true). */
export function mcpToolError(message: string): MockResponseInit {
  return {
    status: 200,
    json: {
      jsonrpc: '2.0',
      id: 1,
      result: {
        isError: true,
        content: [{ type: 'text', text: message }],
      },
    },
  }
}

/** Build a JSON-RPC error envelope (top-level error object). */
export function mcpRpcError(message: string, code = -32000): MockResponseInit {
  return {
    status: 200,
    json: {
      jsonrpc: '2.0',
      id: 1,
      error: { code, message },
    },
  }
}

/** The fetch calls that target an `/mcp/<method>` endpoint, in order. */
export function mcpCalls(fetchMock: { mock: { calls: any[][] } }) {
  return fetchMock.mock.calls.filter((c) => String(c[0]).includes('/mcp/'))
}

/**
 * Extract the parsed JSON-RPC arguments of the Nth `/mcp/` fetch call
 * (default: the last MCP call). The hook (via `useMCP`) also issues a
 * `/auth/me` request from `checkAuth`, so this filters to MCP calls only.
 * Tool args live under `params.arguments`.
 */
export function mcpArgsAt(fetchMock: { mock: { calls: any[][] } }, callIndex = -1) {
  const calls = mcpCalls(fetchMock)
  const init = calls.at(callIndex)?.[1]
  const body = JSON.parse(init.body as string)
  return body.params.arguments as Record<string, any>
}

/** The URL string of the Nth `/mcp/` fetch call (default last). */
export function mcpUrlAt(fetchMock: { mock: { calls: any[][] } }, callIndex = -1): string {
  return String(mcpCalls(fetchMock).at(callIndex)?.[0])
}
