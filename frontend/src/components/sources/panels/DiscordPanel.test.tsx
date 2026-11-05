import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithUser, mcpToolFromRequest, mockFetch, mockFetchRoutes, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'
import { DiscordPanel } from './DiscordPanel'
import type { DiscordBot } from '@/hooks/useDiscord'

const authMe = { json: { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes: ['*'] } }

const bot: DiscordBot = {
  id: 'bot-1',
  application_id: '1111',
  name: 'KB Bot',
  is_active: true,
  has_token: true,
  realtime: 'gateway',
  public_key: null,
  api_base_url: 'https://discord.com/api/v10',
  events_url: 'https://memory.example.com/discord/events/bot-1',
  last_event_at: null,
  last_sync_at: null,
  created_at: '',
  updated_at: '2030-01-01T00:00:00Z',
  connected: true,
}

const webhookBot: DiscordBot = {
  ...bot,
  id: 'bot-2',
  name: 'Hooked',
  realtime: 'webhook',
  public_key: 'ab'.repeat(32),
  events_url: 'https://memory.example.com/discord/events/bot-2',
  last_event_at: new Date(Date.now() - 5 * 60_000).toISOString(),
  connected: false,
}

const server = {
  llm_key: { choice: 'default', key_id: null, label: 'personal', provider: 'anthropic', paid_by_you: true, runs: true, can_choose_default: true },
  id: 'srv-1',
  name: 'My Server',
  description: null,
  member_count: 42,
  collect_messages: false,
  last_sync_at: null,
  channel_count: 3,
  bot_ids: ['bot-1'],
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

afterEach(() => {
  vi.restoreAllMocks()
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
    expect(screen.getByText('Gateway')).toBeInTheDocument()
    expect(screen.getByText('Connected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add to server' })).toBeInTheDocument()
  })

  it('renders a disconnected bot when connected is false', async () => {
    mockFetchRoutes(routes({ bots: [{ ...bot, connected: false }] }))
    renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('Disconnected')).toBeInTheDocument())
  })

  it('does not flag a paused bot as disconnected', async () => {
    mockFetchRoutes(routes({ bots: [{ ...bot, is_active: false, connected: false }] }))
    renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('Inactive')).toBeInTheDocument())
    expect(screen.queryByText('Disconnected')).not.toBeInTheDocument()
  })

  it('lists under each bot only the servers it is in', async () => {
    const servers = [
      { ...server, id: 'srv-1', name: 'Mine only', bot_ids: ['bot-1'] },
      { ...server, id: 'srv-2', name: 'Shared', bot_ids: ['bot-1', 'bot-2'] },
    ]
    mockFetchRoutes(routes({ bots: [bot, webhookBot], servers }))
    const { user } = renderWithUser(<DiscordPanel />)
    const hooked = await screen.findByRole('button', { name: 'Servers (1)' })
    expect(screen.getByRole('button', { name: 'Servers (2)' })).toBeInTheDocument()

    await user.click(hooked)

    expect(await screen.findByText('Shared')).toBeInTheDocument()
    expect(screen.queryByText('Mine only')).not.toBeInTheDocument()
  })

  it('shows an error state when the bots fetch fails', async () => {
    mockFetchRoutes({ ...routes({}), '/discord/bots': { status: 500, json: {} } })
    renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument())
  })
})

// The bots route answers a POST with the created bot and a GET with the list.
const withCreate = (created: unknown) => {
  const list = routes({})
  return mockFetch(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/discord/bots') && init?.method === 'POST') return mockResponse({ json: created })
    const tool = mcpToolFromRequest(input, init)
    const target = tool === null ? url : `${url}/${tool}`
    const match = Object.entries(list).find(([pattern]) => pattern !== '__default' && target.includes(pattern))
    return mockResponse(match ? match[1] : list.__default)
  })
}

const postedBot = (mock: ReturnType<typeof mockFetch>) => {
  const post = mock.mock.calls.find(
    ([url, init]) => String(url).endsWith('/discord/bots') && (init as RequestInit)?.method === 'POST',
  )
  return post && JSON.parse((post[1] as RequestInit).body as string)
}

const openAddBot = async () => {
  const { user } = renderWithUser(<DiscordPanel />)
  await waitFor(() => expect(screen.getByText(/No Discord bots configured/)).toBeInTheDocument())
  await user.click(screen.getAllByRole('button', { name: 'Add bot' })[0])
  return { user, dialog: await screen.findByRole('dialog') }
}

describe('DiscordPanel add bot', () => {
  it('defaults to the WebSocket gateway and needs a name and token', async () => {
    const mock = withCreate(bot)
    const { user, dialog } = await openAddBot()

    expect(within(dialog).getByRole('button', { name: 'WebSocket gateway' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(dialog).queryByLabelText('Public key')).not.toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('Bot name'), '  My Bot  ')
    await user.type(within(dialog).getByLabelText('Bot token'), 'tok123')
    await user.click(within(dialog).getByRole('button', { name: 'Add bot' }))

    await waitFor(() => expect(postedBot(mock)).toEqual({ name: 'My Bot', token: 'tok123', realtime: 'gateway' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it.each([
    ['a token', 'X', ' '],
    ['a name', ' ', 'tok'],
  ])('refuses to add a bot without %s', async (_, name, token) => {
    mockFetchRoutes(routes({}))
    const { user, dialog } = await openAddBot()
    await user.type(within(dialog).getByLabelText('Bot name'), name)
    await user.type(within(dialog).getByLabelText('Bot token'), token)
    await user.click(within(dialog).getByRole('button', { name: 'Add bot' }))
    expect(await within(dialog).findByText('Please fill in all fields')).toBeInTheDocument()
  })

  it('asks a webhook bot for its public key, then shows the events URL to paste', async () => {
    const mock = withCreate(webhookBot)
    const { user, dialog } = await openAddBot()

    await user.click(within(dialog).getByRole('button', { name: 'Webhook' }))
    await user.type(within(dialog).getByLabelText('Bot name'), 'Hooked')
    await user.type(within(dialog).getByLabelText('Bot token'), 'tok')
    await user.type(within(dialog).getByLabelText('Public key'), ' ')
    await user.click(within(dialog).getByRole('button', { name: 'Add bot' }))
    expect(await within(dialog).findByText('Please fill in all fields')).toBeInTheDocument()

    await user.type(within(dialog).getByLabelText('Public key'), 'ab'.repeat(32))
    await user.click(within(dialog).getByRole('button', { name: 'Add bot' }))

    await waitFor(() => expect(postedBot(mock)).toEqual({
      name: 'Hooked', token: 'tok', realtime: 'webhook', public_key: 'ab'.repeat(32),
    }))
    expect(await within(dialog).findByDisplayValue(webhookBot.events_url)).toBeInTheDocument()
  })

  it('sends the application ID and API base URL when given', async () => {
    const mock = withCreate(bot)
    const { user, dialog } = await openAddBot()

    await user.type(within(dialog).getByLabelText('Bot name'), 'Proxied')
    await user.type(within(dialog).getByLabelText(/Application ID/), '1111')
    await user.type(within(dialog).getByLabelText('Bot token'), 'tok')
    await user.click(within(dialog).getByText('Advanced'))
    await user.type(within(dialog).getByLabelText('API base URL'), 'https://chat.example.com/discord/api/v10')
    await user.click(within(dialog).getByRole('button', { name: 'Add bot' }))

    await waitFor(() => expect(postedBot(mock)).toEqual({
      name: 'Proxied', application_id: '1111', token: 'tok', realtime: 'gateway',
      api_base_url: 'https://chat.example.com/discord/api/v10',
    }))
  })
})

const patchedBot = (mock: ReturnType<typeof mockFetch>, botId: string) => {
  const patch = mock.mock.calls.find(
    ([url, init]) => String(url).endsWith(`/discord/bots/${botId}`) && (init as RequestInit)?.method === 'PATCH',
  )
  return patch && JSON.parse((patch[1] as RequestInit).body as string)
}

const openEdit = async (shown: DiscordBot) => {
  const mock = mockFetchRoutes(routes({ bots: [shown] }))
  const { user } = renderWithUser(<DiscordPanel />)
  await waitFor(() => expect(screen.getByText(shown.name)).toBeInTheDocument())
  await user.click(screen.getByRole('button', { name: 'Edit' }))
  return { mock, user, dialog: await screen.findByRole('dialog', { name: 'Edit Discord bot' }) }
}

describe('DiscordPanel edit bot', () => {
  it('switches a gateway bot to webhook delivery, showing where to send events', async () => {
    const { mock, user, dialog } = await openEdit(bot)

    await user.click(within(dialog).getByRole('button', { name: 'Webhook' }))
    expect(within(dialog).getByDisplayValue(bot.events_url)).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('Public key'), 'cd'.repeat(32))
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(patchedBot(mock, bot.id)).toEqual({ realtime: 'webhook', public_key: 'cd'.repeat(32) }))
  })

  it('switches a webhook bot back to the gateway, keeping its key', async () => {
    const { mock, user, dialog } = await openEdit(webhookBot)

    await user.click(within(dialog).getByRole('button', { name: 'WebSocket gateway' }))
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(patchedBot(mock, webhookBot.id)).toEqual({ realtime: 'gateway' }))
  })

  it('replaces the token and API base URL only when changed', async () => {
    const { mock, user, dialog } = await openEdit(bot)

    await user.clear(within(dialog).getByLabelText('Bot name'))
    await user.type(within(dialog).getByLabelText('Bot name'), 'Renamed')
    await user.type(within(dialog).getByLabelText(/Bot token/), 'new-token')
    await user.click(within(dialog).getByText('Advanced'))
    await user.clear(within(dialog).getByLabelText('API base URL'))
    await user.type(within(dialog).getByLabelText('API base URL'), 'https://chat.example.com/discord/api/v10')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(patchedBot(mock, bot.id)).toEqual({
      name: 'Renamed', token: 'new-token', api_base_url: 'https://chat.example.com/discord/api/v10',
    }))
  })
})

describe('DiscordPanel tokenless bot', () => {
  it('says the bot needs a token before it can resume', async () => {
    mockFetchRoutes(routes({ bots: [{ ...bot, has_token: false, is_active: false }] }))
    renderWithUser(<DiscordPanel />)
    expect(await screen.findByText(/Add the bot's token in Edit to resume it/)).toBeInTheDocument()
  })

  it('sends the token from Edit, which resumes the bot', async () => {
    const { mock, user, dialog } = await openEdit({ ...bot, has_token: false, is_active: false })

    await user.type(within(dialog).getByLabelText('Bot token'), 'tok')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(patchedBot(mock, bot.id)).toEqual({ token: 'tok' }))
  })
})

describe('DiscordPanel webhook bot row', () => {
  it('shows delivery, the last event and the events URL instead of the gateway state', async () => {
    mockFetchRoutes(routes({ bots: [webhookBot] }))
    renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('Hooked')).toBeInTheDocument())
    expect(screen.getByText('Webhook')).toBeInTheDocument()
    expect(screen.queryByText('Disconnected')).not.toBeInTheDocument()
    expect(screen.getByText(/Last event: 5m ago/)).toBeInTheDocument()
    expect(screen.getByDisplayValue(webhookBot.events_url)).toBeInTheDocument()
  })
})

describe('DiscordPanel last sync', () => {
  it.each([
    [new Date(Date.now() - 10 * 60_000).toISOString(), 'Last sync: 10m ago'],
    [null, 'Last sync: Never'],
  ])('shows when the bot last listed its servers, not when it was edited', async (last_sync_at, text) => {
    mockFetchRoutes(routes({ bots: [{ ...bot, last_sync_at, updated_at: new Date().toISOString() }] }))
    renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    expect(screen.getByText(new RegExp(text))).toBeInTheDocument()
  })
})

describe('DiscordPanel bot actions', () => {
  it('toggles bot active state with a PATCH', async () => {
    const mock = mockFetchRoutes(routes({ bots: [bot] }))
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Pause' }))
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
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/Remove "KB Bot"\?/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))
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

  it('opens the tab during the click and then points it at the invite url, without an opener', async () => {
    const popup = { location: { href: '' }, close: vi.fn(), opener: window as Window | null }
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => popup as unknown as Window)
    mockFetchRoutes({
      '/discord/bots/bot-1/invite': { json: { invite_url: 'https://discord.com/invite/xyz' } },
      ...routes({ bots: [bot] }),
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add to server' }))
    expect(openSpy).toHaveBeenCalledWith('', '_blank', expect.any(String))
    await waitFor(() => expect(popup.location.href).toBe('https://discord.com/invite/xyz'))
    expect(popup.opener).toBeNull()
  })

  it('offers the invite as a link when the popup is blocked', async () => {
    vi.spyOn(window, 'open').mockImplementation(() => null)
    mockFetchRoutes({
      '/discord/bots/bot-1/invite': { json: { invite_url: 'https://discord.com/invite/xyz' } },
      ...routes({ bots: [bot] }),
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add to server' }))
    expect(await screen.findByRole('link', { name: 'Open the Discord invite (opens in a new tab)' })).toHaveAttribute('href', 'https://discord.com/invite/xyz')
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
    expect(screen.getByText('Paused')).toBeInTheDocument()
  })

  it('toggles server message collection with a PATCH', async () => {
    const mock = mockFetchRoutes(routes({ bots: [bot], servers: [server] }))
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Servers \(1\)/ }))
    await screen.findByText('My Server')
    await user.click(screen.getByRole('button', { name: 'Resume' }))
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
    await user.click(screen.getByRole('button', { name: 'Refresh metadata' }))
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
    expect(screen.getByRole('combobox', { name: 'Collect messages' })).toHaveDisplayValue('Inherit (collect)')
  })

  it('renders explicit collect and skip channel states', async () => {
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
    const selects = screen.getAllByRole('combobox', { name: 'Collect messages' })
    expect(selects.map(s => (s as HTMLSelectElement).value)).toEqual(['collect', 'skip'])
  })

  it('sets a channel to skip in one PATCH', async () => {
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
    await screen.findByText('general')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Collect messages' }), 'skip')
    await waitFor(() => {
      const patch = mock.mock.calls.find(
        ([url, init]) => String(url).includes('/discord/channels/chan-1') && (init as RequestInit)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ collect_messages: false })
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

  it('says so when a server has no channels', async () => {
    mockFetchRoutes({
      ...routes({ bots: [bot], servers: [server] }),
      '/mcp/discord_list_channels': mcp({ channels: [] }),
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Servers \(1\)/ }))
    await screen.findByText('My Server')
    await user.click(screen.getByRole('button', { name: /Channels/ }))
    expect(await screen.findByText('No channels')).toBeInTheDocument()
  })

  it('shows a channel load failure with a retry', async () => {
    mockFetchRoutes({
      ...routes({ bots: [bot], servers: [server] }),
      '/mcp/discord_list_channels': { status: 500, json: {} },
    })
    const { user } = renderWithUser(<DiscordPanel />)
    await waitFor(() => expect(screen.getByText('KB Bot')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Servers \(1\)/ }))
    await screen.findByText('My Server')
    await user.click(screen.getByRole('button', { name: /Channels/ }))
    expect(await screen.findByText(/Couldn't load channels/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

it('does not offer bot user sharing', async () => {
  mockFetchRoutes(routes({ bots: [bot] }))
  renderWithUser(<DiscordPanel />)
  await screen.findByText('KB Bot')
  expect(screen.queryByRole('button', { name: 'Manage users' })).not.toBeInTheDocument()
})
