import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useSlackWizard } from './useSlackWizard'
import { setAuthCookies, clearCookies, mockFetch, mockResponse } from '@/test/utils'

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

function setup() {
  return renderHook(() => useSlackWizard()).result.current
}

function callTo(fetchMock: ReturnType<typeof mockFetch>, substr: string) {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes(substr))
  return { url: String(call?.[0]), init: call?.[1] }
}

function mockJson(json: unknown, status = 200) {
  return mockFetch(async () => mockResponse({ status, json }))
}

const APP: Record<string, unknown> = {
  id: 1,
  client_id: 'cid',
  name: 'My App',
  setup_state: 'draft',
  is_active: true,
  is_owner: true,
  is_authorized: true,
  client_secret_configured: false,
  signing_secret_configured: false,
  created_by_user_id: 1,
  authorized_users: [],
  authorized_user_ids: [],
  created_at: null,
  updated_at: null,
}

describe('useSlackWizard read operations', () => {
  it('listApps GETs /slack/apps and returns the list', async () => {
    const fetchMock = mockJson([APP])
    const r = await setup().listApps()
    expect(r).toEqual([APP])
    expect(callTo(fetchMock, '/slack/apps').url).toContain('/slack/apps')
  })

  it('listApps throws the default message on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().listApps()).rejects.toThrow('Failed to list Slack apps')
  })

  it('getApp fetches a single app by id', async () => {
    const fetchMock = mockJson(APP)
    const r = await setup().getApp(1)
    expect(r.id).toBe(1)
    expect(callTo(fetchMock, '/slack/apps/1').url).toContain('/slack/apps/1')
  })

  it('getApp throws default message on non-ok', async () => {
    mockJson({}, 404)
    await expect(setup().getApp(1)).rejects.toThrow('Failed to fetch Slack app')
  })

  it('getWizardStatus returns the wizard status payload', async () => {
    const status = { setup_state: 'draft', has_credentials: false, test_message_pending: false }
    const fetchMock = mockJson(status)
    const r = await setup().getWizardStatus(1)
    expect(r).toEqual(status)
    expect(callTo(fetchMock, '/slack/apps/1/wizard-status').url).toContain('/wizard-status')
  })

  it('getWizardStatus throws default message on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().getWizardStatus(1)).rejects.toThrow('Failed to fetch wizard status')
  })

  it('pollTestMessage GETs the test-message endpoint and returns status', async () => {
    const fetchMock = mockJson({ status: 'waiting' })
    const r = await setup().pollTestMessage(1)
    expect(r).toEqual({ status: 'waiting' })
    const { url, init } = callTo(fetchMock, '/slack/apps/1/test-message')
    expect(url).toContain('/test-message')
    expect(init?.method).toBeUndefined() // GET (no explicit method)
  })

  it('pollTestMessage throws default message on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().pollTestMessage(1)).rejects.toThrow('Failed to poll test message')
  })
})

describe('useSlackWizard mutations', () => {
  it('createApp POSTs the create body', async () => {
    const fetchMock = mockJson(APP)
    const r = await setup().createApp({ name: 'My App', client_id: 'cid' })
    expect(r).toEqual(APP)
    const { init } = callTo(fetchMock, '/slack/apps')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ name: 'My App', client_id: 'cid' })
  })

  it('createApp surfaces the server detail on failure', async () => {
    mockJson({ detail: 'duplicate client_id' }, 409)
    await expect(setup().createApp({ name: 'x', client_id: 'y' })).rejects.toThrow(
      'duplicate client_id',
    )
  })

  it('createApp falls back to the default message when detail absent', async () => {
    mockJson({}, 400)
    await expect(setup().createApp({ name: 'x', client_id: 'y' })).rejects.toThrow(
      'Failed to create Slack app',
    )
  })

  it('deleteApp DELETEs and resolves void on success', async () => {
    const fetchMock = mockJson({}, 204)
    await expect(setup().deleteApp(1)).resolves.toBeUndefined()
    const { init } = callTo(fetchMock, '/slack/apps/1')
    expect(init?.method).toBe('DELETE')
  })

  it('deleteApp throws server detail when the delete fails', async () => {
    mockJson({ detail: 'not the owner' }, 403)
    await expect(setup().deleteApp(1)).rejects.toThrow('not the owner')
  })

  it('setClientSecret POSTs the secret to /client-secret', async () => {
    const fetchMock = mockJson(APP)
    await setup().setClientSecret(1, 'shh')
    const { url, init } = callTo(fetchMock, '/slack/apps/1/client-secret')
    expect(url).toContain('/client-secret')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ secret: 'shh' })
  })

  it('setClientSecret throws default message on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().setClientSecret(1, 'shh')).rejects.toThrow('Failed to set client secret')
  })

  it('setSigningSecret POSTs the secret to /signing-secret', async () => {
    const fetchMock = mockJson(APP)
    await setup().setSigningSecret(1, 'sig')
    const { url, init } = callTo(fetchMock, '/slack/apps/1/signing-secret')
    expect(url).toContain('/signing-secret')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ secret: 'sig' })
  })

  it('setSigningSecret throws default message on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().setSigningSecret(1, 'sig')).rejects.toThrow('Failed to set signing secret')
  })

  it('issueWizardNonce POSTs and returns the nonce payload', async () => {
    const payload = { nonce: 'n1', callback_url: 'https://cb', events_url: 'https://ev' }
    const fetchMock = mockJson(payload)
    const r = await setup().issueWizardNonce(1)
    expect(r).toEqual(payload)
    const { init } = callTo(fetchMock, '/slack/apps/1/wizard-nonce')
    expect(init?.method).toBe('POST')
  })

  it('issueWizardNonce throws default message on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().issueWizardNonce(1)).rejects.toThrow('Failed to issue wizard nonce')
  })

  it('issueOAuthState POSTs and unwraps the state field', async () => {
    const fetchMock = mockJson({ state: 'abc123' })
    const r = await setup().issueOAuthState(1)
    expect(r).toBe('abc123')
    const { url, init } = callTo(fetchMock, '/slack/apps/1/oauth-state')
    expect(url).toContain('/oauth-state')
    expect(init?.method).toBe('POST')
  })

  it('issueOAuthState throws default message on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().issueOAuthState(1)).rejects.toThrow('Failed to issue OAuth state')
  })

  it('beginTestMessage POSTs the token and returns status', async () => {
    const fetchMock = mockJson({ status: 'waiting' })
    const r = await setup().beginTestMessage(1, 'token-42')
    expect(r).toEqual({ status: 'waiting' })
    const { url, init } = callTo(fetchMock, '/slack/apps/1/test-message')
    expect(url).toContain('/test-message')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ token: 'token-42' })
  })

  it('beginTestMessage throws default message on non-ok', async () => {
    mockJson({}, 500)
    await expect(setup().beginTestMessage(1, 't')).rejects.toThrow('Failed to begin test message')
  })
})

describe('useSlackWizard apiOk error parsing', () => {
  it('falls back to the default message when the error body is not JSON', async () => {
    mockFetch(async () =>
      mockResponse({ status: 500, text: '<html>500</html>', json: undefined }),
    )
    // mockResponse.json() resolves to undefined here, so body?.detail is undefined.
    await expect(setup().createApp({ name: 'x', client_id: 'y' })).rejects.toThrow(
      'Failed to create Slack app',
    )
  })

  it('uses the detail field when the error body has one', async () => {
    mockFetch(async () => mockResponse({ status: 400, json: { detail: 'precise reason' } }))
    await expect(setup().getApp(1)).rejects.toThrow('precise reason')
  })
})

describe('useSlackWizard multi-step state transitions', () => {
  it('walks a draft app through to live via getWizardStatus polling', async () => {
    // Distinct responses keyed by URL for the wizard happy path.
    const states = ['draft', 'signing_verified', 'live']
    let pollIdx = 0
    const fetchMock = mockFetch(async (input) => {
      const url = String(input)
      if (url.includes('/wizard-status')) {
        const setup_state = states[Math.min(pollIdx++, states.length - 1)]
        return mockResponse({
          json: {
            setup_state,
            has_credentials: setup_state !== 'draft',
            test_message_pending: setup_state === 'signing_verified',
          },
        })
      }
      if (url.includes('/test-message')) {
        return mockResponse({ json: { status: 'matched' } })
      }
      return mockResponse({ json: APP })
    })

    const wiz = setup()
    await wiz.createApp({ name: 'My App', client_id: 'cid' })
    await wiz.setClientSecret(1, 'cs')
    const nonce = await wiz.issueWizardNonce(1)
    expect(nonce).toBeDefined()

    const s1 = await wiz.getWizardStatus(1)
    expect(s1.setup_state).toBe('draft')

    await wiz.setSigningSecret(1, 'ss')
    const s2 = await wiz.getWizardStatus(1)
    expect(s2.setup_state).toBe('signing_verified')
    expect(s2.test_message_pending).toBe(true)

    const begun = await wiz.beginTestMessage(1, 'tok')
    expect(begun.status).toBe('matched')

    const s3 = await wiz.getWizardStatus(1)
    expect(s3.setup_state).toBe('live')

    // wizard-status was polled exactly three times across the flow.
    const statusCalls = fetchMock.mock.calls.filter((c) =>
      String(c[0]).includes('/wizard-status'),
    )
    expect(statusCalls).toHaveLength(3)
  })
})
