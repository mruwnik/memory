import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor } from '@/test/utils'
import userEvent from '@testing-library/user-event'
import type { BeatScheduleEntry, TaskActivity } from '@/hooks/useCelery'
import { CeleryOverview } from './index'

const getBeatSchedule = vi.fn()
const getTaskActivity = vi.fn()

vi.mock('@/hooks/useCelery', () => ({
  useCelery: () => ({ getBeatSchedule, getTaskActivity }),
}))

const schedule: BeatScheduleEntry[] = [
  { key: 'a', name: 'Beta Task', task: 'memory.workers.tasks.beta', schedule_display: 'every 5m', last_run: null, last_status: null, last_duration_ms: null },
  { key: 'b', name: 'Alpha Task', task: 'memory.workers.tasks.alpha', schedule_display: 'hourly', last_run: new Date(Date.now() - 3600_000).toISOString(), last_status: 'success', last_duration_ms: 1500 },
]

const activity: TaskActivity = {
  hours: 24,
  totals: { total: 10, success: 8, failure: 2, avg_duration_ms: 250 },
  by_task: [
    { task: 'memory.workers.tasks.alpha', total: 6, success: 5, failure: 1, avg_duration_ms: 120 },
    { task: 'memory.workers.tasks.beta', total: 4, success: 4, failure: 0, avg_duration_ms: 500 },
  ],
  recent_failures: [
    { task: 'memory.workers.tasks.alpha', timestamp: new Date().toISOString(), duration_ms: 90, labels: { worker: 'w1' }, error: 'boom traceback' },
  ],
}

beforeEach(() => {
  getBeatSchedule.mockReset().mockResolvedValue(schedule)
  getTaskActivity.mockReset().mockResolvedValue(activity)
})

describe('CeleryOverview', () => {
  it('shows a loading state initially', () => {
    renderWithRouter(<CeleryOverview />)
    expect(screen.getByText('Loading Celery overview...')).toBeInTheDocument()
  })

  it('shows an error when fetching fails', async () => {
    getTaskActivity.mockRejectedValue(new Error('celery down'))
    renderWithRouter(<CeleryOverview />)
    await waitFor(() => expect(screen.getByText('celery down')).toBeInTheDocument())
  })

  it('renders the totals summary cards', async () => {
    renderWithRouter(<CeleryOverview />)
    await waitFor(() => expect(screen.getByText('Succeeded')).toBeInTheDocument())
    expect(screen.getByText('8')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('renders the beat schedule rows and count', async () => {
    renderWithRouter(<CeleryOverview />)
    await waitFor(() => expect(screen.getByText('Beat schedule (2 tasks)')).toBeInTheDocument())
    expect(screen.getByText('Alpha Task')).toBeInTheDocument()
    expect(screen.getByText('Beta Task')).toBeInTheDocument()
    expect(screen.getByText('success')).toBeInTheDocument()
  })

  it('sorts the beat schedule by name ascending by default and toggles from the keyboard', async () => {
    const user = userEvent.setup()
    renderWithRouter(<CeleryOverview />)
    await waitFor(() => expect(screen.getByText('Alpha Task')).toBeInTheDocument())
    const rows = () => screen.getAllByRole('row').filter(r => r.querySelector('td'))
    const header = screen.getByRole('columnheader', { name: 'Name' })
    expect(rows()[0]).toHaveTextContent('Alpha Task')
    expect(header).toHaveAttribute('aria-sort', 'ascending')
    screen.getByRole('button', { name: 'Name' }).focus()
    await user.keyboard('{Enter}')
    expect(rows()[0]).toHaveTextContent('Beta Task')
    expect(header).toHaveAttribute('aria-sort', 'descending')
  })

  it.each([
    ['Rate', 'alpha'],
    ['Success', 'beta'],
  ])('sorts task activity ascending by %s with %s first', async (column, first) => {
    const user = userEvent.setup()
    renderWithRouter(<CeleryOverview />)
    await waitFor(() => expect(screen.getByText('Task activity (last 24h)')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: column }))
    const activityRows = screen.getAllByRole('table')[1].querySelectorAll('tbody tr')
    expect(activityRows[0]).toHaveTextContent(`memory.workers.tasks.${first}`)
  })

  it('shows an empty state when no tasks ran', async () => {
    getTaskActivity.mockResolvedValue({ ...activity, by_task: [], recent_failures: [] })
    renderWithRouter(<CeleryOverview />)
    await waitFor(() => expect(screen.getByText('No tasks ran in the last 24h')).toBeInTheDocument())
  })

  it('refreshes without dropping the loaded content', async () => {
    const user = userEvent.setup()
    renderWithRouter(<CeleryOverview />)
    await waitFor(() => expect(screen.getByText('Alpha Task')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(screen.getByText('Alpha Task')).toBeInTheDocument()
    await waitFor(() => expect(getBeatSchedule).toHaveBeenCalledTimes(2))
  })

  it('renders the task activity table', async () => {
    renderWithRouter(<CeleryOverview />)
    await waitFor(() => expect(screen.getByText('Task activity (last 24h)')).toBeInTheDocument())
    expect(screen.getAllByText('alpha').length).toBeGreaterThan(0)
  })

  it('expands a recent failure to show its error', async () => {
    const user = userEvent.setup()
    renderWithRouter(<CeleryOverview />)
    await waitFor(() => expect(screen.getByText('Recent failures')).toBeInTheDocument())
    expect(screen.queryByText('boom traceback')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'alpha' }))
    expect(screen.getByText('boom traceback')).toBeInTheDocument()
  })

  it('hides summary cards when there are no totals', async () => {
    getTaskActivity.mockResolvedValue({ ...activity, totals: undefined as never })
    renderWithRouter(<CeleryOverview />)
    await waitFor(() => expect(screen.getByText('Beat schedule (2 tasks)')).toBeInTheDocument())
    expect(screen.queryByText('Succeeded')).not.toBeInTheDocument()
  })
})
