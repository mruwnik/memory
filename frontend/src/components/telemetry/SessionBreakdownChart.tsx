import React, { useState } from 'react'
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
import { SessionStatsResponse, SessionStats } from '@/hooks/useTelemetry'

type SortBy = 'tokens' | 'cost' | 'events'
type UsageCategory = 'high' | 'medium' | 'low'

const sortLabels: Record<SortBy, string> = {
  tokens: 'Total Tokens',
  cost: 'Total Cost',
  events: 'Event Count',
}

const categoryColors: Record<UsageCategory, string> = {
  high: '#ef4444',    // red
  medium: '#f59e0b',  // amber
  low: '#10b981',     // green
}

const categoryLabels: Record<UsageCategory, string> = {
  high: 'High Usage',
  medium: 'Medium Usage',
  low: 'Low Usage',
}

interface SessionBreakdownChartProps {
  data: SessionStatsResponse | null
}

export const SessionBreakdownChart: React.FC<SessionBreakdownChartProps> = ({ data }) => {
  const [sortBy, setSortBy] = useState<SortBy>('tokens')
  const [showCount, setShowCount] = useState<number>(15)

  if (!data || data.sessions.length === 0) {
    return (
      <div className="h-64 flex items-center justify-center text-slate-400">
        No session data available
      </div>
    )
  }

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
    sessionId: formatSessionId(session.session_id),
    fullSessionId: session.session_id,
    value: getSortValue(session),
    total_tokens: session.total_tokens,
    total_cost: session.total_cost,
    event_count: session.event_count,
    first_seen: session.first_seen,
    last_seen: session.last_seen,
    category: getCategory(session.total_tokens),
  }))

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
            <span
              key={cat}
              className="inline-flex items-center gap-1.5 text-xs font-medium px-2 py-1 rounded-full"
              style={{ backgroundColor: `${categoryColors[cat]}20`, color: categoryColors[cat] }}
            >
              <span
                className="w-2 h-2 rounded-full"
                style={{ backgroundColor: categoryColors[cat] }}
              />
              {categoryLabels[cat]}: {categoryCounts[cat]}
            </span>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <select
            value={showCount}
            onChange={(e) => setShowCount(Number(e.target.value))}
            className="text-sm border border-slate-200 rounded-md px-2 py-1 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary/50"
          >
            <option value={10}>Top 10</option>
            <option value={15}>Top 15</option>
            <option value={25}>Top 25</option>
            <option value={50}>Top 50</option>
          </select>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as SortBy)}
            className="text-sm border border-slate-200 rounded-md px-2 py-1 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary/50"
          >
            {(Object.keys(sortLabels) as SortBy[]).map((key) => (
              <option key={key} value={key}>
                Sort by {sortLabels[key]}
              </option>
            ))}
          </select>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={Math.max(300, chartData.length * 28)}>
        <BarChart
          data={chartData}
          layout="vertical"
          margin={{ top: 5, right: 30, left: 110, bottom: 5 }}
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
            dataKey="sessionId"
            tick={{ fontSize: 11 }}
            stroke="#94a3b8"
            width={105}
          />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload || !payload.length) return null
              const d = payload[0].payload
              return (
                <div className="bg-white border border-slate-200 rounded-lg p-3 shadow-lg text-sm min-w-[260px]">
                  <div className="flex items-center gap-2 mb-2">
                    <span
                      className="w-3 h-3 rounded-full"
                      style={{ backgroundColor: categoryColors[d.category as UsageCategory] }}
                    />
                    <p className="font-semibold text-slate-800">{d.fullSessionId}</p>
                  </div>
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
    </div>
  )
}

function formatSessionId(id: string): string {
  // Truncate long session IDs to show beginning and end
  if (id.length <= 16) return id
  return `${id.slice(0, 8)}...${id.slice(-6)}`
}

function formatNumber(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return Math.round(value).toString()
}

function formatCost(value: number): string {
  if (value >= 1) return `$${value.toFixed(2)}`
  if (value >= 0.01) return `$${value.toFixed(3)}`
  return `$${value.toFixed(4)}`
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
