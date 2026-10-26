import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, within } from '@/test/utils'
import { KeysPage } from './index'
import type { ProviderKey } from '@/hooks/useAgents'

const hasScope = vi.fn()
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ hasScope }) }))

const listKeys = vi.fn()
const upsertKey = vi.fn()
const deleteKey = vi.fn()
const listSystemAgents = vi.fn()

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => ({ listKeys, upsertKey, deleteKey, listSystemAgents }),
}))

const providerKey = (overrides: Partial<ProviderKey> = {}): ProviderKey => ({
  id: 1, provider: 'anthropic', label: 'personal', key_hint: 'ab12', is_default: true, is_system_default: false, status: 'ok', status_detail: null,
  last_used_at: null, max_cost_usd_per_day: null, created_at: null, updated_at: null, ...overrides,
})

const listed = (anthropic: ProviderKey[]) => ({ providers: { anthropic, openai: [] } })

beforeEach(() => {
  hasScope.mockReturnValue(false)
  listKeys.mockReset()
  upsertKey.mockReset()
  deleteKey.mockReset()
  listSystemAgents.mockReset()
  listSystemAgents.mockResolvedValue([])
})

describe('KeysPage', () => {
  it('lists the system agents your default keys pay for, and which have none', async () => {
    listKeys.mockResolvedValue(listed([providerKey()]))
    listSystemAgents.mockResolvedValue([
      { slug: 'system:summarise', title: 'Summaries', description: 'd', use: 'ingest', model: 'anthropic/claude-haiku-5-5', provider: 'anthropic', has_key: true, problem: null },
      { slug: 'system:hyde', title: 'Search: hypothetical passages', description: 'd', use: 'search', model: 'anthropic/claude-haiku-5-5', provider: 'anthropic', has_key: false, problem: 'Skipped until you add a default Anthropic or OpenAI key.' },
    ])
    renderWithRouter(<KeysPage />)
    const section = (await screen.findByText('Summaries')).closest('section')!
    expect(within(section).getByText('Search: hypothetical passages')).toBeInTheDocument()
    expect(within(section).getAllByText("Can't run")).toHaveLength(1)
    expect(within(section).getByText(/Skipped until/)).toBeInTheDocument()
  })

  it.each([
    ['the same problem once, above the list', ['Skipped until you add a default Anthropic or OpenAI key.', 'Skipped until you add a default Anthropic or OpenAI key.'], 1, true],
    ['different problems on their own rows', ['Skipped until you add a default Anthropic key.', 'Skipped until you add a default OpenAI key.'], 2, false],
  ])('says %s', async (_, problems, shown, summary) => {
    listKeys.mockResolvedValue(listed([]))
    listSystemAgents.mockResolvedValue(problems.map((problem, i) => (
      { slug: `system:${i}`, title: `agent ${i}`, description: 'd', use: 'search', model: 'anthropic/claude-haiku-5-5', provider: 'anthropic', has_key: false, problem }
    )))
    renderWithRouter(<KeysPage />)
    const section = (await screen.findByText('agent 1')).closest('section')!
    expect(within(section).getAllByText("Can't run")).toHaveLength(2)
    expect(within(section).getAllByText(/Skipped until/)).toHaveLength(shown)
    expect(within(section).queryByText(/None of these can run/) !== null).toBe(summary)
  })

  it('groups keys by provider with their hint, default, status and cap', async () => {
    listKeys.mockResolvedValue(listed([
      providerKey({ status: 'invalid', status_detail: 'anthropic rejected this key', max_cost_usd_per_day: '5.000000' }),
    ]))
    renderWithRouter(<KeysPage />)
    expect(await screen.findByText('personal')).toBeInTheDocument()
    expect(screen.getByText('#1')).toBeInTheDocument()
    expect(screen.getByText('••••ab12')).toBeInTheDocument()
    expect(screen.getByText('Default')).toBeInTheDocument()
    expect(screen.getByText('Rejected')).toBeInTheDocument()
    expect(screen.getByText(/anthropic rejected this key/)).toBeInTheDocument()
    expect(screen.getByText(/\$5\.00\/day cap/)).toBeInTheDocument()
    expect(screen.getByText('No OpenAI keys')).toBeInTheDocument()
  })

  it('shows the key check error on the add form', async () => {
    listKeys.mockResolvedValue(listed([]))
    upsertKey.mockRejectedValue(new Error('anthropic rejected this key'))
    const { user } = renderWithRouter(<KeysPage />)
    await user.click(await screen.findByRole('button', { name: 'Add key' }))
    await user.type(screen.getByLabelText('Label'), 'personal')
    await user.type(screen.getByLabelText('API key'), 'sk-ant-secret')
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Add key' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('anthropic rejected this key')
    expect(upsertKey).toHaveBeenCalledWith({ provider: 'anthropic', label: 'personal', key: 'sk-ant-secret' })
    expect(screen.getByLabelText('API key')).toHaveAttribute('type', 'password')
  })

  it('reports what deleting a key changed', async () => {
    listKeys.mockResolvedValue(listed([providerKey(), providerKey({ id: 2, label: 'work', is_default: false })]))
    deleteKey.mockResolvedValue({ deleted: 1, pinned_by: ['digest'], new_default: 2 })
    const { user } = renderWithRouter(<KeysPage />)
    const row = (await screen.findByText('personal')).closest('li')!
    await user.click(within(row).getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    expect(deleteKey).toHaveBeenCalledWith(1)
    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent('These agents used it and fail until you pick another key: digest.')
    expect(notice).toHaveTextContent("'work' is now your default Anthropic key.")
  })

  it('focuses the edit field, and the button that opened it once done', async () => {
    listKeys.mockResolvedValue(listed([providerKey()]))
    const { user } = renderWithRouter(<KeysPage />)
    await user.click(await screen.findByRole('button', { name: 'Rename' }))
    expect(screen.getByLabelText('Label')).toHaveFocus()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('button', { name: 'Rename' })).toHaveFocus()
  })

  it('adds a key for an empty provider from its empty state', async () => {
    listKeys.mockResolvedValue(listed([]))
    const { user } = renderWithRouter(<KeysPage />)
    await user.click(await screen.findByRole('button', { name: 'Add OpenAI key' }))
    expect(within(screen.getByRole('dialog')).getByLabelText('Provider')).toHaveDisplayValue('OpenAI')
  })
})


it.each([false, true])('admins can toggle system default from %s', async selected => {
  hasScope.mockReturnValue(true)
  listKeys.mockResolvedValue(listed([providerKey({ is_system_default: selected })]))
  upsertKey.mockResolvedValue({})
  const { user } = renderWithRouter(<KeysPage />)
  await user.click(await screen.findByRole('button', { name: selected ? 'Clear system default' : 'Use as system default' }))
  expect(upsertKey).toHaveBeenCalledWith({ keyId: 1, isSystemDefault: !selected })
})

it('hides system default controls from non-admins', async () => {
  listKeys.mockResolvedValue(listed([providerKey()]))
  renderWithRouter(<KeysPage />)
  await screen.findByText('personal')
  expect(screen.queryByRole('button', { name: /system default/i })).not.toBeInTheDocument()
})
