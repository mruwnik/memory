import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useSources, TodoTask } from '@/hooks/useSources'
import './TodoWidget.css'

const PRIORITY_ORDER = { urgent: 0, high: 1, medium: 2, low: 3 }
const PRIORITY_COLORS = {
  urgent: '#dc2626',
  high: '#ea580c',
  medium: '#ca8a04',
  low: '#16a34a',
}

interface TodoWidgetProps {
  maxItems?: number
}

const TodoWidget = ({ maxItems = 5 }: TodoWidgetProps) => {
  const { listTasks, completeTask, createTask } = useSources()
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
        const aPriority = a.priority ? PRIORITY_ORDER[a.priority] : 4
        const bPriority = b.priority ? PRIORITY_ORDER[b.priority] : 4
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
      return { text: 'Overdue', className: 'overdue' }
    }
    if (taskDate.getTime() === today.getTime()) {
      return { text: 'Today', className: 'today' }
    }
    if (taskDate.getTime() === tomorrow.getTime()) {
      return { text: 'Tomorrow', className: 'tomorrow' }
    }
    return {
      text: date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      className: ''
    }
  }

  if (loading) {
    return (
      <div className="todo-widget">
        <div className="todo-widget-header">
          <h3>Tasks</h3>
        </div>
        <div className="todo-widget-loading">Loading...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="todo-widget">
        <div className="todo-widget-header">
          <h3>Tasks</h3>
        </div>
        <div className="todo-widget-error">{error}</div>
      </div>
    )
  }

  return (
    <div className="todo-widget">
      <div className="todo-widget-header">
        <h3>Tasks</h3>
        <span className="todo-count">{tasks.length}</span>
      </div>

      <form onSubmit={handleAddTask} className="todo-add-form">
        <input
          type="text"
          placeholder="Add a task..."
          value={newTaskTitle}
          onChange={(e) => setNewTaskTitle(e.target.value)}
          disabled={isAdding}
          className="todo-add-input"
        />
        <button type="submit" disabled={isAdding || !newTaskTitle.trim()} className="todo-add-btn">
          +
        </button>
      </form>

      {tasks.length === 0 ? (
        <div className="todo-widget-empty">
          No pending tasks
        </div>
      ) : (
        <ul className="todo-list">
          {tasks.map(task => (
            <li key={task.id} className="todo-item">
              <button
                className="todo-checkbox"
                onClick={() => handleComplete(task.id)}
                title="Mark as complete"
              >
                <span className="checkmark"></span>
              </button>
              <div className="todo-content">
                <span className="todo-title">{task.task_title}</span>
                <div className="todo-meta">
                  {task.priority && (
                    <span
                      className="todo-priority"
                      style={{ backgroundColor: PRIORITY_COLORS[task.priority] }}
                    >
                      {task.priority}
                    </span>
                  )}
                  {task.due_date && (
                    <span className={`todo-due ${formatDueDate(task.due_date).className}`}>
                      {formatDueDate(task.due_date).text}
                    </span>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="todo-widget-footer">
        <Link to="/ui/tasks" className="view-all-link">View all tasks</Link>
      </div>
    </div>
  )
}

export default TodoWidget
