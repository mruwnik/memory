import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  renderWithRouter,
  screen,
  waitFor,
  within,
  setAuthCookies,
  clearCookies,
  mockFetchRoutes,
  type MockResponseInit,
} from '@/test/utils'

// The hooks assert a JSON content-type before parsing, so every mocked route
// must advertise one.
const json = (
  routes: Record<string, MockResponseInit>,
): Record<string, MockResponseInit> =>
  Object.fromEntries(
    Object.entries(routes).map(([k, v]) => [
      k,
      { ...v, headers: { 'content-type': 'application/json', ...(v.headers ?? {}) } },
    ]),
  )

vi.mock('recharts', () => {
  const Passthrough = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>
  const Noop = () => <div />
  return {
    ResponsiveContainer: Passthrough,
    LineChart: Passthrough,
    AreaChart: Passthrough,
    BarChart: Passthrough,
    Line: Noop,
    Area: Noop,
    Bar: Passthrough,
    Cell: Noop,
    XAxis: Noop,
    YAxis: Noop,
    CartesianGrid: Noop,
    Tooltip: Noop,
    Legend: Noop,
  }
})

import Usage from './Usage'

const adminUser = {
  user_id: 1,
  name: 'Admin',
  email: 'admin@example.com',
  user_type: 'human',
  scopes: ['*'],
}

const regularUser = {
  user_id: 2,
  name: 'Regular',
  email: 'r@example.com',
  user_type: 'human',
  scopes: ['teams'],
}

const usageRow = (kind: string, cost: number, tokens: number) => ({
  timestamp: '2026-10-28T00:00:00+00:00',
  kind,
  cost_usd: cost,
  input_tokens: tokens,
  output_tokens: 0,
  cache_read_tokens: 0,
  cache_write_tokens: 0,
  tokens,
})

const usageResponse = (data: unknown[]) => ({
  from: '2026-10-22T00:00:00+00:00',
  to: '2026-10-29T00:00:00+00:00',
  interval: 'day',
  group_by: ['kind'],
  data,
  users: {},
  keys: {},
})

const metricsResponse = {
  metric: 'session.count',
  granularity_minutes: 360,
  from: '2026-10-22T00:00:00Z',
  to: '2026-10-29T00:00:00Z',
  group_by: [],
  data: [{ timestamp: '2026-10-28T00:00:00Z', count: 2, sum: 2, min: null, max: null }],
}

const rawResponse = (total: number) => ({
  total,
  offset: 0,
  limit: 50,
  from: '2026-10-22T00:00:00Z',
  to: '2026-10-29T00:00:00Z',
  events: Array.from({ length: total }, (_, i) => ({
    id: i + 1,
    timestamp: '2026-10-28T00:30:00Z',
    event_type: 'metric',
    name: 'token.usage',
    value: 100,
    session_id: 'sess-1',
    source: 'claude',
    tool_name: null,
    api_key_id: null,
    attributes: {},
    body: null,
  })),
})

const toolUsageResponse = { from_time: 'a', to_time: 'b', session_count: 1, tools: [] }
const sessionsResponse = { from_time: 'a', to_time: 'b', sessions: [] }
const laptopKey = { id: 4, name: 'laptop', key_type: 'internal', user_id: 2, revoked: false }

const routes = (
  me: typeof adminUser,
  { usage = usageResponse([usageRow('memory', 1, 1000), usageRow('run', 0.5, 500)]), apiKeys = [] as unknown[], events = 3 }:
    { usage?: unknown; apiKeys?: unknown[]; events?: number } = {},
) => json({
  '/auth/me': { json: me },
  '/usage?': { json: usage },
  '/usage/api-keys': { json: apiKeys },
  '/usage/sessions': { json: sessionsResponse },
  '/usage/metrics': { json: metricsResponse },
  '/usage/raw': { json: rawResponse(events) },
  '/usage/tools': { json: toolUsageResponse },
  '/audit': { json: { total: 0, offset: 0, limit: 50, events: [] } },
  // Two users: the picker hides itself when there is only one.
  '/users': {
    json: [
      { id: 1, name: 'Admin', email: 'admin@example.com', user_type: 'human', scopes: ['*'], api_key_count: 0 },
      { id: 2, name: 'Other', email: 'other@example.com', user_type: 'human', scopes: [], api_key_count: 0 },
    ],
  },
})

const urls = (fetchMock: ReturnType<typeof mockFetchRoutes>, path: string) =>
  fetchMock.mock.calls.map((c) => String(c[0])).filter((u) => u.includes(path))

const lastParams = (fetchMock: ReturnType<typeof mockFetchRoutes>, path: string) =>
  new URL(urls(fetchMock, path).at(-1)!, 'http://x').searchParams

beforeEach(() => {
  clearCookies()
  localStorage.clear()
  setAuthCookies()
})

describe('Usage page', () => {
  it('shows spend and the Claude Code panels once data resolves', async () => {
    mockFetchRoutes(routes(regularUser))
    renderWithRouter(<Usage />)

    expect(await screen.findByText('Spend over time')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Usage' })).toBeInTheDocument()
    expect(await screen.findByText('Session activity')).toBeInTheDocument()
    expect(screen.getByText('Recent events')).toBeInTheDocument()
    expect(screen.getByText('Token usage by tool')).toBeInTheDocument()
    expect(screen.getByText('Session breakdown')).toBeInTheDocument()
  })

  it('totals cost and tokens over every source', async () => {
    mockFetchRoutes(routes(regularUser))
    renderWithRouter(<Usage />)

    expect(await screen.findByText('$1.50')).toBeInTheDocument()
    expect(screen.getAllByText('1.5K').length).toBeGreaterThan(0)
    const table = screen.getAllByRole('table')[0]
    expect(within(table).getByText('Memory')).toBeInTheDocument()
    expect(within(table).getByText('Agent runs')).toBeInTheDocument()
  })

  it('explains what produces usage when there is none', async () => {
    mockFetchRoutes(routes(regularUser, { usage: usageResponse([]), events: 0 }))
    renderWithRouter(<Usage />)

    expect(await screen.findByText('No usage in this period')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Manage LLM keys' })).toHaveAttribute('href', '/ui/keys')
    expect(await screen.findByText(/No Claude Code telemetry in this period/)).toBeInTheDocument()
    expect(screen.queryByText('Session activity')).not.toBeInTheDocument()
  })

  it('keeps the Claude Code panels when spend fails to load, and offers a retry', async () => {
    mockFetchRoutes(json({ ...routes(regularUser), '/usage?': { status: 500, json: { detail: 'boom' } } }))
    renderWithRouter(<Usage />)

    expect(await screen.findByText('boom')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(await screen.findByText('Session activity')).toBeInTheDocument()
  })

  it('requests usage grouped by the chosen dimension', async () => {
    const fetchMock = mockFetchRoutes(routes(regularUser))
    const { user } = renderWithRouter(<Usage />)

    await screen.findByText('Spend over time')
    expect(lastParams(fetchMock, '/usage?').getAll('group_by')).toEqual(['kind'])
    await user.selectOptions(screen.getByLabelText('Group by'), 'model')

    await waitFor(() => expect(lastParams(fetchMock, '/usage?').getAll('group_by')).toEqual(['model']))
  })

  it('labels the chart by the grouping the loaded data has, not the one being fetched', async () => {
    mockFetchRoutes(routes(regularUser))
    const { user } = renderWithRouter(<Usage />)

    await screen.findByText('Spend over time')
    await user.selectOptions(screen.getByLabelText('Group by'), 'model')

    const table = screen.getAllByRole('table')[0]
    expect(within(table).getByRole('columnheader', { name: 'Source' })).toBeInTheDocument()
    expect(within(table).getByText('Memory')).toBeInTheDocument()
  })

  it('drops the previous spend when a refetch fails', async () => {
    mockFetchRoutes(routes(regularUser))
    const { user } = renderWithRouter(<Usage />)

    await screen.findByText('$1.50')
    mockFetchRoutes(json({ ...routes(regularUser), '/usage?': { status: 500, json: { detail: 'boom' } } }))
    await user.click(screen.getByRole('button', { name: '30 days' }))

    expect(await screen.findByText('boom')).toBeInTheDocument()
    expect(screen.queryByText('$1.50')).not.toBeInTheDocument()
    expect(screen.queryByText('Spend over time')).not.toBeInTheDocument()
  })

  it('shows no Claude Code counts until its telemetry loads', async () => {
    mockFetchRoutes(json({ ...routes(regularUser), '/usage/raw': { status: 500, json: { detail: 'down' } } }))
    renderWithRouter(<Usage />)

    expect(await screen.findByText('down')).toBeInTheDocument()
    expect(await screen.findByText('$1.50')).toBeInTheDocument()
    expect(screen.getAllByText('—')).toHaveLength(2)
  })

  it.each([
    ['24 hours', 'hour', '60'],
    ['30 days', 'day', '1440'],
  ])('picking %s requests %s buckets', async (range, interval, granularity) => {
    const fetchMock = mockFetchRoutes(routes(regularUser))
    const { user } = renderWithRouter(<Usage />)

    await screen.findByText('Spend over time')
    await user.click(screen.getByRole('button', { name: range }))

    await waitFor(() => expect(lastParams(fetchMock, '/usage?').get('interval')).toBe(interval))
    await waitFor(() => expect(lastParams(fetchMock, '/usage/metrics').get('granularity')).toBe(granularity))
  })

  it('lets a regular user see usage on their own keys by sending no user filter', async () => {
    const fetchMock = mockFetchRoutes(routes(regularUser))
    renderWithRouter(<Usage />)

    await screen.findByText('Spend over time')
    expect(lastParams(fetchMock, '/usage?').has('user_id')).toBe(false)
    expect(screen.queryByLabelText('User')).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Audit log' })).not.toBeInTheDocument()
  })

  it('gives admins a user picker and the audit log', async () => {
    const fetchMock = mockFetchRoutes(routes(adminUser))
    const { user } = renderWithRouter(<Usage />)

    expect(await screen.findByLabelText('User')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Audit log' }))

    expect(await screen.findByText(/No audit events yet/)).toBeInTheDocument()
    expect(urls(fetchMock, '/audit')).not.toHaveLength(0)
  })

  it('hides the API key filter when no key has sent telemetry', async () => {
    mockFetchRoutes(routes(regularUser))
    renderWithRouter(<Usage />)

    await screen.findByText('Session activity')
    expect(screen.queryByLabelText('API key')).not.toBeInTheDocument()
    expect(screen.queryByText('Token usage by API key')).not.toBeInTheDocument()
  })

  it('filters the Claude Code panels by the chosen API key', async () => {
    const fetchMock = mockFetchRoutes(routes(regularUser, { apiKeys: [laptopKey] }))
    const { user } = renderWithRouter(<Usage />)

    expect(await screen.findByText('Token usage by API key')).toBeInTheDocument()
    await user.selectOptions(await screen.findByLabelText('API key'), 'laptop')

    await waitFor(() => expect(lastParams(fetchMock, '/usage/sessions').get('api_key_id')).toBe('4'))
    expect(lastParams(fetchMock, '/usage/metrics').get('api_key_id')).toBe('4')
    expect(lastParams(fetchMock, '/usage/raw').get('api_key_id')).toBe('4')
    expect(screen.queryByText('Token usage by API key')).not.toBeInTheDocument()
  })
})
