import { describe, it, expect, vi } from 'vitest'
import { render, renderWithUser, screen } from '@/test/utils'

vi.mock('recharts', () => {
  const Passthrough = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>
  return {
    ResponsiveContainer: Passthrough,
    BarChart: ({ data, children }: { data?: unknown[]; children?: React.ReactNode }) => (
      <div data-testid="chart" data-chart-data={JSON.stringify(data ?? [])}>
        {children}
      </div>
    ),
    Bar: ({ children }: { children?: React.ReactNode }) => <div data-testid="bar">{children}</div>,
    Cell: ({ fill }: { fill?: string }) => (
      <div data-testid="cell" data-fill={String(fill ?? '')} />
    ),
    XAxis: () => <div />,
    YAxis: ({ dataKey, tickFormatter }: { dataKey?: string; tickFormatter?: (v: string) => string }) => (
      <div data-testid="yaxis" data-key={dataKey} data-tick-for-a={tickFormatter?.('a')} />
    ),
    CartesianGrid: () => <div />,
    Tooltip: () => <div />,
  }
})

import { SessionBreakdownChart } from './SessionBreakdownChart'
import type { SessionStatsResponse, SessionStats } from '@/hooks/useTelemetry'

const readData = () =>
  JSON.parse(screen.getByTestId('chart').getAttribute('data-chart-data') ?? '[]')

const makeSession = (overrides: Partial<SessionStats>): SessionStats => ({
  session_id: 'sess',
  label: 'sess',
  summary: null,
  project: null,
  total_tokens: 0,
  total_cost: 0,
  event_count: 0,
  first_seen: '2026-05-29T00:00:00Z',
  last_seen: '2026-05-29T01:00:00Z',
  ...overrides,
})

const makeResponse = (sessions: SessionStats[]): SessionStatsResponse => ({
  from_time: '2026-05-29T00:00:00Z',
  to_time: '2026-05-29T01:00:00Z',
  sessions,
})

// 10 sessions with distinct token volumes lets percentile thresholds bite.
const tenSessions = Array.from({ length: 10 }, (_, i) =>
  makeSession({
    session_id: `s${i}`,
    total_tokens: (10 - i) * 100, // s0=1000 (highest) ... s9=100 (lowest)
    total_cost: (10 - i) * 0.5,
    event_count: 10 - i,
  }),
)

describe('SessionBreakdownChart', () => {
  it.each([[null], [makeResponse([])]])('shows empty state for %s', (data) => {
    render(<SessionBreakdownChart data={data} />)
    expect(screen.getByText('No session data available')).toBeInTheDocument()
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument()
  })

  it('plots each session on a unique category even when labels collide', () => {
    const data = makeResponse([
      makeSession({ session_id: 'a', label: 'memory · 2026-05-29', total_tokens: 100 }),
      makeSession({ session_id: 'b', label: 'memory · 2026-05-29', total_tokens: 50 }),
    ])
    render(<SessionBreakdownChart data={data} />)
    const axis = screen.getByTestId('yaxis')
    const key = axis.getAttribute('data-key') as string
    const categories = readData().map((d: Record<string, string>) => d[key])
    expect(new Set(categories).size).toBe(2)
    expect(axis.getAttribute('data-tick-for-a')).toBe('memory · 2026-05-29')
  })

  it('sorts sessions by total tokens descending by default', () => {
    render(<SessionBreakdownChart data={makeResponse(tenSessions)} />)
    const values = readData().map((d: { value: number }) => d.value)
    expect(values).toEqual([...values].sort((a, b) => b - a))
    expect(values[0]).toBe(1000)
  })

  it('categorizes sessions into high/medium/low by token percentile', () => {
    render(<SessionBreakdownChart data={makeResponse(tenSessions)} />)
    const cats = readData().map((d: { category: string }) => d.category)
    // top 20% high, next 30% medium, bottom 50% low
    expect(cats).toContain('high')
    expect(cats).toContain('medium')
    expect(cats).toContain('low')
  })

  it('marks all sessions medium when there are fewer than 3', () => {
    const data = makeResponse([
      makeSession({ session_id: 'a', total_tokens: 100 }),
      makeSession({ session_id: 'b', total_tokens: 5 }),
    ])
    render(<SessionBreakdownChart data={data} />)
    const cats = readData().map((d: { category: string }) => d.category)
    expect(cats).toEqual(['medium', 'medium'])
  })

  it('re-sorts when sortBy is changed to cost', async () => {
    const data = makeResponse([
      makeSession({ session_id: 'lowCostHighToken', total_tokens: 1000, total_cost: 0.1 }),
      makeSession({ session_id: 'highCostLowToken', total_tokens: 10, total_cost: 9 }),
      makeSession({ session_id: 'mid', total_tokens: 500, total_cost: 1 }),
    ])
    const { user } = renderWithUser(<SessionBreakdownChart data={data} />)
    expect(readData()[0].fullSessionId).toBe('lowCostHighToken')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Sort sessions by' }), 'cost')
    expect(readData()[0].fullSessionId).toBe('highCostLowToken')
  })

  it('re-sorts when sortBy is changed to events', async () => {
    const data = makeResponse([
      makeSession({ session_id: 'fewEvents', total_tokens: 1000, event_count: 1 }),
      makeSession({ session_id: 'manyEvents', total_tokens: 10, event_count: 99 }),
      makeSession({ session_id: 'mid', total_tokens: 500, event_count: 50 }),
    ])
    const { user } = renderWithUser(<SessionBreakdownChart data={data} />)
    await user.selectOptions(screen.getByRole('combobox', { name: 'Sort sessions by' }), 'events')
    expect(readData()[0].fullSessionId).toBe('manyEvents')
  })

  it('limits the rows to the selected top-N count', async () => {
    const twenty = Array.from({ length: 20 }, (_, i) =>
      makeSession({ session_id: `x${i}`, total_tokens: 20 - i }),
    )
    const { user } = renderWithUser(<SessionBreakdownChart data={makeResponse(twenty)} />)
    // default is Top 15
    expect(readData()).toHaveLength(15)
    await user.selectOptions(screen.getByRole('combobox', { name: 'Sessions shown' }), '10')
    expect(readData()).toHaveLength(10)
  })

  it('reports how many of the total sessions are shown', () => {
    render(<SessionBreakdownChart data={makeResponse(tenSessions)} />)
    expect(screen.getByText('Showing 10 of 10 sessions')).toBeInTheDocument()
  })

  it('labels each bar with the session name and keeps the summary and id for the tooltip', () => {
    const data = makeResponse([
      makeSession({
        session_id: 'ec2a0d98-dbca-4399-92fc-516ad323da36',
        label: 'Added API key grouping',
        summary: 'Added API key grouping. Then tests.',
        total_tokens: 1,
      }),
    ])
    render(<SessionBreakdownChart data={data} />)
    const [bar] = readData()
    expect([bar.label, bar.summary, bar.fullSessionId]).toEqual([
      'Added API key grouping',
      'Added API key grouping. Then tests.',
      'ec2a0d98-dbca-4399-92fc-516ad323da36',
    ])
  })

  it('lists each charted session with its details outside the hover tooltip', () => {
    const data = makeResponse([
      makeSession({ session_id: 'a', label: 'Fix login', summary: 'Fixed the auth loop', project: '/code/memory', total_tokens: 1500, event_count: 7 }),
    ])
    render(<SessionBreakdownChart data={data} />)
    const row = screen.getByRole('row', { name: /Fix login/ })
    expect(row).toHaveTextContent('Fixed the auth loop')
    expect(row).toHaveTextContent('/code/memory')
    expect(row).toHaveTextContent('Medium usage')
    expect(row).toHaveTextContent('1.5K')
  })

  it('renders category count legend badges', () => {
    render(<SessionBreakdownChart data={makeResponse(tenSessions)} />)
    expect(screen.getByText(/High usage:/)).toBeInTheDocument()
    expect(screen.getByText(/Medium usage:/)).toBeInTheDocument()
    expect(screen.getByText(/Low usage:/)).toBeInTheDocument()
  })
})
