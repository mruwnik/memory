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
  api_key_id: number | null
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
  api_key_id?: number | null
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

export interface SessionStats {
  session_id: string
  label: string
  summary: string | null
  project: string | null
  total_tokens: number
  total_cost: number
  event_count: number
  first_seen: string
  last_seen: string
}

export interface SessionStatsResponse {
  from_time: string
  to_time: string
  sessions: SessionStats[]
}

export interface TelemetryApiKey {
  id: number
  name: string | null
  key_type: string
  user_id: number
  revoked: boolean
}

export interface TelemetryUser {
  id: number
  name: string
  email: string
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
      apiKeyId?: number
      userId?: number  // Admin only: filter by specific user, omit for all users
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
    if (options.apiKeyId !== undefined) params.set('api_key_id', options.apiKeyId.toString())
    if (options.userId !== undefined) params.set('user_id', options.userId.toString())

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
      apiKeyId?: number
      userId?: number  // Admin only: filter by specific user, omit for all users
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
    if (options.apiKeyId !== undefined) params.set('api_key_id', options.apiKeyId.toString())
    if (options.userId !== undefined) params.set('user_id', options.userId.toString())

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
      userId?: number  // Admin only: filter by specific user, omit for all users
    } = {}
  ): Promise<ToolUsageResponse> => {
    const params = new URLSearchParams()
    if (options.from) params.set('from', options.from.toISOString())
    if (options.to) params.set('to', options.to.toISOString())
    if (options.userId !== undefined) params.set('user_id', options.userId.toString())

    const response = await apiCall(`/sessions/stats/tool-usage?${params}`)
    if (!response.ok) {
      throw new Error(`Failed to fetch tool usage: ${response.status}`)
    }
    return parseJsonResponse<ToolUsageResponse>(response)
  }, [apiCall])

  const getSessionStats = useCallback(async (
    options: {
      from?: Date
      to?: Date
      apiKeyId?: number
      userId?: number
    } = {}
  ): Promise<SessionStatsResponse> => {
    const params = new URLSearchParams()
    if (options.from) params.set('from', options.from.toISOString())
    if (options.to) params.set('to', options.to.toISOString())
    if (options.apiKeyId !== undefined) params.set('api_key_id', options.apiKeyId.toString())
    if (options.userId !== undefined) params.set('user_id', options.userId.toString())

    const response = await apiCall(`/telemetry/sessions?${params}`)
    if (!response.ok) {
      throw new Error(`Failed to fetch session stats: ${response.status}`)
    }
    return parseJsonResponse<SessionStatsResponse>(response)
  }, [apiCall])

  const getApiKeysWithTelemetry = useCallback(async (userId?: number): Promise<TelemetryApiKey[]> => {
    const params = new URLSearchParams()
    if (userId !== undefined) params.set('user_id', userId.toString())
    const response = await apiCall(`/telemetry/api-keys?${params}`)
    if (!response.ok) {
      throw new Error(`Failed to fetch telemetry API keys: ${response.status}`)
    }
    return parseJsonResponse<TelemetryApiKey[]>(response)
  }, [apiCall])

  const getUsersWithTelemetry = useCallback(async (): Promise<TelemetryUser[]> => {
    const response = await apiCall('/telemetry/users')
    if (!response.ok) {
      throw new Error(`Failed to fetch telemetry users: ${response.status}`)
    }
    return parseJsonResponse<TelemetryUser[]>(response)
  }, [apiCall])

  return {
    getRawEvents,
    getMetrics,
    getToolUsage,
    getSessionStats,
    getApiKeysWithTelemetry,
    getUsersWithTelemetry,
  }
}
