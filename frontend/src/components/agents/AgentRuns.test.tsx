import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor } from '@/test/utils'
import type { AgentRun } from '@/hooks/useAgents'
import AgentRuns from './AgentRuns'

const listRuns = vi.fn()
const listDefinitions = vi.fn()

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => ({ listRuns, listDefinitions }),
}))

const run = (o: Partial<AgentRun>): AgentRun => ({
  id: 'r1', definition_id: null, input: 'New report: - [report #5] summary', status: 'done', result: null, error: null,
  usage: {}, cost_usd: '0.000228', max_cost_usd: '1', num_turns: 1, trigger_kind: 'manual', starred: false,
  model: 'anthropic/claude-haiku-5-5', created_at: null, started_at: null, finished_at: null, agent: null, executor: 'server', parent_run_id: null, depth: 0,
  ...o,
})

beforeEach(() => {
  listRuns.mockReset().mockResolvedValue([run({ id: 'r1', definition_id: 3, trigger_kind: 'ingest' }), run({ id: 'r2', status: 'budget_exceeded' })])
  listDefinitions.mockReset().mockResolvedValue([{ id: 3, name: 'digest' }])
})

describe('AgentRuns', () => {
  it('titles a run with its agent, or as ad-hoc, and keeps the input as the body', async () => {
    renderWithRouter(<AgentRuns />)
    expect(await screen.findByRole('link', { name: 'digest' })).toHaveAttribute('href', '/ui/agents/runs/r1')
    expect(screen.getByRole('link', { name: 'Ad-hoc run' })).toBeInTheDocument()
    expect(screen.getAllByText('New report: - [report #5] summary')).toHaveLength(2)
    expect(screen.getByText('New items')).toBeInTheDocument()
  })

  it('names statuses and costs in plain words', async () => {
    renderWithRouter(<AgentRuns />)
    await screen.findByRole('link', { name: 'Ad-hoc run' })
    expect(screen.getByRole('button', { name: 'Over budget' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pending' })).toBeInTheDocument()
    expect(screen.getAllByText('Over budget')).toHaveLength(2)
    expect(screen.getAllByText('<$0.01')).toHaveLength(2)
  })

  it('retries a failed load', async () => {
    listRuns.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderWithRouter(<AgentRuns />)
    await user.click(await screen.findByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('link', { name: 'Ad-hoc run' })).toBeInTheDocument()
  })

  it('refreshes the list on demand', async () => {
    const { user } = renderWithRouter(<AgentRuns />)
    await screen.findByRole('link', { name: 'Ad-hoc run' })
    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(listRuns).toHaveBeenCalledTimes(2)
  })

  it('pages older runs with the before cursor until a short page', async () => {
    const full = Array.from({ length: 100 }, (_, i) => run({ id: `n${i}`, created_at: `2026-10-24T10:${String(59 - (i % 60)).padStart(2, '0')}:00` }))
    full[99] = run({ id: 'n99', created_at: '2026-10-01T00:00:00' })
    listRuns.mockReset().mockResolvedValueOnce(full).mockResolvedValueOnce([run({ id: 'old', input: 'an older run' })])
    const { user } = renderWithRouter(<AgentRuns />)
    await user.click(await screen.findByRole('button', { name: 'Load more' }))
    expect(listRuns).toHaveBeenLastCalledWith({ status: undefined, limit: 100, before: { createdAt: '2026-10-01T00:00:00', id: 'n99' } })
    expect(await screen.findByText('an older run')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  const fullPage = () => Array.from({ length: 100 }, (_, i) => run({ id: `n${i}`, input: `page one ${i}`, created_at: '2026-10-01T00:00:00' }))

  it('keeps page one and retries only the next page when Load more fails', async () => {
    listRuns.mockReset().mockResolvedValueOnce(fullPage()).mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce([run({ id: 'old', input: 'an older run' })])
    const { user } = renderWithRouter(<AgentRuns />)
    await user.click(await screen.findByRole('button', { name: 'Load more' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
    expect(screen.getByText('page one 0')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('an older run')).toBeInTheDocument()
    expect(screen.getByText('page one 0')).toBeInTheDocument()
    expect(listRuns).toHaveBeenCalledTimes(3)
    expect(listRuns).toHaveBeenLastCalledWith(expect.objectContaining({ before: { createdAt: '2026-10-01T00:00:00', id: 'n99' } }))
  })

  it('drops a Load more response that arrives after the filter changed', async () => {
    let finishMore!: (page: AgentRun[]) => void
    listRuns.mockReset()
      .mockResolvedValueOnce(fullPage())
      .mockReturnValueOnce(new Promise(resolve => { finishMore = resolve }))
      .mockResolvedValueOnce([run({ id: 'f1', input: 'filtered run', status: 'failed' })])
    const { user } = renderWithRouter(<AgentRuns />)
    await user.click(await screen.findByRole('button', { name: 'Load more' }))
    await user.click(screen.getByRole('button', { name: 'Failed' }))
    expect(await screen.findByText('filtered run')).toBeInTheDocument()

    finishMore([run({ id: 'stale', input: 'stale run' })])
    await waitFor(() => expect(screen.queryByText('page one 0')).not.toBeInTheDocument())
    expect(screen.queryByText('stale run')).not.toBeInTheDocument()
  })
})
