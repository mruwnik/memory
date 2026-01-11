import React from 'react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { TelemetryMetricsResponse } from '@/hooks/useTelemetry'

interface SessionActivityChartProps {
  data: TelemetryMetricsResponse | null
}

export const SessionActivityChart: React.FC<SessionActivityChartProps> = ({ data }) => {
  if (!data || data.data.length === 0) {
    return (
      <div className="h-64 flex items-center justify-center text-slate-400">
        No session data available
      </div>
    )
  }

  const chartData = data.data
    .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
    .map(point => ({
      timestamp: point.timestamp,
      time: formatTime(point.timestamp),
      sessions: point.count,
    }))

  return (
    <ResponsiveContainer width="100%" height={250}>
      <BarChart data={chartData} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis
          dataKey="time"
          tick={{ fontSize: 12 }}
          stroke="#94a3b8"
        />
        <YAxis
          tick={{ fontSize: 12 }}
          stroke="#94a3b8"
          allowDecimals={false}
        />
        <Tooltip
          formatter={(value: number) => [value, 'Sessions']}
          labelFormatter={(label) => `Time: ${label}`}
          contentStyle={{
            backgroundColor: 'white',
            border: '1px solid #e2e8f0',
            borderRadius: '8px',
          }}
        />
        <Bar
          dataKey="sessions"
          fill="#8b5cf6"
          radius={[4, 4, 0, 0]}
        />
      </BarChart>
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
