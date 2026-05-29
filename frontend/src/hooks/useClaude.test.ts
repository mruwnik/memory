import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useClaude, getDifferUrl, getLogStreamUrl } from './useClaude'
import { setAuthCookies, clearCookies, mockFetch, mockResponse } from '@/test/utils'

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

function setup() {
  return renderHook(() => useClaude()).result.current
}

function callTo(fetchMock: ReturnType<typeof mockFetch>, substr: string) {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes(substr))
  return { url: String(call?.[0]), init: call?.[1] }
}

function mockJson(json: unknown, status = 200) {
  return mockFetch(async () => mockResponse({ status, json }))
}

describe('getDifferUrl', () => {
  it('builds an http differ url under window.location.origin when SERVER_URL is empty', () => {
    expect(getDifferUrl('sess-1')).toBe(`${window.location.origin}/claude/sess-1/differ/`)
  })

  it('appends the supplied path segment', () => {
    expect(getDifferUrl('sess-1', 'diff/123')).toBe(
      `${window.location.origin}/claude/sess-1/differ/diff/123`,
    )
  })
})

describe('getLogStreamUrl', () => {
  it('returns null when no auth token cookie is present', () => {
    clearCookies()
    expect(getLogStreamUrl('sess-1')).toBeNull()
  })

  it('converts http origin to ws and embeds the encoded access token', () => {
    // setAuthCookies set access_token=test-access-token
    const url = getLogStreamUrl('sess-1')
    expect(url).toContain('/claude/sess-1/logs/stream?token=')
    expect(url).toContain('token=test-access-token')
    expect(url?.startsWith('ws')).toBe(true)
  })

  it('prefers the access_token cookie over session_id', () => {
    clearCookies()
    document.cookie = 'session_id=sid-only'
    expect(getLogStreamUrl('s')).toContain('token=sid-only')
    document.cookie = 'access_token=at-wins'
    expect(getLogStreamUrl('s')).toContain('token=at-wins')
  })
})

describe('useClaude sessions', () => {
  it('listSessions GETs /claude/list', async () => {
    const fetchMock = mockJson([{ session_id: 's1' }])
    const r = await setup().listSessions()
    expect(r).toEqual([{ session_id: 's1' }])
    expect(callTo(fetchMock, '/claude/list').url).toContain('/claude/list')
  })

  it('listSessions throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().listSessions()).rejects.toThrow('Failed to list sessions')
  })

  it('getSession fetches a single session', async () => {
    const fetchMock = mockJson({ session_id: 's1' })
    const r = await setup().getSession('s1')
    expect(r.session_id).toBe('s1')
    expect(callTo(fetchMock, '/claude/s1').url).toContain('/claude/s1')
  })

  it('getSession throws on non-ok', async () => {
    mockJson({}, 404)
    await expect(setup().getSession('s1')).rejects.toThrow('Failed to get session')
  })

  it('spawnSession POSTs the request body', async () => {
    const fetchMock = mockJson({ session_id: 'new' })
    const r = await setup().spawnSession({ repo_url: 'https://gh/x', enable_playwright: true })
    expect(r.session_id).toBe('new')
    const { url, init } = callTo(fetchMock, '/claude/spawn')
    expect(url).toContain('/claude/spawn')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({
      repo_url: 'https://gh/x',
      enable_playwright: true,
    })
  })

  it('spawnSession surfaces server detail on failure', async () => {
    mockJson({ detail: 'no capacity' }, 503)
    await expect(setup().spawnSession({})).rejects.toThrow('no capacity')
  })

  it('spawnSession falls back to default message when detail absent', async () => {
    mockJson({}, 500)
    await expect(setup().spawnSession({})).rejects.toThrow('Failed to spawn session')
  })

  it('scheduleSession upserts a claude_session schedule via scheduler_upsert', async () => {
    const task = {
      id: 't1',
      cron_expression: '0 9 * * *',
      next_scheduled_time: '2024',
      topic: 'tp',
    }
    const fetchMock = mockFetch(async (input) => {
      if (String(input).includes('/mcp/scheduler_upsert')) {
        return mockResponse({
          json: { jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text: JSON.stringify(task) }] } },
        })
      }
      return mockResponse({ json: {} }) // /auth/me on mount
    })
    const r = await setup().scheduleSession({
      cron_expression: '0 9 * * *',
      spawn_config: { repo_url: 'r', initial_prompt: 'hi' },
    })
    expect(r).toEqual({
      task_id: 't1',
      cron_expression: '0 9 * * *',
      next_scheduled_time: '2024',
      topic: 'tp',
    })
    // initial_prompt is forwarded as `message`; the rest becomes spawn_config.
    const { init } = callTo(fetchMock, '/mcp/scheduler_upsert')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string).params.arguments).toEqual({
      task_type: 'claude_session',
      cron_expression: '0 9 * * *',
      message: 'hi',
      spawn_config: { repo_url: 'r' },
    })
  })

  it('scheduleSession drops empty/undefined spawn_config fields', async () => {
    const fetchMock = mockFetch(async (input) => {
      if (String(input).includes('/mcp/scheduler_upsert')) {
        return mockResponse({
          json: { jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text: JSON.stringify({ id: 't2' }) }] } },
        })
      }
      return mockResponse({ json: {} })
    })
    await setup().scheduleSession({
      cron_expression: '0 9 * * *',
      spawn_config: { repo_url: 'r', branch: '', enable_playwright: undefined },
    })
    expect(JSON.parse(callTo(fetchMock, '/mcp/scheduler_upsert').init?.body as string).params.arguments.spawn_config).toEqual({
      repo_url: 'r',
    })
  })

  it('scheduleSession surfaces server detail on failure', async () => {
    mockJson({ detail: 'bad cron' }, 422)
    await expect(
      setup().scheduleSession({ cron_expression: 'x', spawn_config: {} }),
    ).rejects.toThrow('bad cron')
  })

  it('scheduleSession throws an MCP error carrying the HTTP status when the call fails', async () => {
    mockFetch(async (input) => {
      if (String(input).includes('/mcp/scheduler_upsert')) {
        return mockResponse({ status: 500, text: 'proxy boom', json: undefined })
      }
      return mockResponse({ json: {} })
    })
    await expect(
      setup().scheduleSession({ cron_expression: 'x', spawn_config: {} }),
    ).rejects.toThrow(/scheduler_upsert failed \(500\)/)
  })

  it('killSession DELETEs and resolves void', async () => {
    const fetchMock = mockJson({}, 204)
    await expect(setup().killSession('s1')).resolves.toBeUndefined()
    const { init } = callTo(fetchMock, '/claude/s1')
    expect(init?.method).toBe('DELETE')
  })

  it('killSession throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().killSession('s1')).rejects.toThrow('Failed to kill session')
  })

  it('getAttachInfo returns attach metadata', async () => {
    const fetchMock = mockJson({ session_id: 's1', container_name: 'c', attach_cmd: 'a', exec_cmd: 'e' })
    const r = await setup().getAttachInfo('s1')
    expect(r.attach_cmd).toBe('a')
    expect(callTo(fetchMock, '/claude/s1/attach').url).toContain('/attach')
  })

  it('getAttachInfo throws on non-ok', async () => {
    mockJson({}, 404)
    await expect(setup().getAttachInfo('s1')).rejects.toThrow('Failed to get attach info')
  })

  it('getOrchestratorStatus returns the status payload', async () => {
    const fetchMock = mockJson({ available: true, socket_path: '/sock' })
    const r = await setup().getOrchestratorStatus()
    expect(r.available).toBe(true)
    expect(callTo(fetchMock, '/claude/status').url).toContain('/claude/status')
  })

  it('getOrchestratorStatus throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().getOrchestratorStatus()).rejects.toThrow(
      'Failed to get orchestrator status',
    )
  })

  it('getFleetStats returns the fleet stats', async () => {
    const fetchMock = mockJson({ ts: 'now', containers: [] })
    const r = await setup().getFleetStats()
    expect(r.containers).toEqual([])
    expect(callTo(fetchMock, '/claude/stats').url).toContain('/claude/stats')
  })

  it('getFleetStats throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().getFleetStats()).rejects.toThrow('Failed to get fleet stats')
  })
})

describe('useClaude getStatsHistory query building', () => {
  it('omits the query string entirely when no options are given', async () => {
    const fetchMock = mockJson({ points: [], count: 0, truncated: false })
    await setup().getStatsHistory()
    const { url } = callTo(fetchMock, '/claude/stats/history')
    expect(url.endsWith('/claude/stats/history')).toBe(true)
  })

  it('serializes sessionId, since, and max (including max=0)', async () => {
    const fetchMock = mockJson({ points: [], count: 0, truncated: false })
    await setup().getStatsHistory({ sessionId: 's1', since: '2024', max: 0 })
    const { url } = callTo(fetchMock, '/claude/stats/history')
    expect(url).toContain('session_id=s1')
    expect(url).toContain('since=2024')
    expect(url).toContain('max=0')
  })

  it('throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().getStatsHistory()).rejects.toThrow('Failed to get stats history')
  })
})

describe('useClaude panes (graceful degradation)', () => {
  it('returns empty panes + null stats on non-ok instead of throwing', async () => {
    mockJson({}, 500)
    const r = await setup().listPanes('s1')
    expect(r).toEqual({ panes: [], stats: null })
  })

  it('returns the panes payload on success', async () => {
    const payload = { panes: [{ id: '%0', position: '0.0' }], stats: null }
    const fetchMock = mockJson(payload)
    const r = await setup().listPanes('s1')
    expect(r).toEqual(payload)
    expect(callTo(fetchMock, '/claude/s1/panes').url).toContain('/panes')
  })
})

describe('useClaude logs / snapshots / repos', () => {
  it('getSessionLogs defaults tail to 200', async () => {
    const fetchMock = mockJson({ session_id: 's1', source: 'file', logs: 'x' })
    await setup().getSessionLogs('s1')
    expect(callTo(fetchMock, '/claude/s1/logs').url).toContain('tail=200')
  })

  it('getSessionLogs honors a custom tail value', async () => {
    const fetchMock = mockJson({ session_id: 's1', source: 'file', logs: 'x' })
    await setup().getSessionLogs('s1', 42)
    expect(callTo(fetchMock, '/claude/s1/logs').url).toContain('tail=42')
  })

  it('getSessionLogs throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().getSessionLogs('s1')).rejects.toThrow('Failed to get session logs')
  })

  it('listSnapshots returns the snapshot list', async () => {
    const fetchMock = mockJson([{ id: 1, name: 'snap' }])
    const r = await setup().listSnapshots()
    expect(r).toEqual([{ id: 1, name: 'snap' }])
    expect(callTo(fetchMock, '/claude/snapshots/list').url).toContain('/snapshots/list')
  })

  it('listSnapshots throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().listSnapshots()).rejects.toThrow('Failed to list snapshots')
  })

  it('listUserRepos hits /github/repos', async () => {
    const fetchMock = mockJson([{ id: 1, owner: 'o', name: 'n', repo_path: 'o/n' }])
    const r = await setup().listUserRepos()
    expect(r[0].repo_path).toBe('o/n')
    expect(callTo(fetchMock, '/github/repos').url).toContain('/github/repos')
  })

  it('listUserRepos throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().listUserRepos()).rejects.toThrow('Failed to list GitHub repos')
  })
})

describe('useClaude environments CRUD', () => {
  it('listEnvironments returns the list', async () => {
    const fetchMock = mockJson([{ id: 1, name: 'env' }])
    const r = await setup().listEnvironments()
    expect(r).toEqual([{ id: 1, name: 'env' }])
    expect(callTo(fetchMock, '/claude/environments/list').url).toContain('/environments/list')
  })

  it('listEnvironments throws on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().listEnvironments()).rejects.toThrow('Failed to list environments')
  })

  it('getEnvironment fetches by id', async () => {
    const fetchMock = mockJson({ id: 5, name: 'env5' })
    const r = await setup().getEnvironment(5)
    expect(r.id).toBe(5)
    expect(callTo(fetchMock, '/claude/environments/5').url).toContain('/environments/5')
  })

  it('getEnvironment throws on non-ok', async () => {
    mockJson({}, 404)
    await expect(setup().getEnvironment(5)).rejects.toThrow('Failed to get environment')
  })

  it('createEnvironment POSTs the request body', async () => {
    const fetchMock = mockJson({ id: 9, name: 'new-env' })
    await setup().createEnvironment({ name: 'new-env', snapshot_id: 3 })
    const { url, init } = callTo(fetchMock, '/claude/environments/create')
    expect(url).toContain('/environments/create')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ name: 'new-env', snapshot_id: 3 })
  })

  it('createEnvironment surfaces server detail on failure', async () => {
    mockJson({ detail: 'name taken' }, 409)
    await expect(setup().createEnvironment({ name: 'x' })).rejects.toThrow('name taken')
  })

  it('deleteEnvironment DELETEs and resolves void', async () => {
    const fetchMock = mockJson({}, 204)
    await expect(setup().deleteEnvironment(9)).resolves.toBeUndefined()
    const { init } = callTo(fetchMock, '/claude/environments/9')
    expect(init?.method).toBe('DELETE')
  })

  it('deleteEnvironment surfaces server detail on failure', async () => {
    mockJson({ detail: 'in use' }, 409)
    await expect(setup().deleteEnvironment(9)).rejects.toThrow('in use')
  })

  it('resetEnvironment POSTs an empty body by default', async () => {
    const fetchMock = mockJson({ id: 9, name: 'env' })
    await setup().resetEnvironment(9)
    const { url, init } = callTo(fetchMock, '/claude/environments/9/reset')
    expect(url).toContain('/reset')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({})
  })

  it('resetEnvironment forwards a snapshot_id when supplied', async () => {
    const fetchMock = mockJson({ id: 9, name: 'env' })
    await setup().resetEnvironment(9, { snapshot_id: 4 })
    const { init } = callTo(fetchMock, '/claude/environments/9/reset')
    expect(JSON.parse(init?.body as string)).toEqual({ snapshot_id: 4 })
  })

  it('resetEnvironment surfaces server detail on failure', async () => {
    mockJson({ detail: 'cannot reset' }, 500)
    await expect(setup().resetEnvironment(9)).rejects.toThrow('cannot reset')
  })
})
