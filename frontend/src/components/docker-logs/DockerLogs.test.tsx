import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor } from '@/test/utils'
import userEvent from '@testing-library/user-event'
import type { ContainerInfo, LogsResponse } from '../../hooks/useDockerLogs'
import { DockerLogs } from './index'

const listContainers = vi.fn()
const getLogs = vi.fn()

vi.mock('../../hooks/useDockerLogs', () => ({
  useDockerLogs: () => ({ listContainers, getLogs }),
}))

const containers: ContainerInfo[] = [
  { name: 'memory-api-1', status: 'Up 2 hours', started_at: null },
  { name: 'memory-worker-1', status: 'Exited (0)', started_at: null },
]

const logsFor = (logs: string): LogsResponse => ({
  container: 'memory-api-1',
  logs,
  since: null,
  until: null,
  lines: logs.split('\n').length,
})

beforeEach(() => {
  listContainers.mockReset().mockResolvedValue(containers)
  getLogs.mockReset().mockResolvedValue(logsFor('INFO starting up\nERROR boom\nplain line'))
  // jsdom doesn't implement Element.scrollTo; the auto-scroll effect calls it.
  if (!HTMLElement.prototype.scrollTo) {
    HTMLElement.prototype.scrollTo = () => {}
  }
  vi.spyOn(HTMLElement.prototype, 'scrollTo').mockImplementation(() => {})
})

describe('DockerLogs', () => {
  it('renders container tabs with friendly display names and statuses', async () => {
    renderWithRouter(<DockerLogs />)
    await waitFor(() => expect(screen.getByText('API')).toBeInTheDocument())
    expect(screen.getByText('Worker')).toBeInTheDocument()
    expect(screen.getByText('Up 2 hours')).toBeInTheDocument()
  })

  it('auto-selects the first container and loads its logs', async () => {
    renderWithRouter(<DockerLogs />)
    await waitFor(() => expect(getLogs).toHaveBeenCalled())
    expect(getLogs.mock.calls[0][0]).toBe('memory-api-1')
    await waitFor(() => expect(screen.getByText('INFO starting up')).toBeInTheDocument())
  })

  it('shows the line count', async () => {
    renderWithRouter(<DockerLogs />)
    await waitFor(() => expect(screen.getByText('3 lines')).toBeInTheDocument())
  })

  it('shows an error from container listing', async () => {
    listContainers.mockRejectedValue(new Error('no docker'))
    renderWithRouter(<DockerLogs />)
    await waitFor(() => expect(screen.getByText('no docker')).toBeInTheDocument())
  })

  it('shows an error when log fetch fails, retryable', async () => {
    getLogs.mockRejectedValueOnce(new Error('logs failed'))
    renderWithRouter(<DockerLogs />)
    await waitFor(() => expect(screen.getByText('logs failed')).toBeInTheDocument())
    getLogs.mockResolvedValue(logsFor('recovered output'))
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByText('recovered output')).toBeInTheDocument())
  })

  it('switches containers when a tab is clicked', async () => {
    const user = userEvent.setup()
    renderWithRouter(<DockerLogs />)
    await waitFor(() => expect(getLogs).toHaveBeenCalled())
    await user.click(screen.getByText('Worker'))
    await waitFor(() =>
      expect(getLogs.mock.calls.some(c => c[0] === 'memory-worker-1')).toBe(true),
    )
  })

  it('passes a different time range to getLogs when selected', async () => {
    const user = userEvent.setup()
    renderWithRouter(<DockerLogs />)
    await waitFor(() => expect(getLogs).toHaveBeenCalled())
    const before = getLogs.mock.calls.length
    await user.click(screen.getByRole('button', { name: '24 hours' }))
    await waitFor(() => expect(getLogs.mock.calls.length).toBeGreaterThan(before))
  })

  it('debounces the filter text into the getLogs call', async () => {
    const user = userEvent.setup()
    renderWithRouter(<DockerLogs />)
    await waitFor(() => expect(getLogs).toHaveBeenCalled())
    await user.type(screen.getByPlaceholderText('Filter logs...'), 'ERROR')
    await waitFor(
      () => {
        const last = getLogs.mock.calls.at(-1)?.[1]
        expect(last?.filter_text).toBe('ERROR')
      },
      { timeout: 2000 },
    )
  })

  it('highlights matched filter text in log lines', async () => {
    const user = userEvent.setup()
    renderWithRouter(<DockerLogs />)
    await waitFor(() => expect(screen.getByText('INFO starting up')).toBeInTheDocument())
    await user.type(screen.getByPlaceholderText('Filter logs...'), 'boom')
    await waitFor(() => expect(document.querySelector('mark')).not.toBeNull())
  })

  it('shows "No logs available" when logs are empty', async () => {
    getLogs.mockResolvedValue(logsFor(''))
    renderWithRouter(<DockerLogs />)
    await waitFor(() => expect(screen.getByText('No logs available')).toBeInTheDocument())
  })
})
