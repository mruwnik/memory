import { useCallback } from 'react'
import { useAuth } from './useAuth'

export interface ContainerInfo {
  name: string
  status: string
  started_at: string | null
}

export interface LogsResponse {
  container: string
  logs: string
  since: string | null
  until: string | null
  lines: number
}

export interface LogsParams {
  since?: Date
  until?: Date
  tail?: number
  filter_text?: string
  timestamps?: boolean
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get('content-type')
  if (!contentType?.includes('application/json')) {
    const text = await response.text()
    throw new Error(`Expected JSON response but got ${contentType}: ${text.substring(0, 100)}`)
  }
  return await response.json()
}

export const useDockerLogs = () => {
  const { apiCall } = useAuth()

  const listContainers = useCallback(async (): Promise<ContainerInfo[]> => {
    const response = await apiCall('/api/docker/containers')
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
      throw new Error(error.detail || `Failed to fetch containers: ${response.status}`)
    }
    return parseJsonResponse<ContainerInfo[]>(response)
  }, [apiCall])

  const getLogs = useCallback(async (
    container: string,
    params: LogsParams = {}
  ): Promise<LogsResponse> => {
    const searchParams = new URLSearchParams()

    if (params.since) searchParams.set('since', params.since.toISOString())
    if (params.until) searchParams.set('until', params.until.toISOString())
    if (params.tail) searchParams.set('tail', params.tail.toString())
    if (params.filter_text) searchParams.set('filter_text', params.filter_text)
    if (params.timestamps !== undefined) searchParams.set('timestamps', params.timestamps.toString())

    const query = searchParams.toString()
    const response = await apiCall(`/api/docker/logs/${encodeURIComponent(container)}${query ? `?${query}` : ''}`)

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
      throw new Error(error.detail || `Failed to fetch logs: ${response.status}`)
    }
    return parseJsonResponse<LogsResponse>(response)
  }, [apiCall])

  return {
    listContainers,
    getLogs,
  }
}
