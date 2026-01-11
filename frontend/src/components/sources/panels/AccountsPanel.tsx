import { useState, useEffect, useCallback, useRef } from 'react'
import { useSources, GithubAccount, GoogleAccount, GoogleOAuthConfig, GoogleAvailableScopes } from '@/hooks/useSources'
import {
  SourceCard,
  Modal,
  EmptyState,
  LoadingState,
  ErrorState,
  StatusBadge,
  SyncStatus,
} from '../shared'
import { styles, cx } from '../styles'

export const AccountsPanel = () => {
  const {
    listGithubAccounts, createGithubAccount, updateGithubAccount, deleteGithubAccount, validateGithubAccount,
    listGoogleAccounts, getGoogleAvailableScopes, getGoogleAuthUrl, deleteGoogleAccount, reauthorizeGoogleAccount,
    getGoogleOAuthConfig, uploadGoogleOAuthConfig, deleteGoogleOAuthConfig
  } = useSources()

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
        listGithubAccounts(),
        listGoogleAccounts(),
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
  }, [listGithubAccounts, listGoogleAccounts, getGoogleOAuthConfig])

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
    <div className={styles.panel}>
      {/* GitHub Accounts Section */}
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>GitHub Accounts</h3>
        <button className={styles.btnAdd} onClick={() => setShowGithubForm(true)}>Add Account</button>
      </div>

      {githubAccounts.length === 0 ? (
        <EmptyState
          message="No GitHub accounts configured. Add an account to track repositories."
          actionLabel="Add GitHub Account"
          onAction={() => setShowGithubForm(true)}
        />
      ) : (
        <div className={styles.sourceList}>
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
                <button className={styles.btnEdit} onClick={() => handleValidateGithubAccount(account.id)}>Validate</button>
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
      <div className={cx(styles.panelHeader, 'mt-8')}>
        <h3 className={styles.panelTitle}>Google Accounts</h3>
        {oauthConfig && <button className={styles.btnAdd} onClick={handleConnectGoogle}>Connect Account</button>}
      </div>

      {/* OAuth Config required first */}
      {oauthConfig === null ? (
        <div className={styles.configBox}>
          <h4 className="font-medium text-slate-800 mb-2">OAuth Configuration Required</h4>
          <p className="text-sm text-slate-600 mb-2">Upload your Google OAuth credentials JSON file to enable Google integrations (Gmail, Calendar, Drive).</p>
          <p className={cx(styles.formHint, 'mb-4')}>Get this from the Google Cloud Console under APIs & Services → Credentials.</p>
          <label className={cx(styles.btnUpload, 'inline-block')}>
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
          <details className={styles.detailsSection}>
            <summary className={styles.detailsSummary}>OAuth Configuration</summary>
            <div className={styles.detailsContent}>
              <p className="text-sm text-slate-600 mb-1"><strong>Project:</strong> {oauthConfig.project_id}</p>
              <p className="text-sm text-slate-600 mb-3"><strong>Client ID:</strong> {oauthConfig.client_id.substring(0, 20)}...</p>
              <div className="flex gap-2">
                <label className={cx(styles.btnUpload, 'text-xs')}>
                  {uploadingConfig ? 'Uploading...' : 'Replace Config'}
                  <input
                    type="file"
                    accept=".json"
                    onChange={handleConfigUpload}
                    disabled={uploadingConfig}
                    className="hidden"
                  />
                </label>
                <button className={cx(styles.btnDelete, 'text-xs')} onClick={handleDeleteConfig}>
                  Delete Config
                </button>
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
            <div className={cx(styles.sourceList, 'mt-4')}>
              {googleAccounts.map(account => (
                <div key={account.id} className={account.active ? styles.card : styles.cardInactive}>
                  <div className={styles.cardHeader}>
                    <div className={styles.cardInfo}>
                      <SyncStatus lastSyncAt={account.last_sync_at} />
                      <h4 className={styles.cardTitle}>{account.name}</h4>
                      <p className={styles.cardSubtitle}>{account.email}</p>
                    </div>
                    <div className={styles.cardActions}>
                      <StatusBadge active={account.active} />
                      <button className={styles.btnEdit} onClick={() => handleReauthorizeGoogle(account.id)}>Re-authorize</button>
                      <button className={styles.btnDelete} onClick={() => handleDeleteGoogleAccount(account.id)}>Disconnect</button>
                    </div>
                  </div>
                  {account.sync_error && (
                    <div className={styles.errorBanner}>{account.sync_error}</div>
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
                    className="mt-0.5 rounded border-slate-300"
                  />
                  <div>
                    <span className="font-medium text-slate-800 block">{info.label}</span>
                    <span className="text-sm text-slate-500">{info.description}</span>
                  </div>
                </label>
              ))}
            </div>
            <div className={styles.modalActions}>
              <button className={styles.btnCancel} onClick={() => setShowScopeModal(false)}>Cancel</button>
              <button
                className={styles.btnSubmit}
                onClick={handleScopeSelectionConfirm}
                disabled={selectedScopes.length === 0}
              >
                {reauthorizingAccountId ? "Update Permissions" : "Connect"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
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
      <form onSubmit={handleSubmit} className={styles.form}>
        {error && <div className={styles.formError}>{error}</div>}

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Name</label>
          <input
            type="text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            required
            placeholder="My GitHub Account"
            className={styles.formInput}
          />
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Authentication Type</label>
          <select
            value={formData.auth_type}
            onChange={e => setFormData({ ...formData, auth_type: e.target.value as 'pat' | 'app' })}
            disabled={!!account}
            className={styles.formSelect}
          >
            <option value="pat">Personal Access Token</option>
            <option value="app">GitHub App</option>
          </select>
        </div>

        {formData.auth_type === 'pat' ? (
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>
              Access Token {account?.has_access_token && <span className="text-slate-400">(leave blank to keep current)</span>}
            </label>
            <input
              type="password"
              value={formData.access_token}
              onChange={e => setFormData({ ...formData, access_token: e.target.value })}
              required={!account}
              placeholder="ghp_..."
              className={styles.formInput}
            />
          </div>
        ) : (
          <>
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>App ID</label>
              <input
                type="number"
                value={formData.app_id || ''}
                onChange={e => setFormData({ ...formData, app_id: parseInt(e.target.value) || undefined })}
                required={!account}
                className={styles.formInput}
              />
            </div>
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>Installation ID</label>
              <input
                type="number"
                value={formData.installation_id || ''}
                onChange={e => setFormData({ ...formData, installation_id: parseInt(e.target.value) || undefined })}
                required={!account}
                className={styles.formInput}
              />
            </div>
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>
                Private Key {account?.has_private_key && <span className="text-slate-400">(leave blank to keep current)</span>}
              </label>
              <textarea
                value={formData.private_key}
                onChange={e => setFormData({ ...formData, private_key: e.target.value })}
                required={!account}
                rows={5}
                placeholder="-----BEGIN RSA PRIVATE KEY-----..."
                className={styles.formTextarea}
              />
            </div>
          </>
        )}

        <div className={styles.formActions}>
          <button type="button" className={styles.btnCancel} onClick={onCancel}>Cancel</button>
          <button type="submit" className={styles.btnSubmit} disabled={submitting}>
            {submitting ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

export default AccountsPanel
