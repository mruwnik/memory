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
    Cell: () => <div data-testid="cell" />,
    XAxis: () => <div />,
    YAxis: () => <div />,
    CartesianGrid: () => <div />,
    Tooltip: () => <div />,
  }
})

import { ToolBreakdownChart } from './ToolBreakdownChart'
import type { ToolUsageResponse, ToolUsageStats } from '@/hooks/useTelemetry'

const readData = () =>
  JSON.parse(screen.getByTestId('chart').getAttribute('data-chart-data') ?? '[]')

const makeTool = (overrides: Partial<ToolUsageStats>): ToolUsageStats => ({
  tool_name: 'Bash',
  call_count: 1,
  input_tokens: 0,
  output_tokens: 0,
  cache_read_tokens: 0,
  cache_creation_tokens: 0,
  total_tokens: 0,
  per_call: null,
  ...overrides,
})

const makeResponse = (tools: ToolUsageStats[]): ToolUsageResponse => ({
  from_time: '2026-05-29T00:00:00Z',
  to_time: '2026-05-29T01:00:00Z',
  session_count: 1,
  tools,
})

describe('ToolBreakdownChart', () => {
  it.each([[null], [makeResponse([])]])('shows empty state for %s', (data) => {
    render(<ToolBreakdownChart data={data} />)
    expect(screen.getByText('No tool usage data available')).toBeInTheDocument()
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument()
  })

  it('sorts tools by total tokens descending by default', () => {
    const data = makeResponse([
      makeTool({ tool_name: 'Low', total_tokens: 10 }),
      makeTool({ tool_name: 'High', total_tokens: 100 }),
      makeTool({ tool_name: 'Mid', total_tokens: 50 }),
    ])
    render(<ToolBreakdownChart data={data} />)
    expect(readData().map((d: { fullName: string }) => d.fullName)).toEqual(['High', 'Mid', 'Low'])
  })

  it('caps the chart at the top 15 tools', () => {
    const tools = Array.from({ length: 20 }, (_, i) =>
      makeTool({ tool_name: `t${i}`, total_tokens: i }),
    )
    render(<ToolBreakdownChart data={makeResponse(tools)} />)
    expect(readData()).toHaveLength(15)
  })

  it('uses the total_tokens metric for value when "total" is selected', () => {
    const data = makeResponse([makeTool({ tool_name: 'Bash', total_tokens: 42 })])
    render(<ToolBreakdownChart data={data} />)
    expect(readData()[0].value).toBe(42)
  })

  it('switches the sort metric to a per-call percentile when selected', async () => {
    const data = makeResponse([
      makeTool({
        tool_name: 'A',
        total_tokens: 1000,
        per_call: { median: 5, p75: 6, p90: 7, p99: 8, min: 1, max: 9 },
      }),
      makeTool({
        tool_name: 'B',
        total_tokens: 10,
        per_call: { median: 500, p75: 600, p90: 700, p99: 800, min: 100, max: 900 },
      }),
    ])
    const { user } = renderWithUser(<ToolBreakdownChart data={data} />)
    // Default total ordering: A before B.
    expect(readData().map((d: { fullName: string }) => d.fullName)).toEqual(['A', 'B'])
    await user.selectOptions(screen.getByRole('combobox'), 'median')
    // Median ordering: B (500) before A (5).
    const sorted = readData()
    expect(sorted.map((d: { fullName: string }) => d.fullName)).toEqual(['B', 'A'])
    expect(sorted[0].value).toBe(500)
  })

  it('treats a missing per_call as zero for per-call metrics', async () => {
    const data = makeResponse([
      makeTool({ tool_name: 'NoStats', total_tokens: 5, per_call: null }),
      makeTool({
        tool_name: 'WithStats',
        total_tokens: 1,
        per_call: { median: 50, p75: 60, p90: 70, p99: 80, min: 10, max: 90 },
      }),
    ])
    const { user } = renderWithUser(<ToolBreakdownChart data={data} />)
    await user.selectOptions(screen.getByRole('combobox'), 'p90')
    const sorted = readData()
    expect(sorted[0].fullName).toBe('WithStats')
    expect(sorted.find((d: { fullName: string }) => d.fullName === 'NoStats').value).toBe(0)
  })

  it('formats mcp tool names by stripping prefixes and truncating', () => {
    const data = makeResponse([
      makeTool({ tool_name: 'mcp__server__do_thing', total_tokens: 1 }),
      makeTool({ tool_name: 'unknown', total_tokens: 2 }),
    ])
    render(<ToolBreakdownChart data={data} />)
    const names = readData().map((d: { tool: string }) => d.tool)
    expect(names).toContain('Unknown')
    expect(names).toContain('server: do thing')
  })
})
