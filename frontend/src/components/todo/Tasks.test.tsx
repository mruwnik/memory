import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor } from '@/test/utils'
import userEvent from '@testing-library/user-event'
import type { TodoTask, TodoTaskFilters } from '@/hooks/useTasks'
import Tasks from './Tasks'

const listTasks = vi.fn()
const completeTask = vi.fn()
const createTask = vi.fn()
const updateTask = vi.fn()
const deleteTask = vi.fn()

vi.mock('@/hooks/useTasks', () => ({
  useTasks: () => ({ listTasks, completeTask, createTask, updateTask, deleteTask }),
}))

const makeTask = (o: Partial<TodoTask> = {}): TodoTask => ({
  id: 1,
  task_title: 'Task A',
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
  updateTask.mockReset().mockResolvedValue(undefined)
  deleteTask.mockReset().mockResolvedValue(undefined)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

describe('Tasks page', () => {
  it('shows the loading state then empty state', async () => {
    renderWithRouter(<Tasks />)
    expect(screen.getByText('Loading tasks...')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByText('No active tasks. Add one above!')).toBeInTheDocument(),
    )
  })

  it('shows the error state with retry', async () => {
    listTasks.mockRejectedValueOnce(new Error('load failed'))
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(screen.getByText('load failed')).toBeInTheDocument())
    listTasks.mockResolvedValue([makeTask({ task_title: 'recovered' })])
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByText('recovered')).toBeInTheDocument())
  })

  it('renders the active/completed counts', async () => {
    listTasks.mockResolvedValue([
      makeTask({ id: 1, status: 'pending' }),
      makeTask({ id: 2, status: 'done' }),
      makeTask({ id: 3, status: 'in_progress' }),
    ])
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(screen.getByText('2 active')).toBeInTheDocument())
    expect(screen.getByText('1 completed')).toBeInTheDocument()
  })

  it.each([
    ['completed', 'No completed tasks yet'],
    ['all', 'No tasks yet. Add one above!'],
  ])('switches the %s filter and shows its empty message', async (filter, msg) => {
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(listTasks).toHaveBeenCalled())
    await user.click(screen.getByRole('button', { name: filter[0].toUpperCase() + filter.slice(1) }))
    await waitFor(() => expect(screen.getByText(msg)).toBeInTheDocument())
  })

  it('sends status=done filter when Completed is selected', async () => {
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(listTasks).toHaveBeenCalled())
    await user.click(screen.getByRole('button', { name: 'Completed' }))
    await waitFor(() => {
      const lastCall = listTasks.mock.calls.at(-1)?.[0] as TodoTaskFilters
      expect(lastCall.status).toBe('done')
    })
  })

  it('creates a task with priority and due date', async () => {
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(listTasks).toHaveBeenCalled())
    await user.type(screen.getByPlaceholderText('What needs to be done?'), 'Buy milk')
    await user.selectOptions(screen.getByRole('combobox'), 'high')
    await user.click(screen.getByRole('button', { name: 'Add Task' }))
    expect(createTask).toHaveBeenCalledWith({
      task_title: 'Buy milk',
      priority: 'high',
      due_date: undefined,
    })
  })

  it('disables Add Task when the title is blank', async () => {
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(listTasks).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'Add Task' })).toBeDisabled()
  })

  it('completes a task', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 7, task_title: 'finish me' })])
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(screen.getByText('finish me')).toBeInTheDocument())
    await user.click(screen.getByTitle('Mark as complete'))
    expect(completeTask).toHaveBeenCalledWith(7)
  })

  it('deletes a task after confirmation', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 9, task_title: 'delete me' })])
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(screen.getByText('delete me')).toBeInTheDocument())
    await user.click(screen.getByTitle('Delete'))
    expect(window.confirm).toHaveBeenCalled()
    expect(deleteTask).toHaveBeenCalledWith(9)
    await waitFor(() => expect(screen.queryByText('delete me')).not.toBeInTheDocument())
  })

  it('does not delete when confirmation is declined', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    listTasks.mockResolvedValue([makeTask({ id: 9, task_title: 'keep me' })])
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(screen.getByText('keep me')).toBeInTheDocument())
    await user.click(screen.getByTitle('Delete'))
    expect(deleteTask).not.toHaveBeenCalled()
  })

  it('edits a task title inline and saves', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 4, task_title: 'old' })])
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(screen.getByText('old')).toBeInTheDocument())
    await user.click(screen.getByTitle('Edit'))
    const editInput = screen.getByDisplayValue('old')
    await user.clear(editInput)
    await user.type(editInput, 'new title')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(updateTask).toHaveBeenCalledWith(4, { task_title: 'new title' })
  })

  it('cancels inline editing without calling updateTask', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 4, task_title: 'unchanged' })])
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(screen.getByText('unchanged')).toBeInTheDocument())
    await user.click(screen.getByTitle('Edit'))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(updateTask).not.toHaveBeenCalled()
    expect(screen.getByText('unchanged')).toBeInTheDocument()
  })

  it('marks a done task with a checkmark and disables its complete button', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 5, task_title: 'completed task', status: 'done' })])
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(screen.getByText('completed task')).toBeInTheDocument())
    expect(screen.getByTitle('Completed')).toBeDisabled()
  })
})
