import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import userEvent from '@testing-library/user-event'
import type { TodoTask } from '@/hooks/useTasks'
import TodoWidget from './TodoWidget'

const listTasks = vi.fn()
const completeTask = vi.fn()
const createTask = vi.fn()

vi.mock('@/hooks/useTasks', () => ({
  useTasks: () => ({ listTasks, completeTask, createTask }),
}))

const makeTask = (o: Partial<TodoTask> = {}): TodoTask => ({
  id: 1,
  task_title: 'Write tests',
  due_date: null,
  priority: null,
  status: 'pending',
  recurrence: null,
  completed_at: null,
  source_item_id: null,
  tags: [],
  inserted_at: null,
  ...o,
})

beforeEach(() => {
  listTasks.mockReset().mockResolvedValue([])
  completeTask.mockReset().mockResolvedValue(undefined)
  createTask.mockReset().mockResolvedValue(undefined)
})

describe('TodoWidget', () => {
  it('shows a loading state first then the empty state', async () => {
    renderWithRouter(<TodoWidget />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No pending tasks')).toBeInTheDocument())
  })

  it('renders an error state when loading fails', async () => {
    listTasks.mockRejectedValue(new Error('nope'))
    renderWithRouter(<TodoWidget />)
    await waitFor(() => expect(screen.getByText('nope')).toBeInTheDocument())
  })

  it('renders tasks and the count badge', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 1, task_title: 'A' }), makeTask({ id: 2, task_title: 'B' })])
    renderWithRouter(<TodoWidget />)
    await waitFor(() => expect(screen.getByText('A')).toBeInTheDocument())
    expect(screen.getByText('B')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('caps the list at maxItems', async () => {
    listTasks.mockResolvedValue(
      Array.from({ length: 8 }, (_, i) => makeTask({ id: i + 1, task_title: `T${i}` })),
    )
    renderWithRouter(<TodoWidget maxItems={3} />)
    await waitFor(() => expect(screen.getByText('T0')).toBeInTheDocument())
    expect(screen.queryByText('T3')).not.toBeInTheDocument()
  })

  it('sorts by priority order', async () => {
    listTasks.mockResolvedValue([
      makeTask({ id: 1, task_title: 'low task', priority: 'low' }),
      makeTask({ id: 2, task_title: 'urgent task', priority: 'urgent' }),
    ])
    renderWithRouter(<TodoWidget />)
    await waitFor(() => expect(screen.getByText('urgent task')).toBeInTheDocument())
    const items = screen.getAllByRole('listitem')
    expect(within(items[0]).getByText('urgent task')).toBeInTheDocument()
  })

  it('renders due-date labels (overdue/today/tomorrow)', async () => {
    const iso = (d: Date) => d.toISOString().slice(0, 10)
    const today = new Date()
    const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1)
    const tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1)
    listTasks.mockResolvedValue([
      makeTask({ id: 1, task_title: 'od', due_date: iso(yesterday) }),
      makeTask({ id: 2, task_title: 'td', due_date: iso(today) }),
      makeTask({ id: 3, task_title: 'tm', due_date: iso(tomorrow) }),
    ])
    renderWithRouter(<TodoWidget />)
    await waitFor(() => expect(screen.getByText('Overdue')).toBeInTheDocument())
    expect(screen.getByText('Today')).toBeInTheDocument()
    expect(screen.getByText('Tomorrow')).toBeInTheDocument()
  })

  it('completes a task: optimistically removes it and reloads', async () => {
    listTasks.mockResolvedValueOnce([makeTask({ id: 1, task_title: 'done me' })])
    listTasks.mockResolvedValue([])
    const user = userEvent.setup()
    renderWithRouter(<TodoWidget />)
    await waitFor(() => expect(screen.getByText('done me')).toBeInTheDocument())
    await user.click(screen.getByTitle('Mark as complete'))
    expect(completeTask).toHaveBeenCalledWith(1)
    await waitFor(() => expect(screen.getByText('No pending tasks')).toBeInTheDocument())
  })

  it('adds a task via the form and clears the input', async () => {
    const user = userEvent.setup()
    renderWithRouter(<TodoWidget />)
    await waitFor(() => expect(screen.getByText('No pending tasks')).toBeInTheDocument())
    const input = screen.getByPlaceholderText('Add a task...')
    await user.type(input, 'New thing')
    await user.click(screen.getByRole('button', { name: '+' }))
    expect(createTask).toHaveBeenCalledWith({ task_title: 'New thing' })
    await waitFor(() => expect(input).toHaveValue(''))
  })

  it('disables the add button when the input is empty', async () => {
    renderWithRouter(<TodoWidget />)
    await waitFor(() => expect(screen.getByText('No pending tasks')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '+' })).toBeDisabled()
  })

  it('links to the full tasks page', async () => {
    renderWithRouter(<TodoWidget />)
    await waitFor(() => expect(screen.getByText('No pending tasks')).toBeInTheDocument())
    expect(screen.getByRole('link', { name: 'View all tasks' })).toHaveAttribute('href', '/ui/tasks')
  })
})
