import { ReactElement, ReactNode } from 'react'
import { render, RenderOptions } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'

/**
 * Render a component wrapped in a MemoryRouter and return a userEvent instance
 * alongside the usual RTL result. Use `initialEntries` to control the route.
 */
export function renderWithRouter(
  ui: ReactElement,
  {
    initialEntries = ['/'],
    ...options
  }: { initialEntries?: string[] } & Omit<RenderOptions, 'wrapper'> = {},
) {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
  )
  return {
    user: userEvent.setup(),
    ...render(ui, { wrapper: Wrapper, ...options }),
  }
}

/** Plain render plus a userEvent instance (no router). */
export function renderWithUser(ui: ReactElement, options?: Omit<RenderOptions, 'wrapper'>) {
  return {
    user: userEvent.setup(),
    ...render(ui, options),
  }
}

export interface MockResponseInit {
  ok?: boolean
  status?: number
  json?: unknown
  text?: string
  headers?: Record<string, string>
}

/** Build a Response-like object suitable for a mocked fetch resolution. */
export function mockResponse({
  ok,
  status = 200,
  json,
  text,
  headers = {},
}: MockResponseInit = {}): Response {
  const resolvedOk = ok ?? (status >= 200 && status < 300)
  const body = {
    ok: resolvedOk,
    status,
    statusText: '',
    headers: new Headers(headers),
    json: async () => json,
    text: async () => (text !== undefined ? text : JSON.stringify(json ?? '')),
    blob: async () => new Blob([text ?? JSON.stringify(json ?? '')]),
    clone() {
      return body
    },
  }
  return body as unknown as Response
}

/**
 * Install a mocked global `fetch`. Pass a function for full control, or omit to
 * get a vi.fn() you can configure. Returns the mock for assertions. Remember
 * that vitest's `restoreMocks: true` resets spies between tests, so call this
 * inside each test (or in beforeEach).
 */
export function mockFetch(
  impl?: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> | Response,
) {
  const fn = impl ? vi.fn(impl) : vi.fn(async () => mockResponse({ json: {} }))
  vi.stubGlobal('fetch', fn)
  return fn
}

/**
 * Route fetch calls by URL substring. Each key is matched with `includes`
 * against the request URL; the first match wins. Use `__default` for a
 * fallback. Values are MockResponseInit objects.
 */
export function mockFetchRoutes(
  routes: Record<string, MockResponseInit>,
): ReturnType<typeof mockFetch> {
  return mockFetch(async (input) => {
    const url = typeof input === 'string' ? input : input.toString()
    for (const [pattern, init] of Object.entries(routes)) {
      if (pattern === '__default') continue
      if (url.includes(pattern)) return mockResponse(init)
    }
    if (routes.__default) return mockResponse(routes.__default)
    return mockResponse({ status: 404, json: { detail: 'not found' } })
  })
}

/** Set document.cookie with the auth cookies useAuth expects. */
export function setAuthCookies() {
  document.cookie = 'access_token=test-access-token'
  document.cookie = 'session_id=test-session-id'
}

/** Clear all cookies (best effort, for jsdom). */
export function clearCookies() {
  document.cookie.split(';').forEach((c) => {
    const name = c.split('=')[0].trim()
    if (name) document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:01 GMT;path=/`
  })
}

// Re-export everything from RTL so tests can import from a single module.
export * from '@testing-library/react'
export { userEvent }
