import { describe, it, expect, vi, beforeEach } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { renderWithRouter, screen } from '@/test/utils'
import type { Catalog } from '@/hooks/useAgents'
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
}

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => agents,
}))

beforeEach(() => { saveDefinition.mockReset() })

const renderAt = (path: string) => renderWithRouter(
  <Routes><Route path="/ui/agents/:id" element={<AgentEditor />} /></Routes>,
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

  it("explains an agent id that can't exist", () => {
    renderAt('/ui/agents/abc')
    expect(screen.getByRole('alert')).toHaveTextContent("This agent doesn't exist or isn't yours")
    expect(screen.getByRole('link', { name: /Agents/ })).toHaveAttribute('href', '/ui/agents')
  })
})
