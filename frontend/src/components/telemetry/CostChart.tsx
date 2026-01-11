import React from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'
import { TelemetryMetricsResponse } from '@/hooks/useTelemetry'

interface CostChartProps {
  data: TelemetryMetricsResponse | null
}

export const CostChart: React.FC<CostChartProps> = ({ data }) => {
  if (!data || data.data.length === 0) {
    return (
      <div className="h-64 flex items-center justify-center text-slate-400">
        No cost data available
      </div>
    )
  }

  // Group by timestamp and aggregate by source
  const byTimestamp = new Map<string, { total: number; bySource: Record<string, number> }>()

  for (const point of data.data) {
    const ts = point.timestamp
    if (!byTimestamp.has(ts)) {
      byTimestamp.set(ts, { total: 0, bySource: {} })
    }
    const record = byTimestamp.get(ts)!
    const source = (point.source as string) || 'unknown'
    const cost = point.sum ?? 0
    record.total += cost
    record.bySource[source] = (record.bySource[source] || 0) + cost
  }

  // Get all unique sources
  const sources = new Set<string>()
  for (const record of byTimestamp.values()) {
    for (const source of Object.keys(record.bySource)) {
      sources.add(source)
    }
  }

  // Convert to chart data with cumulative cost
  let cumulative = 0
  const chartData = Array.from(byTimestamp.entries())
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([timestamp, values]) => {
      cumulative += values.total
      return {
        timestamp,
        time: formatTime(timestamp),
        cost: values.total,
        cumulative,
        ...values.bySource,
      }
    })

  const colors = ['#10b981', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6']
  const sourceList = Array.from(sources)

  return (
    <ResponsiveContainer width="100%" height={250}>
      <LineChart data={chartData} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis
          dataKey="time"
          tick={{ fontSize: 12 }}
          stroke="#94a3b8"
        />
        <YAxis
          tick={{ fontSize: 12 }}
          stroke="#94a3b8"
          tickFormatter={(value) => `$${value.toFixed(3)}`}
        />
        <Tooltip
          formatter={(value: number, name: string) => [
            `$${value.toFixed(4)}`,
            name === 'cumulative' ? 'Cumulative' : name
          ]}
          labelFormatter={(label) => `Time: ${label}`}
          contentStyle={{
            backgroundColor: 'white',
            border: '1px solid #e2e8f0',
            borderRadius: '8px',
          }}
        />
        <Legend />
        <Line
          type="monotone"
          dataKey="cumulative"
          stroke="#10b981"
          strokeWidth={2}
          dot={false}
          name="Cumulative"
        />
        {sourceList.map((source, i) => (
          <Line
            key={source}
            type="monotone"
            dataKey={source}
            stroke={colors[(i + 1) % colors.length]}
            strokeWidth={1}
            dot={false}
            strokeDasharray="5 5"
          />
        ))}
      </LineChart>
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
