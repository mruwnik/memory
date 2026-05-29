import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@/test/utils'

vi.mock('recharts', () => {
  const Passthrough = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>
  return {
    ResponsiveContainer: Passthrough,
    BarChart: ({ data, children }: { data?: unknown[]; children?: React.ReactNode }) => (
      <div data-testid="chart" data-chart-data={JSON.stringify(data ?? [])}>
        {children}
      </div>
    ),
    Bar: ({ dataKey }: { dataKey?: string }) => (
      <div data-testid="bar" data-key={String(dataKey ?? '')} />
    ),
    XAxis: () => <div />,
    YAxis: () => <div />,
    CartesianGrid: () => <div />,
    Tooltip: () => <div />,
  }
})

import { SessionActivityChart } from './SessionActivityChart'
import type { TelemetryMetricsResponse } from '@/hooks/useTelemetry'

const readData = () =>
  JSON.parse(screen.getByTestId('chart').getAttribute('data-chart-data') ?? '[]')

const makeResponse = (
  data: Partial<TelemetryMetricsResponse['data'][number]>[],
): TelemetryMetricsResponse => ({
  metric: 'session.count',
  granularity_minutes: 60,
  from: '2026-05-29T00:00:00Z',
  to: '2026-05-29T01:00:00Z',
  group_by: [],
  data: data.map((d) => ({
    timestamp: '2026-05-29T00:00:00Z',
    count: 0,
    sum: null,
    min: null,
    max: null,
    ...d,
  })),
})

describe('SessionActivityChart', () => {
  it.each([[null], [makeResponse([])]])('shows empty state for %s', (data) => {
    render(<SessionActivityChart data={data} />)
    expect(screen.getByText('No session data available')).toBeInTheDocument()
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument()
  })

  it('maps each point to a sessions count bar value sorted by timestamp', () => {
    const data = makeResponse([
      { timestamp: '2026-05-29T02:00:00Z', count: 5 },
      { timestamp: '2026-05-29T00:00:00Z', count: 1 },
      { timestamp: '2026-05-29T01:00:00Z', count: 3 },
    ])
    render(<SessionActivityChart data={data} />)
    const chart = readData()
    expect(chart.map((d: { sessions: number }) => d.sessions)).toEqual([1, 3, 5])
    expect(chart.map((d: { timestamp: string }) => d.timestamp)).toEqual([
      '2026-05-29T00:00:00Z',
      '2026-05-29T01:00:00Z',
      '2026-05-29T02:00:00Z',
    ])
  })

  it('renders a single sessions bar', () => {
    const data = makeResponse([{ timestamp: '2026-05-29T00:00:00Z', count: 2 }])
    render(<SessionActivityChart data={data} />)
    const bars = screen.getAllByTestId('bar')
    expect(bars).toHaveLength(1)
    expect(bars[0].getAttribute('data-key')).toBe('sessions')
  })
})
