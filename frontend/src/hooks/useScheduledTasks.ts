import { useCallback } from 'react'
import { useMCP } from './useMCP'

export interface ScheduledTask {
  id: string
  user_id: number
  task_type: string
  topic: string | null
  message: string | null
  notification_channel: string | null
  notification_target: string | null
  data: Record<string, unknown> | null
  cron_expression: string | null
  next_scheduled_time: string | null
  enabled: boolean
  created_at: string | null
  updated_at: string | null
}

export interface TaskExecution {
  id: string
  task_id: string
  scheduled_time: string | null
  started_at: string | null
  finished_at: string | null
  status: string
  response: string | null
  error_message: string | null
  celery_task_id: string | null
  data: Record<string, unknown> | null
}

export interface UpdateTaskBody {
  enabled?: boolean
  cron_expression?: string
  topic?: string
  message?: string
  notification_channel?: string
  notification_target?: string
  spawn_config?: Record<string, unknown>
}

export const useScheduledTasks = () => {
  const { mcpCall } = useMCP()

  const listTasks = useCallback(async (filters?: {
    task_type?: string
    enabled?: boolean
    limit?: number
  }): Promise<ScheduledTask[]> => {
    const args: Record<string, unknown> = {}
    if (filters?.task_type) args.task_type = filters.task_type
    if (filters?.enabled !== undefined) args.enabled = filters.enabled
    if (filters?.limit) args.limit = filters.limit

    const result = await mcpCall('scheduler_list_all', args)
    return result[0] as ScheduledTask[]
  }, [mcpCall])

  const updateTask = useCallback(async (taskId: string, updates: UpdateTaskBody): Promise<ScheduledTask> => {
    const result = await mcpCall('scheduler_upsert', {
      task_id: taskId,
      ...updates,
    })
    return result[0] as ScheduledTask
  }, [mcpCall])

  const toggleTask = useCallback(async (taskId: string, enabled: boolean): Promise<ScheduledTask> => {
    return updateTask(taskId, { enabled })
  }, [updateTask])

  const deleteTask = useCallback(async (taskId: string): Promise<void> => {
    await mcpCall('scheduler_delete', { task_id: taskId })
  }, [mcpCall])

  const getExecutions = useCallback(async (taskId: string, limit = 20): Promise<TaskExecution[]> => {
    const result = await mcpCall('scheduler_executions', { task_id: taskId, limit })
    return result[0] as TaskExecution[]
  }, [mcpCall])

  return { listTasks, updateTask, toggleTask, deleteTask, getExecutions }
}
