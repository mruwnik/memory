import { describe, it, expect, vi } from 'vitest'
import { renderWithRouter, screen, within } from '@/test/utils'
import { SpecEditor } from './SpecEditor'
import { defaultSpec, type Catalog, type ExternalServer, type ProviderKey } from '@/hooks/useAgents'

const server = (overrides: Partial<ExternalServer> = {}): ExternalServer => ({
  id: 3, name: 'linear', url: 'https://mcp.linear.app/mcp', transport: 'http', auth_type: 'oauth',
  status: 'connected', status_detail: null, header_names: [], issuer: null, scopes: null, client_id: 'c',
  client_source: 'dcr', has_token: true, token_expires_at: null, tools_refreshed_at: null, tools_changed_at: null,
  created_at: null, updated_at: null,
  tools: [
    { name: 'list_issues', description: 'List', input_schema: {}, read_only: true, hash: 'a' },
    { name: 'create_issue', description: 'Create', input_schema: {}, read_only: false, hash: 'b' },
    { name: 'agent_spawn', description: 'Nope', input_schema: {}, read_only: false, hash: 'c' },
  ],
  ...overrides,
})

const providerKey = (overrides: Partial<ProviderKey> = {}): ProviderKey => ({
  id: 3, provider: 'anthropic', label: 'personal', key_hint: 'ab12', is_default: true, status: 'ok', status_detail: null,
  last_used_at: null, max_cost_usd_per_day: null, created_at: null, updated_at: null, ...overrides,
})

const catalog: Catalog = {
  builtins: ['web_fetch', 'web_search'],
  tools: [
    { name: 'core_search', description: 'Search', read_only: true },
    { name: 'discord_send_message', description: 'Send', read_only: false },
  ],
  models: [
    {
      model: 'anthropic/claude-opus-5-5',
      capabilities: { tool_choice_required: false, strict_tools: true, reasoning_levels: ['high', 'low', 'medium'], sampling_params: false, native_web_search: true, native_web_fetch: true, max_output_tokens: 128000 },
      extras_schema: {},
    },
    {
      model: 'anthropic/claude-sonnet-5-5',
      capabilities: { tool_choice_required: false, strict_tools: true, reasoning_levels: ['high', 'low', 'medium', 'off'], sampling_params: false, native_web_search: true, native_web_fetch: true, max_output_tokens: 128000 },
      extras_schema: {},
    },
    {
      model: 'openai/gpt-5',
      capabilities: { tool_choice_required: true, strict_tools: true, reasoning_levels: ['low', 'medium', 'high'], sampling_params: false, native_web_search: false, native_web_fetch: false, max_output_tokens: 128000 },
      extras_schema: {},
    },
  ],
  ceilings: { max_run_cost_usd: '1.00', max_daily_cost_usd: '10.00', max_timeout_sec: 1800, max_runs_per_hour: 60, default_user_key_run_cost_usd: '2.00' },
  default_model: 'anthropic/claude-sonnet-5-5',
  trigger_source_types: ['blog_post', 'discord_message'],
  mcp_servers: [server()],
  provider_keys: [providerKey(), providerKey({ id: 4, label: 'work', key_hint: 'cd34', is_default: false }), providerKey({ id: 5, provider: 'openai', label: 'gpt' })],
  server_key_allowed: false,
}

const withServer = (s: ExternalServer): Catalog => ({ ...catalog, mcp_servers: [s] })

const sonnet = defaultSpec('anthropic/claude-sonnet-5-5')
const keyOptions = () => Array.from(screen.getByLabelText('API key').querySelectorAll('option')).map(o => o.textContent)

describe('SpecEditor', () => {
  it.each([
    [false, ['Default key for Anthropic', 'personal (••••ab12)', 'work (••••cd34)']],
    [true, ['Default key for Anthropic', 'personal (••••ab12)', 'work (••••cd34)', 'Server key']],
  ])('offers the provider\'s keys, and the server key only when allowed (%s)', (allowed, expected) => {
    renderWithRouter(<SpecEditor value={sonnet} catalog={{ ...catalog, server_key_allowed: allowed }} onChange={vi.fn()} />)
    expect(keyOptions()).toEqual(expected)
  })

  it.each([
    [null, "your key 'personal'", 'Max $ per run', '2.00'],
    [4, "your key 'work'", 'Max $ per run', '2.00'],
    ['server' as const, "the server's Anthropic key", 'Max $ per run (≤ 1.00)', '1.00'],
  ])('says which key pays for %s and which ceilings apply', (provider_key, payer, costLabel, placeholder) => {
    renderWithRouter(<SpecEditor value={{ ...sonnet, provider_key }} catalog={{ ...catalog, server_key_allowed: true }} onChange={vi.fn()} />)
    expect(screen.getByText(new RegExp(`Runs pay with ${payer}`))).toBeInTheDocument()
    expect(screen.getByLabelText(costLabel)).toHaveAttribute('placeholder', placeholder)
  })

  it('links to the keys page when no key can pay', () => {
    renderWithRouter(<SpecEditor value={sonnet} catalog={{ ...catalog, provider_keys: [] }} onChange={vi.fn()} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Add an API key for Anthropic so this agent can run.')
    expect(screen.getByRole('link', { name: 'Manage API keys' })).toHaveAttribute('href', '/ui/agents/keys')
  })

  it.each([
    ['anthropic/claude-opus-5-5', 4],
    ['openai/gpt-5', null],
  ])('keeps a pinned key only while the model stays with its provider (%s)', async (model, provider_key) => {
    const onChange = vi.fn()
    const { user } = renderWithRouter(<SpecEditor value={{ ...sonnet, provider_key: 4 }} catalog={catalog} onChange={onChange} />)
    await user.selectOptions(screen.getByLabelText('Model'), model)
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ model, provider_key }))
  })

  it('pins the picked key', async () => {
    const onChange = vi.fn()
    const { user } = renderWithRouter(<SpecEditor value={sonnet} catalog={catalog} onChange={onChange} />)
    await user.selectOptions(screen.getByLabelText('API key'), 'work (••••cd34)')
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ provider_key: 4 }))
  })

  it('only offers the reasoning levels the model supports', () => {
    renderWithRouter(<SpecEditor value={defaultSpec('anthropic/claude-opus-5-5')} catalog={catalog} onChange={vi.fn()} />)
    const options = Array.from(screen.getByLabelText('Reasoning').querySelectorAll('option')).map(o => o.textContent)
    expect(options).toEqual(['low', 'medium', 'high'])
  })

  it('disables temperature when the model forbids sampling', () => {
    renderWithRouter(<SpecEditor value={defaultSpec('anthropic/claude-sonnet-5-5')} catalog={catalog} onChange={vi.fn()} />)
    expect(screen.getByLabelText('Temperature')).toBeDisabled()
  })

  it('leaves max output tokens to the run by default', () => {
    renderWithRouter(<SpecEditor value={defaultSpec('anthropic/claude-sonnet-5-5')} catalog={catalog} onChange={vi.fn()} />)
    expect(screen.getByLabelText('Max output tokens')).toHaveValue(null)
  })

  it('marks tools with side effects', () => {
    renderWithRouter(<SpecEditor value={defaultSpec('anthropic/claude-sonnet-5-5')} catalog={catalog} onChange={vi.fn()} />)
    expect(screen.getByText('discord_send_message').closest('label')).toHaveTextContent('side effects')
    expect(screen.getByText('core_search').closest('label')).not.toHaveTextContent('side effects')
  })

  it('adds a selected tool to the spec', async () => {
    const onChange = vi.fn()
    const { user } = renderWithRouter(<SpecEditor value={defaultSpec('anthropic/claude-sonnet-5-5')} catalog={catalog} onChange={onChange} />)
    await user.click(screen.getByLabelText(/core_search/))
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ tools: ['core_search'] }))
  })

  it('fits reasoning to the new model when the model changes', async () => {
    const onChange = vi.fn()
    const spec = { ...defaultSpec('anthropic/claude-sonnet-5-5'), reasoning: 'off' as const }
    const { user } = renderWithRouter(<SpecEditor value={spec} catalog={catalog} onChange={onChange} />)
    await user.selectOptions(screen.getByLabelText('Model'), 'anthropic/claude-opus-5-5')
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ model: 'anthropic/claude-opus-5-5', reasoning: 'medium' }))
  })

  it('reports JSON that does not parse', async () => {
    const onValidityChange = vi.fn()
    const { user } = renderWithRouter(
      <SpecEditor value={defaultSpec('anthropic/claude-sonnet-5-5')} catalog={catalog} onChange={vi.fn()} onValidityChange={onValidityChange} />,
    )
    await user.type(screen.getByLabelText('Result schema (JSON, optional)'), '{{{{')
    expect(onValidityChange).toHaveBeenLastCalledWith(false)
    expect(screen.getByText('Not valid JSON')).toBeInTheDocument()
  })

  it('adds an external tool under its server', async () => {
    const onChange = vi.fn()
    const { user } = renderWithRouter(<SpecEditor value={defaultSpec('anthropic/claude-sonnet-5-5')} catalog={catalog} onChange={onChange} />)
    await user.click(screen.getByLabelText(/list_issues/))
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ mcp_servers: [{ server_id: 3, tools: ['list_issues'] }] }))
  })

  it('drops a server once none of its tools are selected', async () => {
    const onChange = vi.fn()
    const spec = { ...defaultSpec('anthropic/claude-sonnet-5-5'), mcp_servers: [{ server_id: 3, tools: ['list_issues'] }] }
    const { user } = renderWithRouter(<SpecEditor value={spec} catalog={catalog} onChange={onChange} />)
    await user.click(screen.getByLabelText(/list_issues/))
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ mcp_servers: [] }))
  })

  it('never offers agent_* tools', () => {
    renderWithRouter(<SpecEditor value={defaultSpec('anthropic/claude-sonnet-5-5')} catalog={catalog} onChange={vi.fn()} />)
    expect(screen.getByLabelText(/agent_spawn/)).toBeDisabled()
  })

  it('warns when a selected server is not connected', () => {
    const spec = { ...defaultSpec('anthropic/claude-sonnet-5-5'), mcp_servers: [{ server_id: 3, tools: ['list_issues'] }] }
    renderWithRouter(<SpecEditor value={spec} catalog={withServer(server({ status: 'needs_reauth' }))} onChange={vi.fn()} />)
    expect(screen.getByText(/fail until it is connected/)).toBeInTheDocument()
  })

  it.each([
    [['core_search', 'web_fetch'], ['list_issues'], true],
    [['core_search'], ['create_issue'], true],
    [['core_search'], ['list_issues'], false],
    [['web_fetch'], ['list_issues'], false],
  ])('warns about exfiltration only with private data and a way out (%j, %j)', (tools, external, warned) => {
    const spec = { ...defaultSpec('anthropic/claude-sonnet-5-5'), tools, mcp_servers: [{ server_id: 3, tools: external }] }
    renderWithRouter(<SpecEditor value={spec} catalog={catalog} onChange={vi.fn()} />)
    expect(screen.queryByRole('alert') !== null).toBe(warned)
  })

  const finishSpec = (finish: { mode: 'answer' | 'tool'; tools: string[] }) => ({
    ...defaultSpec('anthropic/claude-sonnet-5-5'),
    tools: ['core_search', 'web_fetch'],
    mcp_servers: [{ server_id: 3, tools: ['list_issues'] }],
    finish,
  })

  it('shows finish tools only when a finish tool ends the run', () => {
    renderWithRouter(<SpecEditor value={finishSpec({ mode: 'answer', tools: [] })} catalog={catalog} onChange={vi.fn()} />)
    expect(screen.queryByRole('combobox', { name: 'Finish tools' })).not.toBeInTheDocument()
  })

  it('offers only the allowed non-builtin tools, including external ones, as finish tools', async () => {
    const { user } = renderWithRouter(<SpecEditor value={finishSpec({ mode: 'tool', tools: [] })} catalog={catalog} onChange={vi.fn()} />)
    await user.click(screen.getByRole('combobox', { name: 'Finish tools' }))
    const offered = within(screen.getByRole('listbox')).getAllByRole('option').map(o => o.textContent)
    expect(offered).toEqual(['core_search', 'list_issues (linear)'])
  })

  it('picks a finish tool by its exposed name', async () => {
    const onChange = vi.fn()
    const { user } = renderWithRouter(<SpecEditor value={finishSpec({ mode: 'tool', tools: [] })} catalog={catalog} onChange={onChange} />)
    await user.click(screen.getByRole('combobox', { name: 'Finish tools' }))
    await user.click(screen.getByRole('option', { name: 'list_issues (linear)' }))
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ finish: { mode: 'tool', tools: ['mcp_3_list_issues'] } }))
  })

  it('drops a finish tool when it is no longer allowed', async () => {
    const onChange = vi.fn()
    const spec = finishSpec({ mode: 'tool', tools: ['core_search', 'mcp_3_list_issues'] })
    const { user } = renderWithRouter(<SpecEditor value={spec} catalog={catalog} onChange={onChange} />)
    await user.click(screen.getByLabelText(/list_issues/, { selector: 'input[type=checkbox]' }))
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ finish: { mode: 'tool', tools: ['core_search'] } }))
  })
})
