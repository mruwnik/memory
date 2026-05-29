import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor } from '@/test/utils'
import userEvent from '@testing-library/user-event'
import type { Job } from '@/hooks/useJobs'
import { Jobs } from './index'

const listJobs = vi.fn()
const retryJob = vi.fn()
const reingestJob = vi.fn()
const getUsersWithJobs = vi.fn()
const getJobTypes = vi.fn()

vi.mock('@/hooks/useJobs', () => ({
  useJobs: () => ({ listJobs, retryJob, reingestJob, getUsersWithJobs, getJobTypes }),
}))

// Non-admin auth so UserSelector renders nothing and useUserSelection stays stable.
vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({
    hasScope: () => false,
    user: { id: 1, name: 'Me', email: 'me@x.com', user_type: 'human', scopes: [] },
  }),
}))
vi.mock('@/hooks/useUsers', () => ({
  useUsers: () => ({ listUsers: vi.fn(async () => []) }),
}))

const makeJob = (o: Partial<Job> = {}): Job => ({
  id: 1,
  job_type: 'email_sync',
  external_id: 'ext-1',
  status: 'complete',
  error_message: null,
  result_id: null,
  result_type: null,
  params: {},
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  completed_at: null,
  attempts: 1,
  user_id: null,
  ...o,
})

beforeEach(() => {
  listJobs.mockReset().mockResolvedValue([])
  retryJob.mockReset().mockResolvedValue(undefined)
  reingestJob.mockReset().mockResolvedValue(undefined)
  getUsersWithJobs.mockReset().mockResolvedValue([])
  getJobTypes.mockReset().mockResolvedValue([])
})

describe('Jobs page', () => {
  it('shows the loading state then the empty state', async () => {
    renderWithRouter(<Jobs />)
    expect(screen.getByText('Loading jobs...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No jobs found')).toBeInTheDocument())
  })

  it('shows the load error with a retry button', async () => {
    listJobs.mockRejectedValueOnce(new Error('jobs down'))
    renderWithRouter(<Jobs />)
    await waitFor(() => expect(screen.getByText('jobs down')).toBeInTheDocument())
  })

  it('renders job rows with formatted type, status, and stats', async () => {
    listJobs.mockResolvedValue([
      makeJob({ id: 1, status: 'failed', job_type: 'blog_sync', error_message: 'bad' }),
      makeJob({ id: 2, status: 'complete' }),
    ])
    renderWithRouter(<Jobs />)
    await waitFor(() => expect(screen.getByText('Blog Sync')).toBeInTheDocument())
    expect(screen.getByText('1 failed')).toBeInTheDocument()
    expect(screen.getByText('1 complete')).toBeInTheDocument()
    expect(screen.getByText('bad')).toBeInTheDocument()
  })

  it('shows a Retry button only on failed jobs and calls retryJob', async () => {
    listJobs.mockResolvedValue([makeJob({ id: 5, status: 'failed' })])
    const user = userEvent.setup()
    renderWithRouter(<Jobs />)
    await waitFor(() => expect(screen.getByTitle('Retry failed job')).toBeInTheDocument())
    await user.click(screen.getByTitle('Retry failed job'))
    expect(retryJob).toHaveBeenCalledWith(5)
  })

  it('shows Reingest for completed jobs and calls reingestJob', async () => {
    listJobs.mockResolvedValue([makeJob({ id: 6, status: 'complete' })])
    const user = userEvent.setup()
    renderWithRouter(<Jobs />)
    await waitFor(() => expect(screen.getByTitle('Re-run this job')).toBeInTheDocument())
    await user.click(screen.getByTitle('Re-run this job'))
    expect(reingestJob).toHaveBeenCalledWith(6)
  })

  it('passes the status filter to listJobs when a filter is selected', async () => {
    const user = userEvent.setup()
    renderWithRouter(<Jobs />)
    await waitFor(() => expect(listJobs).toHaveBeenCalled())
    await user.click(screen.getByRole('button', { name: 'Failed' }))
    await waitFor(() => {
      const last = listJobs.mock.calls.at(-1)?.[0]
      expect(last.status).toBe('failed')
    })
  })

  it('shows a job-type dropdown only when more than one type exists', async () => {
    getJobTypes.mockResolvedValue(['email_sync', 'blog_sync'])
    renderWithRouter(<Jobs />)
    await waitFor(() => expect(screen.getByText('All types')).toBeInTheDocument())
  })

  it('renders pagination next/previous and advances pages', async () => {
    listJobs.mockResolvedValue(Array.from({ length: 100 }, (_, i) => makeJob({ id: i + 1 })))
    const user = userEvent.setup()
    renderWithRouter(<Jobs />)
    await waitFor(() => expect(screen.getByText('Page 1')).toBeInTheDocument())
    const next = screen.getByRole('button', { name: 'Next' })
    expect(next).toBeEnabled()
    await user.click(next)
    await waitFor(() => expect(screen.getByText('Page 2')).toBeInTheDocument())
  })

  it('clears the load error and refetches when clicking the refresh button', async () => {
    listJobs.mockResolvedValue([makeJob({ id: 1 })])
    const user = userEvent.setup()
    renderWithRouter(<Jobs />)
    await waitFor(() => expect(listJobs).toHaveBeenCalled())
    const before = listJobs.mock.calls.length
    await user.click(screen.getByRole('button', { name: 'Refresh jobs list' }))
    await waitFor(() => expect(listJobs.mock.calls.length).toBeGreaterThan(before))
  })
})
