import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useTasks, TodoTask } from '@/hooks/useTasks'
import { PRIORITY_ORDER, PRIORITY_COLORS } from '@/constants/priority'

interface TodoWidgetProps {
  maxItems?: number
}

const TodoWidget = ({ maxItems = 5 }: TodoWidgetProps) => {
  const { listTasks, completeTask, createTask } = useTasks()
  const [tasks, setTasks] = useState<TodoTask[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [newTaskTitle, setNewTaskTitle] = useState('')
  const [isAdding, setIsAdding] = useState(false)

  const loadTasks = useCallback(async () => {
    try {
      const data = await listTasks({ limit: maxItems + 5 })
      // Sort by priority then due date
      const sorted = [...data].sort((a, b) => {
        // Priority first
        const aPriority = a.priority ? PRIORITY_ORDER[a.priority] ?? 4 : 4
        const bPriority = b.priority ? PRIORITY_ORDER[b.priority] ?? 4 : 4
        if (aPriority !== bPriority) return aPriority - bPriority
        // Then by due date
        if (a.due_date && b.due_date) {
          return new Date(a.due_date).getTime() - new Date(b.due_date).getTime()
        }
        if (a.due_date) return -1
        if (b.due_date) return 1
        return 0
      })
      setTasks(sorted.slice(0, maxItems))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tasks')
    } finally {
      setLoading(false)
    }
  }, [listTasks, maxItems])

  useEffect(() => {
    loadTasks()
  }, [loadTasks])

  const handleComplete = async (taskId: number) => {
    try {
      await completeTask(taskId)
      // Remove from list
      setTasks(prev => prev.filter(t => t.id !== taskId))
      // Reload to get next task if available
      loadTasks()
    } catch (e) {
      console.error('Failed to complete task:', e)
    }
  }

  const handleAddTask = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newTaskTitle.trim()) return

    setIsAdding(true)
    try {
      await createTask({ task_title: newTaskTitle.trim() })
      setNewTaskTitle('')
      loadTasks()
    } catch (e) {
      console.error('Failed to create task:', e)
    } finally {
      setIsAdding(false)
    }
  }

  const formatDueDate = (dueDate: string) => {
    const date = new Date(dueDate)
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const tomorrow = new Date(today)
    tomorrow.setDate(tomorrow.getDate() + 1)
    const taskDate = new Date(date)
    taskDate.setHours(0, 0, 0, 0)

    if (taskDate.getTime() < today.getTime()) {
      return { text: 'Overdue', className: 'text-[var(--color-danger)] font-medium' }
    }
    if (taskDate.getTime() === today.getTime()) {
      return { text: 'Today', className: 'text-[var(--color-high)] font-medium' }
    }
    if (taskDate.getTime() === tomorrow.getTime()) {
      return { text: 'Tomorrow', className: 'text-[var(--color-medium)]' }
    }
    return {
      text: date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      className: 'text-slate-500'
    }
  }

  if (loading) {
    return (
      <div className="bg-white rounded-xl shadow-md p-6">
        <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-100">
          <h3 className="text-lg font-semibold text-slate-800">Tasks</h3>
        </div>
        <div className="text-slate-500 text-center py-4">Loading...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-white rounded-xl shadow-md p-6">
        <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-100">
          <h3 className="text-lg font-semibold text-slate-800">Tasks</h3>
        </div>
        <div className="text-[var(--color-danger)] text-center py-4">{error}</div>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-xl shadow-md p-6">
      <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-100">
        <h3 className="text-lg font-semibold text-slate-800">Tasks</h3>
        <span className="bg-primary text-white text-xs font-medium px-2 py-1 rounded-full">{tasks.length}</span>
      </div>

      <form onSubmit={handleAddTask} className="flex gap-2 mb-4">
        <input
          type="text"
          placeholder="Add a task..."
          value={newTaskTitle}
          onChange={(e) => setNewTaskTitle(e.target.value)}
          disabled={isAdding}
          className="flex-1 py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
        <button
          type="submit"
          disabled={isAdding || !newTaskTitle.trim()}
          className="w-9 h-9 bg-primary text-white rounded-lg font-bold text-lg hover:bg-primary-dark disabled:bg-slate-300 disabled:cursor-not-allowed"
        >
          +
        </button>
      </form>

      {tasks.length === 0 ? (
        <div className="text-slate-400 text-center py-8 text-sm">
          No pending tasks
        </div>
      ) : (
        <ul className="space-y-2">
          {tasks.map(task => (
            <li key={task.id} className="flex items-start gap-3 p-2 rounded-lg hover:bg-slate-50 transition-colors">
              <button
                className="w-5 h-5 mt-0.5 rounded-full border-2 border-slate-300 flex items-center justify-center hover:border-primary hover:bg-primary/10 transition-colors shrink-0"
                onClick={() => handleComplete(task.id)}
                title="Mark as complete"
              >
              </button>
              <div className="flex-1 min-w-0">
                <span className="block text-slate-700 text-sm leading-tight">{task.task_title}</span>
                <div className="flex gap-2 mt-1 flex-wrap">
                  {task.priority && (
                    <span className={`${PRIORITY_COLORS[task.priority]} text-white text-xs px-1.5 py-0.5 rounded`}>
                      {task.priority}
                    </span>
                  )}
                  {task.due_date && (
                    <span className={`text-xs ${formatDueDate(task.due_date).className}`}>
                      {formatDueDate(task.due_date).text}
                    </span>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4 pt-3 border-t border-slate-100 text-center">
        <Link to="/ui/tasks" className="text-primary text-sm hover:underline">View all tasks</Link>
      </div>
    </div>
  )
}

export default TodoWidget
