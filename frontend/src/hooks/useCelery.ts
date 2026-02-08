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

export interface TaskActivityEntry {
  task: string
  total: number
  success: number
  failure: number
  avg_duration_ms: number | null
}

export interface TaskTotals {
  total: number
  success: number
  failure: number
  avg_duration_ms: number | null
}

export interface TaskFailure {
  task: string
  timestamp: string | null
  duration_ms: number | null
  labels: Record<string, unknown>
  error: string | null
}

export interface TaskActivity {
  hours: number
  by_task: TaskActivityEntry[]
  totals: TaskTotals
  recent_failures: TaskFailure[]
}

export const useCelery = () => {
  const { apiCall } = useAuth()

  const getBeatSchedule = useCallback(async (): Promise<BeatScheduleEntry[]> => {
    const response = await apiCall('/api/celery/beat-schedule')
    if (!response.ok) throw new Error('Failed to fetch beat schedule')
    return response.json()
  }, [apiCall])

  const getTaskActivity = useCallback(async (hours: number = 24): Promise<TaskActivity> => {
    const response = await apiCall(`/api/celery/task-activity?hours=${hours}`)
    if (!response.ok) throw new Error('Failed to fetch task activity')
    return response.json()
  }, [apiCall])

  return { getBeatSchedule, getTaskActivity }
}
