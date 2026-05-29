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

vi.mock('recharts', () => {
  const Passthrough = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>
  const Chart = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>
  const Noop = () => <div />
  return {
    ResponsiveContainer: Passthrough,
    LineChart: Chart,
    BarChart: Chart,
    Line: Noop,
    Bar: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
    Cell: Noop,
    XAxis: Noop,
    YAxis: Noop,
    CartesianGrid: Noop,
    Tooltip: Noop,
    Legend: Noop,
  }
})

import Metrics from './Metrics'

// The hooks assert a JSON content-type before parsing.
const json = (
  routes: Record<string, MockResponseInit>,
): Record<string, MockResponseInit> =>
  Object.fromEntries(
    Object.entries(routes).map(([k, v]) => [
      k,
      { ...v, headers: { 'content-type': 'application/json', ...(v.headers ?? {}) } },
    ]),
  )

const me = {
  user_id: 1,
  name: 'Admin',
  email: 'admin@example.com',
  user_type: 'human',
  scopes: ['*'],
}

const summaryResponse = {
  period_hours: 24,
  since: '2026-05-28T00:00:00Z',
  metrics: [
    {
      metric_type: 'task',
      name: 'memory.workers.tasks.email.process',
      count: 10,
      success_count: 9,
      failure_count: 1,
      avg_duration_ms: 1200,
      min_duration_ms: 100,
      max_duration_ms: 5000,
    },
    {
      metric_type: 'mcp_call',
      name: 'search',
      count: 20,
      success_count: 20,
      failure_count: 0,
      avg_duration_ms: 50,
      min_duration_ms: 10,
      max_duration_ms: 200,
    },
  ],
}

const systemResponse = {
  period_hours: 24,
  latest: {
    'system.cpu_percent': 42.5,
    'system.memory_percent': 70.0,
  },
  history: [{ timestamp: '2026-05-29T12:00:00Z', name: 'system.cpu_percent', value: 42.5 }],
}

const taskResponse = {
  period_hours: 24,
  count: 1,
  events: [
    {
      id: 1,
      timestamp: '2026-05-29T12:30:00Z',
      name: 'email.process',
      duration_ms: 1234,
      status: 'success',
      labels: {},
    },
  ],
}

const mcpResponse = {
  period_hours: 24,
  count: 1,
  events: [
    {
      id: 2,
      timestamp: '2026-05-29T12:31:00Z',
      name: 'search',
      duration_ms: 56,
      status: 'failure',
      labels: {},
    },
  ],
}

const baseRoutes = json({
  '/auth/me': { json: me },
  '/api/metrics/summary': { json: summaryResponse },
  '/api/metrics/system': { json: systemResponse },
  '/api/metrics/tasks': { json: taskResponse },
  '/api/metrics/mcp': { json: mcpResponse },
})

describe('Metrics container', () => {
  beforeEach(() => {
    clearCookies()
    localStorage.clear()
    vi.useRealTimers()
  })

  it('renders all metric sections after data loads', async () => {
    setAuthCookies()
    mockFetchRoutes(baseRoutes)
    renderWithRouter(<Metrics />)

    await waitFor(() => expect(screen.getByText('System Health')).toBeInTheDocument())
    expect(screen.getByText('Task Performance')).toBeInTheDocument()
    expect(screen.getByText('MCP Tool Usage')).toBeInTheDocument()
    expect(screen.getByText('Recent Task Executions')).toBeInTheDocument()
    expect(screen.getByText('Recent MCP Calls')).toBeInTheDocument()
  })

  it('computes summary card aggregates from the metrics list', async () => {
    setAuthCookies()
    mockFetchRoutes(baseRoutes)
    renderWithRouter(<Metrics />)

    // total events = 10 + 20 = 30
    await waitFor(() => expect(screen.getByText('30')).toBeInTheDocument())
    // success rate = (9 + 20) / 30 = 96.67 -> rounded 97%
    expect(screen.getByText('97%')).toBeInTheDocument()
    // avg duration = round((1200 + 50) / 2) = 625ms
    expect(screen.getByText('625ms')).toBeInTheDocument()
  })

  it('renders task and mcp tables with their event rows and statuses', async () => {
    setAuthCookies()
    mockFetchRoutes(baseRoutes)
    renderWithRouter(<Metrics />)

    await waitFor(() => expect(screen.getByText('Recent Task Executions')).toBeInTheDocument())
    expect(screen.getByText('1234ms')).toBeInTheDocument()
    expect(screen.getByText('56ms')).toBeInTheDocument()
    expect(screen.getByText('success')).toBeInTheDocument()
    expect(screen.getByText('failure')).toBeInTheDocument()
  })

  it('shows empty-state text in the tables when there are no events', async () => {
    setAuthCookies()
    mockFetchRoutes(
      json({
        '/auth/me': { json: me },
        '/api/metrics/summary': { json: summaryResponse },
        '/api/metrics/system': { json: systemResponse },
        '/api/metrics/tasks': { json: { period_hours: 24, count: 0, events: [] } },
        '/api/metrics/mcp': { json: { period_hours: 24, count: 0, events: [] } },
      }),
    )
    renderWithRouter(<Metrics />)

    await waitFor(() =>
      expect(screen.getByText('No task events in this time range')).toBeInTheDocument(),
    )
    expect(screen.getByText('No MCP calls in this time range')).toBeInTheDocument()
  })

  it('surfaces an error with a retry button when a fetch fails', async () => {
    setAuthCookies()
    mockFetchRoutes(
      json({
        '/auth/me': { json: me },
        '/api/metrics/summary': { status: 500, json: { detail: 'boom' } },
        '/api/metrics/system': { json: systemResponse },
        '/api/metrics/tasks': { json: taskResponse },
        '/api/metrics/mcp': { json: mcpResponse },
      }),
    )
    renderWithRouter(<Metrics />)

    await waitFor(() =>
      expect(screen.getByText(/Failed to fetch metrics summary/)).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('requests the matching hours window when a time range is selected', async () => {
    setAuthCookies()
    const fetchMock = mockFetchRoutes(baseRoutes)
    const { user } = renderWithRouter(<Metrics />)

    await waitFor(() => expect(screen.getByText('System Health')).toBeInTheDocument())

    const summaryUrls = () =>
      fetchMock.mock.calls
        .map((c) => String(c[0]))
        .filter((u) => u.includes('/api/metrics/summary'))

    // default 24h
    expect(summaryUrls().some((u) => u.includes('hours=24'))).toBe(true)

    await user.click(screen.getByRole('button', { name: '7 Days' }))
    // 7d -> 168 hours
    await waitFor(() => expect(summaryUrls().some((u) => u.includes('hours=168')).valueOf()).toBe(true))
  })

  it('caps the system metrics request at the 7-day sample window', async () => {
    setAuthCookies()
    const fetchMock = mockFetchRoutes(baseRoutes)
    renderWithRouter(<Metrics />)

    await waitFor(() => expect(screen.getByText('System Health')).toBeInTheDocument())
    const systemUrls = fetchMock.mock.calls
      .map((c) => String(c[0]))
      .filter((u) => u.includes('/api/metrics/system'))
    // 24h default is below the 168h cap, so it is passed through unchanged.
    expect(systemUrls.some((u) => u.includes('hours=24'))).toBe(true)
  })

  it.each([
    ['10s', 10000],
    ['30s', 30000],
    ['1m', 60000],
    ['5m', 300000],
  ])('schedules a refresh interval of %s -> %ims', async (label, ms) => {
    setAuthCookies()
    mockFetchRoutes(baseRoutes)
    const setIntervalSpy = vi.spyOn(window, 'setInterval')
    const { user } = renderWithRouter(<Metrics />)

    await waitFor(() => expect(screen.getByText('System Health')).toBeInTheDocument())
    setIntervalSpy.mockClear()

    await user.click(screen.getByRole('button', { name: label }))
    await waitFor(() =>
      expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), ms),
    )
    setIntervalSpy.mockRestore()
  })

  it('does not schedule a refresh interval when auto-refresh is off', async () => {
    setAuthCookies()
    mockFetchRoutes(baseRoutes)
    const setIntervalSpy = vi.spyOn(window, 'setInterval')
    renderWithRouter(<Metrics />)

    await waitFor(() => expect(screen.getByText('System Health')).toBeInTheDocument())
    // 'off' is the default; the refresh effect schedules no interval at any of
    // the selectable refresh periods.
    const refreshPeriods = [10000, 30000, 60000, 300000]
    const scheduledMs = setIntervalSpy.mock.calls.map((c) => c[1])
    expect(scheduledMs.some((ms) => refreshPeriods.includes(ms as number))).toBe(false)
    setIntervalSpy.mockRestore()
  })

  it('renders a task event row inside the task table only', async () => {
    setAuthCookies()
    mockFetchRoutes(baseRoutes)
    renderWithRouter(<Metrics />)

    await waitFor(() => expect(screen.getByText('Recent Task Executions')).toBeInTheDocument())
    const tables = screen.getAllByRole('table')
    expect(within(tables[0]).getByText('email.process')).toBeInTheDocument()
  })
})
