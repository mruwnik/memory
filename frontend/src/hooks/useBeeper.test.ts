import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useBeeper } from './useBeeper'
import { setAuthCookies, clearCookies, mockFetch, mockResponse } from '@/test/utils'

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

type Hook = ReturnType<typeof useBeeper>

const setup = () => renderHook(() => useBeeper()).result.current

// useAuth fires /auth/me on mount, so find the call under test by its path.
const beeperCall = (fetchMock: ReturnType<typeof mockFetch>) => {
  const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/beeper/'))!
  const init = call[1] as RequestInit
  return { url: String(call[0]), method: init.method, body: init.body ? JSON.parse(init.body as string) : undefined }
}

describe('useBeeper requests', () => {
  it.each<[string, (hook: Hook) => Promise<unknown>, string, string, unknown]>([
    ['listServers', h => h.listServers(), 'GET', '/beeper/servers', undefined],
    ['listAccounts', h => h.listAccounts(), 'GET', '/beeper/accounts', undefined],
    ['listAccounts for a user', h => h.listAccounts(7), 'GET', '/beeper/accounts?user_id=7', undefined],
    ['createAccount', h => h.createAccount({ server_url: 'http://beeper:23373', collect_by_default: true }), 'POST', '/beeper/accounts',
      { server_url: 'http://beeper:23373', collect_by_default: true }],
    ['updateAccount', h => h.updateAccount(3, { is_active: false }), 'PATCH', '/beeper/accounts/3', { is_active: false }],
    ['deleteAccount', h => h.deleteAccount(3), 'DELETE', '/beeper/accounts/3', undefined],
    ['signOut', h => h.signOut(3), 'POST', '/beeper/accounts/3/sign-out', undefined],
    ['sendEmail', h => h.sendEmail(3, 'me@example.com'), 'POST', '/beeper/accounts/3/email', { email: 'me@example.com' }],
    ['sendCode', h => h.sendCode(3, '123456'), 'POST', '/beeper/accounts/3/code', { code: '123456' }],
    ['getSetup', h => h.getSetup(3), 'GET', '/beeper/accounts/3/setup', undefined],
    ['startVerification', h => h.startVerification(3), 'POST', '/beeper/accounts/3/verifications', undefined],
    ['verificationAction', h => h.verificationAction(3, 'v/1', 'sas-confirm'), 'POST', '/beeper/accounts/3/verifications/v%2F1/sas-confirm', undefined],
    ['sendRecoveryKey', h => h.sendRecoveryKey(3, 'EsT0 key'), 'POST', '/beeper/accounts/3/recovery-key', { recovery_key: 'EsT0 key' }],
    ['refreshNetworks', h => h.refreshNetworks(3), 'POST', '/beeper/accounts/3/refresh', undefined],
    ['listNetworks', h => h.listNetworks(3), 'GET', '/beeper/accounts/3/networks', undefined],
    ['updateNetwork', h => h.updateNetwork(5, { project_id: null }), 'PATCH', '/beeper/networks/5', { project_id: null }],
    ['listChats', h => h.listChats(5), 'GET', '/beeper/networks/5/chats', undefined],
    ['updateChat', h => h.updateChat(9, { collect_messages: null }), 'PATCH', '/beeper/chats/9', { collect_messages: null }],
  ])('%s', async (_, request, method, path, body) => {
    const fetchMock = mockFetch(async () => mockResponse({ json: { ok: true } }))
    expect(await request(setup())).toEqual({ ok: true })
    const call = beeperCall(fetchMock)
    expect([call.method ?? 'GET', call.url.slice(call.url.indexOf('/beeper/')), call.body]).toEqual([method, path, body])
  })

  it.each([
    [{ detail: "There's no Beeper account for this email." }, "There's no Beeper account for this email."],
    [{}, 'Failed to sign in'],
  ])('throws the server detail, or a fallback, on failure', async (json, message) => {
    mockFetch(async () => mockResponse({ status: 400, json }))
    await expect(setup().sendCode(3, '1')).rejects.toThrow(message)
  })
})
