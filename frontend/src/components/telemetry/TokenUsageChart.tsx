import React from 'react'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'
import { TelemetryMetricsResponse } from '@/hooks/useTelemetry'

interface TokenUsageChartProps {
  data: TelemetryMetricsResponse | null
}

export const TokenUsageChart: React.FC<TokenUsageChartProps> = ({ data }) => {
  if (!data || data.data.length === 0) {
    return (
      <div className="h-64 flex items-center justify-center text-slate-400">
        No token usage data available
      </div>
    )
  }

  // Group by timestamp and aggregate by source
  const byTimestamp = new Map<string, Record<string, number>>()

  for (const point of data.data) {
    const ts = point.timestamp
    if (!byTimestamp.has(ts)) {
      byTimestamp.set(ts, {})
    }
    const record = byTimestamp.get(ts)!
    const source = (point.source as string) || 'unknown'
    record[source] = (record[source] || 0) + (point.sum ?? 0)
  }

  // Get all unique sources
  const sources = new Set<string>()
  for (const record of byTimestamp.values()) {
    for (const source of Object.keys(record)) {
      sources.add(source)
    }
  }

  // Convert to chart data
  const chartData = Array.from(byTimestamp.entries())
    .map(([timestamp, values]) => ({
      timestamp,
      time: formatTime(timestamp),
      ...values,
    }))
    .sort((a, b) => a.timestamp.localeCompare(b.timestamp))

  const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6']
  const sourceList = Array.from(sources)

  return (
    <ResponsiveContainer width="100%" height={250}>
      <AreaChart data={chartData} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis
          dataKey="time"
          tick={{ fontSize: 12 }}
          stroke="#94a3b8"
        />
        <YAxis
          tick={{ fontSize: 12 }}
          stroke="#94a3b8"
          tickFormatter={(value) => formatNumber(value)}
        />
        <Tooltip
          formatter={(value: number, name: string) => [formatNumber(value), name]}
          labelFormatter={(label) => `Time: ${label}`}
          contentStyle={{
            backgroundColor: 'white',
            border: '1px solid #e2e8f0',
            borderRadius: '8px',
          }}
        />
        <Legend />
        {sourceList.map((source, i) => (
          <Area
            key={source}
            type="monotone"
            dataKey={source}
            stackId="1"
            stroke={colors[i % colors.length]}
            fill={colors[i % colors.length]}
            fillOpacity={0.6}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  )
}

function formatTime(isoString: string): string {
  const date = new Date(isoString)
  return date.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function formatNumber(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return value.toString()
}
