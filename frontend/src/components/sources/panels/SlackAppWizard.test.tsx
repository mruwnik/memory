import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderWithUser, screen, waitFor } from '@/test/utils'

const createApp = vi.fn()
const getApp = vi.fn()
const setClientSecret = vi.fn()
const setSigningSecret = vi.fn()
const issueWizardNonce = vi.fn()
const issueOAuthState = vi.fn()
const getWizardStatus = vi.fn()
const beginTestMessage = vi.fn()

vi.mock('../../../hooks/useSlackWizard', () => ({
  useSlackWizard: () => ({
    createApp,
    getApp,
    setClientSecret,
    setSigningSecret,
    issueWizardNonce,
    issueOAuthState,
    getWizardStatus,
    beginTestMessage,
  }),
}))

vi.mock('../../../hooks/useAuth', () => ({
  useAuth: () => ({ user: { id: 42, name: 'Tester', email: 't@e.com' } }),
}))

import { SlackAppWizard } from './SlackAppWizard'
import type { SlackAppResponse } from '../../../hooks/useSlackWizard'

const app = (over: Partial<SlackAppResponse> = {}): SlackAppResponse => ({
  id: 5,
  client_id: 'cid-123',
  name: 'My App',
  setup_state: 'draft',
  is_active: true,
  is_owner: true,
  is_authorized: false,
  client_secret_configured: false,
  signing_secret_configured: false,
  created_by_user_id: 42,
  authorized_users: [],
  authorized_user_ids: [],
  created_at: null,
  updated_at: null,
  ...over,
})

const nonce = {
  nonce: 'n1',
  callback_url: 'https://app.example.com/slack/callback/5',
  events_url: 'https://app.example.com/slack/events/5',
}

beforeEach(() => {
  vi.clearAllMocks()
  createApp.mockResolvedValue(app())
  getApp.mockResolvedValue(app())
  setClientSecret.mockResolvedValue(app({ client_secret_configured: true }))
  setSigningSecret.mockResolvedValue(app({ signing_secret_configured: true }))
  issueWizardNonce.mockResolvedValue(nonce)
  issueOAuthState.mockResolvedValue('signed-state')
  getWizardStatus.mockResolvedValue({ setup_state: 'draft', has_credentials: false, test_message_pending: false })
  beginTestMessage.mockResolvedValue({ status: 'waiting' })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('SlackAppWizard - initial step', () => {
  it('starts on the register step with a 7-step progress label', () => {
    renderWithUser(<SlackAppWizard />)
    expect(screen.getByText('Step 1 of 7')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create app' })).toBeInTheDocument()
  })

  it('derives the starting step from an initial app setup_state', () => {
    renderWithUser(<SlackAppWizard initialApp={app({ setup_state: 'live' })} />)
    expect(screen.getByText(/configured and live/)).toBeInTheDocument()
    expect(screen.getByText('Step 7 of 7')).toBeInTheDocument()
  })

  it('validates that both name and client_id are required', async () => {
    const { user } = renderWithUser(<SlackAppWizard />)
    await user.click(screen.getByRole('button', { name: 'Create app' }))
    expect(await screen.findByText('Both name and client_id are required')).toBeInTheDocument()
    expect(createApp).not.toHaveBeenCalled()
  })

  it('renders a Cancel button only when onCancel is provided', () => {
    const { rerender } = renderWithUser(<SlackAppWizard />)
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
    rerender(<SlackAppWizard onCancel={() => {}} />)
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
  })
})

describe('SlackAppWizard - register -> client-secret -> oauth', () => {
  it('creates the app and advances to the client-secret step', async () => {
    const { user } = renderWithUser(<SlackAppWizard />)
    await user.type(screen.getByPlaceholderText("My team's Slack"), 'Acme')
    await user.type(screen.getByPlaceholderText('1234567890.1234567890'), 'cid-9')
    await user.click(screen.getByRole('button', { name: 'Create app' }))

    await waitFor(() => expect(createApp).toHaveBeenCalledWith({ name: 'Acme', client_id: 'cid-9' }))
    expect(await screen.findByText("Paste the Client Secret from Slack's \"Basic Information\" page.")).toBeInTheDocument()
    expect(screen.getByText('Step 2 of 7')).toBeInTheDocument()
  })

  it('shows an error if app creation fails', async () => {
    createApp.mockRejectedValueOnce(new Error('client_id taken'))
    const { user } = renderWithUser(<SlackAppWizard />)
    await user.type(screen.getByPlaceholderText("My team's Slack"), 'Acme')
    await user.type(screen.getByPlaceholderText('1234567890.1234567890'), 'cid-9')
    await user.click(screen.getByRole('button', { name: 'Create app' }))

    expect(await screen.findByText('client_id taken')).toBeInTheDocument()
  })

  it('requires a non-empty client secret', async () => {
    const { user } = renderWithUser(<SlackAppWizard initialApp={app()} />)
    await user.click(screen.getByRole('button', { name: 'Save and continue' }))
    expect(await screen.findByText('client_secret is required')).toBeInTheDocument()
    expect(setClientSecret).not.toHaveBeenCalled()
  })

  it('stores the client secret, issues a nonce, and advances to oauth showing the callback url', async () => {
    const { user } = renderWithUser(<SlackAppWizard initialApp={app()} />)
    const input = document.querySelector('input[type="password"]') as HTMLInputElement
    await user.type(input, 'super-secret')
    await user.click(screen.getByRole('button', { name: 'Save and continue' }))

    await waitFor(() => expect(setClientSecret).toHaveBeenCalledWith(5, 'super-secret'))
    expect(issueWizardNonce).toHaveBeenCalledWith(5)
    expect(await screen.findByText(nonce.callback_url)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Authorize workspace' })).toBeInTheDocument()
  })
})

describe('SlackAppWizard - oauth redirect', () => {
  it('mints an oauth state and opens the Slack authorize URL in a popup', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    const { user } = renderWithUser(<SlackAppWizard initialApp={app()} />)

    // advance to oauth
    const input = document.querySelector('input[type="password"]') as HTMLInputElement
    await user.type(input, 'sec')
    await user.click(screen.getByRole('button', { name: 'Save and continue' }))
    await screen.findByRole('button', { name: 'Authorize workspace' })

    await user.click(screen.getByRole('button', { name: 'Authorize workspace' }))
    await waitFor(() => expect(issueOAuthState).toHaveBeenCalledWith(5))
    expect(openSpy).toHaveBeenCalledTimes(1)
    const url = openSpy.mock.calls[0][0] as string
    expect(url).toContain('https://slack.com/oauth/v2/authorize?')
    expect(url).toContain('client_id=cid-123')
    expect(url).toContain('state=signed-state')
    // User-token-only install: scopes ride on user_scope, and there must be NO
    // bot `scope` param — requesting it makes Slack demand a bot user the app
    // never uses and fails the install with "doesn't have a bot user".
    const params = new URLSearchParams(url.split('?')[1])
    expect(params.get('user_scope')).toContain('channels:history')
    expect(params.has('scope')).toBe(false)
    openSpy.mockRestore()
  })
})

// Drive the wizard from the client-secret step through OAuth to the signing-secret
// step by posting an `oauth-complete` event on the per-user BroadcastChannel.
const advanceToSigningSecret = async (user: ReturnType<typeof renderWithUser>['user']) => {
  const input = document.querySelector('input[type="password"]') as HTMLInputElement
  await user.type(input, 'sec')
  await user.click(screen.getByRole('button', { name: 'Save and continue' }))
  await screen.findByRole('button', { name: 'Authorize workspace' })

  const channel = new BroadcastChannel('slack-oauth-42')
  channel.postMessage({ type: 'oauth-complete' })
  channel.close()
  await screen.findByText("Paste the Signing Secret from Slack's \"Basic Information\" page.")
}

describe('SlackAppWizard - signing-secret -> events-url', () => {
  it('advances to the signing-secret step after OAuth completes', async () => {
    getApp.mockResolvedValue(app({ client_secret_configured: true, is_authorized: true }))
    const { user } = renderWithUser(<SlackAppWizard initialApp={app()} />)
    await advanceToSigningSecret(user)
    expect(screen.getByText('Step 4 of 7')).toBeInTheDocument()
  })

  it('requires a non-empty signing secret', async () => {
    getApp.mockResolvedValue(app({ client_secret_configured: true, is_authorized: true }))
    const { user } = renderWithUser(<SlackAppWizard initialApp={app()} />)
    await advanceToSigningSecret(user)

    await user.click(screen.getByRole('button', { name: 'Save and continue' }))
    expect(await screen.findByText('signing_secret is required')).toBeInTheDocument()
    expect(setSigningSecret).not.toHaveBeenCalled()
  })

  it('stores the signing secret, re-issues a nonce, and advances to events-url', async () => {
    getApp.mockResolvedValue(app({ client_secret_configured: true, is_authorized: true }))
    const { user } = renderWithUser(<SlackAppWizard initialApp={app()} />)
    await advanceToSigningSecret(user)

    const signingInput = document.querySelector('input[type="password"]') as HTMLInputElement
    await user.type(signingInput, 'sign-me')
    await user.click(screen.getByRole('button', { name: 'Save and continue' }))

    await waitFor(() => expect(setSigningSecret).toHaveBeenCalledWith(5, 'sign-me'))
    expect(await screen.findByText(nonce.events_url)).toBeInTheDocument()
    expect(screen.getByText('Step 5 of 7')).toBeInTheDocument()
  })
})

describe('SlackAppWizard - test message', () => {
  it('shows a generated token and starts the 60s window', async () => {
    const { user } = renderWithUser(<SlackAppWizard initialApp={app({ setup_state: 'signing_verified' })} />)
    expect(screen.getByText(/Post the following token/)).toBeInTheDocument()
    expect(screen.getByText('Step 6 of 7')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Start 60-second window' }))
    await waitFor(() => expect(beginTestMessage).toHaveBeenCalledTimes(1))
    expect(beginTestMessage.mock.calls[0][0]).toBe(5)
    expect(screen.getByText('Status: waiting')).toBeInTheDocument()
  })

  it('surfaces an error if beginTestMessage rejects', async () => {
    beginTestMessage.mockRejectedValueOnce(new Error('window failed'))
    const { user } = renderWithUser(<SlackAppWizard initialApp={app({ setup_state: 'signing_verified' })} />)
    await user.click(screen.getByRole('button', { name: 'Start 60-second window' }))
    expect(await screen.findByText('window failed')).toBeInTheDocument()
  })
})

describe('SlackAppWizard - polling transitions', () => {
  it('advances events-url -> test-message when status becomes signing_verified', async () => {
    getApp.mockResolvedValue(app({ client_secret_configured: true, is_authorized: true }))
    getWizardStatus.mockResolvedValue({ setup_state: 'signing_verified', has_credentials: true, test_message_pending: false })

    const { user } = renderWithUser(<SlackAppWizard initialApp={app()} />)
    await advanceToSigningSecret(user)
    const signingInput = document.querySelector('input[type="password"]') as HTMLInputElement
    await user.type(signingInput, 'sign-me')
    await user.click(screen.getByRole('button', { name: 'Save and continue' }))
    await screen.findByText(nonce.events_url)

    // Polling on the events-url step should detect signing_verified and advance.
    await screen.findByText(/Post the following token/, {}, { timeout: 4000 })
    expect(screen.getByText('Step 6 of 7')).toBeInTheDocument()
  })

  it('moves test-message -> done (live) on poll and calls onComplete', async () => {
    vi.useFakeTimers()
    const onComplete = vi.fn()
    getWizardStatus.mockResolvedValue({ setup_state: 'live', has_credentials: true, test_message_pending: false })
    getApp.mockResolvedValue(app({ setup_state: 'live' }))

    renderWithUser(<SlackAppWizard initialApp={app({ setup_state: 'signing_verified' })} onComplete={onComplete} />)

    await vi.advanceTimersByTimeAsync(3100)
    await vi.waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1))
    expect(getApp).toHaveBeenCalledWith(5)
    expect(onComplete.mock.calls[0][0].setup_state).toBe('live')
  })
})
