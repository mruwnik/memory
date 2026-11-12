import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useIdentity } from './useIdentity'
import { setAuthCookies, clearCookies, mockFetch, mockResponse } from '@/test/utils'

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

const setup = () => renderHook(() => useIdentity()).result.current

const IDENTITY = {
  person: { id: 3, identifier: 'ada', display_name: 'Ada' },
  accounts: [],
  suggestions: [],
  notify: { order: ['discord', 'slack', 'email'], chosen: null, problems: {} },
}

const serve = (json: unknown, status = 200) => mockFetch(async () => mockResponse({ status, json }))

const identityCall = (fetchMock: ReturnType<typeof mockFetch>) => {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/auth/me/identity'))
  return { url: String(call?.[0]), method: call?.[1]?.method, body: call?.[1]?.body }
}

describe('useIdentity', () => {
  it.each([
    ['getIdentity', (h: ReturnType<typeof setup>) => h.getIdentity(), '/auth/me/identity', 'GET', undefined],
    ['claimPerson', (h: ReturnType<typeof setup>) => h.claimPerson(7), '/auth/me/identity/claim', 'POST', '{"person_id":7}'],
    ['unlinkAccount', (h: ReturnType<typeof setup>) => h.unlinkAccount('email', 'a/b@x.com'), '/auth/me/identity/accounts/email/a%2Fb%40x.com', 'DELETE', undefined],
    ['discordLinkCode', (h: ReturnType<typeof setup>) => h.discordLinkCode(), '/auth/me/identity/discord-code', 'POST', undefined],
  ])('%s calls its endpoint and returns the body', async (_, call, path, method, body) => {
    const fetchMock = serve(IDENTITY)
    expect(await call(setup())).toEqual(IDENTITY)
    const sent = identityCall(fetchMock)
    expect(sent.url.endsWith(path)).toBe(true)
    expect(sent.method).toBe(method)
    expect(sent.body).toBe(body)
  })

  it.each([
    [{ detail: 'None of your verified accounts belongs to that person' }, 403, 'None of your verified accounts belongs to that person'],
    [{}, 500, 'Identity request failed (500)'],
  ])('throws the server reason: %o %s', async (json, status, message) => {
    serve(json, status)
    await expect(setup().claimPerson(7)).rejects.toThrow(message)
  })
})
