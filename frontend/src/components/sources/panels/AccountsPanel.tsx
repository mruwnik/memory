import { useState, useEffect, useCallback, useRef } from 'react'
import { useSources, GithubAccount, LLMKeyChoice, GoogleAccount, GoogleOAuthConfig, GoogleAvailableScopes } from '@/hooks/useSources'
import { FileButton, PopupBlockedNotice, SourceRow, StatusBadge, SyncFailingBadge, SyncFailure, formatRelativeTime, openPopup } from '../shared'
import { LLMKeyField, llmKeyLabel } from '../LLMKeyField'
import { Button, Card, ConfirmButton, DetailList, Disclosure, EmptyState, ErrorState, Field, FormActions, Hint, Input, List, LoadingState, Modal, Notice, Row, Section, Select, Textarea, useActionNotice } from '@/components/shared/ui'
import { useSourcesContext } from '../context'
import { useAuth } from '@/hooks/useAuth'
import { GoogleSetupInstructions } from './GoogleSetupInstructions'

/** Both providers' accounts, or only one's where a page is about that provider. */
export const AccountsPanel = ({ provider }: { provider?: 'github' | 'google' }) => {
  const {
    listGithubAccounts, createGithubAccount, updateGithubAccount, deleteGithubAccount, validateGithubAccount,
    listGoogleAccounts, getGoogleAvailableScopes, getGoogleAuthUrl, deleteGoogleAccount, reauthorizeGoogleAccount,
    getGoogleOAuthConfig, uploadGoogleOAuthConfig, deleteGoogleOAuthConfig
  } = useSources()
  const { userId, refreshCounts } = useSourcesContext()
  const { user: authUser } = useAuth()

  const [githubAccounts, setGithubAccounts] = useState<GithubAccount[]>([])
  const [googleAccounts, setGoogleAccounts] = useState<GoogleAccount[]>([])
  const [oauthConfig, setOauthConfig] = useState<GoogleOAuthConfig | null | undefined>(undefined)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showGithubForm, setShowGithubForm] = useState(false)
  const [editingGithubAccount, setEditingGithubAccount] = useState<GithubAccount | null>(null)
  const [uploadingConfig, setUploadingConfig] = useState(false)
  // Google scope selection
  const [showScopeModal, setShowScopeModal] = useState(false)
  const [availableScopes, setAvailableScopes] = useState<GoogleAvailableScopes | null>(null)
  const [selectedScopes, setSelectedScopes] = useState<string[]>(['drive', 'calendar', 'gmail_read', 'gmail_send'])
  const [reauthorizingAccountId, setReauthorizingAccountId] = useState<number | null>(null)
  const [validations, setValidations] = useState<Record<number, { status: string; message: string }>>({})
  const { run, notice } = useActionNotice()
  const [googleWait, setGoogleWait] = useState<'waiting' | 'timeout' | null>(null)
  const [blockedGoogleUrl, setBlockedGoogleUrl] = useState<string | null>(null)

  const configInputRef = useRef<HTMLInputElement>(null)
  const waitTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const stopWaiting = (outcome: 'timeout' | null = null) => {
    if (waitTimeoutRef.current) clearTimeout(waitTimeoutRef.current)
    waitTimeoutRef.current = null
    setGoogleWait(outcome)
  }

  useEffect(() => () => { if (waitTimeoutRef.current) clearTimeout(waitTimeoutRef.current) }, [])

  const loadData = useCallback(async () => {
    try {
      const [githubData, googleData, configData] = await Promise.all([
        listGithubAccounts(userId),
        listGoogleAccounts(userId),
        getGoogleOAuthConfig()
      ])
      setGithubAccounts(githubData)
      setGoogleAccounts(googleData)
      setOauthConfig(configData)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load accounts')
    } finally {
      setLoading(false)
      refreshCounts()
    }
  }, [listGithubAccounts, listGoogleAccounts, getGoogleOAuthConfig, userId, refreshCounts])

  useEffect(() => { loadData() }, [loadData])

  // GitHub handlers
  const handleCreateGithubAccount = async (data: any) => {
    await createGithubAccount(data)
    setShowGithubForm(false)
    loadData()
  }

  const handleUpdateGithubAccount = async (data: any) => {
    if (editingGithubAccount) {
      await updateGithubAccount(editingGithubAccount.id, data)
      setEditingGithubAccount(null)
      loadData()
    }
  }

  const handleDeleteGithubAccount = (id: number) => run(async () => {
    await deleteGithubAccount(id)
    loadData()
  })

  const handleToggleGithubActive = (account: GithubAccount) => run(async () => {
    await updateGithubAccount(account.id, { active: !account.active })
    loadData()
  })

  const handleValidateGithubAccount = async (id: number) => {
    const result = await validateGithubAccount(id)
      .catch(e => ({ status: 'error', message: e instanceof Error ? e.message : 'Validation failed' }))
    setValidations(prev => ({ ...prev, [id]: result }))
  }

  // Google handlers
  const handleConfigUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return

    setUploadingConfig(true)
    await run(async () => setOauthConfig(await uploadGoogleOAuthConfig(file)))
    setUploadingConfig(false)
  }

  const handleDeleteConfig = () => run(async () => {
    await deleteGoogleOAuthConfig()
    setOauthConfig(null)
  })

  // Load available scopes and show the modal, to connect a new account (null) or re-authorize one.
  const openScopeModal = (accountId: number | null) => run(async () => {
    if (!availableScopes) setAvailableScopes(await getGoogleAvailableScopes())
    setReauthorizingAccountId(accountId)
    setShowScopeModal(true)
  })

  const handleConnectGoogle = () => openScopeModal(null)

  // Google's callback page posts here once the account is saved (google_callback in google_drive.py).
  // The popup's closed state isn't used: Google's COOP headers can make it read closed mid-sign-in.
  useEffect(() => {
    if (googleWait !== 'waiting' || !authUser) return
    const channel = new BroadcastChannel(`google-oauth-${authUser.id}`)
    channel.onmessage = event => {
      if (event.data?.type !== 'oauth-complete') return
      stopWaiting()
      setBlockedGoogleUrl(null)
      run(async () => setGoogleAccounts(await listGoogleAccounts(userId)))
    }
    return () => channel.close()
  }, [googleWait, authUser, run, listGoogleAccounts, userId])

  const handleScopeSelectionConfirm = () => {
    setShowScopeModal(false)
    stopWaiting()
    return run(async () => {
      const { blockedUrl } = await openPopup(async () => (reauthorizingAccountId !== null
        ? await reauthorizeGoogleAccount(reauthorizingAccountId, selectedScopes)
        : await getGoogleAuthUrl(selectedScopes)).authorization_url)
      setBlockedGoogleUrl(blockedUrl)
      setGoogleWait('waiting')
      waitTimeoutRef.current = setTimeout(() => stopWaiting('timeout'), 5 * 60_000)
    })
  }

  const toggleScope = (scopeKey: string) => {
    setSelectedScopes(prev =>
      prev.includes(scopeKey)
        ? prev.filter(s => s !== scopeKey)
        : [...prev, scopeKey]
    )
  }

  const handleDeleteGoogleAccount = (id: number) => run(async () => {
    await deleteGoogleAccount(id)
    loadData()
  })

  const handleReauthorizeGoogle = (id: number) => openScopeModal(id)

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  const uploadButton = (label: string) => (
    <FileButton inputRef={configInputRef} label={label} busy={uploadingConfig} accept=".json"
      onChange={handleConfigUpload} variant="secondary" size="sm" />
  )

  return (
    <>
      {notice}
      <PopupBlockedNotice url={blockedGoogleUrl} label="Open Google sign-in" onDismiss={() => setBlockedGoogleUrl(null)} />
      {googleWait === 'waiting' && <Notice tone="info" onDismiss={() => stopWaiting()}>Waiting for Google. Finish signing in in the popup.</Notice>}
      {googleWait === 'timeout' && (
        <Notice tone="warning" onDismiss={() => setGoogleWait(null)}>
          Stopped waiting for Google. If you finish signing in later, reload the page to see the account.
        </Notice>
      )}
      {provider !== 'google' && (
        <Section title="GitHub accounts" actions={<Button variant="primary" onClick={() => setShowGithubForm(true)}>Add account</Button>}>
          {githubAccounts.length === 0 ? (
            <EmptyState
              message="No GitHub accounts configured. Add an account to track repositories."
              actionLabel="Add GitHub account"
              onAction={() => setShowGithubForm(true)}
            />
          ) : (
            <List>
              {githubAccounts.map(account => (
                <SourceRow
                  key={account.id}
                  title={account.name}
                  subtitle={account.auth_type === 'pat' ? 'Personal access token' : 'GitHub App'}
                  meta={[account.repos.length > 0 && `${account.repos.length} repos tracked`, `LLM: ${llmKeyLabel(account.llm_key)}`]}
                  active={account.active}
                  lastSyncAt={account.last_sync_at}
                  onToggleActive={() => handleToggleGithubActive(account)}
                  onEdit={() => setEditingGithubAccount(account)}
                  onDelete={() => handleDeleteGithubAccount(account.id)}
                  additionalActions={
                    <Button size="sm" onClick={() => handleValidateGithubAccount(account.id)}>Validate</Button>
                  }
                >
                  {validations[account.id] && (
                    <Notice tone={validations[account.id].status === 'success' ? 'success' : 'error'}
                      onDismiss={() => setValidations(({ [account.id]: _, ...rest }) => rest)}>
                      {validations[account.id].message}
                    </Notice>
                  )}
                </SourceRow>
              ))}
            </List>
          )}
        </Section>
      )}

      {showGithubForm && (
        <GitHubAccountForm
          onSubmit={handleCreateGithubAccount}
          onCancel={() => setShowGithubForm(false)}
        />
      )}

      {editingGithubAccount && (
        <GitHubAccountForm
          account={editingGithubAccount}
          onSubmit={handleUpdateGithubAccount}
          onCancel={() => setEditingGithubAccount(null)}
        />
      )}

      {provider !== 'github' && (
        <Section title="Google accounts" actions={oauthConfig && <Button variant="primary" onClick={handleConnectGoogle}>Connect account</Button>}>
          <GoogleSetupInstructions />
          {!oauthConfig ? (
            <Card title="OAuth configuration required"
              description="Upload your Google OAuth credentials JSON file to enable Google integrations (Gmail, Calendar, Drive).">
              <Hint className="mb-4">Get this from the Google Cloud Console under APIs & Services → Credentials.</Hint>
              {uploadButton('Upload credentials JSON')}
            </Card>
          ) : (
            <>
              <Disclosure summary="OAuth configuration">
                <div className="space-y-3">
                  <DetailList items={[
                    ['Project', oauthConfig.project_id],
                    ['Client ID', `${oauthConfig.client_id.substring(0, 20)}...`],
                  ]} />
                  <div className="flex flex-wrap gap-2">
                    {uploadButton('Replace config')}
                    <ConfirmButton confirm="Delete the Google OAuth configuration?" onConfirm={handleDeleteConfig}>Delete config</ConfirmButton>
                  </div>
                </div>
              </Disclosure>

              {googleAccounts.length === 0 ? (
                <EmptyState
                  message="No Google accounts connected. Connect an account to use Gmail, Calendar, or Drive."
                  actionLabel="Connect Google account"
                  onAction={handleConnectGoogle}
                />
              ) : (
                <List>
                  {googleAccounts.map(account => (
                    <Row
                      key={account.id}
                      title={account.name}
                      muted={!account.active}
                      badges={<><StatusBadge active={account.active} /><SyncFailingBadge status={account} /></>}
                      meta={[account.email, `Last sync: ${formatRelativeTime(account.last_sync_at)}`]}
                      actions={<>
                        <Button size="sm" onClick={() => handleReauthorizeGoogle(account.id)}>Re-authorize</Button>
                        <ConfirmButton confirm={`Disconnect ${account.email}?`} onConfirm={() => handleDeleteGoogleAccount(account.id)}>Disconnect</ConfirmButton>
                      </>}
                    >
                      <SyncFailure status={account} />
                    </Row>
                  ))}
                </List>
              )}
            </>
          )}
        </Section>
      )}

      {/* Google Scope Selection Modal */}
      {showScopeModal && availableScopes && (
        <Modal
          title={reauthorizingAccountId ? "Update Google permissions" : "Connect Google account"}
          onClose={() => setShowScopeModal(false)}
        >
          <div className="space-y-4">
            <p className="text-slate-600">
              Select which permissions to grant. You can always update these later by re-authorizing.
            </p>
            <div className="space-y-3">
              {Object.entries(availableScopes).map(([key, info]) => (
                <label key={key} className="flex items-start gap-3 p-3 border border-slate-200 rounded-lg hover:bg-slate-50 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedScopes.includes(key)}
                    onChange={() => toggleScope(key)}
                    className="mt-0.5"
                  />
                  <div>
                    <span className="font-medium text-slate-800 block">{info.label}</span>
                    <span className="text-sm text-slate-500">{info.description}</span>
                  </div>
                </label>
              ))}
            </div>
            <FormActions>
              <Button onClick={() => setShowScopeModal(false)}>Cancel</Button>
              <Button variant="primary"
                onClick={handleScopeSelectionConfirm}
                disabled={selectedScopes.length === 0}
              >
                {reauthorizingAccountId ? "Update permissions" : "Connect"}
              </Button>
            </FormActions>
          </div>
        </Modal>
      )}
    </>
  )
}

interface GitHubAccountFormProps {
  account?: GithubAccount
  onSubmit: (data: any) => Promise<void>
  onCancel: () => void
}

const GitHubAccountForm = ({ account, onSubmit, onCancel }: GitHubAccountFormProps) => {
  const [formData, setFormData] = useState({
    name: account?.name || '',
    auth_type: account?.auth_type || 'pat',
    access_token: '',
    app_id: account?.app_id || undefined,
    installation_id: account?.installation_id || undefined,
    private_key: '',
    llm_key: undefined as LLMKeyChoice | undefined,
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const data: any = { name: formData.name, auth_type: formData.auth_type }
      if (formData.auth_type === 'pat') {
        if (formData.access_token) data.access_token = formData.access_token
      } else {
        if (formData.app_id) data.app_id = formData.app_id
        if (formData.installation_id) data.installation_id = formData.installation_id
        if (formData.private_key) data.private_key = formData.private_key
      }
      if (formData.llm_key !== undefined) data.llm_key = formData.llm_key
      await onSubmit(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title={account ? 'Edit GitHub account' : 'Add GitHub account'} onClose={onCancel}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        <Field label="Name">
          <Input
            type="text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            required
            placeholder="My GitHub Account"
          />
        </Field>

        <Field label="Authentication type">
          <Select
            value={formData.auth_type}
            onChange={e => setFormData({ ...formData, auth_type: e.target.value as 'pat' | 'app' })}
            disabled={!!account}
          >
            <option value="pat">Personal access token</option>
            <option value="app">GitHub App</option>
          </Select>
        </Field>

        {formData.auth_type === 'pat' ? (
          <Disclosure key="pat" summary="Instructions">
            <p>
              Open{' '}
              <a href="https://github.com/settings/tokens" target="_blank" rel="noopener noreferrer" className="underline">
                GitHub personal access token settings
              </a>{' '}
              and generate a token for the account and repositories you want to sync.
            </p>
            <ol className="list-decimal pl-5 mt-2 space-y-1">
              <li>For a fine-grained token, choose the resource owner and repositories, then grant repository permissions: Issues → Read-only and Pull requests → Read-only. Metadata read access is automatic.</li>
              <li>Choose an expiration date. For a classic token accessing private repositories, use the repo scope; it grants broader access than a fine-grained token. Complete any organization approval or SSO authorization required.</li>
              <li>Copy the generated token and paste it into the Access token field. GitHub only shows the token once.</li>
            </ol>
            <p className="mt-2">No callback URL or webhook is needed. After saving, add the repositories you want to sync in Memory. Replace the token here when it expires.</p>
          </Disclosure>
        ) : (
          <Disclosure key="app" summary="Instructions">
            <p>
              Open{' '}
              <a href="https://github.com/settings/apps" target="_blank" rel="noopener noreferrer" className="underline">
                GitHub Apps settings
              </a>{' '}
              and select your app, or choose “New GitHub App”.
            </p>
            <ol className="list-decimal pl-5 mt-2 space-y-1">
              <li>Leave the callback URL blank and uncheck “Active” under Webhook. Memory uses installation credentials and polling, so neither an OAuth redirect nor a webhook is needed.</li>
              <li>Under Permissions &amp; events → Repository permissions, set Issues → Read-only and Pull requests → Read-only. Metadata read access is automatic. No write permissions are needed for ingestion.</li>
              <li>If syncing organization Projects or their fields, also grant Organization permissions → Projects → Read-only.</li>
              <li>Save the permissions. For an existing installation, approve the updated permissions in its configuration page.</li>
              <li>Copy the App ID from the app’s settings.</li>
              <li>Choose “Install App”, install it on the account or organization that owns the repositories, and choose “All repositories” or “Only select repositories”. Select the repositories you want to sync.</li>
              <li>Open the installation’s configuration page and copy the number after /installations/ in its URL.</li>
              <li>In the app’s settings, choose “Generate a private key” and paste the downloaded PEM file below.</li>
            </ol>
            <p className="mt-2">If no repositories appear, check the installation’s repository selection, pending organization approval, and Installation ID. GitHub hides repository-selection controls when the app requests no repository permissions. After updating the installation, reopen Manage repos in Memory.</p>
          </Disclosure>
        )}

        {formData.auth_type === 'pat' ? (
          <Field label={<>
              Access token {account?.has_access_token && <span className="text-slate-500">(leave blank to keep current)</span>}</>}>
            <Input
              type="password"
              value={formData.access_token}
              onChange={e => setFormData({ ...formData, access_token: e.target.value })}
              required={!account}
              placeholder="ghp_..."
            />
          </Field>
        ) : (
          <>
            <Field label="App ID" hint="Use the numeric App ID, not the Client ID.">
              <Input
                type="number"
                value={formData.app_id || ''}
                onChange={e => setFormData({ ...formData, app_id: parseInt(e.target.value) || undefined })}
                required={!account}
              />
            </Field>
            <Field label="Installation ID" hint="This identifies the app’s installation on your account or organization, not the app itself.">
              <Input
                type="number"
                value={formData.installation_id || ''}
                onChange={e => setFormData({ ...formData, installation_id: parseInt(e.target.value) || undefined })}
                required={!account}
              />
            </Field>
            <Field hint="Paste the entire PEM file, including the BEGIN and END lines." label={<>
                Private key {account?.has_private_key && <span className="text-slate-500">(leave blank to keep current)</span>}</>}>
              <Textarea
                value={formData.private_key}
                onChange={e => setFormData({ ...formData, private_key: e.target.value })}
                required={!account}
                rows={5}
                placeholder="-----BEGIN RSA PRIVATE KEY-----..."
              />
            </Field>
          </>
        )}

        <LLMKeyField value={account?.llm_key} onChange={llm_key => setFormData({ ...formData, llm_key })} />

        <FormActions>
          <Button type="button" onClick={onCancel}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={submitting}>
            {submitting ? 'Saving...' : 'Save'}
          </Button>
        </FormActions>
      </form>
    </Modal>
  )
}

export default AccountsPanel
