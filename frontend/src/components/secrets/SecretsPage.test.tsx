import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithUser, screen, waitFor, within } from '@/test/utils'

// --- Hook mock ---
const listSecrets = vi.fn()
const createSecret = vi.fn()
const updateSecret = vi.fn()
const deleteSecret = vi.fn()
const getSecretValue = vi.fn()

vi.mock('@/hooks/useSecrets', () => ({
  useSecrets: () => ({
    listSecrets,
    createSecret,
    updateSecret,
    deleteSecret,
    getSecretValue,
  }),
}))

import { SecretsPage } from './SecretsPage'

const secret = (over: Partial<{ id: number; name: string; description: string | null }> = {}) => ({
  id: 1,
  name: 'api-key',
  description: 'My API key',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-02T00:00:00Z',
  ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  listSecrets.mockResolvedValue([])
  createSecret.mockResolvedValue(secret())
  updateSecret.mockResolvedValue(secret())
  deleteSecret.mockResolvedValue(undefined)
  getSecretValue.mockResolvedValue({ ...secret(), value: 's3cr3t-value' })
})

describe('SecretsPage - load states', () => {
  it('shows loading state first', () => {
    listSecrets.mockReturnValue(new Promise(() => {}))
    renderWithUser(<SecretsPage />)
    expect(screen.getByText('Loading secrets...')).toBeInTheDocument()
  })

  it('shows empty state when there are no secrets', async () => {
    renderWithUser(<SecretsPage />)
    expect(await screen.findByText('No secrets configured')).toBeInTheDocument()
  })

  it('shows error state and retries on Retry click', async () => {
    listSecrets.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderWithUser(<SecretsPage />)
    expect(await screen.findByText('boom')).toBeInTheDocument()

    listSecrets.mockResolvedValueOnce([])
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('No secrets configured')).toBeInTheDocument()
  })

  it('renders populated list with name and description', async () => {
    listSecrets.mockResolvedValue([secret(), secret({ id: 2, name: 'token', description: null })])
    renderWithUser(<SecretsPage />)
    expect(await screen.findByText('api-key')).toBeInTheDocument()
    expect(screen.getByText('token')).toBeInTheDocument()
    expect(screen.getByText('My API key')).toBeInTheDocument()
  })
})

describe('SecretsPage - create flow', () => {
  beforeEach(() => listSecrets.mockResolvedValue([secret()]))

  it('creates a secret with all fields then closes the form', async () => {
    const { user } = renderWithUser(<SecretsPage />)
    await screen.findByText('api-key')

    await user.click(screen.getByRole('button', { name: 'Add secret' }))
    await user.type(screen.getByPlaceholderText('api-key or github-token'), 'new-key')
    await user.type(screen.getByPlaceholderText('Enter secret value'), 'val123')
    await user.type(screen.getByPlaceholderText('What is this secret used for?'), 'desc')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(createSecret).toHaveBeenCalledWith({ name: 'new-key', value: 'val123', description: 'desc' }),
    )
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Add secret' })).not.toBeInTheDocument())
    expect(listSecrets).toHaveBeenCalledTimes(2)
  })

  it('displays the error returned by createSecret without closing the form', async () => {
    createSecret.mockRejectedValueOnce(new Error('duplicate name'))
    const { user } = renderWithUser(<SecretsPage />)
    await screen.findByText('api-key')

    await user.click(screen.getByRole('button', { name: 'Add secret' }))
    await user.type(screen.getByPlaceholderText('api-key or github-token'), 'dup')
    await user.type(screen.getByPlaceholderText('Enter secret value'), 'v')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('duplicate name')).toBeInTheDocument()
  })
})

describe('SecretsPage - edit flow', () => {
  beforeEach(() => listSecrets.mockResolvedValue([secret()]))

  it('only sends changed value/description fields and disables the name input', async () => {
    const { user } = renderWithUser(<SecretsPage />)
    await screen.findByText('api-key')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const nameInput = screen.getByPlaceholderText('api-key or github-token')
    expect(nameInput).toBeDisabled()

    await user.type(screen.getByPlaceholderText('Enter new value to update'), 'rotated')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(updateSecret).toHaveBeenCalledWith(1, { value: 'rotated' }))
  })

  it('sends an empty update when nothing changes', async () => {
    const { user } = renderWithUser(<SecretsPage />)
    await screen.findByText('api-key')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(updateSecret).toHaveBeenCalledWith(1, {}))
  })
})

describe('SecretsPage - delete flow', () => {
  beforeEach(() => listSecrets.mockResolvedValue([secret()]))

  it('deletes after confirm is accepted', async () => {
    const { user } = renderWithUser(<SecretsPage />)
    await screen.findByText('api-key')

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(deleteSecret).toHaveBeenCalledWith(1))
  })

  it('does not delete when confirm is rejected', async () => {
    const { user } = renderWithUser(<SecretsPage />)
    await screen.findByText('api-key')

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(deleteSecret).not.toHaveBeenCalled()
  })

  it('surfaces a delete error', async () => {
    deleteSecret.mockRejectedValueOnce(new Error('cannot delete'))
    const { user } = renderWithUser(<SecretsPage />)
    await screen.findByText('api-key')

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    expect(await screen.findByText('cannot delete')).toBeInTheDocument()
    expect(screen.getByText('api-key')).toBeInTheDocument()
  })
})

describe('SecretsPage - reveal & copy', () => {
  beforeEach(() => listSecrets.mockResolvedValue([secret()]))

  it('reveals the value then hides it again without re-fetching', async () => {
    const { user } = renderWithUser(<SecretsPage />)
    await screen.findByText('api-key')

    await user.click(screen.getByRole('button', { name: 'Reveal value' }))
    expect(await screen.findByText('s3cr3t-value')).toBeInTheDocument()
    expect(getSecretValue).toHaveBeenCalledWith(1)

    await user.click(screen.getByRole('button', { name: 'Hide value' }))
    expect(screen.queryByText('s3cr3t-value')).not.toBeInTheDocument()
    expect(getSecretValue).toHaveBeenCalledTimes(1)
  })

  it('copies the revealed value from clipboard cache without refetching', async () => {
    const { user } = renderWithUser(<SecretsPage />)
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    await screen.findByText('api-key')

    await user.click(screen.getByRole('button', { name: 'Reveal value' }))
    await screen.findByText('s3cr3t-value')
    await user.click(screen.getByRole('button', { name: 'Copy to clipboard' }))

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('s3cr3t-value'))
    expect(getSecretValue).toHaveBeenCalledTimes(1)
  })

  it('fetches the value to copy when not revealed', async () => {
    const { user } = renderWithUser(<SecretsPage />)
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    await screen.findByText('api-key')

    await user.click(screen.getByRole('button', { name: 'Copy to clipboard' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('s3cr3t-value'))
    expect(getSecretValue).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Copied api-key')).toBeInTheDocument()
  })

  it('shows an error when revealing fails', async () => {
    getSecretValue.mockRejectedValueOnce(new Error('forbidden'))
    const { user } = renderWithUser(<SecretsPage />)
    await screen.findByText('api-key')

    await user.click(screen.getByRole('button', { name: 'Reveal value' }))
    expect(await screen.findByText('forbidden')).toBeInTheDocument()
    expect(screen.getByText('api-key')).toBeInTheDocument()
  })
})
