// Multi-step Slack-app setup wizard (slack-changes.md §3.4).
//
// Walks the user through registering a Slack app at api.slack.com, pasting
// client_id / client_secret / signing_secret, completing the OAuth flow,
// configuring the events URL, and posting a first message, whose ingestion
// flips the app to 'live'. Polls /slack/apps/{id}/wizard-status for async
// transitions (url_verification, first ingested event).
import {useCallback, useEffect, useState} from 'react'
import {useAuth} from '../../../hooks/useAuth'
import {
  type SlackAppCreate,
  type SlackAppNonceResponse,
  type SlackAppResponse,
  useSlackWizard,
} from '../../../hooks/useSlackWizard'
import { Button, Disclosure, Field, FormActions, Hint, Input, Modal, Notice, CopyButton, NewTabLink } from '@/components/shared/ui'
import { PopupBlockedNotice, openPopup } from '../shared'

// The server's defaults; the Advanced fields hold only a Slack-compatible server's URLs.
const SLACK_URL_DEFAULTS = {
  authorize_url: 'https://slack.com/oauth/v2/authorize',
  token_url: 'https://slack.com/api/oauth.v2.access',
  api_base_url: 'https://slack.com/api',
}

// The register step's fields as the form shows them for `app`: '' for a Slack URL.
const formFields = (app: SlackAppResponse | null) => ({
  client_id: app?.client_id ?? '',
  ...Object.fromEntries(Object.entries(SLACK_URL_DEFAULTS).map(([field, slackUrl]) => {
    const url = app?.[field as keyof typeof SLACK_URL_DEFAULTS]
    return [field, url && url !== slackUrl ? url : '']
  })) as Record<keyof typeof SLACK_URL_DEFAULTS, string>,
})

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
  | 'test-message'    // user posts any message; we poll until it's ingested
  | 'done'

const STEPS: Step[] = ['register', 'client-secret', 'oauth', 'signing-secret', 'events-url', 'test-message', 'done']

// An app with no workspace needs OAuth first, even if it was live before its workspace was removed.
const resumeStep = (app: SlackAppResponse, connected: boolean): Step => {
  if (!connected && app.client_secret_configured) return 'oauth'
  if (app.setup_state === 'live' || app.setup_state === 'degraded') return 'done'
  if (app.setup_state === 'signing_verified') return 'test-message'
  if (!app.client_secret_configured) return 'client-secret'
  if (!app.signing_secret_configured) return 'signing-secret'
  return 'events-url'
}

export const POLL_INTERVAL_MS = 3000
export const MESSAGE_HINT_AFTER_MS = 30_000

const SUGGESTED_TEST_MESSAGE = 'Testing the Memory connection'

const submit = (handler: () => void) => (e: React.FormEvent) => {
  e.preventDefault()
  handler()
}

const AwaitingMessage = () => {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), MESSAGE_HINT_AFTER_MS)
    return () => clearTimeout(timer)
  }, [])
  return (
    <>
      <p role="status" className="text-sm text-slate-600">Waiting for your message…</p>
      {slow && (
        <Notice tone="warning">
          Still nothing from Slack. Usually the bot isn't in that channel, or
          the app isn't subscribed to the message.channels (public) or
          message.groups (private) bot events on step 5.
        </Notice>
      )}
    </>
  )
}

// Hand-selecting a long URL is hard on touch screens, so each value gets a copy button.
const CopyableCode = ({ value, label }: { value: string; label: string }) => (
  <div className="flex items-start gap-2">
    <code className="block flex-1 min-w-0 break-all whitespace-pre-wrap font-mono text-xs bg-slate-100 border border-slate-200 rounded px-3 py-2 text-slate-800">{value}</code>
    <CopyButton text={value} label={label} />
  </div>
)

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
  const [blockedOAuthUrl, setBlockedOAuthUrl] = useState<string | null>(null)
  const [connected, setConnected] = useState(initialApp !== null && initialAppConnected)

  const [initialFields] = useState(() => formFields(initialApp))
  const [name, setName] = useState(initialApp?.name ?? '')
  const [clientId, setClientId] = useState(initialFields.client_id)
  const [authorizeUrl, setAuthorizeUrl] = useState(initialFields.authorize_url)
  const [tokenUrl, setTokenUrl] = useState(initialFields.token_url)
  const [apiBaseUrl, setApiBaseUrl] = useState(initialFields.api_base_url)
  const [clientSecret, setClientSecret] = useState('')
  const [signingSecret, setSigningSecret] = useState('')

  const [nonceData, setNonceData] = useState<SlackAppNonceResponse | null>(null)

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
  // setup_state: draft → (url_verification) → signing_verified → (first ingested event) → live
  useEffect(() => {
    if (!app) return
    // A verified URL has nothing left to wait for, and polling would bounce a user who came back to it.
    if (!(step === 'events-url' && app.setup_state === 'draft') && step !== 'test-message') return
    let cancelled = false
    let consecutiveFailures = 0
    const FAILURE_THRESHOLD = 3
    const tick = async () => {
      try {
        const status = await wizard.getWizardStatus(app.id)
        if (cancelled) return
        consecutiveFailures = 0
        clearError()
        if (status.setup_state === 'live') {
          const fresh = await wizard.getApp(app.id)
          if (cancelled) return
          setApp(fresh)
          setStep('done')
          return
        }
        if (step === 'events-url' && status.setup_state !== 'draft') {
          setApp({...app, setup_state: status.setup_state})
          setStep('test-message')
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
        setConnected(true)
        setStep(resumeStep(fresh, true))
      }
    })
    return () => channel.close()
  }, [app, step, wizard, authUser])

  const handleError = (e: unknown) => setError((e as Error).message)
  const clearError = () => setError(null)

  const handleRegister = useCallback(async () => {
    clearError()
    if (!name.trim() || !clientId.trim()) {
      setError('Display name and client ID are required')
      return
    }
    setBusy(true)
    try {
      const fields = {
        client_id: clientId.trim(),
        authorize_url: authorizeUrl.trim(),
        token_url: tokenUrl.trim(),
        api_base_url: apiBaseUrl.trim(),
      }
      // Only what the user changed is sent, so the server's checks apply to it
      // alone; an emptied URL (null) resets to Slack's.
      const saved = formFields(app)
      const changed = Object.fromEntries(
        Object.entries(fields)
          .filter(([field, value]) => value !== saved[field as keyof typeof saved])
          .map(([field, value]) => [field, value || null]),
      )
      setApp(app
        ? await wizard.updateApp(app.id, { name: name.trim(), ...changed })
        : await wizard.createApp({ name: name.trim(), ...changed } as SlackAppCreate))
      setStep('client-secret')
    } catch (e) {
      handleError(e)
    } finally {
      setBusy(false)
    }
  }, [app, name, clientId, authorizeUrl, tokenUrl, apiBaseUrl, wizard])

  const handleClientSecret = useCallback(async () => {
    if (!app) return
    clearError()
    if (!clientSecret.trim()) {
      if (app.client_secret_configured) setStep('oauth')
      else setError('Client secret is required')
      return
    }
    setBusy(true)
    try {
      setApp(await wizard.setClientSecret(app.id, clientSecret.trim()))
      setClientSecret('')
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
      const { blockedUrl } = await openPopup(async () => {
        const state = await wizard.issueOAuthState(app.id)
        // Slack-compatible servers issue user tokens only, so they get no bot `scope`.
        const scopes: Record<string, string> = app.authorize_url === SLACK_URL_DEFAULTS.authorize_url
          ? { scope: SLACK_OAUTH_SCOPES, user_scope: SLACK_OAUTH_SCOPES }
          : { user_scope: SLACK_OAUTH_SCOPES }
        const params = new URLSearchParams({
          client_id: app.client_id,
          ...scopes,
          redirect_uri: callbackUrl,
          state,
        })
        return `${app.authorize_url}?${params.toString()}`
      })
      setBlockedOAuthUrl(blockedUrl)
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
      if (app.signing_secret_configured) setStep('events-url')
      else setError('Signing secret is required')
      return
    }
    setBusy(true)
    try {
      setApp(await wizard.setSigningSecret(app.id, signingSecret.trim()))
      setSigningSecret('')
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

  const stepIndex = STEPS.indexOf(step)
  const back = () => {
    clearError()
    setStep(STEPS[stepIndex - 1])
  }
  const leaveButtons = (
    <>
      {stepIndex > 0 && <Button type="button" onClick={back}>Back</Button>}
      <Button type="button" onClick={onCancel}>Cancel</Button>
    </>
  )

  const finish = () => (app ? onComplete?.(app) : onCancel())

  return (
    <Modal title={initialApp ? `Finish setting up ${initialApp.name}` : 'Connect a Slack app'}
      onClose={step === 'done' ? finish : onCancel}>
      <div className="space-y-4">
        <p className="text-xs text-slate-500">Step {stepIndex + 1} of 7</p>

        <div role="progressbar" aria-label="Setup progress" aria-valuemin={1} aria-valuemax={7} aria-valuenow={stepIndex + 1}
          className="h-1 w-full bg-slate-200 rounded overflow-hidden">
          <div
            className="h-full bg-primary transition-all"
            style={{width: `${((stepIndex + 1) / 7) * 100}%`}}
          />
        </div>

        {error && <Notice tone="error">{error}</Notice>}

        {step === 'register' && (
          <form onSubmit={submit(handleRegister)} className="space-y-4">
            <p className="text-sm text-slate-600">
              Create a new Slack app at{' '}
              <NewTabLink className="text-primary hover:underline" href="https://api.slack.com/apps">
                api.slack.com/apps
              </NewTabLink>
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
                readOnly={connected}
                disabled={busy}
              />
            </Field>
            {connected && (
              <Hint>
                A workspace is connected through this app, so its client ID can't change. To use a different Slack app, delete this app and register it again.
              </Hint>
            )}
            <Disclosure summary="Advanced">
              <p className="text-sm text-slate-600 mb-3">For a Slack-compatible server. Leave empty to use Slack.</p>
              <Field label="Authorize URL">
                <Input type="url" value={authorizeUrl} onChange={(e) => setAuthorizeUrl(e.target.value)}
                  placeholder={SLACK_URL_DEFAULTS.authorize_url} disabled={busy} />
              </Field>
              <Field label="Token URL">
                <Input type="url" value={tokenUrl} onChange={(e) => setTokenUrl(e.target.value)}
                  placeholder={SLACK_URL_DEFAULTS.token_url} disabled={busy} />
              </Field>
              <Field label="API base URL">
                <Input type="url" value={apiBaseUrl} onChange={(e) => setApiBaseUrl(e.target.value)}
                  placeholder={SLACK_URL_DEFAULTS.api_base_url} disabled={busy} />
              </Field>
            </Disclosure>
            <FormActions>
              {leaveButtons}
              <Button variant="primary" type="submit" disabled={busy}>
                {app ? (busy ? 'Saving…' : 'Save and continue') : (busy ? 'Creating…' : 'Create app')}
              </Button>
            </FormActions>
          </form>
        )}

        {step === 'client-secret' && app && (
          <form onSubmit={submit(handleClientSecret)} className="space-y-4">
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
            {app.client_secret_configured && <Hint>A client secret is saved. Paste a new one to replace it, or continue to keep it.</Hint>}
            <FormActions>
              {leaveButtons}
              <Button variant="primary" type="submit" disabled={busy}>
                {busy ? 'Storing…' : 'Save and continue'}
              </Button>
            </FormActions>
          </form>
        )}

        {step === 'oauth' && app && callbackUrl && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">
              On Slack's "OAuth &amp; Permissions" page, set your{' '}
              <strong>Redirect URL</strong> to:
            </p>
            <CopyableCode value={callbackUrl} label="Copy redirect URL" />
            <p className="text-sm text-slate-600">
              Then click below to launch the OAuth flow:
            </p>
            <PopupBlockedNotice url={blockedOAuthUrl} label="Open Slack authorization" onDismiss={() => setBlockedOAuthUrl(null)} />
            <FormActions>
              {leaveButtons}
              <Button variant={connected ? undefined : 'primary'}
                type="button"
                onClick={handleOpenOAuth}
                disabled={busy}
              >
                Authorize workspace
              </Button>
              {connected && <Button variant="primary" type="button" onClick={() => setStep('signing-secret')}>Continue</Button>}
            </FormActions>
            <Hint>
              {connected ? 'A workspace is already connected through this app.' : "We'll advance automatically once you complete OAuth."}
            </Hint>
          </div>
        )}

        {step === 'signing-secret' && app && (
          <form onSubmit={submit(handleSigningSecret)} className="space-y-4">
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
            {app.signing_secret_configured && (
              <Hint>A signing secret is saved. Continue to keep it; a different one has to be verified by Slack again.</Hint>
            )}
            <FormActions>
              {leaveButtons}
              <Button variant="primary" type="submit" disabled={busy}>
                {busy ? 'Storing…' : 'Save and continue'}
              </Button>
            </FormActions>
          </form>
        )}

        {step === 'events-url' && app && eventsUrl && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">
              On Slack's "Event Subscriptions" page, enable events and set the{' '}
              <strong>Request URL</strong> to:
            </p>
            <CopyableCode value={eventsUrl} label="Copy request URL" />
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
              <li>channel_created / channel_rename / channel_archive / channel_unarchive</li>
            </ul>
            <p className="text-sm text-slate-600">
              Click "Save Changes" in Slack — we'll detect the verification ping
              and advance automatically.
            </p>
            <Hint>{app.setup_state === 'draft' ? 'Polling…' : 'Slack has verified the events URL.'}</Hint>
            <FormActions>
              {leaveButtons}
              {app.setup_state !== 'draft' && (
                <Button variant="primary" type="button" onClick={() => setStep('test-message')}>Continue</Button>
              )}
            </FormActions>
          </div>
        )}

        {step === 'test-message' && app && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">
              Almost done. Post a message in any Slack channel the bot has
              joined (invite the app to the channel first), for example:
            </p>
            <CopyableCode value={SUGGESTED_TEST_MESSAGE} label="Copy message" />
            <Hint>
              The first message we receive switches events on: the app goes
              live and this wizard finishes by itself. Inviting the bot to a
              channel posts a join message, so that alone can be enough.
            </Hint>
            <AwaitingMessage />
            <FormActions>{leaveButtons}</FormActions>
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
