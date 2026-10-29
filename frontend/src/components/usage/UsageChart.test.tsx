import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@/test/utils'

vi.mock('recharts', () => {
  const Passthrough = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>
  return {
    ResponsiveContainer: Passthrough,
    BarChart: ({ data, children }: { data?: unknown[]; children?: React.ReactNode }) => (
      <div data-testid="chart" data-chart-data={JSON.stringify(data ?? [])}>{children}</div>
    ),
    Bar: ({ dataKey, name, fill }: { dataKey?: string; name?: string; fill?: string }) => (
      <div data-testid="bar" data-key={dataKey} data-name={name} data-fill={fill} />
    ),
    XAxis: () => <div />,
    YAxis: () => <div />,
    CartesianGrid: () => <div />,
    Tooltip: () => <div />,
    Legend: () => <div />,
  }
})

import { UsageChart, bucketStarts, usageGroups } from './UsageChart'
import type { UsageResponse, UsageRow } from '@/hooks/useUsage'

const row = (fields: Partial<UsageRow>): UsageRow => ({
  timestamp: '2026-10-27T00:00:00+00:00',
  cost_usd: 0,
  input_tokens: 0,
  output_tokens: 0,
  cache_read_tokens: 0,
  cache_write_tokens: 0,
  tokens: 0,
  ...fields,
})

const response = (data: UsageRow[], extra: Partial<UsageResponse> = {}): UsageResponse => ({
  from: '2026-10-26T12:00:00+00:00',
  to: '2026-10-29T12:00:00+00:00',
  interval: 'day',
  group_by: ['kind'],
  data,
  users: {},
  keys: {},
  ...extra,
})

const chartData = () => JSON.parse(screen.getByTestId('chart').getAttribute('data-chart-data')!)
const barNames = () => screen.getAllByTestId('bar').map((b) => b.getAttribute('data-name'))

describe('usageGroups', () => {
  it('folds keys you do not own into one shared key group', () => {
    const data = response(
      [row({ key: 1, cost_usd: 1 }), row({ key: 2, cost_usd: 2 }), row({ key: 3, cost_usd: 0.5 })],
      { keys: { '1': 'shared key', '2': 'shared key', '3': 'mine' } },
    )
    expect(usageGroups(data, 'key').map((g) => [g.name, g.cost])).toEqual([['Shared key', 3], ['mine', 0.5]])
  })

  it('tells apart groups that share a name', () => {
    const data = response(
      [row({ user: 1, cost_usd: 2 }), row({ user: 2, cost_usd: 1 })],
      { users: { '1': 'Sam', '2': 'Sam' } },
    )
    expect(usageGroups(data, 'user').map((g) => g.name)).toEqual(['Sam (#1)', 'Sam (#2)'])
  })

  it.each([
    ['kind', row({ kind: 'claude-code' }), 'Claude Code'],
    ['model', row({ model: null }), 'Unknown model'],
    ['agent', row({ agent: null }), 'No agent'],
    ['key', row({ key: null }), 'No key'],
    ['user', row({ user: 7 }), 'User #7'],
  ] as const)('labels a %s group', (groupBy, r, label) => {
    expect(usageGroups(response([r]), groupBy)[0].name).toBe(label)
  })

  it('sums cache reads and writes together', () => {
    const data = response([row({ kind: 'run', cache_read_tokens: 3, cache_write_tokens: 4 })])
    expect(usageGroups(data, 'kind')[0].cache).toBe(7)
  })
})

describe('bucketStarts', () => {
  it('covers every day from the one holding `from` up to `to`', () => {
    expect(bucketStarts('2026-10-26T12:00:00Z', '2026-10-29T00:00:00Z', 'day').map((t) => new Date(t).toISOString())).toEqual([
      '2026-10-26T00:00:00.000Z',
      '2026-10-27T00:00:00.000Z',
      '2026-10-28T00:00:00.000Z',
    ])
  })

  it('starts weeks on Monday, like Postgres', () => {
    expect(new Date(bucketStarts('2026-10-29T00:00:00Z', '2026-10-30T00:00:00Z', 'week')[0]).toISOString())
      .toBe('2026-10-26T00:00:00.000Z')
  })

  it.each([
    ['hour', '2026-10-29T00:30:00Z', '2026-10-29T03:00:00Z', 3],
    ['month', '2026-08-15T00:00:00Z', '2026-10-29T00:00:00Z', 3],
  ] as const)('counts %s buckets', (interval, from, to, count) => {
    expect(bucketStarts(from, to, interval)).toHaveLength(count)
  })
})

describe('UsageChart', () => {
  it('fills quiet days with empty buckets', () => {
    render(<UsageChart data={response([row({ kind: 'memory', cost_usd: 1 })])} groupBy="kind" measure="cost" />)
    expect(chartData()).toHaveLength(4)
    expect(chartData()[1]).toEqual({ time: Date.parse('2026-10-27T00:00:00Z'), memory: 1 })
  })

  it('plots tokens when asked', () => {
    render(<UsageChart data={response([row({ kind: 'memory', cost_usd: 1, tokens: 900 })])} groupBy="kind" measure="tokens" />)
    expect(chartData()[1].memory).toBe(900)
  })

  it('folds the smallest series into Other beyond eight', () => {
    const models = Array.from({ length: 9 }, (_, i) => row({ model: `m${i}`, cost_usd: 9 - i }))
    render(<UsageChart data={response(models)} groupBy="model" measure="cost" />)
    expect(barNames()).toEqual(['m0', 'm1', 'm2', 'm3', 'm4', 'm5', 'm6', 'Other'])
    expect(chartData()[1].__other).toBe(3)
  })

  it('gives each of eight series its own colour', () => {
    const models = Array.from({ length: 8 }, (_, i) => row({ model: `m${i}`, cost_usd: 8 - i }))
    render(<UsageChart data={response(models)} groupBy="model" measure="cost" />)
    const fills = screen.getAllByTestId('bar').map((b) => b.getAttribute('data-fill'))
    expect(new Set(fills).size).toBe(8)
  })

  it('lists every group with its totals in the table', () => {
    render(<UsageChart data={response([row({ kind: 'run', cost_usd: 0.25, input_tokens: 1200, tokens: 1200 })])} groupBy="kind" measure="cost" />)
    const cells = within(screen.getByRole('table')).getAllByRole('cell').map((c) => c.textContent)
    expect(cells).toEqual(['Agent runs', '$0.25', '1.2K', '0', '0', '1.2K'])
  })
})
