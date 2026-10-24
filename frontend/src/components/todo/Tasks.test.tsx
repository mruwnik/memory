import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, renderWithRouter, screen, waitFor, within } from '@/test/utils'
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
    expect(screen.queryByRole('textbox', { name: 'New task' })).not.toBeInTheDocument()
    expect(screen.queryByText('0 active')).not.toBeInTheDocument()
    listTasks.mockResolvedValue([makeTask({ task_title: 'recovered' })])
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByText('recovered')).toBeInTheDocument())
  })

  it.each([
    [3, '3 active'],
    [100, '100+ active'],
  ])('counts only the loaded filter (%i tasks)', async (n, text) => {
    listTasks.mockResolvedValue(Array.from({ length: n }, (_, i) => makeTask({ id: i + 1 })))
    renderWithRouter(<Tasks />)
    expect(await screen.findByText(text)).toBeInTheDocument()
    expect(screen.queryByText(/completed/)).not.toBeInTheDocument()
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
    await user.selectOptions(screen.getByLabelText('Priority'), 'high')
    await user.click(screen.getByRole('button', { name: 'Add task' }))
    expect(createTask).toHaveBeenCalledWith({
      task_title: 'Buy milk',
      priority: 'high',
      due_date: undefined,
    })
  })

  it('says why adding a task failed and keeps the typed title', async () => {
    createTask.mockRejectedValueOnce(new Error("You don't have permission for this. Ask an admin to grant you access."))
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(screen.getByText('No active tasks. Add one above!')).toBeInTheDocument())
    await user.type(screen.getByRole('textbox', { name: 'New task' }), 'Ship it')
    await user.click(screen.getByRole('button', { name: 'Add task' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("You don't have permission")
    expect(screen.getByRole('textbox', { name: 'New task' })).toHaveValue('Ship it')
  })

  it('disables Add Task when the title is blank', async () => {
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(listTasks).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'Add task' })).toBeDisabled()
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
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    expect(deleteTask).toHaveBeenCalledWith(9)
    await waitFor(() => expect(screen.queryByText('delete me')).not.toBeInTheDocument())
  })

  it('does not delete when confirmation is declined', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 9, task_title: 'keep me' })])
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(screen.getByText('keep me')).toBeInTheDocument())
    await user.click(screen.getByTitle('Delete'))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(deleteTask).not.toHaveBeenCalled()
  })

  it('edits title, priority and due date in a dialog', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 4, task_title: 'old', priority: 'low', due_date: '2026-01-01T00:00:00Z' })])
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit task' })
    await user.clear(within(dialog).getByLabelText('Title'))
    await user.type(within(dialog).getByLabelText('Title'), 'new title')
    await user.selectOptions(within(dialog).getByLabelText('Priority'), 'high')
    fireEvent.change(within(dialog).getByLabelText('Due date'), { target: { value: '2026-02-03' } })
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(updateTask).toHaveBeenCalledWith(4, { task_title: 'new title', priority: 'high', due_date: '2026-02-03' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('keeps the dialog open and says why when saving fails', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 4, task_title: 'old' })])
    updateTask.mockRejectedValueOnce(new Error('save broke'))
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save' }))
    expect(await within(screen.getByRole('dialog')).findByText('save broke')).toBeInTheDocument()
  })

  it('leaves an unchanged due date alone when saving', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 4, task_title: 'old', due_date: '2026-01-01T09:30:00Z' })])
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save' }))
    expect(updateTask).toHaveBeenCalledWith(4, { task_title: 'old', priority: undefined, due_date: undefined })
  })

  it('clears the priority and due date when they are emptied', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 4, task_title: 'old', priority: 'low', due_date: '2026-01-01T09:30:00Z' })])
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit task' })
    await user.selectOptions(within(dialog).getByLabelText('Priority'), '')
    fireEvent.change(within(dialog).getByLabelText('Due date'), { target: { value: '' } })
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(updateTask).toHaveBeenCalledWith(4, { task_title: 'old', priority: null, due_date: null })
  })

  it('cancels editing without calling updateTask', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 4, task_title: 'unchanged' })])
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await waitFor(() => expect(screen.getByText('unchanged')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(updateTask).not.toHaveBeenCalled()
    expect(screen.getByText('unchanged')).toBeInTheDocument()
  })

  it('reopens a done task from its checkmark', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 5, task_title: 'completed task', status: 'done' })])
    const user = userEvent.setup()
    renderWithRouter(<Tasks />)
    await user.click(await screen.findByRole('button', { name: 'Reopen "completed task"' }))
    expect(updateTask).toHaveBeenCalledWith(5, { status: 'pending' })
  })
})
