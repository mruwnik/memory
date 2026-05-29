import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useSlack } from './useSlack'
import { setAuthCookies, clearCookies, mockFetch, mockResponse } from '@/test/utils'

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

function setup() {
  return renderHook(() => useSlack()).result.current
}

// useAuth fires a /auth/me call on mount; locate the relevant fetch call.
function callTo(fetchMock: ReturnType<typeof mockFetch>, substr: string) {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes(substr))
  return { url: String(call?.[0]), init: call?.[1] }
}

function mockJson(json: unknown, status = 200) {
  return mockFetch(async () => mockResponse({ status, json }))
}

describe('useSlack workspaces', () => {
  it('listWorkspaces without userId hits the bare endpoint', async () => {
    const fetchMock = mockJson([{ id: 'w1', name: 'Acme' }])
    const r = await setup().listWorkspaces()
    expect(r).toEqual([{ id: 'w1', name: 'Acme' }])
    expect(callTo(fetchMock, '/slack/workspaces').url).not.toContain('user_id')
  })

  it('listWorkspaces with userId 0 still appends the param (undefined check, not falsy)', async () => {
    const fetchMock = mockJson([])
    await setup().listWorkspaces(0)
    expect(callTo(fetchMock, '/slack/workspaces').url).toContain('user_id=0')
  })

  it('listWorkspaces throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().listWorkspaces()).rejects.toThrow('Failed to fetch Slack workspaces')
  })

  it('getWorkspace fetches a single workspace', async () => {
    const fetchMock = mockJson({ id: 'w1', name: 'Acme' })
    const r = await setup().getWorkspace('w1')
    expect(r.name).toBe('Acme')
    expect(callTo(fetchMock, '/slack/workspaces/w1').url).toContain('/slack/workspaces/w1')
  })

  it('getWorkspace throws on non-ok', async () => {
    mockJson({}, 404)
    await expect(setup().getWorkspace('w1')).rejects.toThrow('Failed to fetch workspace')
  })

  it('updateWorkspace PATCHes the body', async () => {
    const fetchMock = mockJson({ id: 'w1', collect_messages: true })
    await setup().updateWorkspace('w1', { collect_messages: true, sync_interval_seconds: 60 })
    const { init } = callTo(fetchMock, '/slack/workspaces/w1')
    expect(init?.method).toBe('PATCH')
    expect(JSON.parse(init?.body as string)).toEqual({
      collect_messages: true,
      sync_interval_seconds: 60,
    })
  })

  it('updateWorkspace surfaces the server detail', async () => {
    mockJson({ detail: 'bad interval' }, 422)
    await expect(setup().updateWorkspace('w1', {})).rejects.toThrow('bad interval')
  })

  it('updateWorkspace falls back to default message when detail missing', async () => {
    mockJson({}, 422)
    await expect(setup().updateWorkspace('w1', {})).rejects.toThrow('Failed to update workspace')
  })

  it('deleteWorkspace DELETEs and resolves void', async () => {
    const fetchMock = mockJson({}, 204)
    await expect(setup().deleteWorkspace('w1')).resolves.toBeUndefined()
    const { init } = callTo(fetchMock, '/slack/workspaces/w1')
    expect(init?.method).toBe('DELETE')
  })

  it('deleteWorkspace surfaces server detail on failure', async () => {
    mockJson({ detail: 'still syncing' }, 409)
    await expect(setup().deleteWorkspace('w1')).rejects.toThrow('still syncing')
  })

  it('triggerSync POSTs to /sync and returns status', async () => {
    const fetchMock = mockJson({ status: 'queued' })
    const r = await setup().triggerSync('w1')
    expect(r).toEqual({ status: 'queued' })
    const { url, init } = callTo(fetchMock, '/slack/workspaces/w1/sync')
    expect(url).toContain('/sync')
    expect(init?.method).toBe('POST')
  })

  it('triggerSync surfaces server detail on failure', async () => {
    mockJson({ detail: 'already running' }, 409)
    await expect(setup().triggerSync('w1')).rejects.toThrow('already running')
  })
})

describe('useSlack channels', () => {
  it('listChannels without channelType uses the bare channels endpoint', async () => {
    const fetchMock = mockJson([{ id: 'c1' }])
    const r = await setup().listChannels('w1')
    expect(r).toEqual([{ id: 'c1' }])
    expect(callTo(fetchMock, '/slack/workspaces/w1/channels').url).not.toContain('channel_type')
  })

  it('listChannels with channelType appends the query param', async () => {
    const fetchMock = mockJson([])
    await setup().listChannels('w1', 'public')
    expect(callTo(fetchMock, '/slack/workspaces/w1/channels').url).toContain(
      'channel_type=public',
    )
  })

  it('listChannels throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().listChannels('w1')).rejects.toThrow('Failed to fetch channels')
  })

  it('updateChannel PATCHes /slack/channels/:id', async () => {
    const fetchMock = mockJson({ id: 'c1', collect_messages: false })
    await setup().updateChannel('c1', { collect_messages: false })
    const { url, init } = callTo(fetchMock, '/slack/channels/c1')
    expect(url).toContain('/slack/channels/c1')
    expect(init?.method).toBe('PATCH')
    expect(JSON.parse(init?.body as string)).toEqual({ collect_messages: false })
  })

  it('updateChannel surfaces server detail on failure', async () => {
    mockJson({ detail: 'channel locked' }, 403)
    await expect(setup().updateChannel('c1', {})).rejects.toThrow('channel locked')
  })
})

describe('useSlack users', () => {
  it('listUsers without search omits the search param but still includes the "?"', async () => {
    const fetchMock = mockJson([{ id: 'u1', username: 'bob' }])
    const r = await setup().listUsers('w1')
    expect(r).toEqual([{ id: 'u1', username: 'bob' }])
    const { url } = callTo(fetchMock, '/slack/workspaces/w1/users')
    expect(url).toContain('/slack/workspaces/w1/users?')
    expect(url).not.toContain('search=')
  })

  it('listUsers with search URL-encodes the query', async () => {
    const fetchMock = mockJson([])
    await setup().listUsers('w1', 'john doe')
    const { url } = callTo(fetchMock, '/slack/workspaces/w1/users')
    expect(url).toContain('search=john+doe')
  })

  it('listUsers throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().listUsers('w1')).rejects.toThrow('Failed to fetch users')
  })
})

describe('useSlack auth precondition', () => {
  it('rejects when no access token cookie is present', async () => {
    clearCookies()
    mockJson([])
    await expect(setup().listWorkspaces()).rejects.toThrow('No access token available')
  })
})
