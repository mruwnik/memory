import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor } from '@/test/utils'
import { AgentsPage } from './index'

const listDefinitions = vi.fn()
const listSystemAgents = vi.fn()

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => ({ listDefinitions, listSystemAgents }),
}))

beforeEach(() => {
  listDefinitions.mockReset()
  listSystemAgents.mockReset()
  listSystemAgents.mockResolvedValue([])
})

describe('AgentsPage', () => {
  it('shows the empty state', async () => {
    listDefinitions.mockResolvedValue([])
    renderWithRouter(<AgentsPage />)
    await waitFor(() => expect(screen.getByText('You have no agents yet')).toBeInTheDocument())
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

  it('lists system agents and flags those you cannot pay for', async () => {
    listDefinitions.mockResolvedValue([])
    listSystemAgents.mockResolvedValue([
      { slug: 'system:hyde', title: 'Search: hypothetical passages', description: 'd', use: 'search', model: 'anthropic/claude-haiku-5-5', provider: 'anthropic', has_key: false, problem: 'add a default anthropic key to run this; until then it is skipped' },
    ])
    renderWithRouter(<AgentsPage />)
    expect(await screen.findByText('Search: hypothetical passages')).toBeInTheDocument()
    expect(screen.getByTitle(/add a default anthropic key/)).toHaveTextContent('!')
  })

  it('flags definitions that cannot run', async () => {
    listDefinitions.mockResolvedValue([{ id: 1, name: 'old', description: '', spec: { model: 'm' }, last_run: null, problems: ['pick one of your keys'] }])
    renderWithRouter(<AgentsPage />)
    expect(await screen.findByTitle('pick one of your keys')).toHaveTextContent('!')
  })

  it('shows a system agents load error without hiding your agents', async () => {
    listDefinitions.mockResolvedValue([{ id: 1, name: 'digest', description: '', spec: { model: 'm' }, last_run: null }])
    listSystemAgents.mockRejectedValue(new Error('MCP agent_system_list tool error: boom'))
    renderWithRouter(<AgentsPage />)
    expect(await screen.findByText(/boom/)).toBeInTheDocument()
    expect(screen.getByText('digest')).toBeInTheDocument()
  })

  it('shows load errors', async () => {
    listDefinitions.mockRejectedValue(new Error('MCP agent_list_all tool error: boom'))
    renderWithRouter(<AgentsPage />)
    await waitFor(() => expect(screen.getByText(/boom/)).toBeInTheDocument())
  })
})
