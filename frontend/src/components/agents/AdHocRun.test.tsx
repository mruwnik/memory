import { describe, it, expect, vi, beforeEach } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { renderWithRouter, screen, within } from '@/test/utils'
import { defaultSpec, type Catalog } from '@/hooks/useAgents'
import AdHocRun from './AdHocRun'

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
const startRun = vi.fn()
const agents = { catalog: async () => catalog, saveDefinition, startRun }

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => agents,
}))

beforeEach(() => {
  saveDefinition.mockReset()
  startRun.mockReset()
})

const renderPage = () => renderWithRouter(
  <Routes>
    <Route path="/ui/agents/runs/new" element={<AdHocRun />} />
    <Route path="/ui/agents/:id" element={<p>agent page</p>} />
  </Routes>,
  { initialEntries: ['/ui/agents/runs/new'] },
)

describe('AdHocRun', () => {
  it('saves the configuration as a named agent', async () => {
    saveDefinition.mockResolvedValue({ id: 7 })
    const { user } = renderPage()
    await user.click(await screen.findByRole('button', { name: 'Save as agent' }))
    const dialog = screen.getByRole('dialog', { name: 'Save as agent' })
    const save = within(dialog).getByRole('button', { name: 'Save agent' })
    expect(save).toBeDisabled()
    await user.type(within(dialog).getByLabelText('Name'), 'digest')
    await user.type(within(dialog).getByLabelText('Description'), 'daily')
    await user.click(save)

    expect(await screen.findByText('agent page')).toBeInTheDocument()
    expect(saveDefinition).toHaveBeenCalledWith({ name: 'digest', description: 'daily', spec: defaultSpec('anthropic/claude-sonnet-5-5') })
  })

  it('shows a save failure inside the dialog', async () => {
    saveDefinition.mockRejectedValue(new Error('name taken'))
    const { user } = renderPage()
    await user.click(await screen.findByRole('button', { name: 'Save as agent' }))
    const dialog = screen.getByRole('dialog', { name: 'Save as agent' })
    await user.type(within(dialog).getByLabelText('Name'), 'digest')
    await user.click(within(dialog).getByRole('button', { name: 'Save agent' }))
    expect(await within(dialog).findByText('name taken')).toBeInTheDocument()
  })

  it('starts one run however often Run is clicked', async () => {
    startRun.mockReturnValue(new Promise(() => {}))
    const { user } = renderPage()
    await user.type(await screen.findByLabelText('Run input'), 'go')
    await user.click(screen.getByRole('button', { name: 'Run' }))
    await user.click(screen.getByRole('button', { name: 'Starting...' }))
    expect(startRun).toHaveBeenCalledTimes(1)
  })
})
