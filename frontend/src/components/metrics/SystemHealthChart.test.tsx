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

import { SystemHealthChart } from './SystemHealthChart'
import type { SystemMetricsResponse, SystemMetricPoint } from '@/hooks/useMetrics'

const readData = () =>
  JSON.parse(screen.getByTestId('chart').getAttribute('data-chart-data') ?? '[]')

const makeResponse = (history: SystemMetricPoint[]): SystemMetricsResponse => ({
  period_hours: 24,
  latest: {},
  history,
})

describe('SystemHealthChart', () => {
  it('shows the empty state when history is empty', () => {
    render(<SystemHealthChart data={makeResponse([])} />)
    expect(screen.getByText('No system metrics available')).toBeInTheDocument()
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument()
  })

  it('groups cpu/memory/disk points into one entry per rounded minute', () => {
    const history: SystemMetricPoint[] = [
      { timestamp: '2026-05-29T12:00:15Z', name: 'system.cpu_percent', value: 40 },
      { timestamp: '2026-05-29T12:00:45Z', name: 'system.memory_percent', value: 60 },
      { timestamp: '2026-05-29T12:00:30Z', name: 'system.disk_usage_percent', value: 80 },
    ]
    render(<SystemHealthChart data={makeResponse(history)} />)
    const rows = readData()
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ cpu: 40, memory: 60, disk: 80 })
  })

  it('prefers system cpu over process cpu within the same minute', () => {
    const history: SystemMetricPoint[] = [
      { timestamp: '2026-05-29T12:00:10Z', name: 'process.cpu_percent', value: 11 },
      { timestamp: '2026-05-29T12:00:20Z', name: 'system.cpu_percent', value: 22 },
    ]
    render(<SystemHealthChart data={makeResponse(history)} />)
    expect(readData()[0].cpu).toBe(22)
  })

  it('falls back to process cpu when no system cpu present', () => {
    const history: SystemMetricPoint[] = [
      { timestamp: '2026-05-29T12:00:10Z', name: 'process.cpu_percent', value: 13 },
    ]
    render(<SystemHealthChart data={makeResponse(history)} />)
    expect(readData()[0].cpu).toBe(13)
  })

  it('sorts grouped entries ascending by timestamp', () => {
    const history: SystemMetricPoint[] = [
      { timestamp: '2026-05-29T12:02:00Z', name: 'system.cpu_percent', value: 3 },
      { timestamp: '2026-05-29T12:00:00Z', name: 'system.cpu_percent', value: 1 },
      { timestamp: '2026-05-29T12:01:00Z', name: 'system.cpu_percent', value: 2 },
    ]
    render(<SystemHealthChart data={makeResponse(history)} />)
    expect(readData().map((d: { cpu: number }) => d.cpu)).toEqual([1, 2, 3])
  })

  it('renders cpu, memory and disk lines', () => {
    const history: SystemMetricPoint[] = [
      { timestamp: '2026-05-29T12:00:00Z', name: 'system.cpu_percent', value: 1 },
    ]
    render(<SystemHealthChart data={makeResponse(history)} />)
    const keys = screen.getAllByTestId('line').map((l) => l.getAttribute('data-key'))
    expect(keys).toEqual(['cpu', 'memory', 'disk'])
  })
})
