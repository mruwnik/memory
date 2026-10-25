import { describe, it, expect, vi, beforeEach } from 'vitest'
import { Route, Routes, useLocation } from 'react-router-dom'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import { MCPError } from '@/hooks/useMCP'
import { defaultSpec, type Catalog } from '@/hooks/useAgents'
import AgentEditor from './AgentEditor'

const catalog: Catalog = {
  builtins: [],
  tools: [],
  models: [{
    model: 'anthropic/claude-sonnet-5-5',
    capabilities: { tool_choice_required: false, strict_tools: true, reasoning_levels: ['medium'], sampling_params: false, native_web_search: true, native_web_fetch: true, max_output_tokens: 128000 },
    extras_schema: {},
  }],
  ceilings: { max_timeout_sec: 1800, max_runs_per_hour: 60, default_user_key_run_cost_usd: '2.00' },
  default_model: 'anthropic/claude-sonnet-5-5',
  trigger_source_types: [],
  mcp_servers: [],
  provider_keys: [],
}

const saveDefinition = vi.fn()
const agents = {
  catalog: async () => catalog,
  saveDefinition,
  getDefinition: vi.fn(),
  deleteDefinition: vi.fn(),
  startRun: vi.fn(),
  listRuns: vi.fn(),
  listSchedules: vi.fn().mockResolvedValue([]),
  listTriggers: vi.fn().mockResolvedValue([]),
}

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => agents,
}))

beforeEach(() => { saveDefinition.mockReset() })

const Where = () => <p data-testid="where">{useLocation().pathname}</p>

const renderAt = (path: string) => renderWithRouter(
  <><Routes><Route path="/ui/agents/:id" element={<AgentEditor />} /><Route path="/ui/agents" element={<p>Agent list</p>} /></Routes><Where /></>,
  { initialEntries: [path] },
)

describe('AgentEditor', () => {
  it('enables Create only once the agent has a name', async () => {
    const { user } = renderAt('/ui/agents/new')
    const create = await screen.findByRole('button', { name: 'Create agent' })
    expect(create).toBeDisabled()
    await user.type(screen.getByLabelText('Name'), 'digest')
    expect(create).toBeEnabled()
  })

  it('shows a save failure next to the button that caused it', async () => {
    saveDefinition.mockRejectedValue(new Error('max_runs_per_hour too high'))
    const { user } = renderAt('/ui/agents/new')
    await user.type(await screen.findByLabelText('Name'), 'digest')
    await user.click(screen.getByRole('button', { name: 'Create agent' }))
    const create = await screen.findByRole('button', { name: 'Create agent' })
    expect(create.parentElement).toHaveTextContent('max_runs_per_hour too high')
  })

  it.each([
    ['a non-numeric id', '/ui/agents/abc', new Error('unused')],
    ['an unknown id', '/ui/agents/99999', new Error('unknown agent definition 99999')],
    ["someone else's agent", '/ui/agents/5', new MCPError('No permission', undefined, true)],
    ['an HTTP 404', '/ui/agents/6', new MCPError('MCP call agent_fetch failed (404) Not Found', 404)],
  ])('explains %s, with no retry', async (_, path, error) => {
    agents.getDefinition.mockRejectedValue(error)
    agents.listRuns.mockResolvedValue([])
    renderAt(path)
    expect(await screen.findByRole('alert')).toHaveTextContent("This agent doesn't exist or isn't yours")
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Agents/ })).toHaveAttribute('href', '/ui/agents')
  })

  it('does not ask to discard when saving a new agent takes you to its page', async () => {
    saveDefinition.mockResolvedValue({ id: 7, name: 'digest', description: '', spec: defaultSpec('anthropic/claude-sonnet-5-5') })
    agents.getDefinition.mockResolvedValue({ id: 7, name: 'digest', description: '', spec: defaultSpec('anthropic/claude-sonnet-5-5') })
    agents.listRuns.mockResolvedValue([])
    const { user } = renderAt('/ui/agents/new')
    await user.type(await screen.findByLabelText('Name'), 'digest')
    await user.click(screen.getByRole('button', { name: 'Create agent' }))
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/ui/agents/7'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('does not ask to discard when deleting an agent with unsaved edits', async () => {
    const stored = { id: 7, name: 'digest', description: '', spec: defaultSpec('anthropic/claude-sonnet-5-5') }
    agents.getDefinition.mockResolvedValue(stored)
    agents.listRuns.mockResolvedValue([])
    agents.deleteDefinition.mockResolvedValue(undefined)
    const { user } = renderAt('/ui/agents/7')
    await user.type(await screen.findByLabelText('Name'), '!')
    await user.click(screen.getByRole('button', { name: 'Delete agent' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete agent' }))
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/ui/agents'))
    expect(screen.getByText('Agent list')).toBeInTheDocument()
    expect(screen.queryByText(/Discard unsaved changes/)).not.toBeInTheDocument()
  })

  it('asks before leaving a dirty agent', async () => {
    agents.getDefinition.mockResolvedValue({ id: 7, name: 'digest', description: '', spec: defaultSpec('anthropic/claude-sonnet-5-5') })
    agents.listRuns.mockResolvedValue([])
    const { user } = renderAt('/ui/agents/7')
    await user.type(await screen.findByLabelText('Name'), '!')
    await user.click(screen.getByRole('link', { name: /Agents/ }))
    expect(screen.getByText(/Discard unsaved changes/)).toBeInTheDocument()
    expect(screen.queryByText('Agent list')).not.toBeInTheDocument()
  })
})
