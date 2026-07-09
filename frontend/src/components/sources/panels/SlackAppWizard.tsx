// Multi-step Slack-app setup wizard (slack-changes.md §3.4).
//
// Walks the user through registering a Slack app at api.slack.com, pasting
// client_id / client_secret / signing_secret, completing the OAuth flow,
// configuring the events URL, and posting a test message that flips the
// app to 'live'. Polls /slack/apps/{id}/wizard-status for async
// transitions (url_verification, test-message match).
import {useCallback, useEffect, useMemo, useState} from 'react'
import {useAuth} from '../../../hooks/useAuth'
import {
  type SlackAppNonceResponse,
  type SlackAppResponse,
  useSlackWizard,
} from '../../../hooks/useSlackWizard'
import {styles} from '../styles'

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

// Resume an existing app at the first UNFINISHED step. Keying off setup_state
// alone is wrong for a reopened `draft` app: the client secret may already be
// set and OAuth already completed (e.g. reopened via "Finish setup"), so
// restarting at client-secret re-triggers a redundant OAuth and loops the user.
// The per-secret / auth flags say what's actually left to do.
const resumeStep = (app: SlackAppResponse): Step => {
  if (app.setup_state === 'live' || app.setup_state === 'degraded') return 'done'
  if (app.setup_state === 'signing_verified') return 'test-message'
  if (!app.client_secret_configured) return 'client-secret'
  if (!app.is_authorized) return 'oauth'
  if (!app.signing_secret_configured) return 'signing-secret'
  return 'events-url'
}

const POLL_INTERVAL_MS = 3000

// User token scopes requested during OAuth (sent as `user_scope`; the app
// authenticates as the user, not a bot). Read-only by design: the integration
// ingests Slack content and never posts. Covers channel/group/im/mpim history +
// read, users/emails for mention resolution, and reactions/files for the
// message pipeline. Keep aligned with the README Slack section.
const SLACK_OAUTH_SCOPES = [
  'channels:history',
  'groups:history',
  'im:history',
  'mpim:history',
  'channels:read',
  'groups:read',
  'im:read',
  'mpim:read',
  'users:read',
  'users:read.email',
  'team:read',
  'reactions:read',
  'files:read',
].join(' ')

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
  const {user: authUser} = useAuth()
  const [app, setApp] = useState<SlackAppResponse | null>(initialApp)
  const [step, setStep] = useState<Step>(() => {
    if (!initialApp) return 'register'
    return resumeStep(initialApp)
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
    let consecutiveFailures = 0
    const FAILURE_THRESHOLD = 3
    const tick = async () => {
      try {
        const status = await wizard.getWizardStatus(app.id)
        if (cancelled) return
        consecutiveFailures = 0
        clearError()
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
        consecutiveFailures += 1
        console.warn('Wizard polling tick failed:', e)
        // Surface only persistent failures, not single network blips.
        if (!cancelled && consecutiveFailures >= FAILURE_THRESHOLD) {
          setError((e as Error).message)
        }
      }
    }
    const id = window.setInterval(tick, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [app, step, wizard, onComplete])

  // === Listen for OAuth completion via per-user BroadcastChannel ===
  // The backend's callback HTML posts to slack-oauth-{user.id}; we listen on
  // the same channel name. Source `user.id` from useAuth() — the previous
  // approach (regex over document.cookie) silently fell back to "0" when the
  // session cookie was HttpOnly or named differently, leaving the wizard
  // stuck waiting on an event that never arrived.
  useEffect(() => {
    if (!app || step !== 'oauth' || !authUser) return
    const channel = new BroadcastChannel(`slack-oauth-${authUser.id}`)
    channel.addEventListener('message', async (evt: MessageEvent) => {
      if (evt.data?.type === 'oauth-complete' && app) {
        const fresh = await wizard.getApp(app.id)
        setApp(fresh)
        setStep('signing-secret')
      }
    })
    return () => channel.close()
  }, [app, step, wizard, authUser])

  // The wizard nonce (callback + events URLs) is normally issued in the
  // client-secret step. When resuming an app past that point (via resumeStep),
  // fetch it so the oauth/signing-secret/events-url steps aren't left blank.
  useEffect(() => {
    if (!app || nonceData) return
    if (step !== 'oauth' && step !== 'signing-secret' && step !== 'events-url') return
    let cancelled = false
    wizard
      .issueWizardNonce(app.id)
      .then(n => {
        if (!cancelled) setNonceData(n)
      })
      .catch(e => console.warn('Wizard nonce fetch failed:', e))
    return () => {
      cancelled = true
    }
  }, [app, step, nonceData, wizard])

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

  const handleOpenOAuth = useCallback(async () => {
    // Per-app multi-tenant flow: build Slack's authorize URL using THIS app's
    // client_id and route Slack's redirect to /slack/callback/{slack_app_id}.
    //
    // Mint a fresh signed OAuth state via /slack/apps/{id}/oauth-state and
    // pass it as `&state=...` — the callback validates the signature, the
    // OAuthClientState row, and the bound user.id (CSRF binding fix
    // a5c9746d).
    if (!app || !callbackUrl) return
    clearError()
    setBusy(true)
    try {
      const state = await wizard.issueOAuthState(app.id)
      const params = new URLSearchParams({
        client_id: app.client_id,
        // User-token-only install: the backend stores and uses only the
        // authed_user token (see slack.py oauth exchange + SlackUserCredentials);
        // requesting bot `scope` would force a bot user the app never uses and
        // breaks install with "no bot user to install".
        user_scope: SLACK_OAUTH_SCOPES,
        redirect_uri: callbackUrl,
        state,
      })
      const authUrl = `https://slack.com/oauth/v2/authorize?${params.toString()}`
      window.open(authUrl, '_blank', 'width=600,height=700')
    } catch (e) {
      handleError(e)
    } finally {
      setBusy(false)
    }
  }, [app, callbackUrl, wizard])

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

  const codeBlock = 'block w-full break-all whitespace-pre-wrap font-mono text-xs bg-slate-100 border border-slate-200 rounded px-3 py-2 text-slate-800'

  return (
    <div className="border border-slate-200 rounded-lg p-5 my-4 bg-slate-50/50 space-y-4">
      <div className="flex items-baseline justify-between">
        <h4 className="text-base font-semibold text-slate-800">
          Connect a Slack app
        </h4>
        <span className="text-xs text-slate-500">Step {stepIndex + 1} of 7</span>
      </div>

      <div className="h-1 w-full bg-slate-200 rounded overflow-hidden">
        <div
          className="h-full bg-primary transition-all"
          style={{width: `${((stepIndex + 1) / 7) * 100}%`}}
        />
      </div>

      {error && <div className={styles.formError}>{error}</div>}

      {step === 'register' && (
        <div className={styles.form}>
          <p className="text-sm text-slate-600">
            Create a new Slack app at{' '}
            <a
              className="text-primary hover:underline"
              href="https://api.slack.com/apps"
              target="_blank"
              rel="noreferrer"
            >
              api.slack.com/apps
            </a>
            , then paste its Client ID below.
          </p>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Display name</label>
            <input
              type="text"
              className={styles.formInput}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My team's Slack"
              disabled={busy}
            />
          </div>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Client ID</label>
            <input
              type="text"
              className={styles.formInput}
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="1234567890.1234567890"
              disabled={busy}
            />
          </div>
          <div className={styles.formActions}>
            {onCancel && (
              <button
                type="button"
                className={styles.btnCancel}
                onClick={onCancel}
              >
                Cancel
              </button>
            )}
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={handleRegister}
              disabled={busy}
            >
              {busy ? 'Creating…' : 'Create app'}
            </button>
          </div>
        </div>
      )}

      {step === 'client-secret' && app && (
        <div className={styles.form}>
          <p className="text-sm text-slate-600">
            Paste the Client Secret from Slack's "Basic Information" page.
          </p>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Client Secret</label>
            <input
              type="password"
              className={styles.formInput}
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className={styles.formActions}>
            {onCancel && (
              <button
                type="button"
                className={styles.btnCancel}
                onClick={onCancel}
              >
                Cancel
              </button>
            )}
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={handleClientSecret}
              disabled={busy}
            >
              {busy ? 'Storing…' : 'Save and continue'}
            </button>
          </div>
        </div>
      )}

      {step === 'oauth' && app && callbackUrl && (
        <div className={styles.form}>
          <p className="text-sm text-slate-600">
            On Slack's "OAuth &amp; Permissions" page, set your{' '}
            <strong>Redirect URL</strong> to:
          </p>
          <code className={codeBlock}>{callbackUrl}</code>
          <p className="text-sm text-slate-600">
            Then click below to launch the OAuth flow:
          </p>
          <div className={styles.formActions}>
            {onCancel && (
              <button
                type="button"
                className={styles.btnCancel}
                onClick={onCancel}
              >
                Cancel
              </button>
            )}
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={handleOpenOAuth}
              disabled={busy}
            >
              Authorize workspace
            </button>
          </div>
          <p className={styles.formHint}>
            We'll advance automatically once you complete OAuth.
          </p>
        </div>
      )}

      {step === 'signing-secret' && app && (
        <div className={styles.form}>
          <p className="text-sm text-slate-600">
            Paste the Signing Secret from Slack's "Basic Information" page.
          </p>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Signing Secret</label>
            <input
              type="password"
              className={styles.formInput}
              value={signingSecret}
              onChange={(e) => setSigningSecret(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className={styles.formActions}>
            {onCancel && (
              <button
                type="button"
                className={styles.btnCancel}
                onClick={onCancel}
              >
                Cancel
              </button>
            )}
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={handleSigningSecret}
              disabled={busy}
            >
              {busy ? 'Storing…' : 'Save and continue'}
            </button>
          </div>
        </div>
      )}

      {step === 'events-url' && app && eventsUrl && (
        <div className={styles.form}>
          <p className="text-sm text-slate-600">
            On Slack's "Event Subscriptions" page, enable events and set the{' '}
            <strong>Request URL</strong> to:
          </p>
          <code className={codeBlock}>{eventsUrl}</code>
          <p className="text-sm font-medium text-slate-700">
            Subscribe to bot events:
          </p>
          <ul className="list-disc list-inside text-sm text-slate-600 space-y-0.5 ml-2">
            <li>message.channels</li>
            <li>message.groups</li>
            <li>message.im</li>
            <li>message.mpim</li>
            <li>reaction_added</li>
            <li>reaction_removed</li>
            <li>channel_created / channel_renamed / channel_archived</li>
          </ul>
          <p className="text-sm text-slate-600">
            Click "Save Changes" in Slack — we'll detect the verification ping
            and advance automatically.
          </p>
          <p className={styles.formHint}>Polling…</p>
        </div>
      )}

      {step === 'test-message' && app && (
        <div className={styles.form}>
          <p className="text-sm text-slate-600">
            Almost done. Post the following token in any Slack channel the
            connected user can see:
          </p>
          <code className={codeBlock}>{testToken}</code>
          <div className={styles.formActions}>
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={handleBeginTestMessage}
              disabled={busy}
            >
              {busy ? 'Starting…' : 'Start 60-second window'}
            </button>
          </div>
          {testStatus && (
            <p className="text-sm text-slate-600">Status: {testStatus}</p>
          )}
          <p className={styles.formHint}>
            Once the message arrives, the app moves to "live" automatically.
          </p>
        </div>
      )}

      {step === 'done' && app && (
        <div className={styles.form}>
          <p className="text-sm text-slate-700">
            Your Slack app is configured and live.
          </p>
          <p className="text-sm text-slate-600">State: {app.setup_state}</p>
          <div className={styles.formActions}>
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={onCancel}
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
