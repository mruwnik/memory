import { describe, it, expect, beforeEach, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithUser, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { DiscordPanel } from './DiscordPanel'

const authMe = { json: { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes: ['*'] } }

const bot = {
  id: 'bot-1',
  name: 'KB Bot',
  is_active: true,
  created_at: '',
  updated_at: '2030-01-01T00:00:00Z',
  connected: true,
}

const server = {
  id: 'srv-1',
  name: 'My Server',
  description: null,
  member_count: 42,
  collect_messages: false,
  last_sync_at: null,
  channel_count: 3,
  project_id: null,
  sensitivity: 'basic',
}

const mcp = (payload: unknown) => ({
  json: { jsonrpc: '2.0', id: 1, result: { content: [{ text: JSON.stringify(payload) }] } },
})

const routes = (opts: { bots?: unknown[]; servers?: unknown[]; users?: unknown[]; channels?: unknown[] } = {}) => ({
  '/discord/servers': { json: opts.servers ?? [] },
  '/discord/bots': { json: opts.bots ?? [] },
  '/mcp/projects_list_all': mcp({ projects: [], count: 0 }),
  '/mcp/discord_list_channels': mcp({ channels: opts.channels ?? [] }),
  '/users': { json: opts.users ?? [] },
  '/auth/me': authMe,
  __default: { json: {} },
})

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('DiscordPanel load states', () => {
  it('shows the empty state when no bots are configured', async () => {
    mockFetchRoutes(routes({}))
    renderWithUser(<DiscordPanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByText(/No Discord bots configured/)).toBeInTheDocument(),
    )
  })

  it('renders a connected bot card', async () => {
    mockFetchRoutes(routes({ bots: [bot] }))
    renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    expect(screen.getByText('Connected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add to Server' })).toBeInTheDocument()
  })

  it('renders a disconnected bot when connected is false', async () => {
    mockFetchRoutes(routes({ bots: [{ ...bot, connected: false }] }))
    renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('Disconnected')).toBeInTheDocument())
  })

  it('shows an error state when the bots fetch fails', async () => {
    mockFetchRoutes({ ...routes({}), '/discord/bots': { status: 500, json: {} } })
    renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument())
  })
})

describe('DiscordPanel add bot', () => {
  it('validates that both fields are required', async () => {
    mockFetchRoutes(routes({}))
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText(/No Discord bots configured/)).toBeInTheDocument())
    await user.click(screen.getAllByRole('button', { name: 'Add Bot' })[0])
    const dialog = await screen.findByRole('dialog')
    // jsdom blocks empty required submit, so fill only one field then submit
    await user.type(within(dialog).getByPlaceholderText('e.g., My Bot'), 'X')
    await user.type(within(dialog).getByPlaceholderText(/Discord bot token/), ' ')
    await user.click(within(dialog).getByRole('button', { name: 'Add Bot' }))
    expect(await within(dialog).findByText('Please fill in all fields')).toBeInTheDocument()
  })

  it('submits a create with trimmed name and token', async () => {
    const mock = mockFetchRoutes(routes({}))
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText(/No Discord bots configured/)).toBeInTheDocument())
    await user.click(screen.getAllByRole('button', { name: 'Add Bot' })[0])
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByPlaceholderText('e.g., My Bot'), '  My Bot  ')
    await user.type(within(dialog).getByPlaceholderText(/Discord bot token/), 'tok123')
    await user.click(within(dialog).getByRole('button', { name: 'Add Bot' }))
    await waitFor(() => {
      const post = mock.mock.calls.find(
        ([url, init]) => String(url).endsWith('/discord/bots') && (init as RequestInit)?.method === 'POST',
      )
      expect(post).toBeTruthy()
      expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({ name: 'My Bot', token: 'tok123' })
    })
  })
})

describe('DiscordPanel bot actions', () => {
  it('toggles bot active state with a PATCH', async () => {
    const mock = mockFetchRoutes(routes({ bots: [bot] }))
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('switch'))
    await waitFor(() => {
      const patch = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/discord/bots/bot-1') && (init as RequestInit)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ is_active: false })
    })
  })

  it('confirms before deleting a bot and sends DELETE on confirm', async () => {
    const mock = mockFetchRoutes(routes({ bots: [bot] }))
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    expect(await screen.findByText(/Are you sure you want to remove "KB Bot"/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => {
      const del = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/discord/bots/bot-1') && (init as RequestInit)?.method === 'DELETE',
      )
      expect(del).toBeTruthy()
    })
  })

  it('cancels the delete confirmation without a DELETE', async () => {
    const mock = mockFetchRoutes(routes({ bots: [bot] }))
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    const del = mock.mock.calls.find(
      ([url, init]) => String(url).includes('/discord/bots/bot-1') && (init as RequestInit)?.method === 'DELETE',
    )
    expect(del).toBeFalsy()
  })

  it('opens the bot invite url in a new tab', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    mockFetchRoutes({
      '/discord/bots/bot-1/invite': { json: { invite_url: 'https://discord.com/invite/xyz' } },
      ...routes({ bots: [bot] }),
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add to Server' }))
    await waitFor(() => expect(openSpy).toHaveBeenCalledWith('https://discord.com/invite/xyz', '_blank'))
  })
})

describe('DiscordPanel servers', () => {
  it('expands the servers section and renders the server card', async () => {
    mockFetchRoutes(routes({ bots: [bot], servers: [server] }))
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Servers \(1\)/ }))
    expect(await screen.findByText('My Server')).toBeInTheDocument()
    expect(screen.getByText('42 members')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Not Collecting' })).toBeInTheDocument()
  })

  it('toggles server message collection with a PATCH', async () => {
    const mock = mockFetchRoutes(routes({ bots: [bot], servers: [server] }))
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Servers \(1\)/ }))
    await screen.findByText('My Server')
    await user.click(screen.getByRole('button', { name: 'Not Collecting' }))
    await waitFor(() => {
      const patch = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/discord/servers/srv-1') && (init as RequestInit)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ collect_messages: true })
    })
  })
})

const mcpChannel = (o: Record<string, unknown> = {}) => ({
  id: 'chan-1',
  server_id: 'srv-1',
  name: 'general',
  type: 'text',
  collect_messages: null,
  ...o,
})

describe('DiscordPanel server controls', () => {
  it('changes a server project assignment with a PATCH', async () => {
    const mock = mockFetchRoutes({
      ...routes({ bots: [bot], servers: [server] }),
      '/mcp/projects_list_all': mcp({ projects: [{ id: 7, title: 'Proj A' }], count: 1 }),
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Servers \(1\)/ }))
    await screen.findByText('My Server')
    await user.selectOptions(screen.getByTitle('Project'), '7')
    await waitFor(() => {
      const patch = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/discord/servers/srv-1') && (init as RequestInit)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ project_id: 7 })
    })
  })

  it('changes a server sensitivity with a PATCH', async () => {
    const mock = mockFetchRoutes(routes({ bots: [bot], servers: [server] }))
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Servers \(1\)/ }))
    await screen.findByText('My Server')
    await user.selectOptions(screen.getByTitle('Sensitivity'), 'internal')
    await waitFor(() => {
      const patch = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/discord/servers/srv-1') && (init as RequestInit)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ sensitivity: 'internal' })
    })
  })

  it('shows the empty-servers hint when expanded with no servers', async () => {
    mockFetchRoutes(routes({ bots: [bot], servers: [] }))
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Servers \(0\)/ }))
    expect(await screen.findByText(/No servers found/)).toBeInTheDocument()
  })

  it('refreshes bot metadata with a POST and re-fetches servers', async () => {
    const mock = mockFetchRoutes({
      '/discord/bots/bot-1/refresh': { json: { success: true } },
      ...routes({ bots: [bot], servers: [server] }),
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Refresh Metadata' }))
    await waitFor(() => {
      const post = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/discord/bots/bot-1/refresh') && (init as RequestInit)?.method === 'POST',
      )
      expect(post).toBeTruthy()
    })
  })
})

describe('DiscordPanel channels', () => {
  it('expands a server and renders its channels', async () => {
    mockFetchRoutes({
      ...routes({ bots: [bot], servers: [{ ...server, collect_messages: true }] }),
      '/mcp/discord_list_channels': mcp({ channels: [mcpChannel({ name: 'general', collect_messages: null })] }),
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Servers \(1\)/ }))
    await screen.findByText('My Server')
    await user.click(screen.getByRole('button', { name: /Channels/ }))
    expect(await screen.findByText('general')).toBeInTheDocument()
    // collect_messages null => inheriting; effective_collect mirrors the MCP
    // resolved value (also null here) so it renders "Inherit (no)".
    expect(screen.getByText('Inherit (no)')).toBeInTheDocument()
  })

  it('renders explicit collecting and skipping channel states', async () => {
    mockFetchRoutes({
      ...routes({ bots: [bot], servers: [server] }),
      '/mcp/discord_list_channels': mcp({
        channels: [
          mcpChannel({ id: 'c-on', name: 'on-chan', collect_messages: true, type: 'voice' }),
          mcpChannel({ id: 'c-off', name: 'off-chan', collect_messages: false }),
        ],
      }),
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Servers \(1\)/ }))
    await screen.findByText('My Server')
    await user.click(screen.getByRole('button', { name: /Channels/ }))
    await screen.findByText('on-chan')
    expect(screen.getByText('Collecting')).toBeInTheDocument()
    expect(screen.getByText('Skipping')).toBeInTheDocument()
  })

  it('cycles a channel collect state (inherit -> on) with a PATCH', async () => {
    const mock = mockFetchRoutes({
      ...routes({ bots: [bot], servers: [server] }),
      '/mcp/discord_list_channels': mcp({ channels: [mcpChannel({ collect_messages: null })] }),
      '/discord/channels/chan-1': { json: { ...mcpChannel(), collect_messages: true, effective_collect: true } },
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Servers \(1\)/ }))
    await screen.findByText('My Server')
    await user.click(screen.getByRole('button', { name: /Channels/ }))
    const chanBtn = await screen.findByText(/Inherit/)
    await user.click(chanBtn)
    await waitFor(() => {
      const patch = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/discord/channels/chan-1') && (init as RequestInit)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ collect_messages: true })
    })
  })

  it('changes a channel sensitivity with a PATCH', async () => {
    const mock = mockFetchRoutes({
      ...routes({ bots: [bot], servers: [server] }),
      '/mcp/discord_list_channels': mcp({ channels: [mcpChannel({ collect_messages: null })] }),
      '/discord/channels/chan-1': { json: { ...mcpChannel(), sensitivity: 'confidential' } },
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Servers \(1\)/ }))
    await screen.findByText('My Server')
    await user.click(screen.getByRole('button', { name: /Channels/ }))
    await screen.findByText('general')
    const sensSelects = screen.getAllByTitle('Sensitivity')
    // The last sensitivity select belongs to the channel row.
    await user.selectOptions(sensSelects[sensSelects.length - 1], 'confidential')
    await waitFor(() => {
      const patch = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/discord/channels/chan-1') && (init as RequestInit)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ sensitivity: 'confidential' })
    })
  })

  it('shows the loading placeholder before channels arrive', async () => {
    mockFetchRoutes({
      ...routes({ bots: [bot], servers: [server] }),
      '/mcp/discord_list_channels': mcp({ channels: [] }),
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Servers \(1\)/ }))
    await screen.findByText('My Server')
    await user.click(screen.getByRole('button', { name: /Channels/ }))
    expect(await screen.findByText('Loading...')).toBeInTheDocument()
  })
})

describe('DiscordPanel manage users', () => {
  it('lists authorized and available users and adds an available one', async () => {
    const mock = mockFetchRoutes({
      // bot-users route precedes /discord/bots; /users override comes last to win
      '/discord/bots/bot-1/users': { json: [{ id: 1, name: 'Owner' }] },
      ...routes({ bots: [bot] }),
      '/users': { json: [{ id: 1, name: 'Owner', email: 'o@e.com' }, { id: 2, name: 'Guest', email: 'g@e.com' }] },
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Manage Users' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Owner')).toBeInTheDocument()
    expect(within(dialog).getByText('Guest')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Add' }))
    await waitFor(() => {
      const post = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/discord/bots/bot-1/users') && (init as RequestInit)?.method === 'POST',
      )
      expect(post).toBeTruthy()
    })
  })

  it('removes an authorized user with a DELETE when more than one exists', async () => {
    const mock = mockFetchRoutes({
      '/discord/bots/bot-1/users/2': { json: { status: 'removed', user_id: 2 } },
      '/discord/bots/bot-1/users': { json: [{ id: 1, name: 'Owner' }, { id: 2, name: 'Second' }] },
      ...routes({ bots: [bot] }),
      '/users': { json: [{ id: 1, name: 'Owner', email: 'o@e.com' }, { id: 2, name: 'Second', email: 's@e.com' }] },
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Manage Users' }))
    const dialog = await screen.findByRole('dialog')
    const removeBtns = within(dialog).getAllByRole('button', { name: 'Remove' })
    await user.click(removeBtns[removeBtns.length - 1])
    await waitFor(() => {
      const del = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/discord/bots/bot-1/users/2') && (init as RequestInit)?.method === 'DELETE',
      )
      expect(del).toBeTruthy()
    })
  })

  it('disables removal of the last authorized user', async () => {
    mockFetchRoutes({
      '/discord/bots/bot-1/users': { json: [{ id: 1, name: 'Owner' }] },
      ...routes({ bots: [bot] }),
      '/users': { json: [{ id: 1, name: 'Owner', email: 'o@e.com' }] },
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Manage Users' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Remove' })).toBeDisabled()
  })
})
