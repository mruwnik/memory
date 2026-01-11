import { useCallback } from 'react'
import { useMCP } from './useMCP'

export type TaskStatus = 'pending' | 'in_progress' | 'done' | 'cancelled'
export type TaskPriority = 'low' | 'medium' | 'high' | 'urgent'

export interface TodoTask {
  id: number
  task_title: string
  due_date: string | null
  priority: TaskPriority | null
  status: TaskStatus
  recurrence: string | null
  completed_at: string | null
  source_item_id: number | null
  tags: string[]
  inserted_at: string | null
}

export interface TodoTaskCreate {
  task_title: string
  due_date?: string
  priority?: TaskPriority
  recurrence?: string
  tags?: string[]
}

export interface TodoTaskUpdate {
  task_title?: string
  due_date?: string
  priority?: TaskPriority
  status?: TaskStatus
  tags?: string[]
}

export interface TodoTaskFilters {
  status?: TaskStatus | string  // Can be single status or comma-separated (e.g., 'pending,in_progress')
  priority?: TaskPriority
  include_completed?: boolean
  limit?: number
  offset?: number
}

export const useTasks = () => {
  const { mcpCall } = useMCP()

  const listTasks = useCallback(async (filters: TodoTaskFilters = {}): Promise<TodoTask[]> => {
    // MCP only accepts single status values, not comma-separated
    // If status contains comma (e.g., 'pending,in_progress'), don't pass status
    // and rely on include_completed=false to filter out done/cancelled
    const statusParam = filters.status?.includes(',') ? undefined : filters.status

    const result = await mcpCall<TodoTask[][]>('organizer_list_tasks', {
      status: statusParam,
      priority: filters.priority,
      include_completed: filters.include_completed ?? false,
      limit: filters.limit ?? 50,
      offset: filters.offset ?? 0,
    })
    // mcpCall returns array from .map(), unwrap the first element
    return result?.[0] || []
  }, [mcpCall])

  const getTask = useCallback(async (taskId: number): Promise<TodoTask | null> => {
    const result = await mcpCall<{ success?: boolean; task?: TodoTask; error?: string }[]>('organizer_get_task', {
      task_id: taskId,
    })
    // mcpCall returns array from .map(), unwrap the first element
    const response = result?.[0]
    if (response?.error) {
      throw new Error(response.error)
    }
    return response?.task || null
  }, [mcpCall])

  const createTask = useCallback(async (data: TodoTaskCreate): Promise<TodoTask> => {
    const result = await mcpCall<TodoTask[]>('organizer_create_task', {
      title: data.task_title,
      due_date: data.due_date,
      priority: data.priority,
      recurrence: data.recurrence,
      tags: data.tags,
    })
    return result?.[0]
  }, [mcpCall])

  const updateTask = useCallback(async (taskId: number, data: TodoTaskUpdate): Promise<TodoTask> => {
    const result = await mcpCall<TodoTask[]>('organizer_update_task', {
      task_id: taskId,
      title: data.task_title,
      due_date: data.due_date,
      priority: data.priority,
      status: data.status,
      tags: data.tags,
    })
    return result?.[0]
  }, [mcpCall])

  const completeTask = useCallback(async (taskId: number): Promise<TodoTask> => {
    const result = await mcpCall<TodoTask[]>('organizer_complete_task_by_id', {
      task_id: taskId,
    })
    return result?.[0]
  }, [mcpCall])

  const deleteTask = useCallback(async (taskId: number): Promise<{ deleted: boolean }> => {
    const result = await mcpCall<{ deleted: boolean }[]>('organizer_delete_task', {
      task_id: taskId,
    })
    return result?.[0]
  }, [mcpCall])

  return {
    listTasks,
    getTask,
    createTask,
    updateTask,
    completeTask,
    deleteTask,
  }
}
