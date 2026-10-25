import { describe, it, expect, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import { mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'
import UserManagement from './UserManagement'

const authMe = { user_id: 1, name: 'Admin', email: 'admin@x.com', user_type: 'human', scopes: ['*'] }

const scopes = [
  { value: 'read', label: 'Read', description: 'Read access', category: 'core' },
  { value: 'admin', label: 'Admin', description: 'Full access', category: 'core' },
]

const user = (overrides = {}) => ({
  id: 2,
  name: 'Bob',
  email: 'bob@x.com',
  user_type: 'human',
  scopes: ['read'],
  api_key_count: 0,
  ...overrides,
})

interface RouteState {
  users: any[]
  scopes?: any[]
}

// Build a fetch implementation that serves /auth/me, /users, /users/scopes,
// and method-specific responses, recording calls for assertions.
const installFetch = (
  state: RouteState,
  handlers: Record<string, (init?: RequestInit) => any> = {},
) =>
  mockFetch(async (input, init) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method || 'GET'
    const key = `${method} ${url.replace(/^.*(\/users.*|\/auth.*)$/, '$1')}`

    if (url.includes('/auth/me')) return mockResponse({ json: authMe })
    if (url.endsWith('/users/scopes')) return mockResponse({ json: state.scopes ?? scopes })

    for (const [pattern, handler] of Object.entries(handlers)) {
      if (key.startsWith(pattern) || url.includes(pattern)) {
        const result = handler(init)
        if (result) return result
      }
    }

    if (method === 'GET' && url.endsWith('/users')) return mockResponse({ json: state.users })
    return mockResponse({ json: {} })
  })

// The create/edit modals use plain <label> elements that are not associated
// with their inputs via htmlFor, so getByLabelText cannot find them. This finds
// the input that follows a label with the given text within a container.
const fieldByLabel = (container: HTMLElement, labelText: string): HTMLInputElement => {
  const label = within(container).getByText(labelText)
  const input = label.parentElement?.querySelector('input, select, textarea')
  return input as HTMLInputElement
}

// Returns the modal container element given a known heading inside it.
const modalByHeading = (name: RegExp | string): HTMLElement => {
  const heading = screen.getByRole('heading', { name })
  return heading.closest('div.fixed') as HTMLElement
}

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('UserManagement list states', () => {
  it('shows loading initially', () => {
    installFetch({ users: [] })
    renderWithRouter(<UserManagement />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('renders empty state', async () => {
    installFetch({ users: [] })
    renderWithRouter(<UserManagement />)
    expect(await screen.findByText(/No users found/)).toBeInTheDocument()
  })

  it('renders users with type badge, email, scopes, and (you) marker', async () => {
    installFetch({
      users: [
        user({ id: 1, name: 'Admin', email: 'admin@x.com', scopes: ['*'] }),
        user({ id: 2, name: 'Bob', user_type: 'bot', scopes: ['read'], api_key_count: 1 }),
      ],
    })
    renderWithRouter(<UserManagement />)
    expect(await screen.findByText('Admin')).toBeInTheDocument()
    expect(screen.getByText('(you)')).toBeInTheDocument()
    expect(screen.getByText('Bob')).toBeInTheDocument()
    expect(screen.getByText('Human')).toBeInTheDocument()
    expect(screen.getByText('Bot')).toBeInTheDocument()
    expect(screen.getByText('Has API key configured')).toBeInTheDocument()
  })

  it('shows an error when loading users fails', async () => {
    mockFetch(async (input) => {
      const url = String(input)
      if (url.includes('/auth/me')) return mockResponse({ json: authMe })
      if (url.endsWith('/users/scopes')) return mockResponse({ json: state.scopes ?? scopes })
      return mockResponse({ status: 403, json: { detail: 'no' } })
    })
    renderWithRouter(<UserManagement />)
    expect(await screen.findByText('Insufficient permissions')).toBeInTheDocument()
  })
})

describe('UserManagement permission gating', () => {
  it('hides Delete for the current user but shows it for others', async () => {
    installFetch({
      users: [user({ id: 1, name: 'Admin' }), user({ id: 2, name: 'Bob' })],
    })
    renderWithRouter(<UserManagement />)
    await screen.findByText('Admin')
    // Two user rows -> one Delete button (the other is the current user)
    expect(screen.getAllByRole('button', { name: 'Delete' })).toHaveLength(1)
  })

  it('shows Generate/Regenerate Key only for bot users', async () => {
    installFetch({
      users: [
        user({ id: 2, name: 'Bot1', user_type: 'bot', api_key_count: 0 }),
        user({ id: 3, name: 'Bot2', user_type: 'bot', api_key_count: 1 }),
        user({ id: 4, name: 'Human1', user_type: 'human' }),
      ],
    })
    renderWithRouter(<UserManagement />)
    await screen.findByText('Bot1')
    expect(screen.getByRole('button', { name: 'Generate key' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Regenerate key' })).toBeInTheDocument()
  })
})

describe('UserManagement create flow', () => {
  it('creates a human user with password and posts the payload', async () => {
    const posted: any[] = []
    installFetch(
      { users: [] },
      {
        'POST /users': (init) => {
          posted.push(JSON.parse(init!.body as string))
          return mockResponse({ json: user({ id: 9, name: 'Carol' }) })
        },
      },
    )
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText(/No users found/)

    await ue.click(screen.getByRole('button', { name: /New user/ }))
    const modal = modalByHeading('New user')
    await ue.type(fieldByLabel(modal, 'Name'), 'Carol')
    await ue.type(fieldByLabel(modal, 'Email'), 'carol@x.com')
    await ue.type(fieldByLabel(modal, 'Password'), 'secretpw{Enter}')

    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]).toMatchObject({
      name: 'Carol',
      email: 'carol@x.com',
      user_type: 'human',
      password: 'secretpw',
    })
  })

  it('blocks creating a human user without a password', async () => {
    const posted: any[] = []
    installFetch(
      { users: [] },
      { 'POST /users': (init) => { posted.push(init); return mockResponse({ json: {} }) } },
    )
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText(/No users found/)

    await ue.click(screen.getByRole('button', { name: /New user/ }))
    await ue.type(fieldByLabel(modalByHeading('New user'), 'Name'), 'Carol')
    await ue.click(screen.getByRole('button', { name: 'Create user' }))

    expect(await screen.findByText('Password is required for human users')).toBeInTheDocument()
    expect(posted).toHaveLength(0)
  })

  it('hides the password field for bot users', async () => {
    installFetch({ users: [] })
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText(/No users found/)
    await ue.click(screen.getByRole('button', { name: /New user/ }))
    const modal = modalByHeading('New user')
    await ue.selectOptions(screen.getByRole('combobox'), 'bot')
    expect(within(modal).queryByText('Password')).not.toBeInTheDocument()
  })

  it.each([
    ['without multi_user, once a human exists', new Set<string>(), [user()], true],
    ['without multi_user, before any human exists', new Set<string>(), [user({ user_type: 'bot' })], false],
    ['with multi_user', new Set(['multi_user']), [user()], false],
  ])('offers a human user %s', async (_, modules, users, locked) => {
    installFetch({ users })
    const { user: ue } = renderWithRouter(<UserManagement />, { modules })
    await screen.findByText(users[0].name)
    await ue.click(screen.getByRole('button', { name: /New user/ }))
    const type = screen.getByRole('combobox', { name: 'User type' }) as HTMLSelectElement
    expect(type.value).toBe(locked ? 'bot' : 'human')
    expect((within(type).getByRole('option', { name: 'Human' }) as HTMLOptionElement).disabled).toBe(locked)
    expect(screen.queryAllByText(/This server allows one human user/)).toHaveLength(locked ? 1 : 0)
    expect(screen.queryAllByRole('link', { name: 'Server settings' }).map(a => a.getAttribute('href'))).toEqual(locked ? ['/ui/server-settings'] : [])
  })

  it('surfaces a server error inside the create modal', async () => {
    installFetch(
      { users: [] },
      { 'POST /users': () => mockResponse({ status: 400, json: { detail: 'email taken' } }) },
    )
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText(/No users found/)
    await ue.click(screen.getByRole('button', { name: /New user/ }))
    const modal = modalByHeading('New user')
    await ue.type(fieldByLabel(modal, 'Name'), 'Carol')
    await ue.type(fieldByLabel(modal, 'Email'), 'c@x.com')
    await ue.type(fieldByLabel(modal, 'Password'), 'secretpw')
    await ue.click(screen.getByRole('button', { name: 'Create user' }))
    expect(await screen.findByText('email taken')).toBeInTheDocument()
  })

  it('shows a taken email on the email field', async () => {
    installFetch(
      { users: [] },
      { 'POST /users': () => mockResponse({ status: 400, json: { detail: [{ loc: ['body', 'email'], msg: 'User with this email already exists' }] } }) },
    )
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText(/No users found/)
    await ue.click(screen.getByRole('button', { name: /New user/ }))
    const modal = modalByHeading('New user')
    await ue.type(fieldByLabel(modal, 'Name'), 'Carol')
    await ue.type(fieldByLabel(modal, 'Email'), 'c@x.com')
    await ue.type(fieldByLabel(modal, 'Password'), 'secretpw')
    await ue.click(screen.getByRole('button', { name: 'Create user' }))
    await waitFor(() => expect(fieldByLabel(modal, 'Email')).toHaveAccessibleDescription('User with this email already exists'))
    expect(fieldByLabel(modal, 'Email')).toHaveFocus()
    expect(within(modal).queryByRole('alert')).not.toBeInTheDocument()
    await ue.type(fieldByLabel(modal, 'Email'), 'm')
    expect(fieldByLabel(modal, 'Email')).not.toHaveAccessibleDescription('User with this email already exists')
  })

  it('toggles scopes via checkboxes', async () => {
    const posted: any[] = []
    installFetch(
      { users: [] },
      { 'POST /users': (init) => { posted.push(JSON.parse(init!.body as string)); return mockResponse({ json: user() }) } },
    )
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText(/No users found/)
    await ue.click(screen.getByRole('button', { name: /New user/ }))
    const modal = modalByHeading('New user')
    await ue.type(fieldByLabel(modal, 'Name'), 'Carol')
    await ue.type(fieldByLabel(modal, 'Email'), 'c@x.com')
    await ue.type(fieldByLabel(modal, 'Password'), 'secretpw')
    // 'read' is preselected; add 'admin'
    await ue.click(screen.getByRole('checkbox', { name: /Admin/ }))
    await ue.click(screen.getByRole('button', { name: 'Create user' }))
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0].scopes).toEqual(expect.arrayContaining(['read', 'admin']))
  })
})

describe('UserManagement edit flow', () => {
  it('opens the edit modal prefilled and PATCHes changes', async () => {
    const patched: any[] = []
    installFetch(
      { users: [user({ id: 1, name: 'Admin' }), user({ id: 2, name: 'Bob', email: 'bob@x.com' })] },
      {
        'PATCH ': (init) => {
          patched.push(JSON.parse(init!.body as string))
          return mockResponse({ json: user({ id: 2, name: 'Bobby' }) })
        },
      },
    )
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText('Bob')

    const rows = screen.getAllByRole('button', { name: 'Edit' })
    await ue.click(rows[1])
    const nameInput = fieldByLabel(modalByHeading('Edit user'), 'Name')
    expect(nameInput).toHaveValue('Bob')
    await ue.clear(nameInput)
    await ue.type(nameInput, 'Bobby')
    await ue.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(patched).toHaveLength(1))
    expect(patched[0]).toMatchObject({ name: 'Bobby' })
  })
})

describe('UserManagement own account', () => {
  it("won't let an admin untick their own full admin scope", async () => {
    installFetch({
      users: [user({ id: 1, name: 'Admin', scopes: ['*'] })],
      scopes: [...scopes, { value: '*', label: 'All permissions', description: 'Everything', category: 'core' }],
    })
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText('Admin')

    await ue.click(screen.getByRole('button', { name: 'Edit' }))
    const modal = modalByHeading('Edit user')

    expect(within(modal).getByRole('group', { name: 'Scopes' })).toBeInTheDocument()
    expect(within(modal).getByRole('checkbox', { name: 'All permissions' })).toBeDisabled()
  })
})

describe('UserManagement reset password', () => {
  it('sets a new password for another human user', async () => {
    const posted: any[] = []
    installFetch(
      { users: [user({ id: 1, name: 'Admin' }), user({ id: 2, name: 'Bob' }), user({ id: 3, name: 'Botty', user_type: 'bot' })] },
      { 'POST /users/2/reset-password': (init) => { posted.push(JSON.parse(init!.body as string)); return mockResponse({ json: { status: 'password_reset' } }) } },
    )
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText('Bob')

    expect(screen.getAllByRole('button', { name: 'Reset password' })).toHaveLength(1)
    await ue.click(screen.getByRole('button', { name: 'Reset password' }))
    const modal = modalByHeading('Reset password for Bob')
    await ue.type(fieldByLabel(modal, 'New password'), 'newpass12')
    await ue.type(fieldByLabel(modal, 'Confirm new password'), 'newpass12{Enter}')

    expect(await screen.findByText('Password reset for Bob. They have been signed out everywhere.')).toBeInTheDocument()
    expect(posted).toEqual([{ new_password: 'newpass12' }])
  })

  it('refuses mismatched passwords without calling the server', async () => {
    const posted: any[] = []
    installFetch(
      { users: [user({ id: 2, name: 'Bob' })] },
      { 'POST /users/2/reset-password': (init) => { posted.push(init); return mockResponse({ json: {} }) } },
    )
    const { user: ue } = renderWithRouter(<UserManagement />)
    await ue.click(await screen.findByRole('button', { name: 'Reset password' }))
    const modal = modalByHeading('Reset password for Bob')
    await ue.type(fieldByLabel(modal, 'New password'), 'newpass12')
    await ue.type(fieldByLabel(modal, 'Confirm new password'), 'other1234{Enter}')

    expect(fieldByLabel(modal, 'Confirm new password')).toHaveAccessibleDescription('Passwords do not match')
    expect(posted).toHaveLength(0)
  })
})

describe('UserManagement delete flow', () => {
  it('deletes a user after confirmation', async () => {
    let deleteCalled = false
    installFetch(
      { users: [user({ id: 1, name: 'Admin' }), user({ id: 2, name: 'Bob' })] },
      {
        'DELETE ': () => {
          deleteCalled = true
          return mockResponse({ status: 200, json: {} })
        },
      },
    )
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText('Bob')
    await ue.click(screen.getByRole('button', { name: 'Delete' }))
    expect(deleteCalled).toBe(false)
    await ue.click(screen.getByRole('button', { name: 'Delete user' }))
    await waitFor(() => expect(deleteCalled).toBe(true))
  })
})

describe('UserManagement API key flow', () => {
  it('regenerates a key for a bot and reveals it once', async () => {
    mockFetch(async (input, init) => {
      const url = String(input)
      const method = init?.method || 'GET'
      if (url.includes('/auth/me')) return mockResponse({ json: authMe })
      if (url.endsWith('/users/scopes')) return mockResponse({ json: scopes })
      if (url.includes('/api-keys') && method === 'POST') return mockResponse({ json: { id: 7, revoked: false, key: 'mcp_secret123' } })
      if (url.includes('/api-keys')) return mockResponse({ json: [] })
      if (url.endsWith('/users')) return mockResponse({ json: [user({ id: 2, name: 'Botty', user_type: 'bot', api_key_count: 1 })] })
      return mockResponse({ json: {} })
    })
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText('Botty')
    await ue.click(screen.getByRole('button', { name: 'Regenerate key' }))
    expect(screen.getByRole('heading', { name: /Regenerate API key/ })).toBeInTheDocument()
    await ue.click(screen.getByRole('button', { name: 'Regenerate' }))
    expect(await screen.findByText('mcp_secret123')).toBeInTheDocument()

    await ue.keyboard('{Escape}')
    expect(screen.getByText('mcp_secret123')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Close' })).not.toBeInTheDocument()
    await ue.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByText('mcp_secret123')).not.toBeInTheDocument()
  })
})
