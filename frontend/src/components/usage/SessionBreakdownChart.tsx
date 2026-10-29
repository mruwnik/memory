import React, { useEffect, useState } from 'react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts'
import { SessionStatsResponse, SessionStats } from '@/hooks/useUsage'
import { Badge, Disclosure, EmptyState, Select, Table } from '@/components/shared/ui'
import { formatCost } from '@/utils/format'

type SortBy = 'tokens' | 'cost' | 'events'
type UsageCategory = 'high' | 'medium' | 'low'

const sortLabels: Record<SortBy, string> = {
  tokens: 'Total tokens',
  cost: 'Total cost',
  events: 'Event count',
}

// A sequential scale on the primary colour: heavier usage is darker, and none of it reads as an error.
const categoryColors: Record<UsageCategory, string> = {
  high: '#4c51bf',
  medium: '#7f9cf5',
  low: '#c3dafe',
}

const categoryLabels: Record<UsageCategory, string> = {
  high: 'High usage',
  medium: 'Medium usage',
  low: 'Low usage',
}

interface SessionBreakdownChartProps {
  data: SessionStatsResponse | null
}

const NARROW = '(max-width: 639px)'

// Phones get a narrower label column so the bars keep most of the width.
const useNarrow = () => {
  const [narrow, setNarrow] = useState(() => window.matchMedia?.(NARROW).matches ?? false)
  useEffect(() => {
    const query = window.matchMedia?.(NARROW)
    if (!query) return
    const onChange = () => setNarrow(query.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])
  return narrow
}

const truncate = (text: string, max: number) => (text.length > max ? `${text.slice(0, max - 1)}…` : text)

export const SessionBreakdownChart: React.FC<SessionBreakdownChartProps> = ({ data }) => {
  const [sortBy, setSortBy] = useState<SortBy>('tokens')
  const [showCount, setShowCount] = useState<number>(15)
  const narrow = useNarrow()

  if (!data || data.sessions.length === 0) return <EmptyState message="No session data available" />

  const getSortValue = (session: SessionStats): number => {
    switch (sortBy) {
      case 'tokens':
        return session.total_tokens
      case 'cost':
        return session.total_cost
      case 'events':
        return session.event_count
    }
  }

  // Categorize sessions by token usage percentiles:
  // - High: top 20% (index 0 to 0.2*length)
  // - Medium: next 30% (index 0.2*length to 0.5*length)
  // - Low: bottom 50% (index 0.5*length to end)
  // Thresholds are the token values at the percentile boundaries.
  const sortedByTokens = [...data.sessions].sort((a, b) => b.total_tokens - a.total_tokens)
  const highThreshold = sortedByTokens[Math.floor(sortedByTokens.length * 0.2)]?.total_tokens ?? 0
  const mediumThreshold = sortedByTokens[Math.floor(sortedByTokens.length * 0.5)]?.total_tokens ?? 0

  const getCategory = (tokens: number): UsageCategory => {
    // With fewer than 3 sessions, percentile-based categorization isn't meaningful
    if (sortedByTokens.length < 3) return 'medium'
    if (tokens >= highThreshold) return 'high'
    if (tokens >= mediumThreshold) return 'medium'
    return 'low'
  }

  // Sort by selected metric and take top N
  const sortedSessions = [...data.sessions].sort((a, b) => getSortValue(b) - getSortValue(a))
  const chartData = sortedSessions.slice(0, showCount).map(session => ({
    label: session.label,
    fullSessionId: session.session_id,
    summary: session.summary,
    project: session.project,
    value: getSortValue(session),
    total_tokens: session.total_tokens,
    total_cost: session.total_cost,
    event_count: session.event_count,
    first_seen: session.first_seen,
    last_seen: session.last_seen,
    category: getCategory(session.total_tokens),
  }))

  // Labels aren't unique, and recharts merges equal category values, so bars are keyed by id.
  const labels = new Map(data.sessions.map(s => [s.session_id, s.label]))
  const maxValue = chartData.length > 0 ? Math.max(...chartData.map(d => d.value)) : 1

  // Count sessions by category (single pass)
  const categoryCounts = data.sessions.reduce(
    (acc, s) => {
      acc[getCategory(s.total_tokens)]++
      return acc
    },
    { high: 0, medium: 0, low: 0 }
  )

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="flex gap-2">
          {(['high', 'medium', 'low'] as UsageCategory[]).map((cat) => (
            <Badge key={cat} tone="neutral">
              <span aria-hidden="true" className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ backgroundColor: categoryColors[cat] }} />
              {categoryLabels[cat]}: {categoryCounts[cat]}
            </Badge>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={showCount}
            onChange={(e) => setShowCount(Number(e.target.value))}
            aria-label="Sessions shown"
            className="w-auto"
          >
            <option value={10}>Top 10</option>
            <option value={15}>Top 15</option>
            <option value={25}>Top 25</option>
            <option value={50}>Top 50</option>
          </Select>
          <Select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as SortBy)}
            aria-label="Sort sessions by"
            className="w-auto"
          >
            {(Object.keys(sortLabels) as SortBy[]).map((key) => (
              <option key={key} value={key}>
                Sort by {sortLabels[key]}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={Math.max(300, chartData.length * 28)}>
        <BarChart
          data={chartData}
          layout="vertical"
          margin={{ top: 5, right: 30, left: 10, bottom: 5 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
          <XAxis
            type="number"
            tick={{ fontSize: 12 }}
            stroke="#94a3b8"
            tickFormatter={sortBy === 'cost' ? formatCost : formatNumber}
            domain={[0, maxValue * 1.1]}
          />
          <YAxis
            type="category"
            dataKey="fullSessionId"
            tickFormatter={(id: string) => truncate(labels.get(id) ?? id, narrow ? 16 : 36)}
            tick={{ fontSize: 11 }}
            stroke="#94a3b8"
            width={narrow ? 110 : 230}
          />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload || !payload.length) return null
              const d = payload[0].payload
              return (
                <div className="bg-white border border-slate-200 rounded-lg p-3 shadow-lg text-sm min-w-[260px] max-w-sm">
                  <div className="flex items-center gap-2 mb-2">
                    <span
                      className="w-3 h-3 rounded-full shrink-0"
                      style={{ backgroundColor: categoryColors[d.category as UsageCategory] }}
                    />
                    <p className="font-semibold text-slate-800">{d.label}</p>
                  </div>
                  {d.summary && <p className="text-slate-600 mb-2">{d.summary}</p>}
                  <div className="space-y-1.5 text-slate-600">
                    <p>
                      <span className="text-slate-500">Tokens:</span>{' '}
                      <span className="font-medium">{formatNumber(d.total_tokens)}</span>
                    </p>
                    <p>
                      <span className="text-slate-500">Cost:</span>{' '}
                      <span className="font-medium">{formatCost(d.total_cost)}</span>
                    </p>
                    <p>
                      <span className="text-slate-500">Events:</span>{' '}
                      <span className="font-medium">{d.event_count}</span>
                    </p>
                    <div className="pt-2 mt-2 border-t border-slate-100 text-xs text-slate-500">
                      {d.project && <p className="break-all">{d.project}</p>}
                      <p className="font-mono break-all">{d.fullSessionId}</p>
                      <p>First seen: {formatTimestamp(d.first_seen)}</p>
                      <p>Last seen: {formatTimestamp(d.last_seen)}</p>
                    </div>
                  </div>
                </div>
              )
            }}
          />
          <Bar dataKey="value" radius={[0, 4, 4, 0]}>
            {chartData.map((entry, index) => (
              <Cell
                key={`cell-${index}`}
                fill={categoryColors[entry.category]}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      <div className="mt-4 text-sm text-slate-500 text-center">
        Showing {chartData.length} of {data.sessions.length} sessions
      </div>

      {/* The chart's details live in a hover tooltip; this gives touch and keyboard users the same facts. */}
      <Disclosure summary="Session details" className="mt-4">
        <Table>
          <thead>
            <tr><th>Session</th><th>Usage</th><th>Tokens</th><th>Cost</th><th>Events</th><th>Last seen</th></tr>
          </thead>
          <tbody>
            {chartData.map(d => (
              <tr key={d.fullSessionId}>
                <td className="max-w-xs">
                  <div className="font-medium text-slate-800 break-words">{d.label}</div>
                  {d.summary && <div className="text-xs text-slate-600 line-clamp-2">{d.summary}</div>}
                  {d.project && <div className="text-xs text-slate-500 break-all">{d.project}</div>}
                </td>
                <td className="whitespace-nowrap">{categoryLabels[d.category]}</td>
                <td className="tabular-nums">{formatNumber(d.total_tokens)}</td>
                <td className="tabular-nums">{formatCost(d.total_cost)}</td>
                <td className="tabular-nums">{d.event_count}</td>
                <td className="whitespace-nowrap">{formatTimestamp(d.last_seen)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Disclosure>
    </div>
  )
}

function formatNumber(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return Math.round(value).toString()
}

function formatTimestamp(isoString: string): string {
  const date = new Date(isoString)
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
