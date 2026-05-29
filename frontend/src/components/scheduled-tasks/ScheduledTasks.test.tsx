import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import userEvent from '@testing-library/user-event'
import type { ScheduledTask, TaskExecution } from '@/hooks/useScheduledTasks'
import { ScheduledTasks } from './index'

const listTasks = vi.fn()
const toggleTask = vi.fn()
const deleteTask = vi.fn()
const updateTask = vi.fn()
const getExecutions = vi.fn()
const listEnvironments = vi.fn()

vi.mock('@/hooks/useScheduledTasks', () => ({
  useScheduledTasks: () => ({ listTasks, toggleTask, deleteTask, updateTask, getExecutions }),
}))
vi.mock('@/hooks/useClaude', () => ({
  useClaude: () => ({ listEnvironments }),
}))

const makeTask = (o: Partial<ScheduledTask> = {}): ScheduledTask => ({
  id: 't1',
  user_id: 1,
  task_type: 'notification',
  topic: 'Daily standup',
  message: 'Remember the standup',
  notification_channel: 'discord',
  notification_target: 'general',
  data: null,
  cron_expression: '0 9 * * *',
  next_scheduled_time: new Date(Date.now() + 3600_000).toISOString(),
  enabled: true,
  created_at: new Date().toISOString(),
  updated_at: null,
  ...o,
})

const claudeTask = (o: Partial<ScheduledTask> = {}): ScheduledTask =>
  makeTask({
    id: 'c1',
    task_type: 'claude_session',
    topic: 'Nightly job',
    message: 'do the thing',
    notification_channel: null,
    notification_target: null,
    data: { spawn_config: { environment_id: 3, repo_url: 'https://github.com/org/repo', run_id: 'nightly', allowed_tools: ['Bash', 'Read'] } },
    ...o,
  })

beforeEach(() => {
  listTasks.mockReset().mockResolvedValue([])
  toggleTask.mockReset()
  deleteTask.mockReset().mockResolvedValue(undefined)
  updateTask.mockReset()
  getExecutions.mockReset().mockResolvedValue([])
  listEnvironments.mockReset().mockResolvedValue([])
})

describe('ScheduledTasks', () => {
  it('shows loading then the empty state', async () => {
    renderWithRouter(<ScheduledTasks />)
    expect(screen.getByText('Loading scheduled tasks...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No scheduled tasks')).toBeInTheDocument())
  })

  it('renders an error state with retry', async () => {
    listTasks.mockRejectedValueOnce(new Error('boom'))
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument())
  })

  it('renders a notification task card with cron description and counts', async () => {
    listTasks.mockResolvedValue([makeTask()])
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('Daily standup')).toBeInTheDocument())
    expect(screen.getByText('Notification')).toBeInTheDocument()
    expect(screen.getByText(/Daily at 09:00 UTC/)).toBeInTheDocument()
    expect(screen.getByText('1 active')).toBeInTheDocument()
  })

  it('renders a claude_session task with its spawn-config data fields', async () => {
    listTasks.mockResolvedValue([claudeTask()])
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('Nightly job')).toBeInTheDocument())
    expect(screen.getByText('Claude Session')).toBeInTheDocument()
    expect(screen.getByText('org/repo')).toBeInTheDocument()
    expect(screen.getByText('nightly')).toBeInTheDocument()
  })

  it('filters by task type, passing the filter to listTasks', async () => {
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(listTasks).toHaveBeenCalled())
    await user.click(screen.getByRole('button', { name: 'Claude Sessions' }))
    await waitFor(() => {
      const last = listTasks.mock.calls.at(-1)?.[0]
      expect(last.task_type).toBe('claude_session')
    })
  })

  it('toggles a task optimistically and persists', async () => {
    const task = makeTask({ enabled: true })
    listTasks.mockResolvedValue([task])
    toggleTask.mockResolvedValue({ ...task, enabled: false })
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('Daily standup')).toBeInTheDocument())
    await user.click(screen.getByText('Active'))
    expect(toggleTask).toHaveBeenCalledWith('t1', false)
  })

  it('confirms and deletes a task', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 'del', topic: 'kill me' })])
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('kill me')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'Yes' }))
    expect(deleteTask).toHaveBeenCalledWith('del')
    await waitFor(() => expect(screen.queryByText('kill me')).not.toBeInTheDocument())
  })

  it('cancels a pending delete', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 'keep', topic: 'survivor' })])
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('survivor')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'No' }))
    expect(deleteTask).not.toHaveBeenCalled()
    expect(screen.getByText('survivor')).toBeInTheDocument()
  })

  it('opens the edit form and saves changes', async () => {
    const task = makeTask({ id: 'edit', topic: 'orig' })
    listTasks.mockResolvedValue([task])
    updateTask.mockResolvedValue({ ...task, topic: 'changed' })
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('orig')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const topicInput = screen.getByDisplayValue('orig')
    await user.clear(topicInput)
    await user.type(topicInput, 'changed')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(updateTask).toHaveBeenCalledWith('edit', expect.objectContaining({ topic: 'changed' })),
    )
  })

  it('loads execution history when History is toggled', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 'hist', topic: 'with history' })])
    const exec: TaskExecution = {
      id: 'e1', task_id: 'hist', scheduled_time: new Date().toISOString(),
      started_at: null, finished_at: null, status: 'completed', response: null,
      error_message: null, celery_task_id: null, data: null,
    }
    getExecutions.mockResolvedValue([exec])
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('with history')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'History' }))
    await waitFor(() => expect(getExecutions).toHaveBeenCalledWith('hist'))
    const card = screen.getByText('with history').closest('li')!
    await waitFor(() => expect(within(card).getByText('completed')).toBeInTheDocument())
  })

  it('shows "No execution history" when there are no executions', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 'h', topic: 'empty hist' })])
    getExecutions.mockResolvedValue([])
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('empty hist')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'History' }))
    await waitFor(() => expect(screen.getByText('No execution history')).toBeInTheDocument())
  })

  it('reverts an optimistic toggle when persisting fails', async () => {
    const task = makeTask({ id: 'flap', topic: 'flaky', enabled: true })
    listTasks.mockResolvedValue([task])
    toggleTask.mockRejectedValueOnce(new Error('nope'))
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('flaky')).toBeInTheDocument())
    await user.click(screen.getByText('Active'))
    await waitFor(() => expect(toggleTask).toHaveBeenCalledWith('flap', false))
    // After the failure it reverts to the enabled "Active" label.
    await waitFor(() => expect(screen.getByText('Active')).toBeInTheDocument())
  })

  it('shows a save error inside the edit form when updateTask fails', async () => {
    const task = makeTask({ id: 'err', topic: 'orig' })
    listTasks.mockResolvedValue([task])
    updateTask.mockRejectedValueOnce(new Error('save failed'))
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('orig')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.clear(screen.getByDisplayValue('orig'))
    await user.type(screen.getByDisplayValue(''), 'new topic')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('save failed')).toBeInTheDocument()
  })

  it('closes the edit form without calling updateTask when nothing changed', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 'noop', topic: 'unchanged' })])
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('unchanged')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(updateTask).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument())
  })

  it('cancels the edit form', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 'cancel', topic: 'keepme' })])
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('keepme')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument())
  })

  it('edits a notification channel and target', async () => {
    const task = makeTask({ id: 'notif', topic: 'notif task', notification_channel: '', notification_target: '' })
    listTasks.mockResolvedValue([task])
    updateTask.mockResolvedValue({ ...task, notification_channel: 'email', notification_target: 'a@b.c' })
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('notif task')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.selectOptions(screen.getByRole('combobox'), 'email')
    await user.type(screen.getByPlaceholderText('channel or email'), 'a@b.c')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(updateTask).toHaveBeenCalledWith('notif', expect.objectContaining({
        notification_channel: 'email', notification_target: 'a@b.c',
      })),
    )
  })

  it('renders notification data fields (subject, from, bot)', async () => {
    listTasks.mockResolvedValue([
      makeTask({ id: 'd', topic: 'with data', data: { subject: 'Hi', from_address: 'x@y.z', discord_bot_id: 'bot9' } }),
    ])
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('with data')).toBeInTheDocument())
    expect(screen.getByText('Hi')).toBeInTheDocument()
    expect(screen.getByText('x@y.z')).toBeInTheDocument()
    expect(screen.getByText('bot9')).toBeInTheDocument()
  })

  it('renders a long claude prompt and a tools-overflow field', async () => {
    const longPrompt = 'p'.repeat(150)
    listTasks.mockResolvedValue([
      claudeTask({
        id: 'long',
        topic: 'long prompt',
        data: {
          spawn_config: {
            initial_prompt: longPrompt,
            allowed_tools: ['Bash', 'Read', 'Write', 'Edit', 'Grep'],
            enable_playwright: true,
            snapshot_id: 'snap-1',
          },
        },
      }),
    ])
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('long prompt')).toBeInTheDocument())
    expect(screen.getByText(longPrompt)).toBeInTheDocument()
    expect(screen.getByText('Bash, Read, Write...')).toBeInTheDocument()
    expect(screen.getByText('enabled')).toBeInTheDocument()
    expect(screen.getByText('snap-1')).toBeInTheDocument()
  })

  it('edits claude_session spawn config and sends spawn_config updates', async () => {
    const task = claudeTask({ id: 'spawn' })
    listTasks.mockResolvedValue([task])
    listEnvironments.mockResolvedValue([
      { id: 3, name: 'env-a', description: 'desc a' },
      { id: 4, name: 'env-b', description: null },
    ])
    updateTask.mockResolvedValue(task)
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('Nightly job')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await waitFor(() => expect(listEnvironments).toHaveBeenCalled())
    // change the environment selection
    await user.selectOptions(screen.getByDisplayValue(/env-a/), '4')
    // toggle playwright on
    await user.click(screen.getByLabelText('Enable Playwright'))
    // edit custom env vars
    await user.type(screen.getByPlaceholderText(/KEY=value/), 'API_KEY=secret')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      const call = updateTask.mock.calls.at(-1)
      expect(call?.[0]).toBe('spawn')
      expect(call?.[1].spawn_config).toMatchObject({
        environment_id: 4,
        enable_playwright: true,
        custom_env: { API_KEY: 'secret' },
      })
    })
  })

  it('renders custom_env back into the edit form for claude tasks', async () => {
    listTasks.mockResolvedValue([
      claudeTask({ id: 'envtask', data: { spawn_config: { environment_id: 3, custom_env: { FOO: 'bar' } } } }),
    ])
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('Nightly job')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(await screen.findByDisplayValue('FOO=bar')).toBeInTheDocument()
  })

  it('shows the per-filter empty message for the notification filter', async () => {
    listTasks.mockResolvedValue([])
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('No scheduled tasks')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Notifications' }))
    await waitFor(() => expect(screen.getByText('No notification tasks')).toBeInTheDocument())
  })

  it('refreshes the list via the refresh button', async () => {
    listTasks.mockResolvedValue([])
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(listTasks).toHaveBeenCalled())
    const before = listTasks.mock.calls.length
    await user.click(screen.getByRole('button', { name: 'Refresh task list' }))
    await waitFor(() => expect(listTasks.mock.calls.length).toBeGreaterThan(before))
  })
})

describe('ScheduledTasks formatting branches', () => {
  it.each([
    ['every minute', '* * * * *', /Every minute/],
    ['hourly at minute', '30 * * * *', /Every hour at :30/],
    ['weekdays', '0 9 * * 1-5', /Weekdays at 09:00 UTC/],
    ['weekends', '0 9 * * 0,6', /Weekends at 09:00 UTC/],
    ['named days', '0 9 * * 1,3', /Mon, Wed at 09:00 UTC/],
    ['no schedule', null, /No schedule/],
    ['raw passthrough', '0 9 1 1 *', /0 9 1 1/],
  ])('describes the cron expression for %s', async (_label, expr, matcher) => {
    listTasks.mockResolvedValue([makeTask({ id: 'cron', topic: 'cron task', cron_expression: expr })])
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('cron task')).toBeInTheDocument())
    expect(screen.getByText(matcher)).toBeInTheDocument()
  })

  it.each([
    ['in minutes', 30 * 60_000, /in \d+m/],
    ['in hours', 5 * 3600_000, /in \d+h/],
    ['in days', 3 * 86_400_000, /in \d+d/],
    ['any moment', 10_000, /any moment/],
  ])('formats the next-scheduled time %s', async (_label, deltaMs, matcher) => {
    listTasks.mockResolvedValue([
      makeTask({ id: 'fut', topic: 'future task', next_scheduled_time: new Date(Date.now() + deltaMs).toISOString() }),
    ])
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('future task')).toBeInTheDocument())
    expect(screen.getByText(matcher)).toBeInTheDocument()
  })

  it('shows "Not scheduled" when there is no next time', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 'none', topic: 'no next', next_scheduled_time: null })])
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('no next')).toBeInTheDocument())
    expect(screen.getByText(/Not scheduled/)).toBeInTheDocument()
  })

  it('renders execution history with session id, duration, error and response', async () => {
    listTasks.mockResolvedValue([makeTask({ id: 'rich', topic: 'rich hist' })])
    const start = new Date()
    const end = new Date(start.getTime() + 2500)
    getExecutions.mockResolvedValue([
      {
        id: 'e1', task_id: 'rich', scheduled_time: start.toISOString(),
        started_at: start.toISOString(), finished_at: end.toISOString(),
        status: 'failed', response: 'partial output',
        error_message: 'it broke', celery_task_id: null,
        data: { session_id: 'sess-abcdefghijklmnopqrstuvwxyz' },
      },
    ])
    const user = userEvent.setup()
    renderWithRouter(<ScheduledTasks />)
    await waitFor(() => expect(screen.getByText('rich hist')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'History' }))
    await waitFor(() => expect(screen.getByText('failed')).toBeInTheDocument())
    expect(screen.getByText('it broke')).toBeInTheDocument()
    expect(screen.getByText('partial output')).toBeInTheDocument()
    expect(screen.getByText((_t, el) => el?.textContent === 'session: sess-abcdefghijklmno...')).toBeInTheDocument()
    expect(screen.getByText('2.5s')).toBeInTheDocument()
  })
})
