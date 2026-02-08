import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useScheduledTasks, ScheduledTask, TaskExecution, UpdateTaskBody } from '@/hooks/useScheduledTasks'
import { StatusBadge, formatRelativeTime } from '@/components/sources/shared'

type TypeFilter = 'all' | 'notification' | 'claude_session'

const TYPE_COLORS: Record<string, string> = {
  notification: 'bg-purple-100 text-purple-700',
  claude_session: 'bg-indigo-100 text-indigo-700',
}

const EXECUTION_STATUS_COLORS: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-700',
  running: 'bg-blue-100 text-blue-700',
  completed: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
}

const CHANNELS = ['discord', 'slack', 'email'] as const

function describeCron(expr: string | null): string {
  if (!expr) return 'No schedule'
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return expr

  const [minute, hour, dom, month, dow] = parts

  if (dom === '*' && month === '*') {
    const timeStr = hour !== '*' && minute !== '*'
      ? `at ${hour.padStart(2, '0')}:${minute.padStart(2, '0')} UTC`
      : hour !== '*'
        ? `at ${hour.padStart(2, '0')}:00 UTC`
        : minute !== '*'
          ? `at :${minute.padStart(2, '0')} every hour`
          : ''

    if (dow === '*') {
      if (hour === '*' && minute === '*') return 'Every minute'
      if (hour === '*') return `Every hour at :${minute.padStart(2, '0')}`
      return `Daily ${timeStr}`
    }
    if (dow === '1-5') return `Weekdays ${timeStr}`
    if (dow === '0,6') return `Weekends ${timeStr}`

    const dayNames: Record<string, string> = {
      '0': 'Sun', '1': 'Mon', '2': 'Tue', '3': 'Wed', '4': 'Thu', '5': 'Fri', '6': 'Sat',
    }
    const dayList = dow.split(',').map(d => dayNames[d] || d).join(', ')
    return `${dayList} ${timeStr}`
  }

  return expr
}

function formatFutureTime(dateStr: string | null): string {
  if (!dateStr) return 'Not scheduled'
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = date.getTime() - now.getTime()
  if (diffMs < 0) return formatRelativeTime(dateStr)

  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'any moment'
  if (diffMins < 60) return `in ${diffMins}m`
  if (diffHours < 24) return `in ${diffHours}h`
  if (diffDays < 7) return `in ${diffDays}d`
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return '-'
  const ms = new Date(end).getTime() - new Date(start).getTime()
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

// --- Edit Form ---

interface EditFormProps {
  task: ScheduledTask
  onSave: (updates: UpdateTaskBody) => Promise<void>
  onCancel: () => void
  saving: boolean
  error: string | null
}

const EditForm = ({ task, onSave, onCancel, saving, error }: EditFormProps) => {
  const [topic, setTopic] = useState(task.topic || '')
  const [cronExpr, setCronExpr] = useState(task.cron_expression || '')
  const [message, setMessage] = useState(task.message || '')
  const [channel, setChannel] = useState(task.notification_channel || '')
  const [target, setTarget] = useState(task.notification_target || '')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const updates: UpdateTaskBody = {}
    if (topic !== (task.topic || '')) updates.topic = topic
    if (cronExpr !== (task.cron_expression || '')) updates.cron_expression = cronExpr
    if (message !== (task.message || '')) updates.message = message
    if (task.task_type === 'notification') {
      if (channel !== (task.notification_channel || '')) updates.notification_channel = channel
      if (target !== (task.notification_target || '')) updates.notification_target = target
    }
    if (Object.keys(updates).length === 0) {
      onCancel()
      return
    }
    await onSave(updates)
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div>
        <label className="block text-xs font-medium text-slate-500 mb-1">Topic</label>
        <input
          type="text"
          value={topic}
          onChange={e => setTopic(e.target.value)}
          className="w-full px-3 py-1.5 border border-slate-200 rounded text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-slate-500 mb-1">Cron Expression</label>
        <input
          type="text"
          value={cronExpr}
          onChange={e => setCronExpr(e.target.value)}
          placeholder="0 9 * * *"
          className="w-full px-3 py-1.5 border border-slate-200 rounded text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <p className="text-xs text-slate-400 mt-1">{describeCron(cronExpr)}</p>
      </div>
      <div>
        <label className="block text-xs font-medium text-slate-500 mb-1">Message</label>
        <textarea
          value={message}
          onChange={e => setMessage(e.target.value)}
          rows={3}
          className="w-full px-3 py-1.5 border border-slate-200 rounded text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        />
      </div>
      {task.task_type === 'notification' && (
        <div className="flex gap-3">
          <div className="flex-1">
            <label className="block text-xs font-medium text-slate-500 mb-1">Channel</label>
            <select
              value={channel}
              onChange={e => setChannel(e.target.value)}
              className="w-full px-3 py-1.5 border border-slate-200 rounded text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="">Select...</option>
              {CHANNELS.map(c => (
                <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>
              ))}
            </select>
          </div>
          <div className="flex-1">
            <label className="block text-xs font-medium text-slate-500 mb-1">Target</label>
            <input
              type="text"
              value={target}
              onChange={e => setTarget(e.target.value)}
              placeholder="channel or email"
              className="w-full px-3 py-1.5 border border-slate-200 rounded text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>
      )}
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="flex gap-2 pt-1">
        <button
          type="submit"
          disabled={saving}
          className="bg-primary text-white py-1.5 px-4 rounded text-sm hover:bg-primary-dark disabled:bg-slate-300"
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={saving}
          className="bg-slate-100 text-slate-700 py-1.5 px-4 rounded text-sm hover:bg-slate-200"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}

// --- Execution History ---

interface ExecutionHistoryProps {
  taskId: string
  getExecutions: (taskId: string) => Promise<TaskExecution[]>
}

const ExecutionHistory = ({ taskId, getExecutions }: ExecutionHistoryProps) => {
  const [executions, setExecutions] = useState<TaskExecution[] | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    getExecutions(taskId)
      .then(data => { if (!cancelled) setExecutions(data) })
      .catch(() => { if (!cancelled) setExecutions([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [taskId, getExecutions])

  if (loading) return <p className="text-xs text-slate-400 py-2">Loading history...</p>
  if (!executions || executions.length === 0) {
    return <p className="text-xs text-slate-400 py-2">No execution history</p>
  }

  return (
    <div className="mt-2 space-y-1">
      {executions.map(ex => (
        <div key={ex.id} className="flex items-center gap-3 text-xs text-slate-600 py-1 border-t border-slate-100">
          <span className="text-slate-400 w-20 shrink-0">{formatRelativeTime(ex.scheduled_time)}</span>
          <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${EXECUTION_STATUS_COLORS[ex.status] || 'bg-slate-100 text-slate-600'}`}>
            {ex.status}
          </span>
          <span className="text-slate-400">{formatDuration(ex.started_at, ex.finished_at)}</span>
          {ex.error_message && (
            <span className="text-red-500 truncate flex-1" title={ex.error_message}>{ex.error_message}</span>
          )}
        </div>
      ))}
    </div>
  )
}

// --- Task Card ---

interface TaskCardProps {
  task: ScheduledTask
  onToggle: (taskId: string, enabled: boolean) => void
  onDelete: (taskId: string) => Promise<void>
  onUpdate: (taskId: string, updates: UpdateTaskBody) => Promise<ScheduledTask>
  getExecutions: (taskId: string) => Promise<TaskExecution[]>
}

const TaskCard = ({ task, onToggle, onDelete, onUpdate, getExecutions }: TaskCardProps) => {
  const [editing, setEditing] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const handleSave = async (updates: UpdateTaskBody) => {
    setSaving(true)
    setEditError(null)
    try {
      await onUpdate(task.id, updates)
      setEditing(false)
    } catch (e) {
      setEditError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await onDelete(task.id)
    } catch {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  const typeLabel = task.task_type === 'claude_session' ? 'Claude Session' : 'Notification'

  return (
    <li className="bg-white p-4 rounded-lg shadow-sm border-l-4 border-slate-200">
      {editing ? (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <span className={`px-2 py-0.5 rounded text-xs font-medium ${TYPE_COLORS[task.task_type] || 'bg-slate-100 text-slate-600'}`}>
              {typeLabel}
            </span>
            <span className="text-sm font-medium text-slate-500">Editing</span>
          </div>
          <EditForm
            task={task}
            onSave={handleSave}
            onCancel={() => { setEditing(false); setEditError(null) }}
            saving={saving}
            error={editError}
          />
        </div>
      ) : (
        <div>
          <div className="flex items-start gap-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${TYPE_COLORS[task.task_type] || 'bg-slate-100 text-slate-600'}`}>
                  {typeLabel}
                </span>
                <span className="font-semibold text-slate-800 text-sm">{task.topic || 'Untitled'}</span>
                <StatusBadge active={task.enabled} onClick={() => onToggle(task.id, !task.enabled)} />
              </div>

              <div className="text-sm text-slate-500 mt-1">
                {describeCron(task.cron_expression)}
              </div>

              {task.message && (
                <p className="text-sm text-slate-600 mt-1 line-clamp-2">{task.message}</p>
              )}

              <div className="flex gap-4 mt-2 text-xs text-slate-400 flex-wrap">
                <span>Next: {formatFutureTime(task.next_scheduled_time)}</span>
                <span>Created: {formatRelativeTime(task.created_at)}</span>
                {task.notification_channel && (
                  <span>{task.notification_channel}{task.notification_target ? `: ${task.notification_target}` : ''}</span>
                )}
              </div>
            </div>

            <div className="shrink-0 flex gap-2">
              <button
                onClick={() => setEditing(true)}
                className="bg-slate-100 text-slate-700 py-1.5 px-3 rounded text-sm hover:bg-slate-200"
              >
                Edit
              </button>
              <button
                onClick={() => setShowHistory(h => !h)}
                className={`py-1.5 px-3 rounded text-sm ${showHistory ? 'bg-primary text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200'}`}
              >
                History
              </button>
              {confirmDelete ? (
                <span className="flex items-center gap-1 text-sm">
                  <span className="text-red-600">Delete?</span>
                  <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="text-red-600 font-medium hover:underline"
                  >
                    {deleting ? '...' : 'Yes'}
                  </button>
                  <button
                    onClick={() => setConfirmDelete(false)}
                    className="text-slate-500 hover:underline"
                  >
                    No
                  </button>
                </span>
              ) : (
                <button
                  onClick={() => setConfirmDelete(true)}
                  className="bg-red-50 text-red-600 py-1.5 px-3 rounded text-sm hover:bg-red-100"
                >
                  Delete
                </button>
              )}
            </div>
          </div>

          {showHistory && (
            <ExecutionHistory taskId={task.id} getExecutions={getExecutions} />
          )}
        </div>
      )}
    </li>
  )
}

// --- Main Page ---

const ScheduledTasks = () => {
  const { listTasks, toggleTask, deleteTask, updateTask, getExecutions } = useScheduledTasks()
  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')

  const loadTasks = useCallback(async () => {
    setLoading(true)
    try {
      const filters = typeFilter !== 'all' ? { task_type: typeFilter } : {}
      const data = await listTasks(filters)
      setTasks(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tasks')
    } finally {
      setLoading(false)
    }
  }, [listTasks, typeFilter])

  useEffect(() => { loadTasks() }, [loadTasks])

  const handleToggle = async (taskId: string, enabled: boolean) => {
    // Optimistic update
    setTasks(prev => prev.map(t => t.id === taskId ? { ...t, enabled } : t))
    try {
      const updated = await toggleTask(taskId, enabled)
      setTasks(prev => prev.map(t => t.id === taskId ? updated : t))
    } catch {
      // Revert
      setTasks(prev => prev.map(t => t.id === taskId ? { ...t, enabled: !enabled } : t))
    }
  }

  const handleDelete = async (taskId: string) => {
    await deleteTask(taskId)
    setTasks(prev => prev.filter(t => t.id !== taskId))
  }

  const handleUpdate = async (taskId: string, updates: UpdateTaskBody) => {
    const updated = await updateTask(taskId, updates)
    setTasks(prev => prev.map(t => t.id === taskId ? updated : t))
    return updated
  }

  const enabledCount = tasks.filter(t => t.enabled).length
  const disabledCount = tasks.length - enabledCount

  const FILTERS: { value: TypeFilter; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'notification', label: 'Notifications' },
    { value: 'claude_session', label: 'Claude Sessions' },
  ]

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <header className="flex items-center gap-4 mb-6 pb-4 border-b border-slate-200">
        <Link to="/ui/dashboard" className="bg-slate-50 text-slate-600 border border-slate-200 py-2 px-4 rounded-md text-sm hover:bg-slate-100">
          Back
        </Link>
        <h1 className="text-2xl font-semibold text-slate-800 flex-1">Scheduled Tasks</h1>
        <div className="flex gap-3 text-sm">
          <span className="text-green-600 font-medium">{enabledCount} active</span>
          {disabledCount > 0 && <span className="text-slate-500">{disabledCount} disabled</span>}
        </div>
      </header>

      <div className="space-y-4">
        <div className="flex gap-2 items-center">
          {FILTERS.map(f => (
            <button
              key={f.value}
              className={`py-2 px-4 rounded-lg text-sm font-medium transition-colors ${
                typeFilter === f.value
                  ? 'bg-primary text-white'
                  : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'
              }`}
              onClick={() => setTypeFilter(f.value)}
            >
              {f.label}
            </button>
          ))}
          <button
            onClick={() => loadTasks()}
            className="w-9 h-9 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 text-lg"
            title="Refresh"
            aria-label="Refresh task list"
          >
            &#8635;
          </button>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg flex justify-between items-center">
            <p>{error}</p>
            <button onClick={() => loadTasks()} className="text-primary hover:underline">Retry</button>
          </div>
        )}

        {loading && <div className="text-center py-8 text-slate-500">Loading scheduled tasks...</div>}

        {!loading && tasks.length === 0 && (
          <div className="text-center py-12 text-slate-500 bg-white rounded-xl">
            {typeFilter === 'all'
              ? 'No scheduled tasks'
              : `No ${typeFilter === 'notification' ? 'notification' : 'Claude session'} tasks`}
          </div>
        )}

        {!loading && tasks.length > 0 && (
          <ul className="space-y-3">
            {tasks.map(task => (
              <TaskCard
                key={task.id}
                task={task}
                onToggle={handleToggle}
                onDelete={handleDelete}
                onUpdate={handleUpdate}
                getExecutions={getExecutions}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default ScheduledTasks
