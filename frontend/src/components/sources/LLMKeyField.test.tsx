import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '@/test/utils'
import { LLMKeyField, llmKeyLabel } from './LLMKeyField'
import type { LLMKeyView } from '@/hooks/useSources'

const listKeys = vi.fn()
vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => ({ listKeys }),
}))

const view = (overrides: Partial<LLMKeyView> = {}): LLMKeyView => ({
  choice: 'default', key_id: null, label: 'personal', provider: 'anthropic', paid_by_you: true, runs: true, can_choose_default: true, ...overrides,
})

beforeEach(() => {
  listKeys.mockReset()
  listKeys.mockResolvedValue({
    providers: {
      anthropic: [{ id: 7, label: 'work', key_hint: 'ab12', provider: 'anthropic' }],
      openai: [{ id: 9, label: 'gpt', key_hint: 'cd34', provider: 'openai' }],
    },
  })
})

describe('LLMKeyField', () => {
  it('offers default, no LLM, and your keys for the source provider', async () => {
    const onChange = vi.fn()
    const { user } = renderWithRouter(<LLMKeyField value={view()} onChange={onChange} />)
    const select = screen.getByLabelText('LLM key')
    await screen.findByRole('option', { name: 'work (••••ab12)' })
    await user.selectOptions(select, '7')
    expect(onChange).toHaveBeenCalledWith(7)
    await user.selectOptions(select, 'none')
    expect(onChange).toHaveBeenCalledWith('none')
    expect(screen.getByRole('option', { name: 'Default (personal)' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /gpt/ })).not.toBeInTheDocument()
  })

  it("shows another user's key without offering it, and default only to those who may choose it", async () => {
    renderWithRouter(<LLMKeyField value={view({ choice: 'key', label: null, paid_by_you: false, can_choose_default: false })} onChange={vi.fn()} />)
    await screen.findByRole('option', { name: 'work (••••ab12)' })
    expect(screen.getByLabelText('LLM key')).toHaveDisplayValue("Another user's key")
    expect(screen.getByRole('option', { name: "Another user's key" })).toBeDisabled()
    expect(screen.getByRole('option', { name: /^Default/ })).toBeDisabled()
  })

  it('says when no LLM work runs', () => {
    renderWithRouter(<LLMKeyField value={view({ label: null, paid_by_you: false, runs: false })} onChange={vi.fn()} />)
    expect(screen.getByText(/No LLM work runs on this source's new items/)).toBeInTheDocument()
  })

  it('labels compact options and shows the saved choice again when saving fails', async () => {
    const onChange = vi.fn().mockResolvedValue(false)
    const { user } = renderWithRouter(<LLMKeyField compact value={view({ choice: 'none', label: null, paid_by_you: false, runs: false })} onChange={onChange} />)
    const select = screen.getByLabelText('LLM key')
    expect(select).toHaveDisplayValue('LLM: No LLM')
    await user.selectOptions(select, 'default')
    expect(onChange).toHaveBeenCalledWith('default')
    expect(select).toHaveDisplayValue('LLM: No LLM')
  })

  it('shares one key request between fields and says when it fails', async () => {
    listKeys.mockRejectedValue(new Error('boom'))
    renderWithRouter(<>
      <LLMKeyField value={view()} onChange={vi.fn()} />
      <LLMKeyField value={view()} onChange={vi.fn()} />
    </>)
    expect(await screen.findAllByText("Couldn't load your keys.")).toHaveLength(2)
    expect(listKeys).toHaveBeenCalledTimes(1)
  })

  it.each([
    [view(), 'Default (personal)'],
    [view({ label: null, paid_by_you: false }), "Default (another user's key)"],
    [view({ label: null, paid_by_you: false, runs: false }), 'Default: no LLM work runs'],
    [view({ choice: 'none', label: null, paid_by_you: false, runs: false }), 'No LLM'],
    [view({ choice: 'key', key_id: 7, label: 'work' }), 'work'],
    [view({ choice: 'key', key_id: 7, label: null, paid_by_you: false, runs: false }), 'Your key: no LLM work runs'],
    [view({ choice: 'key', label: null, paid_by_you: false }), "Another user's key"],
    [view({ choice: 'key', label: null, paid_by_you: false, runs: false }), "Another user's key: no LLM work runs"],
  ])('labels %o as %s', (value, label) => {
    expect(llmKeyLabel(value)).toBe(label)
  })
})
