import type { MockResponseInit } from '@/test/utils'

// `useMCP`'s `parseJsonResponse` (used by useTelemetry/useDiscord and others)
// throws unless the response advertises a JSON content-type, so every
// envelope-bearing MockResponseInit declares it. `mcpCall` itself reads
// `response.text()` regardless, so this header is harmless for the plain path.
const JSON_HEADERS = { 'content-type': 'application/json' }

function toContentItem(payload: unknown) {
  return {
    type: 'text',
    text: typeof payload === 'string' ? payload : JSON.stringify(payload),
  }
}

/**
 * Build the raw JSON-RPC envelope OBJECT wrapping the given content payloads
 * (each becomes one `content[i].text`, JSON-stringified — mirroring how the
 * MCP server streams tool results). Use this when a test builds its own
 * `Response`/`mockFetch` by hand and needs the body object; use `mcpResult`
 * when you want a ready-to-route `MockResponseInit`.
 */
export function mcpEnvelopeJson(...payloads: unknown[]) {
  return {
    jsonrpc: '2.0',
    id: 1,
    result: { content: payloads.map(toContentItem) },
  }
}

/**
 * A `MockResponseInit` whose body is the JSON-RPC envelope from
 * `mcpEnvelopeJson`. `mcpCall` finally `JSON.parse`s each `content[i].text`,
 * so the hook receives the parsed payloads as its result array.
 */
export function mcpResult(...payloads: unknown[]): MockResponseInit {
  return { status: 200, headers: JSON_HEADERS, json: mcpEnvelopeJson(...payloads) }
}

/** Raw tool-error envelope object (result.isError = true). */
export function mcpToolErrorJson(message: string) {
  return {
    jsonrpc: '2.0',
    id: 1,
    result: { isError: true, content: [{ type: 'text', text: message }] },
  }
}

/** Build a JSON-RPC tool-error envelope (result.isError = true). */
export function mcpToolError(message: string): MockResponseInit {
  return { status: 200, headers: JSON_HEADERS, json: mcpToolErrorJson(message) }
}

/** Raw JSON-RPC error envelope object (top-level error). */
export function mcpRpcErrorJson(message: string, code = -32000) {
  return { jsonrpc: '2.0', id: 1, error: { code, message } }
}

/** Build a JSON-RPC error envelope (top-level error object). */
export function mcpRpcError(message: string, code = -32000): MockResponseInit {
  return { status: 200, headers: JSON_HEADERS, json: mcpRpcErrorJson(message, code) }
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
