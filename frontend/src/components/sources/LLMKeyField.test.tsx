import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
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
  vi.restoreAllMocks()
  listKeys.mockReset()
  listKeys.mockResolvedValue({
    providers: {
      anthropic: [{ id: 7, label: 'work', key_hint: 'ab12', provider: 'anthropic' }],
      openai: [{ id: 9, label: 'gpt', key_hint: 'cd34', provider: 'openai' }],
    },
  })
})

describe('LLMKeyField', () => {
  it("offers default, no LLM, and your keys of every provider, named by provider", async () => {
    const onChange = vi.fn()
    const { user } = renderWithRouter(<LLMKeyField value={view()} onChange={onChange} />)
    const select = screen.getByLabelText('LLM key')
    await screen.findByRole('option', { name: 'work (Anthropic ••••ab12)' })
    await user.selectOptions(select, '9')
    expect(onChange).toHaveBeenCalledWith(9)
    await user.selectOptions(select, 'none')
    expect(onChange).toHaveBeenCalledWith('none')
    expect(screen.getByRole('option', { name: 'Default (personal)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'gpt (OpenAI ••••cd34)' })).toBeInTheDocument()
  })

  it("shows another user's key without offering it, and default only to those who may choose it", async () => {
    renderWithRouter(<LLMKeyField value={view({ choice: 'key', label: null, paid_by_you: false, can_choose_default: false })} onChange={vi.fn()} />)
    await screen.findByRole('option', { name: 'work (Anthropic ••••ab12)' })
    expect(screen.getByLabelText('LLM key')).toHaveDisplayValue("Another user's key")
    expect(screen.getByRole('option', { name: "Another user's key" })).toBeDisabled()
    expect(screen.getByRole('option', { name: /^Default/ })).toBeDisabled()
  })

  it.each([
    ['compact', true, view({ choice: 'key', label: null, paid_by_you: false, can_choose_default: false }), /the other user's key/],
    ['compact', true, view({ label: null, paid_by_you: false, can_choose_default: false }), /back to Default/],
    ['form', false, view({ choice: 'key', label: null, paid_by_you: false, can_choose_default: false }), /the other user's key/],
  ])('%s: confirms before leaving a choice the viewer cannot restore', async (_, compact, value, warning) => {
    const onChange = vi.fn().mockResolvedValue(true)
    const { user } = renderWithRouter(<LLMKeyField compact={compact} value={value} onChange={onChange} />)
    const select = screen.getByLabelText('LLM key')
    const before = (select as HTMLSelectElement).value
    await screen.findByRole('option', { name: /work/ })

    await user.selectOptions(select, '7')
    expect(screen.getByRole('dialog')).toHaveTextContent(warning)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onChange).not.toHaveBeenCalled()
    expect(select).toHaveValue(before)

    await user.selectOptions(select, '7')
    await user.click(screen.getByRole('button', { name: 'Switch' }))
    expect(onChange).toHaveBeenCalledWith(7)
  })

  it.each([
    [view({ choice: 'none', label: null, paid_by_you: false, can_choose_default: false }), '7'],
    [view({ choice: 'key', key_id: 7, label: 'work', can_choose_default: false }), 'none'],
    [view(), 'none'],
  ])('switches without asking when the viewer can switch back: %o', async (value, pick) => {
    const onChange = vi.fn().mockResolvedValue(true)
    const { user } = renderWithRouter(<LLMKeyField compact value={value} onChange={onChange} />)
    await screen.findByRole('option', { name: /work/ })
    await user.selectOptions(screen.getByLabelText('LLM key'), pick)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(onChange).toHaveBeenCalled()
  })

  it('says when no LLM work runs', () => {
    renderWithRouter(<LLMKeyField value={view({ label: null, paid_by_you: false, runs: false })} onChange={vi.fn()} />)
    expect(screen.getByText(/No LLM work runs on this source's new items/)).toBeInTheDocument()
  })

  it('says once, in a badge that describes the select, when no LLM work runs on a compact row', () => {
    renderWithRouter(<LLMKeyField compact value={view({ label: null, paid_by_you: false, runs: false })} onChange={vi.fn()} />)
    expect(screen.getByLabelText('LLM key')).toHaveDisplayValue('LLM: Default')
    expect(screen.getAllByText(/no LLM work runs/i)).toHaveLength(1)
    expect(screen.getByLabelText('LLM key')).toHaveAccessibleDescription('No LLM work runs')
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

  it('opens key management in a new tab, keeping the form', () => {
    renderWithRouter(<LLMKeyField value={view()} onChange={vi.fn()} />)
    const link = screen.getByRole('link', { name: /Manage LLM keys/ })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAccessibleName('Manage LLM keys (opens in a new tab)')
  })

  const fresh = { providers: { anthropic: [{ id: 8, label: 'fresh', key_hint: 'ef56', provider: 'anthropic' }] } }
  const returnAfter = async (ms: number) => {
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + ms)
    fireEvent.focus(window)
    await Promise.resolve()
  }

  it('picks up new keys on returning to the tab, at most every 30s', async () => {
    renderWithRouter(<LLMKeyField value={view()} onChange={vi.fn()} />)
    await screen.findByRole('option', { name: /work/ })
    listKeys.mockResolvedValue(fresh)
    await returnAfter(10_000)
    expect(listKeys).toHaveBeenCalledTimes(1)
    await returnAfter(31_000)
    expect(await screen.findByRole('option', { name: 'fresh (Anthropic ••••ef56)' })).toBeInTheDocument()
    expect(listKeys).toHaveBeenCalledTimes(2)
  })

  it('retries a failed first load on returning to the tab', async () => {
    listKeys.mockRejectedValue(new Error('offline'))
    renderWithRouter(<LLMKeyField value={view()} onChange={vi.fn()} />)
    expect(await screen.findByText("Couldn't load your keys.")).toBeInTheDocument()
    listKeys.mockResolvedValue(fresh)
    await returnAfter(1_000)
    expect(await screen.findByRole('option', { name: 'fresh (Anthropic ••••ef56)' })).toBeInTheDocument()
    expect(screen.queryByText("Couldn't load your keys.")).not.toBeInTheDocument()
  })

  it('keeps the keys it has when a refetch fails, without an error', async () => {
    renderWithRouter(<LLMKeyField value={view()} onChange={vi.fn()} />)
    await screen.findByRole('option', { name: /work/ })
    listKeys.mockRejectedValue(new Error('offline'))
    await returnAfter(31_000)
    await waitFor(() => expect(listKeys).toHaveBeenCalledTimes(2))
    await Promise.resolve()
    expect(screen.getByRole('option', { name: 'work (Anthropic ••••ab12)' })).toBeInTheDocument()
    expect(screen.queryByText("Couldn't load your keys.")).not.toBeInTheDocument()
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
