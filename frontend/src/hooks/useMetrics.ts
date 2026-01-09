import { useCallback } from 'react'
import { useAuth } from './useAuth'

// API response types
export interface MetricSummaryItem {
  metric_type: string
  name: string
  count: number
  success_count: number
  failure_count: number
  avg_duration_ms: number | null
  min_duration_ms: number | null
  max_duration_ms: number | null
}

export interface MetricsSummaryResponse {
  period_hours: number
  since: string
  metrics: MetricSummaryItem[]
}

export interface MetricEvent {
  id: number
  timestamp: string
  name: string
  duration_ms: number | null
  status: string | null
  labels: Record<string, unknown>
}

export interface TaskMetricsResponse {
  period_hours: number
  count: number
  events: MetricEvent[]
}

export interface McpMetricsResponse {
  period_hours: number
  count: number
  events: MetricEvent[]
}

export interface SystemMetricPoint {
  timestamp: string
  name: string
  value: number | null
}

export interface SystemMetricsResponse {
  period_hours: number
  latest: Record<string, number>
  history: SystemMetricPoint[]
}

export type TimeRange = '1h' | '6h' | '24h' | '7d'

export const timeRangeToHours = (range: TimeRange): number => {
  switch (range) {
    case '1h': return 1
    case '6h': return 6
    case '24h': return 24
    case '7d': return 168
    default: return 24
  }
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get('content-type')
  if (!contentType?.includes('application/json')) {
    const text = await response.text()
    throw new Error(`Expected JSON response but got ${contentType}: ${text.substring(0, 100)}`)
  }
  try {
    return await response.json()
  } catch (err) {
    throw new Error(`Failed to parse JSON response: ${err instanceof Error ? err.message : 'Unknown error'}`)
  }
}

export const useMetrics = () => {
  const { apiCall } = useAuth()

  const getSummary = useCallback(async (
    hours: number = 24,
    metricType?: string,
    name?: string
  ): Promise<MetricsSummaryResponse> => {
    const params = new URLSearchParams()
    params.set('hours', hours.toString())
    if (metricType) params.set('metric_type', metricType)
    if (name) params.set('name', name)

    const response = await apiCall(`/api/metrics/summary?${params}`)
    if (!response.ok) {
      throw new Error(`Failed to fetch metrics summary: ${response.status}`)
    }
    return parseJsonResponse<MetricsSummaryResponse>(response)
  }, [apiCall])

  const getTaskMetrics = useCallback(async (
    hours: number = 24,
    name?: string,
    limit: number = 100
  ): Promise<TaskMetricsResponse> => {
    const params = new URLSearchParams()
    params.set('hours', hours.toString())
    params.set('limit', limit.toString())
    if (name) params.set('name', name)

    const response = await apiCall(`/api/metrics/tasks?${params}`)
    if (!response.ok) {
      throw new Error(`Failed to fetch task metrics: ${response.status}`)
    }
    return parseJsonResponse<TaskMetricsResponse>(response)
  }, [apiCall])

  const getMcpMetrics = useCallback(async (
    hours: number = 24,
    name?: string,
    limit: number = 100
  ): Promise<McpMetricsResponse> => {
    const params = new URLSearchParams()
    params.set('hours', hours.toString())
    params.set('limit', limit.toString())
    if (name) params.set('name', name)

    const response = await apiCall(`/api/metrics/mcp?${params}`)
    if (!response.ok) {
      throw new Error(`Failed to fetch MCP metrics: ${response.status}`)
    }
    return parseJsonResponse<McpMetricsResponse>(response)
  }, [apiCall])

  const getSystemMetrics = useCallback(async (
    hours: number = 1,
    name?: string
  ): Promise<SystemMetricsResponse> => {
    const params = new URLSearchParams()
    params.set('hours', hours.toString())
    if (name) params.set('name', name)

    const response = await apiCall(`/api/metrics/system?${params}`)
    if (!response.ok) {
      throw new Error(`Failed to fetch system metrics: ${response.status}`)
    }
    return parseJsonResponse<SystemMetricsResponse>(response)
  }, [apiCall])

  return {
    getSummary,
    getTaskMetrics,
    getMcpMetrics,
    getSystemMetrics,
  }
}
