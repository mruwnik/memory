import { useState, useEffect, useCallback, useRef } from 'react'
import { useSources, GithubAccount, GoogleAccount, GoogleOAuthConfig, GoogleAvailableScopes } from '@/hooks/useSources'
import { SourceCard, StatusBadge, SyncStatus } from '../shared'
import { Button, Card, EmptyState, ErrorState, Field, FormActions, Hint, Input, LoadingState, Modal, Notice, Select, Textarea, buttonClass, cx } from '@/components/shared/ui'
import { useSourcesContext } from '../Sources'

export const AccountsPanel = () => {
  const {
    listGithubAccounts, createGithubAccount, updateGithubAccount, deleteGithubAccount, validateGithubAccount,
    listGoogleAccounts, getGoogleAvailableScopes, getGoogleAuthUrl, deleteGoogleAccount, reauthorizeGoogleAccount,
    getGoogleOAuthConfig, uploadGoogleOAuthConfig, deleteGoogleOAuthConfig
  } = useSources()
  const { userId } = useSourcesContext()

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

  // Track polling intervals for cleanup
  const pollingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const pollingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Refs to avoid stale closures in polling callbacks
  const googleAccountsRef = useRef(googleAccounts)
  const reauthorizingAccountIdRef = useRef(reauthorizingAccountId)
  googleAccountsRef.current = googleAccounts
  reauthorizingAccountIdRef.current = reauthorizingAccountId

  // Cleanup intervals on unmount
  useEffect(() => {
    return () => {
      if (pollingIntervalRef.current) clearInterval(pollingIntervalRef.current)
      if (pollingTimeoutRef.current) clearTimeout(pollingTimeoutRef.current)
    }
  }, [])

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [githubData, googleData, configData] = await Promise.all([
        listGithubAccounts(userId),
        listGoogleAccounts(userId),
        getGoogleOAuthConfig()
      ])
      setGithubAccounts(githubData)
      setGoogleAccounts(googleData)
      setOauthConfig(configData)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load accounts')
    } finally {
      setLoading(false)
    }
  }, [listGithubAccounts, listGoogleAccounts, getGoogleOAuthConfig, userId])

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

  const handleDeleteGithubAccount = async (id: number) => {
    await deleteGithubAccount(id)
    loadData()
  }

  const handleToggleGithubActive = async (account: GithubAccount) => {
    await updateGithubAccount(account.id, { active: !account.active })
    loadData()
  }

  const handleValidateGithubAccount = async (id: number) => {
    try {
      const result = await validateGithubAccount(id)
      alert(result.message)
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Validation failed')
    }
  }

  // Google handlers
  const handleConfigUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    setUploadingConfig(true)
    setError(null)
    try {
      const config = await uploadGoogleOAuthConfig(file)
      setOauthConfig(config)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to upload config')
    } finally {
      setUploadingConfig(false)
    }
  }

  const handleDeleteConfig = async () => {
    try {
      await deleteGoogleOAuthConfig()
      setOauthConfig(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete config')
    }
  }

  const handleConnectGoogle = async () => {
    // Load available scopes and show modal
    try {
      if (!availableScopes) {
        const scopes = await getGoogleAvailableScopes()
        setAvailableScopes(scopes)
      }
      setReauthorizingAccountId(null)
      setShowScopeModal(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load available scopes')
    }
  }

  const handleScopeSelectionConfirm = async () => {
    setShowScopeModal(false)
    try {
      const { authorization_url } = reauthorizingAccountId !== null
        ? await reauthorizeGoogleAccount(reauthorizingAccountId, selectedScopes)
        : await getGoogleAuthUrl(selectedScopes)
      window.open(authorization_url, '_blank', 'width=600,height=700')
      // Clear any existing polling
      if (pollingIntervalRef.current) clearInterval(pollingIntervalRef.current)
      if (pollingTimeoutRef.current) clearTimeout(pollingTimeoutRef.current)
      // Poll for new/updated accounts (use refs to avoid stale closure)
      pollingIntervalRef.current = setInterval(async () => {
        const newAccounts = await listGoogleAccounts()
        if (newAccounts.length > googleAccountsRef.current.length || reauthorizingAccountIdRef.current !== null) {
          setGoogleAccounts(newAccounts)
          if (pollingIntervalRef.current) clearInterval(pollingIntervalRef.current)
          pollingIntervalRef.current = null
        }
      }, 2000)
      pollingTimeoutRef.current = setTimeout(() => {
        if (pollingIntervalRef.current) clearInterval(pollingIntervalRef.current)
        pollingIntervalRef.current = null
      }, 60000)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to get Google auth URL')
    }
  }

  const toggleScope = (scopeKey: string) => {
    setSelectedScopes(prev =>
      prev.includes(scopeKey)
        ? prev.filter(s => s !== scopeKey)
        : [...prev, scopeKey]
    )
  }

  const handleDeleteGoogleAccount = async (id: number) => {
    await deleteGoogleAccount(id)
    loadData()
  }

  const handleReauthorizeGoogle = async (id: number) => {
    // Load available scopes and show modal for reauthorization
    try {
      if (!availableScopes) {
        const scopes = await getGoogleAvailableScopes()
        setAvailableScopes(scopes)
      }
      setReauthorizingAccountId(id)
      setShowScopeModal(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load available scopes')
    }
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  return (
    <Card>
      {/* GitHub Accounts Section */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-slate-800">GitHub Accounts</h3>
        <Button variant="primary" onClick={() => setShowGithubForm(true)}>Add Account</Button>
      </div>

      {githubAccounts.length === 0 ? (
        <EmptyState
          message="No GitHub accounts configured. Add an account to track repositories."
          actionLabel="Add GitHub Account"
          onAction={() => setShowGithubForm(true)}
        />
      ) : (
        <div className="space-y-3">
          {githubAccounts.map(account => (
            <SourceCard
              key={account.id}
              title={account.name}
              subtitle={`${account.auth_type === 'pat' ? 'Personal Access Token' : 'GitHub App'}${account.repos.length > 0 ? ` • ${account.repos.length} repos tracked` : ''}`}
              active={account.active}
              lastSyncAt={account.last_sync_at}
              onToggleActive={() => handleToggleGithubActive(account)}
              onEdit={() => setEditingGithubAccount(account)}
              onDelete={() => handleDeleteGithubAccount(account.id)}
              additionalActions={
                <Button size="sm" onClick={() => handleValidateGithubAccount(account.id)}>Validate</Button>
              }
            />
          ))}
        </div>
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

      {/* Google Accounts Section */}
      <div className="flex items-center justify-between mb-4 mt-8">
        <h3 className="text-lg font-semibold text-slate-800">Google Accounts</h3>
        {oauthConfig && <Button variant="primary" onClick={handleConnectGoogle}>Connect Account</Button>}
      </div>

      {/* OAuth Config required first */}
      {oauthConfig === null ? (
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-4">
          <h4 className="font-medium text-slate-800 mb-2">OAuth Configuration Required</h4>
          <p className="text-sm text-slate-600 mb-2">Upload your Google OAuth credentials JSON file to enable Google integrations (Gmail, Calendar, Drive).</p>
          <Hint className="mb-4">Get this from the Google Cloud Console under APIs & Services → Credentials.</Hint>
          <label className={buttonClass('secondary', 'md', 'inline-block')}>
            {uploadingConfig ? 'Uploading...' : 'Upload Credentials JSON'}
            <input
              type="file"
              accept=".json"
              onChange={handleConfigUpload}
              disabled={uploadingConfig}
              className="hidden"
            />
          </label>
        </div>
      ) : (
        <>
          <details className="border border-slate-200 rounded-lg">
            <summary className="p-3 cursor-pointer hover:bg-slate-50 font-medium text-slate-700">OAuth Configuration</summary>
            <div className="p-4 pt-0">
              <p className="text-sm text-slate-600 mb-1"><strong>Project:</strong> {oauthConfig.project_id}</p>
              <p className="text-sm text-slate-600 mb-3"><strong>Client ID:</strong> {oauthConfig.client_id.substring(0, 20)}...</p>
              <div className="flex gap-2">
                <label className={buttonClass('secondary', 'md', 'text-xs')}>
                  {uploadingConfig ? 'Uploading...' : 'Replace Config'}
                  <input
                    type="file"
                    accept=".json"
                    onChange={handleConfigUpload}
                    disabled={uploadingConfig}
                    className="hidden"
                  />
                </label>
                <Button variant="danger" size="sm" className="text-xs" onClick={handleDeleteConfig}>
                  Delete Config
                </Button>
              </div>
            </div>
          </details>

          {googleAccounts.length === 0 ? (
            <EmptyState
              message="No Google accounts connected. Connect an account to use Gmail, Calendar, or Drive."
              actionLabel="Connect Google Account"
              onAction={handleConnectGoogle}
            />
          ) : (
            <div className="space-y-3 mt-4">
              {googleAccounts.map(account => (
                <div key={account.id} className={cx('border border-slate-200 rounded-lg p-4', !account.active && 'opacity-60')}>
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                      <SyncStatus lastSyncAt={account.last_sync_at} />
                      <h4 className="font-medium text-slate-800 truncate">{account.name}</h4>
                      <p className="text-sm text-slate-600 truncate">{account.email}</p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 shrink-0">
                      <StatusBadge active={account.active} />
                      <Button size="sm" onClick={() => handleReauthorizeGoogle(account.id)}>Re-authorize</Button>
                      <Button variant="danger" size="sm" onClick={() => handleDeleteGoogleAccount(account.id)}>Disconnect</Button>
                    </div>
                  </div>
                  {account.sync_error && (
                    <Notice tone="error">{account.sync_error}</Notice>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* Google Scope Selection Modal */}
      {showScopeModal && availableScopes && (
        <Modal
          title={reauthorizingAccountId ? "Update Google Permissions" : "Connect Google Account"}
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
                {reauthorizingAccountId ? "Update Permissions" : "Connect"}
              </Button>
            </FormActions>
          </div>
        </Modal>
      )}
    </Card>
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
      await onSubmit(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title={account ? 'Edit GitHub Account' : 'Add GitHub Account'} onClose={onCancel}>
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

        <Field label="Authentication Type">
          <Select
            value={formData.auth_type}
            onChange={e => setFormData({ ...formData, auth_type: e.target.value as 'pat' | 'app' })}
            disabled={!!account}
          >
            <option value="pat">Personal Access Token</option>
            <option value="app">GitHub App</option>
          </Select>
        </Field>

        {formData.auth_type === 'pat' ? (
          <Field label={<>
              Access Token {account?.has_access_token && <span className="text-slate-400">(leave blank to keep current)</span>}</>}>
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
            <Field label="App ID">
              <Input
                type="number"
                value={formData.app_id || ''}
                onChange={e => setFormData({ ...formData, app_id: parseInt(e.target.value) || undefined })}
                required={!account}
              />
            </Field>
            <Field label="Installation ID">
              <Input
                type="number"
                value={formData.installation_id || ''}
                onChange={e => setFormData({ ...formData, installation_id: parseInt(e.target.value) || undefined })}
                required={!account}
              />
            </Field>
            <Field label={<>
                Private Key {account?.has_private_key && <span className="text-slate-400">(leave blank to keep current)</span>}</>}>
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
