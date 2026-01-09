import React, { useMemo } from 'react'
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
import type { MetricSummaryItem } from '@/hooks/useMetrics'

interface TaskDurationChartProps {
  data: MetricSummaryItem[]
}

interface ChartDataPoint {
  name: string
  shortName: string
  avgDuration: number
  count: number
  successRate: number
}

export const TaskDurationChart: React.FC<TaskDurationChartProps> = ({ data }) => {
  const chartData = useMemo(() => {
    return data
      .filter(m => m.avg_duration_ms !== null && m.count > 0)
      .map(m => ({
        name: m.name,
        shortName: shortenTaskName(m.name),
        avgDuration: Math.round(m.avg_duration_ms ?? 0),
        count: m.count,
        successRate: m.count > 0 ? Math.round((m.success_count / m.count) * 100) : 0,
      }))
      .sort((a, b) => b.avgDuration - a.avgDuration)
      .slice(0, 10) // Top 10 by duration
  }, [data])

  if (chartData.length === 0) {
    return <p className="no-data">No task metrics available</p>
  }

  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart
        data={chartData}
        layout="vertical"
        margin={{ top: 5, right: 30, left: 100, bottom: 5 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis
          type="number"
          tick={{ fontSize: 12 }}
          stroke="#718096"
          tickFormatter={(value) => `${value}ms`}
        />
        <YAxis
          type="category"
          dataKey="shortName"
          tick={{ fontSize: 11 }}
          stroke="#718096"
          width={90}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: 'white',
            border: '1px solid #e2e8f0',
            borderRadius: '4px',
          }}
          formatter={(value: number, name: string, props: { payload: ChartDataPoint }) => {
            const item = props.payload
            return [
              `${value}ms (${item.count} calls, ${item.successRate}% success)`,
              'Avg Duration',
            ]
          }}
          labelFormatter={(label: string, payload: Array<{ payload: ChartDataPoint }>) => {
            if (payload.length > 0) {
              return payload[0].payload.name
            }
            return label
          }}
        />
        <Bar dataKey="avgDuration" name="Avg Duration" radius={[0, 4, 4, 0]}>
          {chartData.map((entry, index) => (
            <Cell
              key={`cell-${index}`}
              fill={getBarColor(entry.successRate)}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

const shortenTaskName = (name: string): string => {
  // Remove common prefixes
  const shortened = name
    .replace(/^memory\.workers\.tasks\./, '')
    .replace(/^tasks\./, '')

  // Truncate if too long
  if (shortened.length > 20) {
    return shortened.substring(0, 17) + '...'
  }
  return shortened
}

const getBarColor = (successRate: number): string => {
  if (successRate >= 95) return '#667eea' // Primary purple
  if (successRate >= 80) return '#f59e0b' // Warning yellow
  return '#e53e3e' // Error red
}
