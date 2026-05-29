import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import {
  usePolls,
  getPollBySlug,
  submitPollResponse,
  updatePollResponse,
  getResponseByToken,
  getPollResults,
} from './usePolls'
import { mockFetchRoutes, mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'
import { mcpResult, mcpArgsAt, mcpUrlAt } from './mcpEnvelope.testhelper'

const setup = () => renderHook(() => usePolls()).result.current

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

const JSON_HEADERS = { 'content-type': 'application/json' }

const poll = (over: Record<string, any> = {}) => ({ id: 1, slug: 's', title: 'T', status: 'open', ...over })

describe('usePolls.createPoll', () => {
  it('sends poll fields and unwraps the first result', async () => {
    const created = poll({ id: 5 })
    const fetchMock = mockFetchRoutes({ polling_upsert_poll: mcpResult(created) })
    const { createPoll } = setup()

    const out = await createPoll({
      title: 'T',
      description: 'd',
      datetime_start: '2026-01-01T00:00:00Z',
      datetime_end: '2026-01-02T00:00:00Z',
      slot_duration: 30,
      closes_at: '2026-01-01T12:00:00Z',
    })

    expect(out).toEqual(created)
    expect(mcpUrlAt(fetchMock)).toContain('/mcp/polling_upsert_poll')
    expect(mcpArgsAt(fetchMock)).toMatchObject({
      title: 'T',
      description: 'd',
      datetime_start: '2026-01-01T00:00:00Z',
      datetime_end: '2026-01-02T00:00:00Z',
      slot_duration: 30,
      closes_at: '2026-01-01T12:00:00Z',
    })
  })
})

describe('usePolls.listPolls', () => {
  it('returns the polls array and forwards status', async () => {
    const polls = [poll(), poll({ id: 2 })]
    const fetchMock = mockFetchRoutes({ polling_list_polls: mcpResult(polls) })
    const { listPolls } = setup()

    const out = await listPolls('open')

    expect(out).toEqual(polls)
    expect(mcpArgsAt(fetchMock)).toEqual({ status: 'open' })
  })

  it('returns [] when result is falsy', async () => {
    mockFetchRoutes({ polling_list_polls: mcpResult(null) })
    const { listPolls } = setup()
    expect(await listPolls()).toEqual([])
  })
})

describe('usePolls.getPoll', () => {
  it('returns poll results and sends poll_id', async () => {
    const results = { poll: poll(), response_count: 0, aggregated: [], best_slots: [] }
    const fetchMock = mockFetchRoutes({ polling_get_poll: mcpResult(results) })
    const { getPoll } = setup()

    const out = await getPoll(7)

    expect(out).toEqual(results)
    expect(mcpArgsAt(fetchMock)).toEqual({ poll_id: 7 })
  })
})

describe('usePolls.updatePoll', () => {
  it('forwards the full update request', async () => {
    const fetchMock = mockFetchRoutes({ polling_upsert_poll: mcpResult(poll()) })
    const { updatePoll } = setup()

    await updatePoll({ poll_id: 3, title: 'New', status: 'closed' })

    expect(mcpArgsAt(fetchMock)).toMatchObject({ poll_id: 3, title: 'New', status: 'closed' })
  })
})

describe('usePolls status transitions', () => {
  it('cancelPoll sets status cancelled', async () => {
    const fetchMock = mockFetchRoutes({ polling_upsert_poll: mcpResult(poll({ status: 'cancelled' })) })
    const { cancelPoll } = setup()
    await cancelPoll(2)
    expect(mcpArgsAt(fetchMock)).toEqual({ poll_id: 2, status: 'cancelled' })
  })

  it('closePoll sets status closed', async () => {
    const fetchMock = mockFetchRoutes({ polling_upsert_poll: mcpResult(poll({ status: 'closed' })) })
    const { closePoll } = setup()
    await closePoll(2)
    expect(mcpArgsAt(fetchMock)).toEqual({ poll_id: 2, status: 'closed' })
  })

  it('finalizePoll sets status finalized with the chosen time', async () => {
    const fetchMock = mockFetchRoutes({ polling_upsert_poll: mcpResult(poll({ status: 'finalized' })) })
    const { finalizePoll } = setup()
    await finalizePoll(2, '2026-02-02T10:00:00Z')
    expect(mcpArgsAt(fetchMock)).toEqual({
      poll_id: 2,
      status: 'finalized',
      finalized_time: '2026-02-02T10:00:00Z',
    })
  })
})

describe('usePolls.deletePoll', () => {
  it('returns the delete result and sends poll_id', async () => {
    const fetchMock = mockFetchRoutes({ polling_delete_poll: mcpResult({ deleted: true, poll_id: 4 }) })
    const { deletePoll } = setup()

    const out = await deletePoll(4)

    expect(out).toEqual({ deleted: true, poll_id: 4 })
    expect(mcpArgsAt(fetchMock)).toEqual({ poll_id: 4 })
  })
})

describe('public poll endpoints', () => {
  it('getPollBySlug GETs the respond endpoint and returns JSON', async () => {
    const fetchMock = mockFetch(async () => mockResponse({ json: poll(), headers: JSON_HEADERS }))

    const out = await getPollBySlug('my-slug')

    expect(out).toEqual(poll())
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/polls/respond/my-slug')
    expect(init?.method ?? 'GET').toBe('GET')
    expect((init?.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('getPollBySlug throws the server detail on failure', async () => {
    mockFetch(async () => mockResponse({ status: 404, json: { detail: 'no such poll' }, headers: JSON_HEADERS }))
    await expect(getPollBySlug('x')).rejects.toThrow('no such poll')
  })

  it('getPollBySlug throws a generic message when error body is not JSON', async () => {
    mockFetch(async () => ({
      ok: false,
      status: 500,
      headers: new Headers(),
      json: async () => {
        throw new Error('not json')
      },
    } as unknown as Response))
    await expect(getPollBySlug('x')).rejects.toThrow('Unknown error')
  })

  it('submitPollResponse POSTs the body', async () => {
    const result = { response_id: 1, edit_token: 'tok', status: 'ok' }
    const fetchMock = mockFetch(async () => mockResponse({ json: result, headers: JSON_HEADERS }))

    const out = await submitPollResponse('slug', { availabilities: [] })

    expect(out).toEqual(result)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/polls/respond/slug')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ availabilities: [] })
  })

  it('updatePollResponse PUTs with the edit token header', async () => {
    const fetchMock = mockFetch(async () => mockResponse({ json: { status: 'updated' }, headers: JSON_HEADERS }))

    const out = await updatePollResponse('slug', 9, 'edit-tok', { availabilities: [] })

    expect(out).toEqual({ status: 'updated' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/polls/respond/slug/9')
    expect(init?.method).toBe('PUT')
    expect((init?.headers as Record<string, string>)['X-Edit-Token']).toBe('edit-tok')
  })

  it('getResponseByToken GETs the response endpoint with the token header', async () => {
    const resp = { response_id: 1, respondent_name: null, respondent_email: null, availabilities: [] }
    const fetchMock = mockFetch(async () => mockResponse({ json: resp, headers: JSON_HEADERS }))

    const out = await getResponseByToken('slug', 'tok')

    expect(out).toEqual(resp)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/polls/respond/slug/response')
    expect((init?.headers as Record<string, string>)['X-Edit-Token']).toBe('tok')
  })

  it('getPollResults GETs the results endpoint', async () => {
    const results = { poll: poll(), response_count: 0, aggregated: [], best_slots: [] }
    const fetchMock = mockFetch(async () => mockResponse({ json: results, headers: JSON_HEADERS }))

    const out = await getPollResults('slug')

    expect(out).toEqual(results)
    expect(String(fetchMock.mock.calls[0][0])).toContain('/polls/respond/slug/results')
  })

  it('throws when the success response is not JSON', async () => {
    mockFetch(async () => mockResponse({ status: 200, text: '<html>', headers: { 'content-type': 'text/html' } }))
    await expect(getPollResults('slug')).rejects.toThrow(/Expected JSON response/)
  })
})
