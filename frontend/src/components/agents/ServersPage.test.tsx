import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor } from '@/test/utils'
import { ServersPage } from './index'

const listServers = vi.fn()

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => ({ listServers, upsertServer: vi.fn() }),
}))

beforeEach(() => {
  listServers.mockReset()
})

describe('ServersPage', () => {
  it('shows the empty state', async () => {
    listServers.mockResolvedValue([])
    renderWithRouter(<ServersPage />)
    await waitFor(() => expect(screen.getByText('No MCP servers yet')).toBeInTheDocument())
  })

  it('lists servers with status, host and tool count', async () => {
    listServers.mockResolvedValue([
      { id: 4, name: 'raspberry', url: 'http://192.168.1.20:8080/mcp', auth_type: 'none', status: 'needs_reauth', tools: [{}, {}], tools_refreshed_at: null },
    ])
    renderWithRouter(<ServersPage />)
    await waitFor(() => expect(screen.getByRole('link', { name: 'raspberry' })).toHaveAttribute('href', '/ui/agents/servers/4'))
    expect(screen.getByText('192.168.1.20:8080')).toBeInTheDocument()
    expect(screen.getByText('Needs re-auth')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })
})
