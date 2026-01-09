import React, { useMemo } from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import type { SystemMetricsResponse } from '@/hooks/useMetrics'

interface SystemHealthChartProps {
  data: SystemMetricsResponse
}

interface ChartDataPoint {
  timestamp: string
  time: string
  cpu?: number
  memory?: number
  disk?: number
}

export const SystemHealthChart: React.FC<SystemHealthChartProps> = ({ data }) => {
  const chartData = useMemo(() => {
    // Group metrics by timestamp (rounded to minute)
    const byTime = new Map<string, ChartDataPoint>()

    for (const point of data.history) {
      const date = new Date(point.timestamp)
      // Round to nearest minute (create new Date to avoid mutating original)
      const roundedDate = new Date(date.getTime() - (date.getSeconds() * 1000) - date.getMilliseconds())
      const key = roundedDate.toISOString()

      if (!byTime.has(key)) {
        byTime.set(key, {
          timestamp: key,
          time: formatTime(roundedDate),
        })
      }

      const entry = byTime.get(key)!
      if (point.name === 'system.cpu_percent') {
        // Prefer system CPU - always use it when available
        entry.cpu = point.value ?? undefined
      } else if (point.name === 'process.cpu_percent' && entry.cpu === undefined) {
        // Fall back to process CPU only if system CPU not set
        entry.cpu = point.value ?? undefined
      } else if (point.name === 'system.memory_percent') {
        entry.memory = point.value ?? undefined
      } else if (point.name === 'system.disk_usage_percent') {
        entry.disk = point.value ?? undefined
      }
    }

    // Sort by timestamp and return array
    return Array.from(byTime.values())
      .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  }, [data])

  if (chartData.length === 0) {
    return <p className="no-data">No system metrics available</p>
  }

  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={chartData} margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis
          dataKey="time"
          tick={{ fontSize: 12 }}
          stroke="#718096"
        />
        <YAxis
          domain={[0, 100]}
          tick={{ fontSize: 12 }}
          stroke="#718096"
          tickFormatter={(value) => `${value}%`}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: 'white',
            border: '1px solid #e2e8f0',
            borderRadius: '4px',
          }}
          formatter={(value: number) => [`${value.toFixed(1)}%`, '']}
          labelStyle={{ color: '#2d3748' }}
        />
        <Legend />
        <Line
          type="monotone"
          dataKey="cpu"
          name="CPU"
          stroke="#667eea"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
        <Line
          type="monotone"
          dataKey="memory"
          name="Memory"
          stroke="#48bb78"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
        <Line
          type="monotone"
          dataKey="disk"
          name="Disk"
          stroke="#f59e0b"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

const formatTime = (date: Date): string => {
  return date.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  })
}
