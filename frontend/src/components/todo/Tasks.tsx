import { useState, useEffect, useCallback } from 'react'
import { useTasks, TodoTask, TodoTaskFilters, TodoTaskUpdate, TaskPriority } from '@/hooks/useTasks'
import { PRIORITY_ORDER, PRIORITY_TONES, dueLabel } from '@/constants/priority'
import { Badge, Button, Card, ConfirmButton, EmptyState, Field, FieldRow, FormActions, Input, List, LoadingState, Modal, Page, Row, Select, ToggleGroup, Toolbar, cx, useActionNotice } from '@/components/shared/ui'

type StatusFilter = 'active' | 'completed' | 'all'

const LIMIT = 100
const COUNT_LABELS: Record<StatusFilter, string> = { active: 'active', completed: 'completed', all: 'tasks' }

const PriorityOptions = () => <>
  <option value="">No priority</option>
  <option value="low">Low</option>
  <option value="medium">Medium</option>
  <option value="high">High</option>
  <option value="urgent">Urgent</option>
</>

/** Only changed fields are sent: an omitted priority or due date is left unchanged, null clears it, and resending the date would drop its time of day. */
const TaskEditModal = ({ task, onSave, onClose }: { task: TodoTask; onSave: (update: TodoTaskUpdate) => Promise<unknown>; onClose: () => void }) => {
  const initialDue = task.due_date?.slice(0, 10) ?? ''
  const [title, setTitle] = useState(task.task_title)
  const [priority, setPriority] = useState<string>(task.priority ?? '')
  const [due, setDue] = useState(initialDue)
  const [saving, setSaving] = useState(false)
  const { run, notice } = useActionNotice()

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    await run(() => onSave({
      task_title: title.trim(),
      priority: priority === (task.priority ?? '') ? undefined : (priority || null) as TaskPriority | null,
      due_date: due === initialDue ? undefined : due || null,
    }))
    setSaving(false)
  }

  return (
    <Modal title="Edit task" onClose={onClose} size="md">
      <form onSubmit={submit} className="space-y-4">
        {notice}
        <Field label="Title"><Input value={title} onChange={e => setTitle(e.target.value)} autoFocus /></Field>
        <FieldRow>
          <Field label="Priority">
            <Select value={priority} onChange={e => setPriority(e.target.value)}><PriorityOptions /></Select>
          </Field>
          <Field label="Due date"><Input type="date" value={due} onChange={e => setDue(e.target.value)} /></Field>
        </FieldRow>
        <FormActions>
          <Button type="button" onClick={onClose}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={saving || !title.trim()}>{saving ? 'Saving...' : 'Save'}</Button>
        </FormActions>
      </form>
    </Modal>
  )
}

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
  const [editingTask, setEditingTask] = useState<TodoTask | null>(null)
  const { run, notice } = useActionNotice()

  const loadTasks = useCallback(async () => {
    setLoading(true)
    try {
      const filters: TodoTaskFilters = {
        include_completed: statusFilter === 'completed' || statusFilter === 'all',
        limit: LIMIT,
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

  const handleToggleDone = (task: TodoTask) => run(async () => {
    await (task.status === 'done' ? updateTask(task.id, { status: 'pending' }) : completeTask(task.id))
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

  const handleSaveEdit = async (task: TodoTask, update: TodoTaskUpdate) => {
    await updateTask(task.id, update)
    setEditingTask(null)
    loadTasks()
  }

  // The API returns no totals, so a full page means there may be more.
  const count = `${tasks.length >= LIMIT ? `${LIMIT}+` : tasks.length} ${COUNT_LABELS[statusFilter]}`

  return (
    <Page
      title="Tasks"
      description={!error && !loading && count}
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
            <Field label="Priority">
              <Select value={newTaskPriority} onChange={(e) => setNewTaskPriority(e.target.value)}><PriorityOptions /></Select>
            </Field>
            <Field label="Due date">
              <Input type="date" value={newTaskDueDate} onChange={(e) => setNewTaskDueDate(e.target.value)} />
            </Field>
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
                      onClick={() => handleToggleDone(task)}
                      title={done ? 'Reopen' : 'Mark as complete'}
                      aria-label={done ? `Reopen "${task.task_title}"` : `Mark "${task.task_title}" as complete`}
                    >
                      {done && <span aria-hidden="true" className="text-sm">✓</span>}
                    </button>
                  }
                  title={<span className={cx(done && 'line-through')}>{task.task_title}</span>}
                  badges={<>
                    {task.priority && <Badge tone={PRIORITY_TONES[task.priority] ?? 'neutral'}>{task.priority}</Badge>}
                    {task.status === 'in_progress' && <Badge tone="info">In progress</Badge>}
                    {due && <Badge tone={due.tone}>{due.text}</Badge>}
                  </>}
                  actions={<>
                    {!done && <Button size="sm" onClick={() => setEditingTask(task)}>Edit</Button>}
                    <ConfirmButton confirm={`Delete "${task.task_title}"?`} onConfirm={() => handleDelete(task.id)} title="Delete" />
                  </>}
                />
              )
            })}
          </List>
        )}
      {editingTask && (
        <TaskEditModal task={editingTask} onClose={() => setEditingTask(null)} onSave={update => handleSaveEdit(editingTask, update)} />
      )}
    </Page>
  )
}

export default Tasks
