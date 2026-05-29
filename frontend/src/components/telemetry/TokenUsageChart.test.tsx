import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@/test/utils'

vi.mock('recharts', () => {
  const Passthrough = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>
  return {
    ResponsiveContainer: Passthrough,
    AreaChart: ({ data, children }: { data?: unknown[]; children?: React.ReactNode }) => (
      <div data-testid="chart" data-chart-data={JSON.stringify(data ?? [])}>
        {children}
      </div>
    ),
    Area: ({ dataKey }: { dataKey?: string }) => (
      <div data-testid="area" data-key={String(dataKey ?? '')} />
    ),
    XAxis: () => <div data-testid="x-axis" />,
    YAxis: () => <div data-testid="y-axis" />,
    CartesianGrid: () => <div />,
    Tooltip: () => <div />,
    Legend: () => <div />,
  }
})

import { TokenUsageChart } from './TokenUsageChart'
import type { TelemetryMetricsResponse } from '@/hooks/useTelemetry'

const readData = () =>
  JSON.parse(screen.getByTestId('chart').getAttribute('data-chart-data') ?? '[]')

const makeResponse = (
  data: Partial<TelemetryMetricsResponse['data'][number]>[],
): TelemetryMetricsResponse => ({
  metric: 'token.usage',
  granularity_minutes: 60,
  from: '2026-05-29T00:00:00Z',
  to: '2026-05-29T01:00:00Z',
  group_by: ['source'],
  data: data.map((d) => ({
    timestamp: '2026-05-29T00:00:00Z',
    count: 1,
    sum: 0,
    min: null,
    max: null,
    ...d,
  })),
})

describe('TokenUsageChart', () => {
  it.each([[null], [makeResponse([])]])('shows empty state for %s', (data) => {
    render(<TokenUsageChart data={data} />)
    expect(screen.getByText('No token usage data available')).toBeInTheDocument()
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument()
  })

  it('aggregates sums per source within a timestamp', () => {
    const data = makeResponse([
      { timestamp: '2026-05-29T00:00:00Z', source: 'a', sum: 100 },
      { timestamp: '2026-05-29T00:00:00Z', source: 'a', sum: 50 },
      { timestamp: '2026-05-29T00:00:00Z', source: 'b', sum: 25 },
    ])
    render(<TokenUsageChart data={data} />)
    const chart = readData()
    expect(chart).toHaveLength(1)
    expect(chart[0].a).toBe(150)
    expect(chart[0].b).toBe(25)
  })

  it('renders one Area per unique source', () => {
    const data = makeResponse([
      { timestamp: '2026-05-29T00:00:00Z', source: 'a', sum: 1 },
      { timestamp: '2026-05-29T01:00:00Z', source: 'b', sum: 2 },
    ])
    render(<TokenUsageChart data={data} />)
    const keys = screen.getAllByTestId('area').map((el) => el.getAttribute('data-key'))
    expect(new Set(keys)).toEqual(new Set(['a', 'b']))
  })

  it('labels missing source as "unknown"', () => {
    const data = makeResponse([{ timestamp: '2026-05-29T00:00:00Z', source: null, sum: 10 }])
    render(<TokenUsageChart data={data} />)
    expect(readData()[0].unknown).toBe(10)
  })

  it('sorts chart points ascending by timestamp', () => {
    const data = makeResponse([
      { timestamp: '2026-05-29T02:00:00Z', source: 'a', sum: 1 },
      { timestamp: '2026-05-29T00:00:00Z', source: 'a', sum: 2 },
      { timestamp: '2026-05-29T01:00:00Z', source: 'a', sum: 3 },
    ])
    render(<TokenUsageChart data={data} />)
    const timestamps = readData().map((d: { timestamp: string }) => d.timestamp)
    expect(timestamps).toEqual([
      '2026-05-29T00:00:00Z',
      '2026-05-29T01:00:00Z',
      '2026-05-29T02:00:00Z',
    ])
  })

  it('treats null sum as zero', () => {
    const data = makeResponse([{ timestamp: '2026-05-29T00:00:00Z', source: 'a', sum: null }])
    render(<TokenUsageChart data={data} />)
    expect(readData()[0].a).toBe(0)
  })
})
