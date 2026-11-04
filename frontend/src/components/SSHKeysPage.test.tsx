import { beforeEach, expect, it, vi } from 'vitest'
import { renderWithUser, screen, waitFor } from '@/test/utils'

const list = vi.fn()
const save = vi.fn()
const remove = vi.fn()
vi.mock('@/hooks/useSSHKeys', () => ({ useSSHKeys: () => ({ list, save, remove }) }))
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ hasScope: () => true }) }))
import { SSHKeysPage } from './SSHKeysPage'

beforeEach(() => {
  vi.clearAllMocks()
  list.mockResolvedValue([])
  save.mockResolvedValue({})
})

it('imports an instance key and passphrase without offering private-key export', async () => {
  const { user } = renderWithUser(<SSHKeysPage />)
  await user.click(await screen.findByRole('button', { name: 'Add SSH key' }))
  await user.type(screen.getByLabelText('Name'), 'Git account')
  await user.selectOptions(screen.getByLabelText('Ownership'), 'instance')
  await user.type(screen.getByLabelText('Private key'), 'private material')
  await user.type(screen.getByLabelText('Passphrase'), 'unlock')
  await user.click(screen.getByRole('button', { name: 'Save key' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith({ name: 'Git account', private_key: 'private material', passphrase: 'unlock', generate: false, instance: true }, undefined))
  await waitFor(() => expect(screen.queryByLabelText('Private key')).not.toBeInTheDocument())
})

it('generates a personal key and lists it', async () => {
  const { user } = renderWithUser(<SSHKeysPage />)
  await user.click(await screen.findByRole('button', { name: 'Add SSH key' }))
  await user.type(screen.getByLabelText('Name'), 'Generated')
  await user.selectOptions(screen.getByLabelText('Method'), 'generate')
  expect(screen.queryByLabelText('Private key')).not.toBeInTheDocument()
  list.mockResolvedValue([{ id: 3, name: 'Generated', user_id: 1, public_key: 'ssh-ed25519 public', fingerprint: 'SHA256:fingerprint' }])
  await user.click(screen.getByRole('button', { name: 'Save key' }))
  expect(await screen.findByText('SHA256:fingerprint')).toBeInTheDocument()
  expect(save).toHaveBeenCalledWith({ name: 'Generated', generate: true, instance: false }, undefined)
})
