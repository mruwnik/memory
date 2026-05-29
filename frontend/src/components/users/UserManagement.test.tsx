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
    if (url.endsWith('/users/scopes')) return mockResponse({ json: scopes })

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
      if (url.endsWith('/users/scopes')) return mockResponse({ json: scopes })
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
    expect(screen.getByRole('button', { name: 'Generate Key' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Regenerate Key' })).toBeInTheDocument()
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

    await ue.click(screen.getByRole('button', { name: /Add User/ }))
    const modal = modalByHeading('Create New User')
    await ue.type(fieldByLabel(modal, 'Name'), 'Carol')
    await ue.type(fieldByLabel(modal, 'Email'), 'carol@x.com')
    await ue.type(fieldByLabel(modal, 'Password'), 'secretpw')
    await ue.click(screen.getByRole('button', { name: 'Create User' }))

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

    await ue.click(screen.getByRole('button', { name: /Add User/ }))
    await ue.type(fieldByLabel(modalByHeading('Create New User'), 'Name'), 'Carol')
    await ue.click(screen.getByRole('button', { name: 'Create User' }))

    expect(await screen.findByText('Password is required for human users')).toBeInTheDocument()
    expect(posted).toHaveLength(0)
  })

  it('hides the password field for bot users', async () => {
    installFetch({ users: [] })
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText(/No users found/)
    await ue.click(screen.getByRole('button', { name: /Add User/ }))
    const modal = modalByHeading('Create New User')
    await ue.selectOptions(screen.getByRole('combobox'), 'bot')
    expect(within(modal).queryByText('Password')).not.toBeInTheDocument()
  })

  it('surfaces a server error inside the create modal', async () => {
    installFetch(
      { users: [] },
      { 'POST /users': () => mockResponse({ status: 400, json: { detail: 'email taken' } }) },
    )
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText(/No users found/)
    await ue.click(screen.getByRole('button', { name: /Add User/ }))
    const modal = modalByHeading('Create New User')
    await ue.type(fieldByLabel(modal, 'Name'), 'Carol')
    await ue.type(fieldByLabel(modal, 'Email'), 'c@x.com')
    await ue.type(fieldByLabel(modal, 'Password'), 'secretpw')
    await ue.click(screen.getByRole('button', { name: 'Create User' }))
    expect(await screen.findByText('email taken')).toBeInTheDocument()
  })

  it('toggles scopes via checkboxes', async () => {
    const posted: any[] = []
    installFetch(
      { users: [] },
      { 'POST /users': (init) => { posted.push(JSON.parse(init!.body as string)); return mockResponse({ json: user() }) } },
    )
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText(/No users found/)
    await ue.click(screen.getByRole('button', { name: /Add User/ }))
    const modal = modalByHeading('Create New User')
    await ue.type(fieldByLabel(modal, 'Name'), 'Carol')
    await ue.type(fieldByLabel(modal, 'Email'), 'c@x.com')
    await ue.type(fieldByLabel(modal, 'Password'), 'secretpw')
    // 'read' is preselected; add 'admin'
    await ue.click(screen.getByRole('checkbox', { name: /Admin/ }))
    await ue.click(screen.getByRole('button', { name: 'Create User' }))
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
    const nameInput = fieldByLabel(modalByHeading('Edit User'), 'Name')
    expect(nameInput).toHaveValue('Bob')
    await ue.clear(nameInput)
    await ue.type(nameInput, 'Bobby')
    await ue.click(screen.getByRole('button', { name: 'Save Changes' }))

    await waitFor(() => expect(patched).toHaveLength(1))
    expect(patched[0]).toMatchObject({ name: 'Bobby' })
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
    expect(screen.getByRole('heading', { name: 'Delete User' })).toBeInTheDocument()
    await ue.click(screen.getByRole('button', { name: 'Delete User' }))
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
      if (url.includes('/api-keys') && method === 'POST') return mockResponse({ json: { key: 'mcp_secret123' } })
      if (url.endsWith('/users')) return mockResponse({ json: [user({ id: 2, name: 'Botty', user_type: 'bot', api_key_count: 1 })] })
      return mockResponse({ json: {} })
    })
    const { user: ue } = renderWithRouter(<UserManagement />)
    await screen.findByText('Botty')
    await ue.click(screen.getByRole('button', { name: 'Regenerate Key' }))
    expect(screen.getByRole('heading', { name: /Regenerate API Key/ })).toBeInTheDocument()
    await ue.click(screen.getByRole('button', { name: 'Regenerate' }))
    expect(await screen.findByText('mcp_secret123')).toBeInTheDocument()
  })
})
