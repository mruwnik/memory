import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen } from '@/test/utils'
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
  model: 'anthropic/claude-haiku-5-5', created_at: null, started_at: null, finished_at: null, steps_purged_at: null,
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
})
