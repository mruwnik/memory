import React, { useMemo } from 'react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import type { MetricSummaryItem } from '@/hooks/useMetrics'

interface McpUsageChartProps {
  data: MetricSummaryItem[]
}

interface ChartDataPoint {
  name: string
  shortName: string
  successCount: number
  failureCount: number
  total: number
  avgDuration: number | null
}

export const McpUsageChart: React.FC<McpUsageChartProps> = ({ data }) => {
  const chartData = useMemo(() => {
    return data
      .map(m => ({
        name: m.name,
        shortName: shortenToolName(m.name),
        successCount: m.success_count,
        failureCount: m.failure_count,
        total: m.count,
        avgDuration: m.avg_duration_ms,
      }))
      .sort((a, b) => b.total - a.total)
      .slice(0, 15) // Top 15 by usage
  }, [data])

  if (chartData.length === 0) {
    return <p className="no-data">No MCP tool calls available</p>
  }

  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart
        data={chartData}
        margin={{ top: 5, right: 30, left: 0, bottom: 60 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis
          dataKey="shortName"
          tick={{ fontSize: 10, angle: -45, textAnchor: 'end' }}
          stroke="#718096"
          height={60}
          interval={0}
        />
        <YAxis
          tick={{ fontSize: 12 }}
          stroke="#718096"
        />
        <Tooltip
          contentStyle={{
            backgroundColor: 'white',
            border: '1px solid #e2e8f0',
            borderRadius: '4px',
          }}
          formatter={(value: number, name: string) => [value, name]}
          labelFormatter={(label: string, payload: Array<{ payload: ChartDataPoint }>) => {
            if (payload.length > 0) {
              const item = payload[0].payload
              const duration = item.avgDuration !== null ? ` (${Math.round(item.avgDuration)}ms avg)` : ''
              return `${item.name}${duration}`
            }
            return label
          }}
        />
        <Legend />
        <Bar
          dataKey="successCount"
          name="Success"
          stackId="a"
          fill="#667eea"
          radius={[0, 0, 0, 0]}
        />
        <Bar
          dataKey="failureCount"
          name="Failures"
          stackId="a"
          fill="#e53e3e"
          radius={[4, 4, 0, 0]}
        />
      </BarChart>
    </ResponsiveContainer>
  )
}

const shortenToolName = (name: string): string => {
  if (name.length <= 15) {
    return name
  }
  return name.substring(0, 12) + '...'
}
