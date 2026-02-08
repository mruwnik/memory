import { useCallback } from 'react'
import { useAuth } from './useAuth'

export interface BeatScheduleEntry {
  key: string
  name: string
  task: string
  schedule_display: string
  last_run: string | null
  last_status: string | null
  last_duration_ms: number | null
}

export interface IngestionTypeSummary {
  job_type: string
  pending: number
  processing: number
  complete: number
  failed: number
  total: number
}

export interface RecentFailure {
  id: number
  job_type: string
  error_message: string | null
  updated_at: string | null
}

export interface TaskMetrics {
  total: number
  success: number
  failure: number
  avg_duration_ms: number | null
}

export interface IngestionSummary {
  by_type: IngestionTypeSummary[]
  recent_failures: RecentFailure[]
  task_metrics: TaskMetrics
}

export const useCelery = () => {
  const { apiCall } = useAuth()

  const getBeatSchedule = useCallback(async (): Promise<BeatScheduleEntry[]> => {
    const response = await apiCall('/api/celery/beat-schedule')
    if (!response.ok) throw new Error('Failed to fetch beat schedule')
    return response.json()
  }, [apiCall])

  const getIngestionSummary = useCallback(async (): Promise<IngestionSummary> => {
    const response = await apiCall('/api/celery/ingestion-summary')
    if (!response.ok) throw new Error('Failed to fetch ingestion summary')
    return response.json()
  }, [apiCall])

  return { getBeatSchedule, getIngestionSummary }
}
