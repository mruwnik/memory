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

import { TaskDurationChart } from './TaskDurationChart'
import type { MetricSummaryItem } from '@/hooks/useMetrics'

const readData = () =>
  JSON.parse(screen.getByTestId('chart').getAttribute('data-chart-data') ?? '[]')

const makeItem = (overrides: Partial<MetricSummaryItem>): MetricSummaryItem => ({
  metric_type: 'task',
  name: 'task',
  count: 1,
  success_count: 1,
  failure_count: 0,
  avg_duration_ms: 100,
  min_duration_ms: null,
  max_duration_ms: null,
  ...overrides,
})

describe('TaskDurationChart', () => {
  it('shows the empty state when there is no data', () => {
    render(<TaskDurationChart data={[]} />)
    expect(screen.getByText('No task metrics available')).toBeInTheDocument()
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument()
  })

  it('filters out tasks with null duration or zero count', () => {
    const data = [
      makeItem({ name: 'keep', avg_duration_ms: 50, count: 2 }),
      makeItem({ name: 'noDuration', avg_duration_ms: null, count: 5 }),
      makeItem({ name: 'zeroCount', avg_duration_ms: 80, count: 0 }),
    ]
    render(<TaskDurationChart data={data} />)
    const names = readData().map((d: { name: string }) => d.name)
    expect(names).toEqual(['keep'])
  })

  it('shows the empty state when every task is filtered out', () => {
    render(<TaskDurationChart data={[makeItem({ avg_duration_ms: null })]} />)
    expect(screen.getByText('No task metrics available')).toBeInTheDocument()
  })

  it('sorts by average duration descending and rounds the value', () => {
    const data = [
      makeItem({ name: 'fast', avg_duration_ms: 10.4 }),
      makeItem({ name: 'slow', avg_duration_ms: 99.6 }),
      makeItem({ name: 'mid', avg_duration_ms: 50.5 }),
    ]
    render(<TaskDurationChart data={data} />)
    const rows = readData()
    expect(rows.map((d: { name: string }) => d.name)).toEqual(['slow', 'mid', 'fast'])
    expect(rows[0].avgDuration).toBe(100)
    expect(rows[2].avgDuration).toBe(10)
  })

  it('caps the chart at the top 10 tasks', () => {
    const data = Array.from({ length: 15 }, (_, i) =>
      makeItem({ name: `t${i}`, avg_duration_ms: i + 1 }),
    )
    render(<TaskDurationChart data={data} />)
    expect(readData()).toHaveLength(10)
  })

  it.each([
    [10, 10, 100],
    [8, 10, 80],
    [5, 10, 50],
    [0, 10, 0],
  ])('computes successRate from %i/%i successes as %i%%', (success, count, expected) => {
    render(
      <TaskDurationChart
        data={[makeItem({ name: 'task', count, success_count: success, avg_duration_ms: 5 })]}
      />,
    )
    expect(readData()[0].successRate).toBe(expected)
  })

  it.each([
    [100, '#667eea'],
    [85, '#f59e0b'],
    [50, '#e53e3e'],
  ])('colors the bar by success rate %i%% as %s', (rate, color) => {
    const success = Math.round(rate)
    render(
      <TaskDurationChart
        data={[
          makeItem({
            name: 'task',
            count: 100,
            success_count: success,
            avg_duration_ms: 5,
          }),
        ]}
      />,
    )
    expect(screen.getByTestId('cell').getAttribute('data-fill')).toBe(color)
  })

  it('strips known task-name prefixes for the axis label', () => {
    const data = [
      makeItem({ name: 'memory.workers.tasks.email.process', avg_duration_ms: 5 }),
      makeItem({ name: 'tasks.cleanup', avg_duration_ms: 4 }),
    ]
    render(<TaskDurationChart data={data} />)
    const shortNames = readData().map((d: { shortName: string }) => d.shortName)
    expect(shortNames).toContain('email.process')
    expect(shortNames).toContain('cleanup')
  })
})
