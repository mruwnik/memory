import { useState, useEffect, useCallback } from 'react'
import { useTasks, TodoTask, TodoTaskFilters } from '@/hooks/useTasks'
import { PRIORITY_ORDER, PRIORITY_TONES, dueLabel } from '@/constants/priority'
import { Badge, Button, Card, ConfirmButton, EmptyState, FieldRow, Input, List, LoadingState, Page, Row, Select, ToggleGroup, Toolbar, cx, useActionNotice } from '@/components/shared/ui'

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
  const { run, notice } = useActionNotice()

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

  const handleComplete = (taskId: number) => run(async () => {
    await completeTask(taskId)
    loadTasks()
  })

  const handleDelete = (taskId: number) => run(async () => {
    await deleteTask(taskId)
    setTasks(prev => prev.filter(t => t.id !== taskId))
  })

  const handleAddTask = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newTaskTitle.trim()) return

    setIsAdding(true)
    await run(async () => {
      await createTask({
        task_title: newTaskTitle.trim(),
        priority: newTaskPriority || undefined,
        due_date: newTaskDueDate || undefined,
      })
      setNewTaskTitle('')
      setNewTaskPriority('')
      setNewTaskDueDate('')
      loadTasks()
    })
    setIsAdding(false)
  }

  const handleStartEdit = (task: TodoTask) => {
    setEditingTask(task.id)
    setEditTitle(task.task_title)
  }

  const handleSaveEdit = async (taskId: number) => {
    if (!editTitle.trim()) return
    await run(async () => {
      await updateTask(taskId, { task_title: editTitle.trim() })
      setEditingTask(null)
      loadTasks()
    })
  }

  const handleCancelEdit = () => {
    setEditingTask(null)
    setEditTitle('')
  }

  const activeCount = tasks.filter(t => t.status !== 'done' && t.status !== 'cancelled').length
  const completedCount = tasks.filter(t => t.status === 'done').length

  return (
    <Page
      title="Tasks"
      description={!error && <span className="flex gap-3"><span>{activeCount} active</span><span>{completedCount} completed</span></span>}
      width="narrow"
      error={error}
      onRetry={loadTasks}
    >
      <Card>
        <form onSubmit={handleAddTask} className="space-y-3">
          <div className="flex gap-2">
            <Input type="text" placeholder="What needs to be done?" aria-label="New task" value={newTaskTitle}
              onChange={(e) => setNewTaskTitle(e.target.value)} disabled={isAdding} className="flex-1" />
            <Button variant="primary" type="submit" disabled={isAdding || !newTaskTitle.trim()}>Add task</Button>
          </div>
          <FieldRow>
            <Select value={newTaskPriority} onChange={(e) => setNewTaskPriority(e.target.value)} aria-label="Priority">
              <option value="">No priority</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="urgent">Urgent</option>
            </Select>
            <Input type="date" value={newTaskDueDate} onChange={(e) => setNewTaskDueDate(e.target.value)} aria-label="Due date" />
          </FieldRow>
        </form>
      </Card>

      <Toolbar>
        <ToggleGroup label="Filter tasks" value={statusFilter} onChange={setStatusFilter} options={[
          { value: 'active', label: 'Active' }, { value: 'completed', label: 'Completed' }, { value: 'all', label: 'All' },
        ]} />
      </Toolbar>

      {notice}

      {loading ? <LoadingState message="Loading tasks..." />
        : tasks.length === 0 ? (
          <EmptyState message={statusFilter === 'completed'
            ? 'No completed tasks yet'
            : statusFilter === 'active'
            ? 'No active tasks. Add one above!'
            : 'No tasks yet. Add one above!'} />
        ) : (
          <List>
            {tasks.map(task => {
              const done = task.status === 'done'
              const due = task.due_date && dueLabel(task.due_date)
              return (
                <Row
                  key={task.id}
                  muted={done}
                  leading={
                    <button type="button"
                      className={cx('w-6 h-6 rounded-full border-2 flex items-center justify-center transition-colors',
                        done ? 'bg-success border-success text-white' : 'border-slate-300 hover:border-primary')}
                      onClick={() => !done && handleComplete(task.id)}
                      title={done ? 'Completed' : 'Mark as complete'}
                      aria-label={done ? 'Completed' : `Mark "${task.task_title}" as complete`}
                      disabled={done}
                    >
                      {done && <span aria-hidden="true" className="text-sm">✓</span>}
                    </button>
                  }
                  title={editingTask === task.id ? (
                    <span className="flex gap-2">
                      <Input type="text" value={editTitle} onChange={(e) => setEditTitle(e.target.value)} className="flex-1" autoFocus
                        aria-label="Task title"
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleSaveEdit(task.id)
                          if (e.key === 'Escape') handleCancelEdit()
                        }} />
                      <Button size="sm" variant="primary" onClick={() => handleSaveEdit(task.id)}>Save</Button>
                      <Button size="sm" onClick={handleCancelEdit}>Cancel</Button>
                    </span>
                  ) : <span className={cx(done && 'line-through')}>{task.task_title}</span>}
                  badges={editingTask !== task.id && <>
                    {task.priority && <Badge tone={PRIORITY_TONES[task.priority] ?? 'neutral'}>{task.priority}</Badge>}
                    {task.status === 'in_progress' && <Badge tone="info">In progress</Badge>}
                    {due && <Badge tone={due.tone}>{due.text}</Badge>}
                  </>}
                  actions={editingTask !== task.id && <>
                    {!done && <Button size="sm" onClick={() => handleStartEdit(task)} title="Edit">Edit</Button>}
                    <ConfirmButton confirm={`Delete "${task.task_title}"?`} onConfirm={() => handleDelete(task.id)} title="Delete" />
                  </>}
                />
              )
            })}
          </List>
        )}
    </Page>
  )
}

export default Tasks
