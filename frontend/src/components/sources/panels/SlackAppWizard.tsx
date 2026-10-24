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
import { Button, Field, FormActions, Hint, Input, Modal, Notice } from '@/components/shared/ui'

interface SlackAppWizardProps {
  initialApp?: SlackAppResponse | null
  /** Whether `initialApp` has been through OAuth, i.e. some workspace is connected through it. */
  initialAppConnected?: boolean
  /** Called when the user closes the wizard after the app went live. */
  onComplete?: (app: SlackAppResponse) => void
  onCancel: () => void
}

type Step =
  | 'register'        // open api.slack.com, paste client_id
  | 'client-secret'   // paste client_secret
  | 'oauth'           // launch OAuth popup, wait for credentials
  | 'signing-secret'  // paste signing_secret
  | 'events-url'      // user pastes events_url into Slack; we poll for url_verification
  | 'test-message'    // user posts the token; we poll for match
  | 'done'

const resumeStep = (app: SlackAppResponse, connected: boolean): Step => {
  if (app.setup_state === 'live' || app.setup_state === 'degraded') return 'done'
  if (app.setup_state === 'signing_verified') return 'test-message'
  if (!app.client_secret_configured) return 'client-secret'
  if (!connected) return 'oauth'
  if (!app.signing_secret_configured) return 'signing-secret'
  return 'events-url'
}

const POLL_INTERVAL_MS = 3000

// OAuth scopes requested for both bot and user tokens. Read-only across
// the workspace surface (channels/groups/im/mpim history + read), users +
// emails for mention resolution, reactions/files for the existing message
// processing pipeline. Keep aligned with the backend's expectations.
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
  initialAppConnected = true,
  onComplete,
  onCancel,
}: SlackAppWizardProps) => {
  const wizard = useSlackWizard()
  const {user: authUser} = useAuth()
  const [app, setApp] = useState<SlackAppResponse | null>(initialApp)
  const [step, setStep] = useState<Step>(() => initialApp ? resumeStep(initialApp, initialAppConnected) : 'register')
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

  // A resumed wizard lands on these steps without the nonce that earlier steps issued.
  useEffect(() => {
    if (!app || nonceData || (step !== 'oauth' && step !== 'events-url')) return
    let cancelled = false
    wizard.issueWizardNonce(app.id)
      .then(nonce => { if (!cancelled) setNonceData(nonce) })
      .catch(e => { if (!cancelled) setError((e as Error).message) })
    return () => { cancelled = true }
  }, [app, nonceData, step, wizard])

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
  }, [app, step, wizard])

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
        scope: SLACK_OAUTH_SCOPES,
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

  const finish = () => (app ? onComplete?.(app) : onCancel())

  return (
    <Modal title={initialApp ? `Finish setting up ${initialApp.name}` : 'Connect a Slack app'}
      onClose={step === 'done' ? finish : onCancel}>
      <div className="space-y-4">
        <p className="text-xs text-slate-500">Step {stepIndex + 1} of 7</p>

        <div className="h-1 w-full bg-slate-200 rounded overflow-hidden">
          <div
            className="h-full bg-primary transition-all"
            style={{width: `${((stepIndex + 1) / 7) * 100}%`}}
          />
        </div>

        {error && <Notice tone="error">{error}</Notice>}

        {step === 'register' && (
          <div className="space-y-4">
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
            <Field label="Display name">
              <Input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="My team's Slack"
                disabled={busy}
              />
            </Field>
            <Field label="Client ID">
              <Input
                type="text"
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                placeholder="1234567890.1234567890"
                disabled={busy}
              />
            </Field>
            <FormActions>
              <Button type="button" onClick={onCancel}>Cancel</Button>
              <Button variant="primary"
                type="button"
                onClick={handleRegister}
                disabled={busy}
              >
                {busy ? 'Creating…' : 'Create app'}
              </Button>
            </FormActions>
          </div>
        )}

        {step === 'client-secret' && app && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">
              Paste the Client Secret from Slack's "Basic Information" page.
            </p>
            <Field label="Client secret">
              <Input
                type="password"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                disabled={busy}
              />
            </Field>
            <FormActions>
              <Button type="button" onClick={onCancel}>Cancel</Button>
              <Button variant="primary"
                type="button"
                onClick={handleClientSecret}
                disabled={busy}
              >
                {busy ? 'Storing…' : 'Save and continue'}
              </Button>
            </FormActions>
          </div>
        )}

        {step === 'oauth' && app && callbackUrl && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">
              On Slack's "OAuth &amp; Permissions" page, set your{' '}
              <strong>Redirect URL</strong> to:
            </p>
            <code className={codeBlock}>{callbackUrl}</code>
            <p className="text-sm text-slate-600">
              Then click below to launch the OAuth flow:
            </p>
            <FormActions>
              <Button type="button" onClick={onCancel}>Cancel</Button>
              <Button variant="primary"
                type="button"
                onClick={handleOpenOAuth}
                disabled={busy}
              >
                Authorize workspace
              </Button>
            </FormActions>
            <Hint>
              We'll advance automatically once you complete OAuth.
            </Hint>
          </div>
        )}

        {step === 'signing-secret' && app && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">
              Paste the Signing Secret from Slack's "Basic Information" page.
            </p>
            <Field label="Signing secret">
              <Input
                type="password"
                value={signingSecret}
                onChange={(e) => setSigningSecret(e.target.value)}
                disabled={busy}
              />
            </Field>
            <FormActions>
              <Button type="button" onClick={onCancel}>Cancel</Button>
              <Button variant="primary"
                type="button"
                onClick={handleSigningSecret}
                disabled={busy}
              >
                {busy ? 'Storing…' : 'Save and continue'}
              </Button>
            </FormActions>
          </div>
        )}

        {step === 'events-url' && app && eventsUrl && (
          <div className="space-y-4">
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
            <Hint>Polling…</Hint>
            <FormActions>
              <Button type="button" onClick={onCancel}>Cancel</Button>
            </FormActions>
          </div>
        )}

        {step === 'test-message' && app && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">
              Almost done. Post the following token in any Slack channel the
              connected user can see:
            </p>
            <code className={codeBlock}>{testToken}</code>
            <FormActions>
              <Button type="button" onClick={onCancel}>Cancel</Button>
              <Button variant="primary"
                type="button"
                onClick={handleBeginTestMessage}
                disabled={busy}
              >
                {busy ? 'Starting…' : 'Start 60-second window'}
              </Button>
            </FormActions>
            {testStatus && (
              <p className="text-sm text-slate-600">Status: {testStatus}</p>
            )}
            <Hint>
              Once the message arrives, the app moves to "live" automatically.
            </Hint>
          </div>
        )}

        {step === 'done' && app && (
          <div className="space-y-4">
            <p className="text-sm text-slate-700">
              Your Slack app is configured and live.
            </p>
            <FormActions>
              <Button variant="primary"
                type="button"
                onClick={finish}
              >
                Done
              </Button>
            </FormActions>
          </div>
        )}
      </div>
    </Modal>
  )
}
