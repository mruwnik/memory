import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@/test/utils'

vi.mock('recharts', () => {
  const Passthrough = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>
  return {
    ResponsiveContainer: Passthrough,
    LineChart: ({ data, children }: { data?: unknown[]; children?: React.ReactNode }) => (
      <div data-testid="chart" data-chart-data={JSON.stringify(data ?? [])}>
        {children}
      </div>
    ),
    Line: ({ dataKey, name }: { dataKey?: string; name?: string }) => (
      <div data-testid="line" data-key={String(dataKey ?? '')} data-name={String(name ?? '')} />
    ),
    XAxis: () => <div />,
    YAxis: () => <div />,
    CartesianGrid: () => <div />,
    Tooltip: () => <div />,
    Legend: () => <div />,
  }
})

import { CostChart } from './CostChart'
import type { TelemetryMetricsResponse } from '@/hooks/useTelemetry'

const readData = () =>
  JSON.parse(screen.getByTestId('chart').getAttribute('data-chart-data') ?? '[]')

const makeResponse = (
  data: Partial<TelemetryMetricsResponse['data'][number]>[],
): TelemetryMetricsResponse => ({
  metric: 'cost.usage',
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

describe('CostChart', () => {
  it.each([[null], [makeResponse([])]])('shows empty state for %s', (data) => {
    render(<CostChart data={data} />)
    expect(screen.getByText('No cost data available')).toBeInTheDocument()
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument()
  })

  it('computes a running cumulative total over sorted timestamps', () => {
    const data = makeResponse([
      { timestamp: '2026-05-29T02:00:00Z', source: 'a', sum: 3 },
      { timestamp: '2026-05-29T00:00:00Z', source: 'a', sum: 1 },
      { timestamp: '2026-05-29T01:00:00Z', source: 'a', sum: 2 },
    ])
    render(<CostChart data={data} />)
    const cumulatives = readData().map((d: { cumulative: number }) => d.cumulative)
    expect(cumulatives).toEqual([1, 3, 6])
  })

  it('sums per-source cost and a total per timestamp', () => {
    const data = makeResponse([
      { timestamp: '2026-05-29T00:00:00Z', source: 'a', sum: 1 },
      { timestamp: '2026-05-29T00:00:00Z', source: 'b', sum: 2 },
    ])
    render(<CostChart data={data} />)
    const point = readData()[0]
    expect(point.cost).toBe(3)
    expect(point.a).toBe(1)
    expect(point.b).toBe(2)
  })

  it('always renders a Cumulative line plus one line per source', () => {
    const data = makeResponse([
      { timestamp: '2026-05-29T00:00:00Z', source: 'a', sum: 1 },
      { timestamp: '2026-05-29T01:00:00Z', source: 'b', sum: 2 },
    ])
    render(<CostChart data={data} />)
    const keys = screen.getAllByTestId('line').map((el) => el.getAttribute('data-key'))
    expect(keys).toContain('cumulative')
    expect(keys).toContain('a')
    expect(keys).toContain('b')
  })

  it('labels missing source as "unknown"', () => {
    const data = makeResponse([{ timestamp: '2026-05-29T00:00:00Z', source: null, sum: 5 }])
    render(<CostChart data={data} />)
    expect(readData()[0].unknown).toBe(5)
  })
})
