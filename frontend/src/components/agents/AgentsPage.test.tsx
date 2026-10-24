import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor } from '@/test/utils'
import { AgentsPage } from './index'

const listDefinitions = vi.fn()

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => ({ listDefinitions }),
}))

beforeEach(() => {
  listDefinitions.mockReset()
})

describe('AgentsPage', () => {
  it('shows the empty state', async () => {
    listDefinitions.mockResolvedValue([])
    renderWithRouter(<AgentsPage />)
    await waitFor(() => expect(screen.getByText('No agents yet')).toBeInTheDocument())
  })

  it('lists definitions with their last run status', async () => {
    listDefinitions.mockResolvedValue([
      { id: 1, name: 'digest', description: 'daily digest', spec: { model: 'anthropic/claude-haiku-5-5' }, last_run: { status: 'failed', cost_usd: '0.01', created_at: null } },
    ])
    renderWithRouter(<AgentsPage />)
    await waitFor(() => expect(screen.getByText('digest')).toBeInTheDocument())
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'digest' })).toHaveAttribute('href', '/ui/agents/1')
  })

  it('flags definitions with a tripped trigger', async () => {
    listDefinitions.mockResolvedValue([
      { id: 1, name: 'watcher', description: '', spec: { model: 'm' }, last_run: null, triggers: { total: 2, enabled: 1, tripped: 1 } },
    ])
    renderWithRouter(<AgentsPage />)
    await waitFor(() => expect(screen.getByText('1 trigger tripped')).toBeInTheDocument())
  })

  it('shows load errors', async () => {
    listDefinitions.mockRejectedValue(new Error('MCP agent_list_all tool error: boom'))
    renderWithRouter(<AgentsPage />)
    await waitFor(() => expect(screen.getByText(/boom/)).toBeInTheDocument())
  })
})
