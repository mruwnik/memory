import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useSources, TodoTask, TodoTaskFilters } from '@/hooks/useSources'
import './Tasks.css'

const PRIORITY_ORDER: Record<string, number> = { urgent: 0, high: 1, medium: 2, low: 3 }
const PRIORITY_COLORS: Record<string, string> = {
  urgent: '#dc2626',
  high: '#ea580c',
  medium: '#ca8a04',
  low: '#16a34a',
}

type StatusFilter = 'active' | 'completed' | 'all'

const Tasks = () => {
  const { listTasks, completeTask, createTask, updateTask, deleteTask } = useSources()
  const [tasks, setTasks] = useState<TodoTask[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [newTaskTitle, setNewTaskTitle] = useState('')
  const [newTaskPriority, setNewTaskPriority] = useState<string>('')
  const [newTaskDueDate, setNewTaskDueDate] = useState('')
  const [isAdding, setIsAdding] = useState(false)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('active')
  const [editingTask, setEditingTask] = useState<number | null>(null)
  const [editTitle, setEditTitle] = useState('')

  const loadTasks = useCallback(async () => {
    setLoading(true)
    try {
      const filters: TodoTaskFilters = {
        include_completed: statusFilter === 'completed' || statusFilter === 'all',
        limit: 100,
      }
      if (statusFilter === 'completed') {
        filters.status = 'done'
      } else if (statusFilter === 'active') {
        filters.status = 'pending,in_progress'
      }

      const data = await listTasks(filters)
      // Sort by priority then due date
      const sorted = [...data].sort((a, b) => {
        // Completed tasks at the end
        if (a.status === 'done' && b.status !== 'done') return 1
        if (a.status !== 'done' && b.status === 'done') return -1
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
      setTasks(sorted)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tasks')
    } finally {
      setLoading(false)
    }
  }, [listTasks, statusFilter])

  useEffect(() => {
    loadTasks()
  }, [loadTasks])

  const handleComplete = async (taskId: number) => {
    try {
      await completeTask(taskId)
      loadTasks()
    } catch (e) {
      console.error('Failed to complete task:', e)
    }
  }

  const handleDelete = async (taskId: number) => {
    if (!confirm('Are you sure you want to delete this task?')) return
    try {
      await deleteTask(taskId)
      setTasks(prev => prev.filter(t => t.id !== taskId))
    } catch (e) {
      console.error('Failed to delete task:', e)
    }
  }

  const handleAddTask = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newTaskTitle.trim()) return

    setIsAdding(true)
    try {
      await createTask({
        task_title: newTaskTitle.trim(),
        priority: newTaskPriority || undefined,
        due_date: newTaskDueDate || undefined,
      })
      setNewTaskTitle('')
      setNewTaskPriority('')
      setNewTaskDueDate('')
      loadTasks()
    } catch (e) {
      console.error('Failed to create task:', e)
    } finally {
      setIsAdding(false)
    }
  }

  const handleStartEdit = (task: TodoTask) => {
    setEditingTask(task.id)
    setEditTitle(task.task_title)
  }

  const handleSaveEdit = async (taskId: number) => {
    if (!editTitle.trim()) return
    try {
      await updateTask(taskId, { task_title: editTitle.trim() })
      setEditingTask(null)
      loadTasks()
    } catch (e) {
      console.error('Failed to update task:', e)
    }
  }

  const handleCancelEdit = () => {
    setEditingTask(null)
    setEditTitle('')
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

  const activeCount = tasks.filter(t => t.status !== 'done' && t.status !== 'cancelled').length
  const completedCount = tasks.filter(t => t.status === 'done').length

  return (
    <div className="tasks-page">
      <header className="tasks-header">
        <Link to="/ui/dashboard" className="back-btn">Back</Link>
        <h1>Tasks</h1>
        <div className="tasks-stats">
          <span className="stat">{activeCount} active</span>
          <span className="stat">{completedCount} completed</span>
        </div>
      </header>

      <div className="tasks-content">
        {/* Add Task Form */}
        <form onSubmit={handleAddTask} className="add-task-form">
          <div className="form-row">
            <input
              type="text"
              placeholder="What needs to be done?"
              value={newTaskTitle}
              onChange={(e) => setNewTaskTitle(e.target.value)}
              disabled={isAdding}
              className="task-input"
            />
            <button type="submit" disabled={isAdding || !newTaskTitle.trim()} className="add-btn">
              Add Task
            </button>
          </div>
          <div className="form-row form-options">
            <select
              value={newTaskPriority}
              onChange={(e) => setNewTaskPriority(e.target.value)}
              className="priority-select"
            >
              <option value="">No priority</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="urgent">Urgent</option>
            </select>
            <input
              type="date"
              value={newTaskDueDate}
              onChange={(e) => setNewTaskDueDate(e.target.value)}
              className="date-input"
            />
          </div>
        </form>

        {/* Filters */}
        <div className="tasks-filters">
          <button
            className={`filter-btn ${statusFilter === 'active' ? 'active' : ''}`}
            onClick={() => setStatusFilter('active')}
          >
            Active
          </button>
          <button
            className={`filter-btn ${statusFilter === 'completed' ? 'active' : ''}`}
            onClick={() => setStatusFilter('completed')}
          >
            Completed
          </button>
          <button
            className={`filter-btn ${statusFilter === 'all' ? 'active' : ''}`}
            onClick={() => setStatusFilter('all')}
          >
            All
          </button>
        </div>

        {/* Error */}
        {error && (
          <div className="tasks-error">
            <p>{error}</p>
            <button onClick={loadTasks}>Retry</button>
          </div>
        )}

        {/* Loading */}
        {loading && <div className="tasks-loading">Loading tasks...</div>}

        {/* Task List */}
        {!loading && tasks.length === 0 && (
          <div className="tasks-empty">
            {statusFilter === 'completed'
              ? 'No completed tasks yet'
              : statusFilter === 'active'
              ? 'No active tasks. Add one above!'
              : 'No tasks yet. Add one above!'}
          </div>
        )}

        {!loading && tasks.length > 0 && (
          <ul className="tasks-list">
            {tasks.map(task => (
              <li key={task.id} className={`task-item ${task.status === 'done' ? 'completed' : ''}`}>
                <button
                  className={`task-checkbox ${task.status === 'done' ? 'checked' : ''}`}
                  onClick={() => task.status !== 'done' && handleComplete(task.id)}
                  title={task.status === 'done' ? 'Completed' : 'Mark as complete'}
                  disabled={task.status === 'done'}
                >
                  {task.status === 'done' && <span className="checkmark">&#10003;</span>}
                </button>

                <div className="task-content">
                  {editingTask === task.id ? (
                    <div className="task-edit-form">
                      <input
                        type="text"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        className="task-edit-input"
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleSaveEdit(task.id)
                          if (e.key === 'Escape') handleCancelEdit()
                        }}
                      />
                      <button onClick={() => handleSaveEdit(task.id)} className="save-btn">Save</button>
                      <button onClick={handleCancelEdit} className="cancel-btn">Cancel</button>
                    </div>
                  ) : (
                    <>
                      <span className={`task-title ${task.status === 'done' ? 'done' : ''}`}>
                        {task.task_title}
                      </span>
                      <div className="task-meta">
                        {task.priority && (
                          <span
                            className="task-priority"
                            style={{ backgroundColor: PRIORITY_COLORS[task.priority] }}
                          >
                            {task.priority}
                          </span>
                        )}
                        {task.due_date && (
                          <span className={`task-due ${formatDueDate(task.due_date).className}`}>
                            {formatDueDate(task.due_date).text}
                          </span>
                        )}
                        {task.status === 'in_progress' && (
                          <span className="task-status in-progress">In Progress</span>
                        )}
                      </div>
                    </>
                  )}
                </div>

                {editingTask !== task.id && (
                  <div className="task-actions">
                    {task.status !== 'done' && (
                      <button onClick={() => handleStartEdit(task)} className="edit-btn" title="Edit">
                        &#9998;
                      </button>
                    )}
                    <button onClick={() => handleDelete(task.id)} className="delete-btn" title="Delete">
                      &#10005;
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default Tasks
