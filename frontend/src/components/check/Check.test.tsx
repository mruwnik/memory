import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor } from '@/test/utils'
import userEvent from '@testing-library/user-event'
import type { CheckJob } from '@/hooks/useCheck'
import { Check } from './index'

const listJobs = vi.fn()
const ask = vi.fn()
const deleteJob = vi.fn()

vi.mock('@/hooks/useCheck', () => ({
  useCheck: () => ({ listJobs, ask, deleteJob }),
  PAGE_LIMIT: 200,
}))

const makeJob = (o: Partial<CheckJob> = {}): CheckJob => ({
  job_id: 'chk_1',
  status: 'ok',
  mode: 'research',
  text: 'is the sky blue?',
  result: { answer: 'yes, it is' },
  error: null,
  submitted_at: new Date().toISOString(),
  completed_at: new Date().toISOString(),
  ...o,
})

beforeEach(() => {
  listJobs.mockReset().mockResolvedValue([])
  ask.mockReset().mockResolvedValue({ job_id: 'chk_new', status: 'queued' })
  deleteJob.mockReset().mockResolvedValue(undefined)
})

describe('Check', () => {
  it('shows loading then the empty state', async () => {
    renderWithRouter(<Check />)
    expect(screen.getByText('Loading questions...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No questions yet')).toBeInTheDocument())
  })

  it('renders an error state with retry', async () => {
    listJobs.mockRejectedValueOnce(new Error('boom'))
    renderWithRouter(<Check />)
    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument())
  })

  it('renders a job card with question, answer, mode and status', async () => {
    listJobs.mockResolvedValue([makeJob()])
    renderWithRouter(<Check />)
    await waitFor(() => expect(screen.getByText('is the sky blue?')).toBeInTheDocument())
    expect(screen.getByText('yes, it is')).toBeInTheDocument()
    expect(screen.getByText('research')).toBeInTheDocument()
    expect(screen.getByText('Answered', { selector: 'span' })).toBeInTheDocument()
    expect(screen.getByText('1 answered')).toBeInTheDocument()
  })

  it('renders a structured verdict answer instead of raw JSON', async () => {
    listJobs.mockResolvedValue([
      makeJob({
        mode: 'verify',
        text: 'is the sky blue?',
        result: {
          verdict: 'true',
          confidence: 0.9,
          verdict_reason: 'well established',
          summary: 'Rayleigh scattering makes the sky appear blue.',
        },
      }),
    ])
    renderWithRouter(<Check />)
    await waitFor(() =>
      expect(screen.getByText('Rayleigh scattering makes the sky appear blue.')).toBeInTheDocument(),
    )
    expect(screen.getByText('true')).toBeInTheDocument()
    expect(screen.getByText('90% confidence')).toBeInTheDocument()
    expect(screen.getByText('well established')).toBeInTheDocument()
    // The raw JSON braces should not be shown.
    expect(screen.queryByText(/"verdict"/)).not.toBeInTheDocument()
  })

  it('renders link-mode sources as anchors', async () => {
    listJobs.mockResolvedValue([
      makeJob({
        mode: 'link',
        result: {
          summary: 'Found two references.',
          sources: ['https://example.com/a', { url: 'https://example.com/b', title: 'Ref B' }],
        },
      }),
    ])
    renderWithRouter(<Check />)
    await waitFor(() => expect(screen.getByText('Ref B')).toBeInTheDocument())
    expect(screen.getByRole('link', { name: 'Ref B' })).toHaveAttribute('href', 'https://example.com/b')
    expect(screen.getByRole('link', { name: 'https://example.com/a' })).toHaveAttribute(
      'href',
      'https://example.com/a',
    )
  })

  it('falls back to JSON for an unrecognised answer shape', async () => {
    listJobs.mockResolvedValue([makeJob({ result: { weird_field: 42 } })])
    renderWithRouter(<Check />)
    await waitFor(() => expect(screen.getByText(/weird_field/)).toBeInTheDocument())
  })

  it('shows the error message for a failed job', async () => {
    listJobs.mockResolvedValue([makeJob({ status: 'error', result: null, error: 'worker exploded' })])
    renderWithRouter(<Check />)
    await waitFor(() => expect(screen.getByText('worker exploded')).toBeInTheDocument())
  })

  it('fetches the full list once and filters by status in-memory', async () => {
    listJobs.mockResolvedValue([
      makeJob({ job_id: 'a', status: 'ok', text: 'answered one' }),
      makeJob({ job_id: 'b', status: 'queued', result: null, completed_at: null, text: 'queued one' }),
    ])
    renderWithRouter(<Check />)
    await waitFor(() => expect(screen.getByText('answered one')).toBeInTheDocument())
    expect(screen.getByText('queued one')).toBeInTheDocument()
    // listJobs is called once, with no status arg — filtering is client-side.
    expect(listJobs).toHaveBeenCalledTimes(1)
    expect(listJobs).toHaveBeenLastCalledWith()

    await userEvent.click(screen.getByRole('button', { name: 'Queued' }))
    // No refetch on tab change; the answered job is filtered out of the display.
    expect(listJobs).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.queryByText('answered one')).not.toBeInTheDocument())
    expect(screen.getByText('queued one')).toBeInTheDocument()
  })

  it('flags counts as approximate and shows a notice at the 200-job cap', async () => {
    const jobs = Array.from({ length: 200 }, (_, i) =>
      makeJob({ job_id: `chk_${i}`, status: 'ok', text: `q ${i}` }),
    )
    listJobs.mockResolvedValue(jobs)
    renderWithRouter(<Check />)
    await waitFor(() =>
      expect(screen.getByText('Showing the 200 most recent questions.')).toBeInTheDocument(),
    )
    expect(screen.getByText('200+ answered')).toBeInTheDocument()
  })

  it('submits a new question through the ask form and reloads', async () => {
    listJobs.mockResolvedValue([])
    renderWithRouter(<Check />)
    await waitFor(() => expect(screen.getByText('No questions yet')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: '+ New question' }))
    await userEvent.type(screen.getByPlaceholderText(/What would you like to verify/), 'why is the sky blue?')
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
    await waitFor(() => expect(ask).toHaveBeenCalledWith({ text: 'why is the sky blue?', mode: 'research' }))
  })

  it('deletes a job after confirmation', async () => {
    listJobs.mockResolvedValue([makeJob()])
    renderWithRouter(<Check />)
    await waitFor(() => expect(screen.getByText('is the sky blue?')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await userEvent.click(screen.getByRole('button', { name: 'Yes' }))
    await waitFor(() => expect(deleteJob).toHaveBeenCalledWith('chk_1'))
    await waitFor(() => expect(screen.queryByText('is the sky blue?')).not.toBeInTheDocument())
  })
})
