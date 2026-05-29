import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useDiscord } from './useDiscord'
import { setAuthCookies, clearCookies, mockFetch, mockResponse } from '@/test/utils'
import { mcpEnvelopeJson } from './mcpEnvelope.testhelper'

// Build a full Response wrapping the JSON-RPC tools/call envelope that
// useMCP.mcpCall expects (single payload → one content item).
function mcpResponse(payload: unknown) {
  return mockResponse({
    status: 200,
    headers: { 'content-type': 'application/json' },
    text: JSON.stringify(mcpEnvelopeJson(payload)),
  })
}

// apiCall reads the access_token cookie; without it, it throws before fetch.
beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

function setup() {
  return renderHook(() => useDiscord()).result.current
}

// useAuth/useMCP fire a /auth/me call on mount, so call[0] is not the call
// under test. Locate the fetch call whose URL contains `substr`.
function callTo(fetchMock: ReturnType<typeof mockFetch>, substr: string) {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes(substr))
  return { url: String(call?.[0]), init: call?.[1] }
}

// Default mock that answers everything (incl. the /auth/me mount call) with json.
function mockJson(json: unknown, status = 200) {
  return mockFetch(async () => mockResponse({ status, json }))
}

describe('useDiscord bots', () => {
  it('listBots without userId hits /discord/bots and returns json', async () => {
    const fetchMock = mockJson([{ id: '1', name: 'Bot' }])
    const bots = await setup().listBots()
    expect(bots).toEqual([{ id: '1', name: 'Bot' }])
    const { url } = callTo(fetchMock, '/discord/bots')
    expect(url).not.toContain('user_id')
  })

  it('listBots with userId appends the user_id query param', async () => {
    const fetchMock = mockJson([])
    await setup().listBots(42)
    expect(callTo(fetchMock, '/discord/bots').url).toContain('user_id=42')
  })

  it('listBots throws a descriptive error on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().listBots()).rejects.toThrow('Failed to fetch Discord bots')
  })

  it('createBot POSTs the payload and returns the created bot', async () => {
    const fetchMock = mockJson({ id: '9', name: 'New' })
    const result = await setup().createBot({ name: 'New', token: 'tok' })
    expect(result).toEqual({ id: '9', name: 'New' })
    const { init } = callTo(fetchMock, '/discord/bots')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ name: 'New', token: 'tok' })
  })

  it('createBot surfaces the server detail message on failure', async () => {
    mockJson({ detail: 'bad token' }, 400)
    await expect(setup().createBot({ name: 'x', token: 'y' })).rejects.toThrow('bad token')
  })

  it('createBot falls back to default message when detail is absent', async () => {
    mockJson({}, 400)
    await expect(setup().createBot({ name: 'x', token: 'y' })).rejects.toThrow(
      'Failed to create Discord bot',
    )
  })

  it('updateBot PATCHes /discord/bots/:id', async () => {
    const fetchMock = mockJson({ id: '3' })
    await setup().updateBot('3', { is_active: false })
    const { init } = callTo(fetchMock, '/discord/bots/3')
    expect(init?.method).toBe('PATCH')
    expect(JSON.parse(init?.body as string)).toEqual({ is_active: false })
  })

  it('updateBot throws server detail on failure', async () => {
    mockJson({ detail: 'nope' }, 422)
    await expect(setup().updateBot('3', {})).rejects.toThrow('nope')
  })

  it('deleteBot issues a DELETE and resolves void on success', async () => {
    const fetchMock = mockJson({}, 204)
    await expect(setup().deleteBot('7')).resolves.toBeUndefined()
    const { init } = callTo(fetchMock, '/discord/bots/7')
    expect(init?.method).toBe('DELETE')
  })

  it('deleteBot throws server detail on failure', async () => {
    mockJson({ detail: 'cannot delete' }, 500)
    await expect(setup().deleteBot('7')).rejects.toThrow('cannot delete')
  })

  it('getBotHealth returns the health payload', async () => {
    const fetchMock = mockJson({ bot_id: '1', connected: true })
    const health = await setup().getBotHealth('1')
    expect(health).toEqual({ bot_id: '1', connected: true })
    expect(callTo(fetchMock, '/discord/bots/1/health').url).toContain('/health')
  })

  it('getBotHealth throws on non-ok', async () => {
    mockJson({}, 503)
    await expect(setup().getBotHealth('1')).rejects.toThrow('Failed to get bot health')
  })

  it('refreshBotMetadata POSTs to /refresh and returns success', async () => {
    const fetchMock = mockJson({ success: true })
    const r = await setup().refreshBotMetadata('5')
    expect(r).toEqual({ success: true })
    const { init } = callTo(fetchMock, '/discord/bots/5/refresh')
    expect(init?.method).toBe('POST')
  })

  it('refreshBotMetadata throws server detail on failure', async () => {
    mockJson({ detail: 'refresh boom' }, 500)
    await expect(setup().refreshBotMetadata('5')).rejects.toThrow('refresh boom')
  })

  it('getBotInviteUrl returns the invite url', async () => {
    const fetchMock = mockJson({ invite_url: 'https://discord/invite' })
    const r = await setup().getBotInviteUrl('5')
    expect(r.invite_url).toBe('https://discord/invite')
    expect(callTo(fetchMock, '/discord/bots/5/invite').url).toContain('/invite')
  })

  it('getBotInviteUrl throws on non-ok', async () => {
    mockJson({}, 404)
    await expect(setup().getBotInviteUrl('5')).rejects.toThrow('Failed to get bot invite URL')
  })
})

describe('useDiscord bot users', () => {
  it('listBotUsers fetches /users for the bot', async () => {
    const fetchMock = mockJson([{ id: 1, name: 'Alice' }])
    const users = await setup().listBotUsers('2')
    expect(users).toEqual([{ id: 1, name: 'Alice' }])
    expect(callTo(fetchMock, '/discord/bots/2/users').url).toContain('/users')
  })

  it('listBotUsers throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().listBotUsers('2')).rejects.toThrow('Failed to fetch bot users')
  })

  it('addBotUser POSTs the user_id body', async () => {
    const fetchMock = mockJson({ status: 'added', user_id: 11 })
    const r = await setup().addBotUser('2', 11)
    expect(r).toEqual({ status: 'added', user_id: 11 })
    const { init } = callTo(fetchMock, '/discord/bots/2/users')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ user_id: 11 })
  })

  it('addBotUser throws server detail on failure', async () => {
    mockJson({ detail: 'already added' }, 409)
    await expect(setup().addBotUser('2', 11)).rejects.toThrow('already added')
  })

  it('removeBotUser DELETEs the nested user route', async () => {
    const fetchMock = mockJson({ status: 'removed', user_id: 11 })
    const r = await setup().removeBotUser('2', 11)
    expect(r).toEqual({ status: 'removed', user_id: 11 })
    const { init } = callTo(fetchMock, '/discord/bots/2/users/11')
    expect(init?.method).toBe('DELETE')
  })

  it('removeBotUser throws server detail on failure', async () => {
    mockJson({ detail: 'no such user' }, 404)
    await expect(setup().removeBotUser('2', 11)).rejects.toThrow('no such user')
  })
})

describe('useDiscord servers', () => {
  it('listServers without botId hits the bare endpoint', async () => {
    const fetchMock = mockJson([])
    await setup().listServers()
    expect(callTo(fetchMock, '/discord/servers').url).not.toContain('bot_id')
  })

  it('listServers with botId appends bot_id query', async () => {
    const fetchMock = mockJson([])
    await setup().listServers('99')
    expect(callTo(fetchMock, '/discord/servers').url).toContain('bot_id=99')
  })

  it('listServers throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().listServers()).rejects.toThrow('Failed to fetch Discord servers')
  })

  it('updateServer PATCHes server settings', async () => {
    const fetchMock = mockJson({ id: 's1', collect_messages: true })
    await setup().updateServer('s1', { collect_messages: true })
    const { init } = callTo(fetchMock, '/discord/servers/s1')
    expect(init?.method).toBe('PATCH')
    expect(JSON.parse(init?.body as string)).toEqual({ collect_messages: true })
  })

  it('updateServer throws server detail on failure', async () => {
    mockJson({ detail: 'forbidden' }, 403)
    await expect(setup().updateServer('s1', {})).rejects.toThrow('forbidden')
  })
})

describe('useDiscord channels (MCP-backed list)', () => {
  it('listChannels transforms the MCP response into DiscordChannel objects', async () => {
    mockFetch(async () =>
      mcpResponse({
        channels: [
          { id: 12345, name: 'general', type: 'text', server_id: 999, collect_messages: true },
        ],
      }),
    )
    const channels = await setup().listChannels('999')
    expect(channels).toEqual([
      {
        id: '12345',
        server_id: '999',
        server_name: null,
        name: 'general',
        channel_type: 'text',
        collect_messages: true,
        effective_collect: true,
      },
    ])
  })

  it('listChannels coerces a null server_id to null (not the string "null")', async () => {
    mockFetch(async () =>
      mcpResponse({
        channels: [{ id: 'c1', name: 'dm', type: 'dm', server_id: null, collect_messages: false }],
      }),
    )
    const [channel] = await setup().listChannels()
    expect(channel.server_id).toBeNull()
    expect(channel.id).toBe('c1')
  })

  it('listChannels passes server_id and server_name as MCP arguments', async () => {
    const fetchMock = mockFetch(async () => mcpResponse({ channels: [] }))
    await setup().listChannels('999', 'My Server')
    const { init } = callTo(fetchMock, '/mcp/discord_list_channels')
    const body = JSON.parse(init?.body as string)
    expect(body.params.arguments).toEqual({ server_id: '999', server_name: 'My Server' })
  })

  it('listChannels returns [] when the MCP payload lacks a channels key', async () => {
    mockFetch(async () => mcpResponse({ unexpected: true }))
    const channels = await setup().listChannels()
    expect(channels).toEqual([])
  })

  it('listChannels treats a missing channels array as empty', async () => {
    mockFetch(async () => mcpResponse({ channels: null }))
    const channels = await setup().listChannels()
    expect(channels).toEqual([])
  })

  it('updateChannel PATCHes the channel route', async () => {
    const fetchMock = mockJson({ id: 'c5', collect_messages: false })
    await setup().updateChannel('c5', { collect_messages: false })
    const { init } = callTo(fetchMock, '/discord/channels/c5')
    expect(init?.method).toBe('PATCH')
    expect(JSON.parse(init?.body as string)).toEqual({ collect_messages: false })
  })

  it('updateChannel throws server detail on failure', async () => {
    mockJson({ detail: 'channel boom' }, 400)
    await expect(setup().updateChannel('c5', {})).rejects.toThrow('channel boom')
  })
})

describe('useDiscord auth precondition', () => {
  it('apiCall-backed methods reject when no access token cookie is present', async () => {
    clearCookies()
    mockJson([])
    await expect(setup().listBots()).rejects.toThrow('No access token available')
  })
})
