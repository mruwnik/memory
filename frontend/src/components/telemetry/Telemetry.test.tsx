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
  const Chart = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>
  const Noop = () => <div />
  return {
    ResponsiveContainer: Passthrough,
    LineChart: Chart,
    AreaChart: Chart,
    BarChart: Chart,
    Line: Noop,
    Area: Noop,
    Bar: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
    Cell: Noop,
    XAxis: Noop,
    YAxis: Noop,
    CartesianGrid: Noop,
    Tooltip: Noop,
    Legend: Noop,
  }
})

import Telemetry from './Telemetry'

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

const metricsResponse = (sum: number, count: number) => ({
  metric: 'token.usage',
  granularity_minutes: 60,
  from: '2026-05-29T00:00:00Z',
  to: '2026-05-29T01:00:00Z',
  group_by: ['source'],
  data: [
    {
      timestamp: '2026-05-29T00:00:00Z',
      count,
      sum,
      min: null,
      max: null,
      source: 'claude',
      session_id: 'sess-1',
    },
  ],
})

const rawResponse = {
  total: 3,
  offset: 0,
  limit: 50,
  from: '2026-05-29T00:00:00Z',
  to: '2026-05-29T01:00:00Z',
  events: [
    {
      id: 1,
      timestamp: '2026-05-29T00:30:00Z',
      event_type: 'metric',
      name: 'token.usage',
      value: 100,
      session_id: 'sess-1',
      source: 'claude',
      tool_name: null,
      attributes: {},
      body: null,
    },
  ],
}

const toolUsageResponse = {
  from_time: '2026-05-29T00:00:00Z',
  to_time: '2026-05-29T01:00:00Z',
  session_count: 1,
  tools: [
    {
      tool_name: 'Bash',
      call_count: 5,
      input_tokens: 10,
      output_tokens: 20,
      cache_read_tokens: 0,
      cache_creation_tokens: 0,
      total_tokens: 500,
      per_call: null,
    },
  ],
}

// Routes: telemetry/users must be matched before telemetry/metrics since both
// share the "/telemetry/" substring and the first include() match wins.
const baseRoutes = (me: typeof adminUser) => json({
  '/auth/me': { json: me },
  '/telemetry/users': { json: [{ id: 1, name: 'Admin', email: 'admin@example.com' }] },
  '/users': {
    json: [
      {
        id: 1,
        name: 'Admin',
        email: 'admin@example.com',
        user_type: 'human',
        scopes: ['*'],
        api_key_count: 0,
      },
    ],
  },
  '/telemetry/metrics': { json: metricsResponse(1500, 2) },
  '/telemetry/raw': { json: rawResponse },
  '/sessions/stats/tool-usage': { json: toolUsageResponse },
})

describe('Telemetry container', () => {
  beforeEach(() => {
    clearCookies()
    localStorage.clear()
  })

  it('renders dashboards once telemetry data resolves', async () => {
    setAuthCookies()
    mockFetchRoutes(baseRoutes(regularUser))
    renderWithRouter(<Telemetry />)

    await waitFor(() => expect(screen.getByText('Token Usage')).toBeInTheDocument())
    expect(screen.getByText('Cost Over Time')).toBeInTheDocument()
    expect(screen.getByText('Session Activity')).toBeInTheDocument()
    expect(screen.getByText('Recent Events')).toBeInTheDocument()
    expect(screen.getByText('Token Usage by Tool')).toBeInTheDocument()
    expect(screen.getByText('Session Breakdown')).toBeInTheDocument()
  })

  it('computes summary totals from the loaded metrics', async () => {
    setAuthCookies()
    mockFetchRoutes(baseRoutes(regularUser))
    renderWithRouter(<Telemetry />)

    // totalTokens = 1500 -> "1.5K"
    await waitFor(() => expect(screen.getByText('1.5K')).toBeInTheDocument())
    // eventCount = rawResponse.total = 3
    expect(screen.getByText('3')).toBeInTheDocument()
  })

  it('surfaces an error and offers a retry when a fetch fails', async () => {
    setAuthCookies()
    mockFetchRoutes(
      json({
        '/auth/me': { json: regularUser },
        '/telemetry/users': { json: [] },
        '/telemetry/metrics': { status: 500, json: { detail: 'boom' } },
        '/telemetry/raw': { json: rawResponse },
        '/sessions/stats/tool-usage': { json: toolUsageResponse },
      }),
    )
    renderWithRouter(<Telemetry />)

    await waitFor(() =>
      expect(screen.getByText(/Failed to fetch telemetry metrics/)).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('requests the matching from-window when a time range is selected', async () => {
    setAuthCookies()
    const fetchMock = mockFetchRoutes(baseRoutes(regularUser))
    const { user } = renderWithRouter(<Telemetry />)

    await waitFor(() => expect(screen.getByText('Token Usage')).toBeInTheDocument())

    const metricsUrl = () => {
      const calls = fetchMock.mock.calls.map((c) => String(c[0]))
      return calls.filter((u) => u.includes('/telemetry/metrics'))
    }

    // 24h is the default -> granularity 60 in the request
    expect(metricsUrl().some((u) => u.includes('granularity=60'))).toBe(true)

    await user.click(screen.getByRole('button', { name: '1 Hour' }))
    // 1h range -> granularity 5
    await waitFor(() => expect(metricsUrl().some((u) => u.includes('granularity=5'))).toBe(true))
  })

  it('shows the admin user selector for admins and hides it for regular users', async () => {
    setAuthCookies()
    mockFetchRoutes(baseRoutes(adminUser))
    const { unmount } = renderWithRouter(<Telemetry />)
    expect(await screen.findByLabelText('View as:')).toBeInTheDocument()
    unmount()

    clearCookies()
    localStorage.clear()
    setAuthCookies()
    mockFetchRoutes(baseRoutes(regularUser))
    renderWithRouter(<Telemetry />)
    await waitFor(() => expect(screen.getByText('Token Usage')).toBeInTheDocument())
    expect(screen.queryByLabelText('View as:')).not.toBeInTheDocument()
  })

  it('renders a recent-events row from the raw events response', async () => {
    setAuthCookies()
    mockFetchRoutes(baseRoutes(regularUser))
    renderWithRouter(<Telemetry />)

    await waitFor(() => expect(screen.getByText('Recent Events')).toBeInTheDocument())
    const table = screen.getByRole('table')
    expect(within(table).getByText('token.usage')).toBeInTheDocument()
  })
})
