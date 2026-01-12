import { useCallback } from 'react'
import { useAuth } from './useAuth'

export interface TelemetryEvent {
  id: number
  timestamp: string
  event_type: string
  name: string
  value: number | null
  session_id: string | null
  source: string | null
  tool_name: string | null
  attributes: Record<string, unknown>
  body: string | null
}

export interface TelemetryRawResponse {
  total: number
  offset: number
  limit: number
  from: string
  to: string
  events: TelemetryEvent[]
}

export interface TelemetryMetricDataPoint {
  timestamp: string
  count: number
  sum: number | null
  min: number | null
  max: number | null
  source?: string | null
  tool_name?: string | null
  session_id?: string | null
  [key: string]: unknown
}

export interface TelemetryMetricsResponse {
  metric: string
  granularity_minutes: number
  from: string
  to: string
  group_by: string[]
  data: TelemetryMetricDataPoint[]
}

export interface ToolCallStats {
  median: number
  p75: number
  p90: number
  p99: number
  min: number
  max: number
}

export interface ToolUsageStats {
  tool_name: string
  call_count: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_creation_tokens: number
  total_tokens: number
  per_call: ToolCallStats | null
}

export interface ToolUsageResponse {
  from_time: string
  to_time: string
  session_count: number
  tools: ToolUsageStats[]
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get('content-type')
  if (!contentType?.includes('application/json')) {
    const text = await response.text()
    throw new Error(`Expected JSON response but got ${contentType}: ${text.substring(0, 100)}`)
  }
  return response.json()
}

export const useTelemetry = () => {
  const { apiCall } = useAuth()

  const getRawEvents = useCallback(async (
    options: {
      eventType?: string
      name?: string
      sessionId?: string
      source?: string
      from?: Date
      to?: Date
      limit?: number
      offset?: number
    } = {}
  ): Promise<TelemetryRawResponse> => {
    const params = new URLSearchParams()
    if (options.eventType) params.set('event_type', options.eventType)
    if (options.name) params.set('name', options.name)
    if (options.sessionId) params.set('session_id', options.sessionId)
    if (options.source) params.set('source', options.source)
    if (options.from) params.set('from', options.from.toISOString())
    if (options.to) params.set('to', options.to.toISOString())
    if (options.limit) params.set('limit', options.limit.toString())
    if (options.offset) params.set('offset', options.offset.toString())

    const response = await apiCall(`/telemetry/raw?${params}`)
    if (!response.ok) {
      throw new Error(`Failed to fetch telemetry events: ${response.status}`)
    }
    return parseJsonResponse<TelemetryRawResponse>(response)
  }, [apiCall])

  const getMetrics = useCallback(async (
    metric: string,
    options: {
      granularity?: number
      from?: Date
      to?: Date
      source?: string
      groupBy?: string[]
    } = {}
  ): Promise<TelemetryMetricsResponse> => {
    const params = new URLSearchParams()
    params.set('metric', metric)
    if (options.granularity) params.set('granularity', options.granularity.toString())
    if (options.from) params.set('from', options.from.toISOString())
    if (options.to) params.set('to', options.to.toISOString())
    if (options.source) params.set('source', options.source)
    if (options.groupBy) {
      options.groupBy.forEach(g => params.append('group_by', g))
    }

    const response = await apiCall(`/telemetry/metrics?${params}`)
    if (!response.ok) {
      throw new Error(`Failed to fetch telemetry metrics: ${response.status}`)
    }
    return parseJsonResponse<TelemetryMetricsResponse>(response)
  }, [apiCall])

  const getToolUsage = useCallback(async (
    options: {
      from?: Date
      to?: Date
    } = {}
  ): Promise<ToolUsageResponse> => {
    const params = new URLSearchParams()
    if (options.from) params.set('from', options.from.toISOString())
    if (options.to) params.set('to', options.to.toISOString())

    const response = await apiCall(`/sessions/stats/tool-usage?${params}`)
    if (!response.ok) {
      throw new Error(`Failed to fetch tool usage: ${response.status}`)
    }
    return parseJsonResponse<ToolUsageResponse>(response)
  }, [apiCall])

  return {
    getRawEvents,
    getMetrics,
    getToolUsage,
  }
}
