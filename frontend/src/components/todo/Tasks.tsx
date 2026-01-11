import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useTasks, TodoTask, TodoTaskFilters } from '@/hooks/useTasks'
import { PRIORITY_ORDER, PRIORITY_COLORS } from '@/constants/priority'

type StatusFilter = 'active' | 'completed' | 'all'

const Tasks = () => {
  const { listTasks, completeTask, createTask, updateTask, deleteTask } = useTasks()
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
      const sorted = [...data].sort((a, b) => {
        if (a.status === 'done' && b.status !== 'done') return 1
        if (a.status !== 'done' && b.status === 'done') return -1
        const aPriority = a.priority ? PRIORITY_ORDER[a.priority] ?? 4 : 4
        const bPriority = b.priority ? PRIORITY_ORDER[b.priority] ?? 4 : 4
        if (aPriority !== bPriority) return aPriority - bPriority
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

  const activeCount = tasks.filter(t => t.status !== 'done' && t.status !== 'cancelled').length
  const completedCount = tasks.filter(t => t.status === 'done').length

  return (
    <div className="min-h-screen bg-slate-50 p-8 max-w-3xl mx-auto">
      <header className="flex items-center gap-4 mb-8 pb-4 border-b border-slate-200">
        <Link to="/ui/dashboard" className="bg-slate-50 text-slate-600 border border-slate-200 py-2 px-4 rounded-md text-sm hover:bg-slate-100">
          Back
        </Link>
        <h1 className="text-2xl font-semibold text-slate-800 flex-1">Tasks</h1>
        <div className="flex gap-4 text-sm text-slate-600">
          <span>{activeCount} active</span>
          <span>{completedCount} completed</span>
        </div>
      </header>

      <div className="space-y-6">
        {/* Add Task Form */}
        <form onSubmit={handleAddTask} className="bg-white p-6 rounded-xl shadow-md">
          <div className="flex gap-3 mb-4">
            <input
              type="text"
              placeholder="What needs to be done?"
              value={newTaskTitle}
              onChange={(e) => setNewTaskTitle(e.target.value)}
              disabled={isAdding}
              className="flex-1 py-2 px-3 border border-slate-200 rounded-lg text-base focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
            <button
              type="submit"
              disabled={isAdding || !newTaskTitle.trim()}
              className="bg-primary text-white py-2 px-4 rounded-lg font-medium hover:bg-primary-dark disabled:bg-gray-400 disabled:cursor-not-allowed"
            >
              Add Task
            </button>
          </div>
          <div className="flex gap-3">
            <select
              value={newTaskPriority}
              onChange={(e) => setNewTaskPriority(e.target.value)}
              className="py-2 px-3 border border-slate-200 rounded-lg text-sm bg-white"
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
              className="py-2 px-3 border border-slate-200 rounded-lg text-sm"
            />
          </div>
        </form>

        {/* Filters */}
        <div className="flex gap-2">
          {(['active', 'completed', 'all'] as StatusFilter[]).map((filter) => (
            <button
              key={filter}
              className={`py-2 px-4 rounded-lg text-sm font-medium transition-colors ${
                statusFilter === filter
                  ? 'bg-primary text-white'
                  : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'
              }`}
              onClick={() => setStatusFilter(filter)}
            >
              {filter.charAt(0).toUpperCase() + filter.slice(1)}
            </button>
          ))}
        </div>

        {/* Error */}
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg flex justify-between items-center">
            <p>{error}</p>
            <button onClick={loadTasks} className="text-primary hover:underline">Retry</button>
          </div>
        )}

        {/* Loading */}
        {loading && <div className="text-center py-8 text-slate-500">Loading tasks...</div>}

        {/* Empty State */}
        {!loading && tasks.length === 0 && (
          <div className="text-center py-12 text-slate-500 bg-white rounded-xl">
            {statusFilter === 'completed'
              ? 'No completed tasks yet'
              : statusFilter === 'active'
              ? 'No active tasks. Add one above!'
              : 'No tasks yet. Add one above!'}
          </div>
        )}

        {/* Task List */}
        {!loading && tasks.length > 0 && (
          <ul className="space-y-2">
            {tasks.map(task => (
              <li
                key={task.id}
                className={`bg-white p-4 rounded-lg shadow-sm flex items-center gap-4 ${
                  task.status === 'done' ? 'opacity-60' : ''
                }`}
              >
                <button
                  className={`w-6 h-6 rounded-full border-2 flex items-center justify-center transition-colors ${
                    task.status === 'done'
                      ? 'bg-success border-success text-white'
                      : 'border-slate-300 hover:border-primary'
                  }`}
                  onClick={() => task.status !== 'done' && handleComplete(task.id)}
                  title={task.status === 'done' ? 'Completed' : 'Mark as complete'}
                  disabled={task.status === 'done'}
                >
                  {task.status === 'done' && <span className="text-sm">✓</span>}
                </button>

                <div className="flex-1 min-w-0">
                  {editingTask === task.id ? (
                    <div className="flex gap-2">
                      <input
                        type="text"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        className="flex-1 py-1 px-2 border border-slate-200 rounded text-sm focus:border-primary focus:outline-none"
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleSaveEdit(task.id)
                          if (e.key === 'Escape') handleCancelEdit()
                        }}
                      />
                      <button onClick={() => handleSaveEdit(task.id)} className="text-sm text-primary hover:underline">Save</button>
                      <button onClick={handleCancelEdit} className="text-sm text-slate-500 hover:underline">Cancel</button>
                    </div>
                  ) : (
                    <>
                      <span className={`block truncate ${task.status === 'done' ? 'line-through text-slate-400' : 'text-slate-800'}`}>
                        {task.task_title}
                      </span>
                      <div className="flex gap-2 mt-1 flex-wrap">
                        {task.priority && (
                          <span className={`${PRIORITY_COLORS[task.priority]} text-white text-xs px-2 py-0.5 rounded`}>
                            {task.priority}
                          </span>
                        )}
                        {task.due_date && (
                          <span className={`text-xs ${formatDueDate(task.due_date).className}`}>
                            {formatDueDate(task.due_date).text}
                          </span>
                        )}
                        {task.status === 'in_progress' && (
                          <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded">In Progress</span>
                        )}
                      </div>
                    </>
                  )}
                </div>

                {editingTask !== task.id && (
                  <div className="flex gap-2">
                    {task.status !== 'done' && (
                      <button
                        onClick={() => handleStartEdit(task)}
                        className="text-slate-400 hover:text-slate-600 p-1"
                        title="Edit"
                      >
                        ✎
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(task.id)}
                      className="text-slate-400 hover:text-danger p-1"
                      title="Delete"
                    >
                      ✕
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
