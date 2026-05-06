// Multi-step Slack-app setup wizard (slack-changes.md §3.4).
//
// Walks the user through registering a Slack app at api.slack.com, pasting
// client_id / client_secret / signing_secret, completing the OAuth flow,
// configuring the events URL, and posting a test message that flips the
// app to 'live'. Polls /slack/apps/{id}/wizard-status for async
// transitions (url_verification, test-message match).
import {useCallback, useEffect, useMemo, useState} from 'react'
import {
  type SlackAppNonceResponse,
  type SlackAppResponse,
  useSlackWizard,
} from '../../../hooks/useSlackWizard'

interface SlackAppWizardProps {
  initialApp?: SlackAppResponse | null
  onComplete?: (app: SlackAppResponse) => void
  onCancel?: () => void
}

type Step =
  | 'register'        // open api.slack.com, paste client_id
  | 'client-secret'   // paste client_secret
  | 'oauth'           // launch OAuth popup, wait for credentials
  | 'signing-secret'  // paste signing_secret
  | 'events-url'      // user pastes events_url into Slack; we poll for url_verification
  | 'test-message'    // user posts the token; we poll for match
  | 'done'

const SETUP_STATE_TO_STEP: Record<string, Step> = {
  draft: 'client-secret',
  signing_verified: 'test-message',
  live: 'done',
  degraded: 'done',
}

const POLL_INTERVAL_MS = 3000

const generateRandomToken = () => {
  // 24 chars of base32-like alphabet — wide enough that a chatty channel
  // is exceedingly unlikely to contain it by accident, and short enough to
  // type if needed.
  const alphabet = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
  return Array.from(
    {length: 24},
    () => alphabet.charAt(Math.floor(Math.random() * alphabet.length)),
  ).join('')
}

export const SlackAppWizard = ({
  initialApp = null,
  onComplete,
  onCancel,
}: SlackAppWizardProps) => {
  const wizard = useSlackWizard()
  const [app, setApp] = useState<SlackAppResponse | null>(initialApp)
  const [step, setStep] = useState<Step>(() => {
    if (!initialApp) return 'register'
    return SETUP_STATE_TO_STEP[initialApp.setup_state] ?? 'client-secret'
  })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [name, setName] = useState(initialApp?.name ?? '')
  const [clientId, setClientId] = useState(initialApp?.client_id ?? '')
  const [clientSecret, setClientSecret] = useState('')
  const [signingSecret, setSigningSecret] = useState('')

  const [nonceData, setNonceData] = useState<SlackAppNonceResponse | null>(null)
  const [testToken, setTestToken] = useState<string>(() => generateRandomToken())
  const [testStatus, setTestStatus] = useState<string | null>(null)

  const callbackUrl = nonceData?.callback_url
  const eventsUrl = nonceData?.events_url

  // === Polling for async state transitions ===
  // setup_state: draft → (url_verification) → signing_verified → (test message) → live
  useEffect(() => {
    if (!app) return
    if (step !== 'events-url' && step !== 'test-message') return
    let cancelled = false
    const tick = async () => {
      try {
        const status = await wizard.getWizardStatus(app.id)
        if (cancelled) return
        if (step === 'events-url' && status.setup_state === 'signing_verified') {
          setStep('test-message')
        }
        if (step === 'test-message' && status.setup_state === 'live') {
          const fresh = await wizard.getApp(app.id)
          if (cancelled) return
          setApp(fresh)
          setStep('done')
          onComplete?.(fresh)
        }
      } catch (e) {
        // Transient errors during polling are non-fatal — keep retrying.
        if (!cancelled) setError((e as Error).message)
      }
    }
    const id = window.setInterval(tick, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [app, step, wizard, onComplete])

  // === Listen for OAuth completion via per-user BroadcastChannel ===
  useEffect(() => {
    if (!app || step !== 'oauth') return
    const userIdMatch = document.cookie.match(/user_id=(\d+)/)
    const userId = userIdMatch?.[1] ?? '0'
    const channel = new BroadcastChannel(`slack-oauth-${userId}`)
    channel.addEventListener('message', async (evt: MessageEvent) => {
      if (evt.data?.type === 'oauth-complete' && app) {
        const fresh = await wizard.getApp(app.id)
        setApp(fresh)
        setStep('signing-secret')
      }
    })
    return () => channel.close()
  }, [app, step, wizard])

  const handleError = (e: unknown) => setError((e as Error).message)
  const clearError = () => setError(null)

  const handleRegister = useCallback(async () => {
    clearError()
    if (!name.trim() || !clientId.trim()) {
      setError('Both name and client_id are required')
      return
    }
    setBusy(true)
    try {
      const created = await wizard.createApp({name: name.trim(), client_id: clientId.trim()})
      setApp(created)
      setStep('client-secret')
    } catch (e) {
      handleError(e)
    } finally {
      setBusy(false)
    }
  }, [name, clientId, wizard])

  const handleClientSecret = useCallback(async () => {
    if (!app) return
    clearError()
    if (!clientSecret.trim()) {
      setError('client_secret is required')
      return
    }
    setBusy(true)
    try {
      const updated = await wizard.setClientSecret(app.id, clientSecret.trim())
      setApp(updated)
      // Issue a wizard nonce so we can show the OAuth + events URLs.
      const nonce = await wizard.issueWizardNonce(app.id)
      setNonceData(nonce)
      setStep('oauth')
    } catch (e) {
      handleError(e)
    } finally {
      setBusy(false)
    }
  }, [app, clientSecret, wizard])

  const handleOpenOAuth = useCallback(() => {
    // Opens the OAuth flow in a popup. Listener above advances state when the
    // BroadcastChannel message arrives.
    if (!app) return
    // Backend expects the same /slack/authorize entry point (it'll bind state
    // to current session) — but routes to the per-app callback via the URL.
    // We rely on the OAuth callback's BroadcastChannel for completion.
    window.open(`/slack/authorize?slack_app_id=${app.id}`, '_blank', 'width=600,height=700')
  }, [app])

  const handleSigningSecret = useCallback(async () => {
    if (!app) return
    clearError()
    if (!signingSecret.trim()) {
      setError('signing_secret is required')
      return
    }
    setBusy(true)
    try {
      const updated = await wizard.setSigningSecret(app.id, signingSecret.trim())
      setApp(updated)
      // Re-issue nonce so the URL the user pastes carries a fresh token.
      const nonce = await wizard.issueWizardNonce(app.id)
      setNonceData(nonce)
      setStep('events-url')
    } catch (e) {
      handleError(e)
    } finally {
      setBusy(false)
    }
  }, [app, signingSecret, wizard])

  const handleBeginTestMessage = useCallback(async () => {
    if (!app) return
    clearError()
    setBusy(true)
    try {
      const status = await wizard.beginTestMessage(app.id, testToken)
      setTestStatus(status.status)
    } catch (e) {
      handleError(e)
    } finally {
      setBusy(false)
    }
  }, [app, testToken, wizard])

  const stepIndex = useMemo(() => {
    const order: Step[] = [
      'register', 'client-secret', 'oauth',
      'signing-secret', 'events-url', 'test-message', 'done',
    ]
    return order.indexOf(step)
  }, [step])

  return (
    <div className="slack-app-wizard">
      <h2>Connect a Slack app ({stepIndex + 1}/7)</h2>
      {error && <div className="error">{error}</div>}

      {step === 'register' && (
        <div>
          <p>
            Create a new Slack app at{' '}
            <a href="https://api.slack.com/apps" target="_blank" rel="noreferrer">
              api.slack.com/apps
            </a>
            , then paste its Client ID below.
          </p>
          <label>
            Display name
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My team's Slack"
              disabled={busy}
            />
          </label>
          <label>
            Client ID
            <input
              type="text"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="1234567890.1234567890"
              disabled={busy}
            />
          </label>
          <button onClick={handleRegister} disabled={busy}>
            {busy ? 'Creating…' : 'Create app'}
          </button>
          {onCancel && <button onClick={onCancel}>Cancel</button>}
        </div>
      )}

      {step === 'client-secret' && app && (
        <div>
          <p>Paste the Client Secret from Slack's "Basic Information" page.</p>
          <label>
            Client Secret
            <input
              type="password"
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              disabled={busy}
            />
          </label>
          <button onClick={handleClientSecret} disabled={busy}>
            {busy ? 'Storing…' : 'Save and continue'}
          </button>
        </div>
      )}

      {step === 'oauth' && app && callbackUrl && (
        <div>
          <p>
            On Slack's "OAuth &amp; Permissions" page, set your <strong>Redirect
            URL</strong> to:
          </p>
          <pre>{callbackUrl}</pre>
          <p>Then click below to launch the OAuth flow:</p>
          <button onClick={handleOpenOAuth} disabled={busy}>
            Authorize workspace
          </button>
          <p className="hint">
            We'll advance automatically once you complete OAuth.
          </p>
        </div>
      )}

      {step === 'signing-secret' && app && (
        <div>
          <p>Paste the Signing Secret from Slack's "Basic Information" page.</p>
          <label>
            Signing Secret
            <input
              type="password"
              value={signingSecret}
              onChange={(e) => setSigningSecret(e.target.value)}
              disabled={busy}
            />
          </label>
          <button onClick={handleSigningSecret} disabled={busy}>
            {busy ? 'Storing…' : 'Save and continue'}
          </button>
        </div>
      )}

      {step === 'events-url' && app && eventsUrl && (
        <div>
          <p>
            On Slack's "Event Subscriptions" page, enable events and set the{' '}
            <strong>Request URL</strong> to:
          </p>
          <pre>{eventsUrl}</pre>
          <p>Subscribe to bot events:</p>
          <ul>
            <li>message.channels</li>
            <li>message.groups</li>
            <li>message.im</li>
            <li>message.mpim</li>
            <li>reaction_added</li>
            <li>reaction_removed</li>
            <li>channel_created / channel_renamed / channel_archived</li>
          </ul>
          <p>
            Click "Save Changes" in Slack — we'll detect the verification ping
            and advance automatically.
          </p>
          <p className="hint">Polling…</p>
        </div>
      )}

      {step === 'test-message' && app && (
        <div>
          <p>
            Almost done. Post the following token in any Slack channel the
            connected user can see:
          </p>
          <pre>{testToken}</pre>
          <button onClick={handleBeginTestMessage} disabled={busy}>
            {busy ? 'Starting…' : 'Start 60-second window'}
          </button>
          {testStatus && <p>Status: {testStatus}</p>}
          <p className="hint">
            Once the message arrives, the app moves to "live" automatically.
          </p>
        </div>
      )}

      {step === 'done' && app && (
        <div>
          <p>Your Slack app is configured and live.</p>
          <p>State: {app.setup_state}</p>
          <button onClick={onCancel}>Close</button>
        </div>
      )}
    </div>
  )
}
