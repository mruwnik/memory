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
    YAxis: () => <div />,
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
  total_tokens: 0,
  total_cost: 0,
  event_count: 0,
  first_seen: '2026-05-29T00:00:00Z',
  last_seen: '2026-05-29T01:00:00Z',
  ...overrides,
})

const makeResponse = (sessions: SessionStats[]): SessionStatsResponse => ({
  from: '2026-05-29T00:00:00Z',
  to: '2026-05-29T01:00:00Z',
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
    const selects = screen.getAllByRole('combobox')
    await user.selectOptions(selects[1], 'cost')
    expect(readData()[0].fullSessionId).toBe('highCostLowToken')
  })

  it('re-sorts when sortBy is changed to events', async () => {
    const data = makeResponse([
      makeSession({ session_id: 'fewEvents', total_tokens: 1000, event_count: 1 }),
      makeSession({ session_id: 'manyEvents', total_tokens: 10, event_count: 99 }),
      makeSession({ session_id: 'mid', total_tokens: 500, event_count: 50 }),
    ])
    const { user } = renderWithUser(<SessionBreakdownChart data={data} />)
    const selects = screen.getAllByRole('combobox')
    await user.selectOptions(selects[1], 'events')
    expect(readData()[0].fullSessionId).toBe('manyEvents')
  })

  it('limits the rows to the selected top-N count', async () => {
    const twenty = Array.from({ length: 20 }, (_, i) =>
      makeSession({ session_id: `x${i}`, total_tokens: 20 - i }),
    )
    const { user } = renderWithUser(<SessionBreakdownChart data={makeResponse(twenty)} />)
    // default is Top 15
    expect(readData()).toHaveLength(15)
    const selects = screen.getAllByRole('combobox')
    await user.selectOptions(selects[0], '10')
    expect(readData()).toHaveLength(10)
  })

  it('reports how many of the total sessions are shown', () => {
    render(<SessionBreakdownChart data={makeResponse(tenSessions)} />)
    expect(screen.getByText('Showing 10 of 10 sessions')).toBeInTheDocument()
  })

  it('truncates long session ids in the chart label', () => {
    const data = makeResponse([
      makeSession({ session_id: 'abcdefghijklmnopqrstuvwxyz', total_tokens: 1 }),
    ])
    render(<SessionBreakdownChart data={data} />)
    expect(readData()[0].sessionId).toBe('abcdefgh...uvwxyz')
  })

  it('renders category count legend badges', () => {
    render(<SessionBreakdownChart data={makeResponse(tenSessions)} />)
    expect(screen.getByText(/High Usage:/)).toBeInTheDocument()
    expect(screen.getByText(/Medium Usage:/)).toBeInTheDocument()
    expect(screen.getByText(/Low Usage:/)).toBeInTheDocument()
  })
})
