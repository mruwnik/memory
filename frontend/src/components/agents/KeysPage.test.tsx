import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, within } from '@/test/utils'
import { KeysPage } from './index'
import type { ProviderKey } from '@/hooks/useAgents'

const listKeys = vi.fn()
const upsertKey = vi.fn()
const deleteKey = vi.fn()

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => ({ listKeys, upsertKey, deleteKey }),
}))

const providerKey = (overrides: Partial<ProviderKey> = {}): ProviderKey => ({
  id: 1, provider: 'anthropic', label: 'personal', key_hint: 'ab12', is_default: true, status: 'ok', status_detail: null,
  last_used_at: null, max_cost_usd_per_day: null, created_at: null, updated_at: null, ...overrides,
})

const listed = (anthropic: ProviderKey[]) => ({ providers: { anthropic, openai: [] }, server_key_allowed: false })

beforeEach(() => {
  listKeys.mockReset()
  upsertKey.mockReset()
  deleteKey.mockReset()
})

describe('KeysPage', () => {
  it('groups keys by provider with their hint, default, status and cap', async () => {
    listKeys.mockResolvedValue(listed([
      providerKey({ status: 'invalid', status_detail: 'anthropic rejected this key', max_cost_usd_per_day: '5.000000' }),
    ]))
    renderWithRouter(<KeysPage />)
    expect(await screen.findByText('personal')).toBeInTheDocument()
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
    await user.click(await screen.findByRole('button', { name: 'New key' }))
    await user.type(screen.getByLabelText('Label'), 'personal')
    await user.type(screen.getByLabelText('API key'), 'sk-ant-secret')
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Add key' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('anthropic rejected this key')
    expect(upsertKey).toHaveBeenCalledWith({ provider: 'anthropic', label: 'personal', key: 'sk-ant-secret' })
    expect(screen.getByLabelText('API key')).toHaveAttribute('type', 'password')
  })

  it('reports what deleting a key changed', async () => {
    listKeys.mockResolvedValue(listed([providerKey(), providerKey({ id: 2, label: 'work', is_default: false })]))
    deleteKey.mockResolvedValue({ deleted: 1, pinned_by: ['digest'], new_default: 2, note: null })
    const { user } = renderWithRouter(<KeysPage />)
    const row = (await screen.findByText('personal')).closest('li')!
    await user.click(within(row).getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    expect(deleteKey).toHaveBeenCalledWith(1)
    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent('These agents used it and fail until you pick another key: digest.')
    expect(notice).toHaveTextContent("'work' is now your default Anthropic key.")
  })
})
