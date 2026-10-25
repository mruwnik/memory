import { describe, it, expect, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import { mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'
import UserSettings from './UserSettings'

const authMe = (overrides = {}) => ({
  user_id: 1,
  name: 'Ada',
  email: 'ada@x.com',
  user_type: 'human',
  scopes: ['read', 'teams'],
  ...overrides,
})

const me = (overrides = {}) => ({
  id: 1,
  name: 'Ada',
  email: 'ada@x.com',
  user_type: 'human',
  scopes: ['read', 'teams'],
  timezone: 'UTC',
  api_key_count: 0,
  ...overrides,
})

const fieldByLabel = (container: HTMLElement, labelText: string): HTMLInputElement => {
  const label = within(container).getByText(labelText)
  return label.parentElement?.querySelector('input') as HTMLInputElement
}

const modalByHeading = (name: RegExp | string): HTMLElement =>
  screen.getByRole('heading', { name }).closest('div.fixed') as HTMLElement

// Serves /auth/me, /users/me, and routes method-specific handlers.
const install = (
  meUser: any,
  authUser: any = authMe(),
  handlers: Record<string, (init?: RequestInit) => Response | undefined> = {},
) =>
  mockFetch(async (input, init) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (url.includes('/auth/me')) return mockResponse({ json: authUser })

    for (const [pattern, handler] of Object.entries(handlers)) {
      if (url.includes(pattern)) {
        const r = handler(init)
        if (r) return r
      }
    }

    if (url.endsWith('/users/me') && method === 'GET') return mockResponse({ json: meUser })
    return mockResponse({ json: {} })
  })

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('UserSettings load states', () => {
  it('shows loading initially', () => {
    install(me())
    renderWithRouter(<UserSettings />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('renders profile, scopes, and API-key status when loaded', async () => {
    install(me({ scopes: ['read', 'admin'], api_key_count: 0 }))
    renderWithRouter(<UserSettings />)
    expect(await screen.findByDisplayValue('Ada')).toBeInTheDocument()
    expect(screen.getByDisplayValue('ada@x.com')).toBeInTheDocument()
    expect(screen.getByText('No API key configured')).toBeInTheDocument()
    expect(screen.getByText('read')).toBeInTheDocument()
    expect(screen.getByText('admin')).toBeInTheDocument()
  })

  it('shows the configured API key message when api_key_count > 0', async () => {
    install(me({ api_key_count: 2 }))
    renderWithRouter(<UserSettings />)
    expect(await screen.findByText('You have an API key configured')).toBeInTheDocument()
  })

  it('shows an error with retry when loading fails', async () => {
    mockFetch(async (input) => {
      const url = String(input)
      if (url.includes('/auth/me')) return mockResponse({ json: authMe() })
      return mockResponse({ status: 500, json: {} })
    })
    renderWithRouter(<UserSettings />)
    expect(await screen.findByText(/Failed to fetch current user/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

describe('UserSettings password section gating', () => {
  it('shows the password row for human users', async () => {
    install(me(), authMe({ user_type: 'human' }))
    renderWithRouter(<UserSettings />)
    expect(await screen.findByRole('button', { name: 'Change password' })).toBeInTheDocument()
  })

  it('hides the password row for bot users', async () => {
    install(me({ user_type: 'bot' }), authMe({ user_type: 'bot' }))
    renderWithRouter(<UserSettings />)
    await screen.findByDisplayValue('Ada')
    expect(screen.queryByRole('button', { name: 'Change password' })).not.toBeInTheDocument()
  })
})

describe('UserSettings save profile', () => {
  it('offers Save only once something changed', async () => {
    install(me())
    const { user } = renderWithRouter(<UserSettings />)
    const nameInput = await screen.findByDisplayValue('Ada')
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
    await user.type(nameInput, 'x')
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled()
  })

  it('PATCHes only the changed fields and shows success', async () => {
    const patched: any[] = []
    install(me(), authMe(), {
      '/users/1': (init) => {
        if (init?.method !== 'PATCH') return undefined
        patched.push(JSON.parse(init.body as string))
        return mockResponse({ json: me({ name: 'Ada Lovelace' }) })
      },
    })
    const { user } = renderWithRouter(<UserSettings />)
    const nameInput = await screen.findByDisplayValue('Ada')
    await user.clear(nameInput)
    await user.type(nameInput, 'Ada Lovelace{Enter}')

    expect(await screen.findByText('Profile updated successfully')).toBeInTheDocument()
    await waitFor(() => expect(patched).toHaveLength(1))
    expect(patched[0]).toEqual({ name: 'Ada Lovelace' })
  })

  it('PATCHes a picked timezone', async () => {
    const patched: any[] = []
    install(me({ timezone: 'UTC' }), authMe(), {
      '/users/1': (init) => {
        if (init?.method !== 'PATCH') return undefined
        patched.push(JSON.parse(init.body as string))
        return mockResponse({ json: me({ timezone: 'Asia/Kolkata' }) })
      },
    })
    const { user } = renderWithRouter(<UserSettings />)
    const timezone = await screen.findByRole('combobox', { name: 'Timezone' })
    await user.clear(timezone)
    await user.type(timezone, 'Kolkata')
    await user.click(await screen.findByRole('option', { name: /Asia\/Kolkata/ }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(patched).toEqual([{ timezone: 'Asia/Kolkata' }]))
  })

  it('shows an error message when the update fails', async () => {
    install(me(), authMe(), {
      '/users/1': (init) =>
        init?.method === 'PATCH' ? mockResponse({ status: 400, json: { detail: 'bad email' } }) : undefined,
    })
    const { user } = renderWithRouter(<UserSettings />)
    const emailInput = await screen.findByDisplayValue('ada@x.com')
    await user.clear(emailInput)
    await user.type(emailInput, 'new@x.com')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('bad email')).toBeInTheDocument()
  })
})

describe('UserSettings change password', () => {
  it('validates that passwords match', async () => {
    install(me())
    const { user } = renderWithRouter(<UserSettings />)
    await user.click(await screen.findByRole('button', { name: 'Change password' }))
    const modal = modalByHeading('Change password')
    await user.type(fieldByLabel(modal, 'Current password'), 'oldpass12')
    await user.type(fieldByLabel(modal, 'New password'), 'newpass12')
    await user.type(fieldByLabel(modal, 'Confirm new password'), 'different')
    await user.click(within(modal).getByRole('button', { name: 'Change password' }))
    expect(fieldByLabel(modal, 'Confirm new password')).toHaveAccessibleDescription('Passwords do not match')
  })

  it('validates minimum password length', async () => {
    install(me())
    const { user } = renderWithRouter(<UserSettings />)
    await user.click(await screen.findByRole('button', { name: 'Change password' }))
    const modal = modalByHeading('Change password')
    await user.type(fieldByLabel(modal, 'Current password'), 'oldpass12')
    await user.type(fieldByLabel(modal, 'New password'), 'short')
    expect(fieldByLabel(modal, 'New password')).toHaveAccessibleDescription('At least 8 characters')
    await user.type(fieldByLabel(modal, 'Confirm new password'), 'short')
    await user.click(within(modal).getByRole('button', { name: 'Change password' }))
    expect(fieldByLabel(modal, 'New password')).toHaveAccessibleDescription('At least 8 characters Password must be at least 8 characters')
  })

  it('posts a valid password change and closes the modal with success', async () => {
    const posted: any[] = []
    install(me(), authMe(), {
      '/users/me/change-password': (init) => {
        posted.push(JSON.parse(init!.body as string))
        return mockResponse({ status: 200, json: {} })
      },
    })
    const { user } = renderWithRouter(<UserSettings />)
    await user.click(await screen.findByRole('button', { name: 'Change password' }))
    const modal = modalByHeading('Change password')
    await user.type(fieldByLabel(modal, 'Current password'), 'oldpass12')
    await user.type(fieldByLabel(modal, 'New password'), 'newpass12')
    await user.type(fieldByLabel(modal, 'Confirm new password'), 'newpass12{Enter}')

    expect(await screen.findByText('Password changed successfully')).toBeInTheDocument()
    expect(posted[0]).toEqual({ current_password: 'oldpass12', new_password: 'newpass12' })
  })

  it('surfaces a server error inside the password modal', async () => {
    install(me(), authMe(), {
      '/users/me/change-password': () => mockResponse({ status: 400, json: { detail: 'wrong current password' } }),
    })
    const { user } = renderWithRouter(<UserSettings />)
    await user.click(await screen.findByRole('button', { name: 'Change password' }))
    const modal = modalByHeading('Change password')
    await user.type(fieldByLabel(modal, 'Current password'), 'oldpass12')
    await user.type(fieldByLabel(modal, 'New password'), 'newpass12')
    await user.type(fieldByLabel(modal, 'Confirm new password'), 'newpass12')
    await user.click(within(modal).getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('wrong current password')).toBeInTheDocument()
  })
})

describe('UserSettings API key', () => {
  it('shows why generating a key failed inside the dialog', async () => {
    install(me({ api_key_count: 1 }), authMe(), {
      '/api-keys': (init) =>
        init?.method === 'POST' ? mockResponse({ status: 500, json: { detail: 'key store down' } }) : undefined,
    })
    const { user } = renderWithRouter(<UserSettings />)
    await user.click(await screen.findByRole('button', { name: /Regenerate API key/ }))
    const modal = modalByHeading(/Regenerate API key/)
    await user.click(within(modal).getByRole('button', { name: 'Regenerate' }))

    expect(await within(modal).findByText(/key store down/)).toBeInTheDocument()
  })

  it('generates a key and reveals it, then copies to clipboard', async () => {
    install(me({ api_key_count: 0 }), authMe(), {
      '/api-keys': (init) =>
        init?.method === 'POST' ? mockResponse({ json: { key: 'mcp_newkey' } }) : undefined,
    })
    const { user } = renderWithRouter(<UserSettings />)
    await screen.findByText('No API key configured')
    await user.click(screen.getByRole('button', { name: /Generate API key/ }))
    const modal = modalByHeading(/Generate API key/)
    await user.click(within(modal).getByRole('button', { name: 'Generate' }))

    expect(await screen.findByText('mcp_newkey')).toBeInTheDocument()
    // Copying invokes navigator.clipboard.writeText; user-event provides its own
    // clipboard stub, so assert via its readText rather than the spy.
    await user.click(screen.getByRole('button', { name: 'Copy' }))
    await waitFor(async () =>
      expect(await navigator.clipboard.readText()).toBe('mcp_newkey'),
    )

    // The key is never shown again, so only Done may close the dialog.
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('button', { name: 'Close' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByText('mcp_newkey')).not.toBeInTheDocument()
  })
})
