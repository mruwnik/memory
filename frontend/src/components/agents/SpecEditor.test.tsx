import { describe, it, expect, vi } from 'vitest'
import { renderWithUser, screen } from '@/test/utils'
import { SpecEditor } from './SpecEditor'
import { defaultSpec, type Catalog } from '@/hooks/useAgents'

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
  ],
  ceilings: { max_run_cost_usd: '1.00', max_daily_cost_usd: '10.00', max_timeout_sec: 1800, max_runs_per_hour: 60 },
  default_model: 'anthropic/claude-sonnet-5-5',
  trigger_source_types: ['blog_post', 'discord_message'],
}

describe('SpecEditor', () => {
  it('only offers the reasoning levels the model supports', () => {
    renderWithUser(<SpecEditor value={defaultSpec('anthropic/claude-opus-5-5')} catalog={catalog} onChange={vi.fn()} />)
    const options = Array.from(screen.getByLabelText('Reasoning').querySelectorAll('option')).map(o => o.textContent)
    expect(options).toEqual(['low', 'medium', 'high'])
  })

  it('disables temperature when the model forbids sampling', () => {
    renderWithUser(<SpecEditor value={defaultSpec('anthropic/claude-sonnet-5-5')} catalog={catalog} onChange={vi.fn()} />)
    expect(screen.getByLabelText('Temperature')).toBeDisabled()
  })

  it('leaves max output tokens to the run by default', () => {
    renderWithUser(<SpecEditor value={defaultSpec('anthropic/claude-sonnet-5-5')} catalog={catalog} onChange={vi.fn()} />)
    expect(screen.getByLabelText('Max output tokens')).toHaveValue(null)
  })

  it('marks tools with side effects', () => {
    renderWithUser(<SpecEditor value={defaultSpec('anthropic/claude-sonnet-5-5')} catalog={catalog} onChange={vi.fn()} />)
    expect(screen.getByText('discord_send_message').closest('label')).toHaveTextContent('side effects')
    expect(screen.getByText('core_search').closest('label')).not.toHaveTextContent('side effects')
  })

  it('adds a selected tool to the spec', async () => {
    const onChange = vi.fn()
    const { user } = renderWithUser(<SpecEditor value={defaultSpec('anthropic/claude-sonnet-5-5')} catalog={catalog} onChange={onChange} />)
    await user.click(screen.getByLabelText(/core_search/))
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ tools: ['core_search'] }))
  })

  it('fits reasoning to the new model when the model changes', async () => {
    const onChange = vi.fn()
    const spec = { ...defaultSpec('anthropic/claude-sonnet-5-5'), reasoning: 'off' as const }
    const { user } = renderWithUser(<SpecEditor value={spec} catalog={catalog} onChange={onChange} />)
    await user.selectOptions(screen.getByLabelText('Model'), 'anthropic/claude-opus-5-5')
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ model: 'anthropic/claude-opus-5-5', reasoning: 'medium' }))
  })

  it('reports JSON that does not parse', async () => {
    const onValidityChange = vi.fn()
    const { user } = renderWithUser(
      <SpecEditor value={defaultSpec('anthropic/claude-sonnet-5-5')} catalog={catalog} onChange={vi.fn()} onValidityChange={onValidityChange} />,
    )
    await user.type(screen.getByLabelText('Result schema (JSON, optional)'), '{{{{')
    expect(onValidityChange).toHaveBeenLastCalledWith(false)
    expect(screen.getByText('Not valid JSON')).toBeInTheDocument()
  })
})
