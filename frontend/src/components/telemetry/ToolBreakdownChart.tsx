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
import { ToolUsageResponse, ToolCallStats } from '@/hooks/useTelemetry'

type MetricType = 'total' | 'median' | 'p75' | 'p90' | 'p99' | 'min' | 'max'

const metricLabels: Record<MetricType, string> = {
  total: 'Total Tokens',
  median: 'Median per Call',
  p75: 'P75 per Call',
  p90: 'P90 per Call',
  p99: 'P99 per Call',
  min: 'Min per Call',
  max: 'Max per Call',
}

interface ToolBreakdownChartProps {
  data: ToolUsageResponse | null
}

export const ToolBreakdownChart: React.FC<ToolBreakdownChartProps> = ({ data }) => {
  const [metric, setMetric] = useState<MetricType>('total')

  if (!data || data.tools.length === 0) {
    return (
      <div className="h-64 flex items-center justify-center text-slate-400">
        No tool usage data available
      </div>
    )
  }

  const getMetricValue = (tool: typeof data.tools[0]): number => {
    if (metric === 'total') return tool.total_tokens
    if (!tool.per_call) return 0
    return tool.per_call[metric]
  }

  // Sort by selected metric and take top 15
  const sortedTools = [...data.tools].sort((a, b) => getMetricValue(b) - getMetricValue(a))
  const chartData = sortedTools.slice(0, 15).map(tool => ({
    tool: formatToolName(tool.tool_name),
    fullName: tool.tool_name,
    value: getMetricValue(tool),
    total_tokens: tool.total_tokens,
    input_tokens: tool.input_tokens,
    output_tokens: tool.output_tokens,
    cache_read_tokens: tool.cache_read_tokens,
    cache_creation_tokens: tool.cache_creation_tokens,
    call_count: tool.call_count,
    per_call: tool.per_call,
  }))

  const colors = [
    '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
    '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
    '#14b8a6', '#eab308', '#a855f7', '#22c55e', '#0ea5e9',
  ]

  const maxValue = Math.max(...chartData.map(d => d.value))

  return (
    <div>
      <div className="flex justify-end mb-3">
        <select
          value={metric}
          onChange={(e) => setMetric(e.target.value as MetricType)}
          className="text-sm border border-slate-200 rounded-md px-2 py-1 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary/50"
        >
          {(Object.keys(metricLabels) as MetricType[]).map((key) => (
            <option key={key} value={key}>
              {metricLabels[key]}
            </option>
          ))}
        </select>
      </div>
      <ResponsiveContainer width="100%" height={Math.max(250, chartData.length * 32)}>
      <BarChart
        data={chartData}
        layout="vertical"
        margin={{ top: 5, right: 30, left: 100, bottom: 5 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
        <XAxis
          type="number"
          tick={{ fontSize: 12 }}
          stroke="#94a3b8"
          tickFormatter={formatNumber}
          domain={[0, maxValue * 1.1]}
        />
        <YAxis
          type="category"
          dataKey="tool"
          tick={{ fontSize: 11 }}
          stroke="#94a3b8"
          width={95}
        />
        <Tooltip
          content={({ active, payload }) => {
            if (!active || !payload || !payload.length) return null
            const d = payload[0].payload
            const perCall: ToolCallStats | null = d.per_call
            return (
              <div className="bg-white border border-slate-200 rounded-lg p-3 shadow-lg text-sm min-w-[240px]">
                <p className="font-semibold text-slate-800 mb-2">{d.fullName}</p>
                <div className="space-y-1 text-slate-600">
                  <p>Calls: <span className="font-medium">{d.call_count}</span></p>
                  <p>Total: <span className="font-medium">{formatNumber(d.total_tokens)}</span></p>
                  <p className="text-xs text-slate-500 pt-1 border-t border-slate-100">
                    Input: {formatNumber(d.input_tokens)} · Output: {formatNumber(d.output_tokens)}
                  </p>
                  <p className="text-xs text-slate-500">
                    Cache read: {formatNumber(d.cache_read_tokens)} · Created: {formatNumber(d.cache_creation_tokens)}
                  </p>
                  {perCall && (
                    <div className="pt-2 mt-2 border-t border-slate-200">
                      <p className="font-medium text-slate-700 mb-1">Per Call Stats</p>
                      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs text-slate-500">
                        <p>Median: <span className="font-medium">{formatNumber(perCall.median)}</span></p>
                        <p>P75: <span className="font-medium">{formatNumber(perCall.p75)}</span></p>
                        <p>P90: <span className="font-medium">{formatNumber(perCall.p90)}</span></p>
                        <p>P99: <span className="font-medium">{formatNumber(perCall.p99)}</span></p>
                        <p>Min: <span className="font-medium">{formatNumber(perCall.min)}</span></p>
                        <p>Max: <span className="font-medium">{formatNumber(perCall.max)}</span></p>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )
          }}
        />
        <Bar dataKey="value" radius={[0, 4, 4, 0]}>
          {chartData.map((_, index) => (
            <Cell key={`cell-${index}`} fill={colors[index % colors.length]} />
          ))}
        </Bar>
      </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function formatToolName(name: string): string {
  if (name === 'unknown') return 'Unknown'
  // Truncate long names and make more readable
  const formatted = name
    .replace(/^mcp__/, '')
    .replace(/__/g, ': ')
    .replace(/_/g, ' ')
  return formatted.length > 20 ? formatted.slice(0, 18) + '...' : formatted
}

function formatNumber(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return Math.round(value).toString()
}
