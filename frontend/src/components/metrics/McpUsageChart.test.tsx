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
    Bar: ({ dataKey, name }: { dataKey?: string; name?: string }) => (
      <div data-testid="bar" data-key={String(dataKey ?? '')} data-name={String(name ?? '')} />
    ),
    XAxis: () => <div />,
    YAxis: () => <div />,
    CartesianGrid: () => <div />,
    Tooltip: () => <div />,
    Legend: () => <div />,
  }
})

import { McpUsageChart } from './McpUsageChart'
import type { MetricSummaryItem } from '@/hooks/useMetrics'

const readData = () =>
  JSON.parse(screen.getByTestId('chart').getAttribute('data-chart-data') ?? '[]')

const makeItem = (overrides: Partial<MetricSummaryItem>): MetricSummaryItem => ({
  metric_type: 'mcp_call',
  name: 'tool',
  count: 0,
  success_count: 0,
  failure_count: 0,
  avg_duration_ms: null,
  min_duration_ms: null,
  max_duration_ms: null,
  ...overrides,
})

describe('McpUsageChart', () => {
  it('shows the empty state when there is no data', () => {
    render(<McpUsageChart data={[]} />)
    expect(screen.getByText('No MCP tool calls available')).toBeInTheDocument()
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument()
  })

  it('sorts tools by total count descending', () => {
    const data = [
      makeItem({ name: 'low', count: 5 }),
      makeItem({ name: 'high', count: 50 }),
      makeItem({ name: 'mid', count: 20 }),
    ]
    render(<McpUsageChart data={data} />)
    expect(readData().map((d: { name: string }) => d.name)).toEqual(['high', 'mid', 'low'])
  })

  it('caps the chart at the top 15 tools', () => {
    const data = Array.from({ length: 20 }, (_, i) => makeItem({ name: `t${i}`, count: i }))
    render(<McpUsageChart data={data} />)
    expect(readData()).toHaveLength(15)
  })

  it('maps success and failure counts onto the chart points', () => {
    const data = [makeItem({ name: 'tool', count: 10, success_count: 7, failure_count: 3 })]
    render(<McpUsageChart data={data} />)
    const point = readData()[0]
    expect(point.successCount).toBe(7)
    expect(point.failureCount).toBe(3)
    expect(point.total).toBe(10)
  })

  it('renders a stacked success bar and failure bar', () => {
    const data = [makeItem({ name: 'tool', count: 1, success_count: 1 })]
    render(<McpUsageChart data={data} />)
    const keys = screen.getAllByTestId('bar').map((b) => b.getAttribute('data-key'))
    expect(keys).toEqual(['successCount', 'failureCount'])
  })

  it('shortens long tool names for the axis label', () => {
    const data = [
      makeItem({ name: 'aVeryLongMcpToolNameExceedingLimit', count: 1 }),
      makeItem({ name: 'short', count: 2 }),
    ]
    render(<McpUsageChart data={data} />)
    const shortNames = readData().map((d: { shortName: string }) => d.shortName)
    expect(shortNames).toContain('short')
    expect(shortNames).toContain('aVeryLongMcp...')
  })
})
