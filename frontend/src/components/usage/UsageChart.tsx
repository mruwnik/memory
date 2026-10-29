import React from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { UsageGroupBy, UsageInterval, UsageResponse, UsageRow } from '@/hooks/useUsage'
import { Table } from '@/components/shared/ui'
import { formatCost } from '@/utils/format'
import { formatUserDateTime } from '@/utils/timezones'

export type Measure = 'cost' | 'tokens'

export const GROUP_LABELS: Record<UsageGroupBy, string> = {
  kind: 'Source',
  model: 'Model',
  agent: 'Agent',
  key: 'LLM key',
  user: 'User',
}

const KIND_LABELS: Record<string, string> = { memory: 'Memory', run: 'Agent runs', 'claude-code': 'Claude Code' }
const SHARED_KEY_LABEL = 'shared key'
const MAX_SERIES = 7
const MAX_BUCKETS = 1000
// One colour per series that can be shown: MAX_SERIES, or MAX_SERIES + 1 when that saves an "Other" of one.
const COLORS = ['#5a67d8', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#0ea5e9', '#ec4899', '#84cc16']
const OTHER = { id: '__other', name: 'Other', color: '#94a3b8' }

export interface UsageGroup {
  id: string
  name: string
  cost: number
  input: number
  output: number
  cache: number
  tokens: number
}

const identify = (row: UsageRow, groupBy: UsageGroupBy, data: UsageResponse): [string, string] => {
  switch (groupBy) {
    case 'kind': return [String(row.kind), KIND_LABELS[row.kind ?? ''] ?? row.kind ?? 'Unknown']
    case 'model': return [String(row.model), row.model ?? 'Unknown model']
    case 'agent': return [String(row.agent), row.agent ?? 'No agent']
    case 'user':
      return row.user == null ? ['none', 'No user'] : [String(row.user), data.users[row.user] ?? `User #${row.user}`]
    case 'key': {
      if (row.key == null) return ['none', 'No key']
      const label = data.keys[row.key] ?? `Key #${row.key}`
      // Keys you don't own all read "shared key" and can't be told apart, so they are one series.
      return label === SHARED_KEY_LABEL ? ['shared', 'Shared key'] : [String(row.key), label]
    }
  }
}

/** Totals per group, largest cost first. Groups that share a name (two users' "default" keys) get their id appended. */
export const usageGroups = (data: UsageResponse, groupBy: UsageGroupBy): UsageGroup[] => {
  const groups = new Map<string, UsageGroup>()
  for (const row of data.data) {
    const [id, name] = identify(row, groupBy, data)
    const group = groups.get(id) ?? { id, name, cost: 0, input: 0, output: 0, cache: 0, tokens: 0 }
    group.cost += row.cost_usd
    group.input += row.input_tokens
    group.output += row.output_tokens
    group.cache += row.cache_read_tokens + row.cache_write_tokens
    group.tokens += row.tokens
    groups.set(id, group)
  }
  const all = [...groups.values()]
  const nameCount = all.reduce((counts, g) => counts.set(g.name, (counts.get(g.name) ?? 0) + 1), new Map<string, number>())
  return all
    .map(g => (nameCount.get(g.name)! > 1 ? { ...g, name: `${g.name} (#${g.id})` } : g))
    .sort((a, b) => b.cost - a.cost || b.tokens - a.tokens)
}

const truncateUtc = (moment: Date, interval: UsageInterval): Date => {
  const start = new Date(Date.UTC(
    moment.getUTCFullYear(),
    moment.getUTCMonth(),
    interval === 'month' ? 1 : moment.getUTCDate(),
    interval === 'hour' ? moment.getUTCHours() : 0,
  ))
  // Postgres date_trunc('week') starts weeks on Monday.
  if (interval === 'week') start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7))
  return start
}

const STEP: Record<UsageInterval, (d: Date) => void> = {
  hour: d => d.setUTCHours(d.getUTCHours() + 1),
  day: d => d.setUTCDate(d.getUTCDate() + 1),
  week: d => d.setUTCDate(d.getUTCDate() + 7),
  month: d => d.setUTCMonth(d.getUTCMonth() + 1),
}

/** Every bucket start in [from, to), so quiet periods show as gaps rather than being squeezed out. */
export const bucketStarts = (from: string, to: string, interval: UsageInterval): number[] => {
  const end = new Date(to).getTime()
  const cursor = truncateUtc(new Date(from), interval)
  const starts: number[] = []
  while (cursor.getTime() < end && starts.length < MAX_BUCKETS) {
    starts.push(cursor.getTime())
    STEP[interval](cursor)
  }
  return starts
}

// Day and longer buckets are UTC days: showing them in local time would shift them onto the previous day west of UTC.
export const formatBucket = (time: number, interval: UsageInterval, long = false): string => {
  const utc = { timeZone: 'UTC' } as const
  switch (interval) {
    case 'hour':
      return formatUserDateTime(new Date(time), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
    case 'day':
      return new Date(time).toLocaleDateString(undefined, long ? { ...utc, weekday: 'short', month: 'short', day: 'numeric' } : { ...utc, month: 'short', day: 'numeric' })
    case 'week':
      return `${long ? 'Week of ' : ''}${new Date(time).toLocaleDateString(undefined, { ...utc, month: 'short', day: 'numeric' })}`
    case 'month':
      return new Date(time).toLocaleDateString(undefined, { ...utc, month: long ? 'long' : 'short', year: 'numeric' })
  }
}

export const formatTokens = (tokens: number): string => {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`
  return tokens.toString()
}

const formatAxisCost = (usd: number) => (usd === 0 ? '$0' : `$${usd.toFixed(usd < 0.1 ? 3 : 2)}`)

const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

interface UsageChartProps {
  data: UsageResponse
  groupBy: UsageGroupBy
  measure: Measure
}

export const UsageChart: React.FC<UsageChartProps> = ({ data, groupBy, measure }) => {
  const groups = usageGroups(data, groupBy)
  const byMeasure = [...groups].sort((a, b) => b[measure] - a[measure])
  const shown = byMeasure.length > MAX_SERIES + 1 ? byMeasure.slice(0, MAX_SERIES) : byMeasure
  const shownIds = new Set(shown.map(g => g.id))
  const series = [
    ...shown.map((g, i) => ({ id: g.id, name: g.name, color: COLORS[i % COLORS.length] })),
    ...(shown.length < byMeasure.length ? [OTHER] : []),
  ]

  const rows = new Map<number, Record<string, number>>(
    bucketStarts(data.from, data.to, data.interval).map(time => [time, { time }]),
  )
  for (const row of data.data) {
    const time = new Date(row.timestamp).getTime()
    const [id] = identify(row, groupBy, data)
    const key = shownIds.has(id) ? id : OTHER.id
    const point = rows.get(time) ?? { time }
    point[key] = (point[key] ?? 0) + (measure === 'cost' ? row.cost_usd : row.tokens)
    rows.set(time, point)
  }
  const chartData = [...rows.values()].sort((a, b) => a.time - b.time)
  const format = measure === 'cost' ? formatCost : (v: number) => v.toLocaleString()
  const animate = !reducedMotion()

  return (
    <div className="space-y-4">
      <div role="img" aria-label={`${measure === 'cost' ? 'Cost' : 'Tokens'} by ${GROUP_LABELS[groupBy].toLowerCase()} per ${data.interval}, as stacked bars. The table below lists the totals.`}>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
            <XAxis dataKey="time" tickFormatter={t => formatBucket(t, data.interval)} tick={{ fontSize: 12 }} stroke="#64748b" minTickGap={16} />
            <YAxis tickFormatter={measure === 'cost' ? formatAxisCost : formatTokens} tick={{ fontSize: 12 }} stroke="#64748b" width={56} />
            <Tooltip
              formatter={(value: number, name: string) => [format(value), name]}
              labelFormatter={t => formatBucket(Number(t), data.interval, true)}
              contentStyle={{ backgroundColor: 'white', border: '1px solid #e2e8f0', borderRadius: '8px', fontSize: 12 }}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {series.map(s => (
              <Bar key={s.id} dataKey={s.id} name={s.name} stackId="usage" fill={s.color} isAnimationActive={animate} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <Table>
        <thead>
          <tr>
            <th scope="col">{GROUP_LABELS[groupBy]}</th>
            <th scope="col" className="text-right">Cost</th>
            <th scope="col" className="text-right">Input</th>
            <th scope="col" className="text-right">Output</th>
            <th scope="col" className="text-right">Cache</th>
            <th scope="col" className="text-right">Total tokens</th>
          </tr>
        </thead>
        <tbody>
          {groups.map(g => (
            <tr key={g.id}>
              <td className="break-words">{g.name}</td>
              <td className="text-right tabular-nums">{formatCost(g.cost)}</td>
              <td className="text-right tabular-nums">{formatTokens(g.input)}</td>
              <td className="text-right tabular-nums">{formatTokens(g.output)}</td>
              <td className="text-right tabular-nums">{formatTokens(g.cache)}</td>
              <td className="text-right tabular-nums">{formatTokens(g.tokens)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  )
}
