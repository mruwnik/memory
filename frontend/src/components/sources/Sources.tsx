import { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useSources, EmailAccount, ArticleFeed, GithubAccount, GoogleAccount, GoogleFolder, GoogleOAuthConfig, DriveItem, BrowseResponse, GoogleFolderCreate, CalendarAccount } from '@/hooks/useSources'
import { useAuth } from '@/hooks/useAuth'
import {
  SourceCard,
  Modal,
  TagsInput,
  IntervalInput,
  EmptyState,
  LoadingState,
  ErrorState,
  SyncButton,
  StatusBadge,
  SyncStatus,
  ConfirmDialog,
} from './shared'

type TabType = 'email' | 'feeds' | 'github' | 'google' | 'calendar' | 'books' | 'forums' | 'photos'

const Sources = () => {
  const [activeTab, setActiveTab] = useState<TabType>('email')

  return (
    <div className="sources-view">
      <div className="sources-header">
        <Link to="/ui/dashboard" className="back-btn">Back</Link>
        <h2>Manage Sources</h2>
      </div>

      <div className="sources-tabs">
        <button
          className={`tab ${activeTab === 'email' ? 'active' : ''}`}
          onClick={() => setActiveTab('email')}
        >
          Email
        </button>
        <button
          className={`tab ${activeTab === 'feeds' ? 'active' : ''}`}
          onClick={() => setActiveTab('feeds')}
        >
          RSS Feeds
        </button>
        <button
          className={`tab ${activeTab === 'github' ? 'active' : ''}`}
          onClick={() => setActiveTab('github')}
        >
          GitHub
        </button>
        <button
          className={`tab ${activeTab === 'google' ? 'active' : ''}`}
          onClick={() => setActiveTab('google')}
        >
          Google Drive
        </button>
        <button
          className={`tab ${activeTab === 'calendar' ? 'active' : ''}`}
          onClick={() => setActiveTab('calendar')}
        >
          Calendar
        </button>
        <button
          className={`tab ${activeTab === 'books' ? 'active' : ''}`}
          onClick={() => setActiveTab('books')}
        >
          Books
        </button>
        <button
          className={`tab ${activeTab === 'forums' ? 'active' : ''}`}
          onClick={() => setActiveTab('forums')}
        >
          Forums
        </button>
        <button
          className={`tab ${activeTab === 'photos' ? 'active' : ''}`}
          onClick={() => setActiveTab('photos')}
        >
          Photos
        </button>
      </div>

      <div className="sources-content">
        {activeTab === 'email' && <EmailPanel />}
        {activeTab === 'feeds' && <FeedsPanel />}
        {activeTab === 'github' && <GitHubPanel />}
        {activeTab === 'google' && <GoogleDrivePanel />}
        {activeTab === 'calendar' && <CalendarPanel />}
        {activeTab === 'books' && <BooksPanel />}
        {activeTab === 'forums' && <ForumsPanel />}
        {activeTab === 'photos' && <PhotosPanel />}
      </div>
    </div>
  )
}

// === Email Panel ===

const EmailPanel = () => {
  const { listEmailAccounts, createEmailAccount, updateEmailAccount, deleteEmailAccount, syncEmailAccount, testEmailAccount } = useSources()
  const [accounts, setAccounts] = useState<EmailAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingAccount, setEditingAccount] = useState<EmailAccount | null>(null)

  const loadAccounts = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listEmailAccounts()
      setAccounts(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load accounts')
    } finally {
      setLoading(false)
    }
  }, [listEmailAccounts])

  useEffect(() => { loadAccounts() }, [loadAccounts])

  const handleCreate = async (data: any) => {
    await createEmailAccount(data)
    setShowForm(false)
    loadAccounts()
  }

  const handleUpdate = async (data: any) => {
    if (editingAccount) {
      await updateEmailAccount(editingAccount.id, data)
      setEditingAccount(null)
      loadAccounts()
    }
  }

  const handleDelete = async (id: number) => {
    await deleteEmailAccount(id)
    loadAccounts()
  }

  const handleToggleActive = async (account: EmailAccount) => {
    await updateEmailAccount(account.id, { active: !account.active })
    loadAccounts()
  }

  const handleSync = async (id: number) => {
    await syncEmailAccount(id)
    loadAccounts()
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadAccounts} />

  return (
    <div className="source-panel">
      <div className="panel-header">
        <h3>Email Accounts</h3>
        <button className="add-btn" onClick={() => setShowForm(true)}>Add Account</button>
      </div>

      {accounts.length === 0 ? (
        <EmptyState
          message="No email accounts configured"
          actionLabel="Add Email Account"
          onAction={() => setShowForm(true)}
        />
      ) : (
        <div className="source-list">
          {accounts.map(account => (
            <SourceCard
              key={account.id}
              title={account.name}
              subtitle={account.email_address}
              active={account.active}
              lastSyncAt={account.last_sync_at}
              onToggleActive={() => handleToggleActive(account)}
              onEdit={() => setEditingAccount(account)}
              onDelete={() => handleDelete(account.id)}
              onSync={() => handleSync(account.id)}
            >
              <div className="source-details">
                <span>Server: {account.imap_server}:{account.imap_port}</span>
                {account.folders.length > 0 && (
                  <span>Folders: {account.folders.join(', ')}</span>
                )}
              </div>
            </SourceCard>
          ))}
        </div>
      )}

      {showForm && (
        <EmailForm
          onSubmit={handleCreate}
          onCancel={() => setShowForm(false)}
        />
      )}

      {editingAccount && (
        <EmailForm
          account={editingAccount}
          onSubmit={handleUpdate}
          onCancel={() => setEditingAccount(null)}
        />
      )}
    </div>
  )
}

interface EmailFormProps {
  account?: EmailAccount
  onSubmit: (data: any) => Promise<void>
  onCancel: () => void
}

const EmailForm = ({ account, onSubmit, onCancel }: EmailFormProps) => {
  const [formData, setFormData] = useState({
    name: account?.name || '',
    email_address: account?.email_address || '',
    imap_server: account?.imap_server || '',
    imap_port: account?.imap_port || 993,
    username: account?.username || '',
    password: '',
    use_ssl: account?.use_ssl ?? true,
    folders: account?.folders || [],
    tags: account?.tags || [],
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const data = { ...formData }
      if (account && !data.password) {
        delete (data as any).password
      }
      await onSubmit(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title={account ? 'Edit Email Account' : 'Add Email Account'} onClose={onCancel}>
      <form onSubmit={handleSubmit} className="source-form">
        {error && <div className="form-error">{error}</div>}

        <div className="form-group">
          <label>Name</label>
          <input
            type="text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            required
          />
        </div>

        <div className="form-group">
          <label>Email Address</label>
          <input
            type="email"
            value={formData.email_address}
            onChange={e => setFormData({ ...formData, email_address: e.target.value })}
            required
            disabled={!!account}
          />
        </div>

        <div className="form-row">
          <div className="form-group">
            <label>IMAP Server</label>
            <input
              type="text"
              value={formData.imap_server}
              onChange={e => setFormData({ ...formData, imap_server: e.target.value })}
              required
              placeholder="imap.gmail.com"
            />
          </div>
          <div className="form-group">
            <label>Port</label>
            <input
              type="number"
              value={formData.imap_port}
              onChange={e => setFormData({ ...formData, imap_port: parseInt(e.target.value) })}
              required
            />
          </div>
        </div>

        <div className="form-group">
          <label>Username</label>
          <input
            type="text"
            value={formData.username}
            onChange={e => setFormData({ ...formData, username: e.target.value })}
            required
          />
        </div>

        <div className="form-group">
          <label>Password {account && '(leave blank to keep current)'}</label>
          <input
            type="password"
            value={formData.password}
            onChange={e => setFormData({ ...formData, password: e.target.value })}
            required={!account}
          />
        </div>

        <div className="form-group checkbox">
          <label>
            <input
              type="checkbox"
              checked={formData.use_ssl}
              onChange={e => setFormData({ ...formData, use_ssl: e.target.checked })}
            />
            Use SSL
          </label>
        </div>

        <div className="form-group">
          <label>Tags</label>
          <TagsInput
            tags={formData.tags}
            onChange={tags => setFormData({ ...formData, tags })}
          />
        </div>

        <div className="form-actions">
          <button type="button" className="cancel-btn" onClick={onCancel}>Cancel</button>
          <button type="submit" className="submit-btn" disabled={submitting}>
            {submitting ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

// === Feeds Panel ===

const FeedsPanel = () => {
  const { listArticleFeeds, createArticleFeed, updateArticleFeed, deleteArticleFeed, syncArticleFeed } = useSources()
  const [feeds, setFeeds] = useState<ArticleFeed[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingFeed, setEditingFeed] = useState<ArticleFeed | null>(null)

  const loadFeeds = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listArticleFeeds()
      setFeeds(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load feeds')
    } finally {
      setLoading(false)
    }
  }, [listArticleFeeds])

  useEffect(() => { loadFeeds() }, [loadFeeds])

  const handleCreate = async (data: any) => {
    await createArticleFeed(data)
    setShowForm(false)
    loadFeeds()
  }

  const handleUpdate = async (data: any) => {
    if (editingFeed) {
      await updateArticleFeed(editingFeed.id, data)
      setEditingFeed(null)
      loadFeeds()
    }
  }

  const handleDelete = async (id: number) => {
    await deleteArticleFeed(id)
    loadFeeds()
  }

  const handleToggleActive = async (feed: ArticleFeed) => {
    await updateArticleFeed(feed.id, { active: !feed.active })
    loadFeeds()
  }

  const handleSync = async (id: number) => {
    await syncArticleFeed(id)
    loadFeeds()
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadFeeds} />

  return (
    <div className="source-panel">
      <div className="panel-header">
        <h3>RSS Feeds</h3>
        <button className="add-btn" onClick={() => setShowForm(true)}>Add Feed</button>
      </div>

      {feeds.length === 0 ? (
        <EmptyState
          message="No RSS feeds configured"
          actionLabel="Add RSS Feed"
          onAction={() => setShowForm(true)}
        />
      ) : (
        <div className="source-list">
          {feeds.map(feed => (
            <SourceCard
              key={feed.id}
              title={feed.title || feed.url}
              subtitle={feed.url}
              active={feed.active}
              lastSyncAt={feed.last_checked_at}
              onToggleActive={() => handleToggleActive(feed)}
              onEdit={() => setEditingFeed(feed)}
              onDelete={() => handleDelete(feed.id)}
              onSync={() => handleSync(feed.id)}
            >
              {feed.description && (
                <p className="source-description">{feed.description}</p>
              )}
            </SourceCard>
          ))}
        </div>
      )}

      {showForm && (
        <FeedForm
          onSubmit={handleCreate}
          onCancel={() => setShowForm(false)}
        />
      )}

      {editingFeed && (
        <FeedForm
          feed={editingFeed}
          onSubmit={handleUpdate}
          onCancel={() => setEditingFeed(null)}
        />
      )}
    </div>
  )
}

interface FeedFormProps {
  feed?: ArticleFeed
  onSubmit: (data: any) => Promise<void>
  onCancel: () => void
}

const FeedForm = ({ feed, onSubmit, onCancel }: FeedFormProps) => {
  const [formData, setFormData] = useState({
    url: feed?.url || '',
    title: feed?.title || '',
    description: feed?.description || '',
    tags: feed?.tags || [],
    check_interval: feed?.check_interval || 1440,
    active: feed?.active ?? true,
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit(formData)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title={feed ? 'Edit RSS Feed' : 'Add RSS Feed'} onClose={onCancel}>
      <form onSubmit={handleSubmit} className="source-form">
        {error && <div className="form-error">{error}</div>}

        <div className="form-group">
          <label>Feed URL</label>
          <input
            type="url"
            value={formData.url}
            onChange={e => setFormData({ ...formData, url: e.target.value })}
            required
            disabled={!!feed}
            placeholder="https://example.com/feed.xml"
          />
        </div>

        <div className="form-group">
          <label>Title (optional)</label>
          <input
            type="text"
            value={formData.title}
            onChange={e => setFormData({ ...formData, title: e.target.value })}
          />
        </div>

        <div className="form-group">
          <label>Description (optional)</label>
          <textarea
            value={formData.description}
            onChange={e => setFormData({ ...formData, description: e.target.value })}
            rows={2}
          />
        </div>

        <IntervalInput
          value={formData.check_interval}
          onChange={check_interval => setFormData({ ...formData, check_interval })}
          label="Check interval"
        />

        <div className="form-group">
          <label>Tags</label>
          <TagsInput
            tags={formData.tags}
            onChange={tags => setFormData({ ...formData, tags })}
          />
        </div>

        <div className="form-actions">
          <button type="button" className="cancel-btn" onClick={onCancel}>Cancel</button>
          <button type="submit" className="submit-btn" disabled={submitting}>
            {submitting ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

// === GitHub Panel ===

const GitHubPanel = () => {
  const {
    listGithubAccounts, createGithubAccount, updateGithubAccount, deleteGithubAccount,
    addGithubRepo, updateGithubRepo, deleteGithubRepo, syncGithubRepo
  } = useSources()
  const [accounts, setAccounts] = useState<GithubAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAccountForm, setShowAccountForm] = useState(false)
  const [editingAccount, setEditingAccount] = useState<GithubAccount | null>(null)
  const [addingRepoTo, setAddingRepoTo] = useState<number | null>(null)

  const loadAccounts = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listGithubAccounts()
      setAccounts(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load accounts')
    } finally {
      setLoading(false)
    }
  }, [listGithubAccounts])

  useEffect(() => { loadAccounts() }, [loadAccounts])

  const handleCreateAccount = async (data: any) => {
    await createGithubAccount(data)
    setShowAccountForm(false)
    loadAccounts()
  }

  const handleUpdateAccount = async (data: any) => {
    if (editingAccount) {
      await updateGithubAccount(editingAccount.id, data)
      setEditingAccount(null)
      loadAccounts()
    }
  }

  const handleDeleteAccount = async (id: number) => {
    await deleteGithubAccount(id)
    loadAccounts()
  }

  const handleToggleActive = async (account: GithubAccount) => {
    await updateGithubAccount(account.id, { active: !account.active })
    loadAccounts()
  }

  const handleAddRepo = async (accountId: number, data: any) => {
    await addGithubRepo(accountId, data)
    setAddingRepoTo(null)
    loadAccounts()
  }

  const handleDeleteRepo = async (accountId: number, repoId: number) => {
    await deleteGithubRepo(accountId, repoId)
    loadAccounts()
  }

  const handleToggleRepoActive = async (accountId: number, repoId: number, active: boolean) => {
    await updateGithubRepo(accountId, repoId, { active: !active })
    loadAccounts()
  }

  const handleSyncRepo = async (accountId: number, repoId: number) => {
    await syncGithubRepo(accountId, repoId)
    loadAccounts()
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadAccounts} />

  return (
    <div className="source-panel">
      <div className="panel-header">
        <h3>GitHub Accounts</h3>
        <button className="add-btn" onClick={() => setShowAccountForm(true)}>Add Account</button>
      </div>

      {accounts.length === 0 ? (
        <EmptyState
          message="No GitHub accounts configured"
          actionLabel="Add GitHub Account"
          onAction={() => setShowAccountForm(true)}
        />
      ) : (
        <div className="source-list">
          {accounts.map(account => (
            <div key={account.id} className="github-account-card">
              <div className="source-card-header">
                <div className="source-card-info">
                  <h4>{account.name}</h4>
                  <p className="source-subtitle">
                    {account.auth_type === 'pat' ? 'Personal Access Token' : 'GitHub App'}
                  </p>
                </div>
                <div className="source-card-actions-inline">
                  <StatusBadge active={account.active} onClick={() => handleToggleActive(account)} />
                  <button className="edit-btn" onClick={() => setEditingAccount(account)}>Edit</button>
                  <button className="delete-btn" onClick={() => handleDeleteAccount(account.id)}>Delete</button>
                </div>
              </div>

              <div className="repos-section">
                <div className="repos-header">
                  <h5>Tracked Repositories ({account.repos.length})</h5>
                  <button className="add-btn small" onClick={() => setAddingRepoTo(account.id)}>Add Repo</button>
                </div>

                {account.repos.length === 0 ? (
                  <p className="no-repos">No repositories tracked</p>
                ) : (
                  <div className="repos-list">
                    {account.repos.map(repo => (
                      <div key={repo.id} className={`repo-card ${repo.active ? '' : 'inactive'}`}>
                        <div className="repo-info">
                          <span className="repo-path">{repo.repo_path}</span>
                          <SyncStatus lastSyncAt={repo.last_sync_at} />
                        </div>
                        <div className="repo-tracking">
                          {repo.track_issues && <span className="tracking-badge">Issues</span>}
                          {repo.track_prs && <span className="tracking-badge">PRs</span>}
                          {repo.track_comments && <span className="tracking-badge">Comments</span>}
                        </div>
                        <div className="repo-actions">
                          <SyncButton
                            onSync={() => handleSyncRepo(account.id, repo.id)}
                            disabled={!repo.active || !account.active}
                            label="Sync"
                          />
                          <button
                            className="toggle-btn"
                            onClick={() => handleToggleRepoActive(account.id, repo.id, repo.active)}
                          >
                            {repo.active ? 'Disable' : 'Enable'}
                          </button>
                          <button
                            className="delete-btn small"
                            onClick={() => handleDeleteRepo(account.id, repo.id)}
                          >
                            Remove
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {showAccountForm && (
        <GitHubAccountForm
          onSubmit={handleCreateAccount}
          onCancel={() => setShowAccountForm(false)}
        />
      )}

      {editingAccount && (
        <GitHubAccountForm
          account={editingAccount}
          onSubmit={handleUpdateAccount}
          onCancel={() => setEditingAccount(null)}
        />
      )}

      {addingRepoTo && (
        <GitHubRepoForm
          accountId={addingRepoTo}
          onSubmit={(data) => handleAddRepo(addingRepoTo, data)}
          onCancel={() => setAddingRepoTo(null)}
        />
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
      <form onSubmit={handleSubmit} className="source-form">
        {error && <div className="form-error">{error}</div>}

        <div className="form-group">
          <label>Name</label>
          <input
            type="text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            required
            placeholder="My GitHub Account"
          />
        </div>

        <div className="form-group">
          <label>Authentication Type</label>
          <select
            value={formData.auth_type}
            onChange={e => setFormData({ ...formData, auth_type: e.target.value as 'pat' | 'app' })}
            disabled={!!account}
          >
            <option value="pat">Personal Access Token</option>
            <option value="app">GitHub App</option>
          </select>
        </div>

        {formData.auth_type === 'pat' ? (
          <div className="form-group">
            <label>Access Token {account?.has_access_token && '(leave blank to keep current)'}</label>
            <input
              type="password"
              value={formData.access_token}
              onChange={e => setFormData({ ...formData, access_token: e.target.value })}
              required={!account}
              placeholder="ghp_..."
            />
          </div>
        ) : (
          <>
            <div className="form-group">
              <label>App ID</label>
              <input
                type="number"
                value={formData.app_id || ''}
                onChange={e => setFormData({ ...formData, app_id: parseInt(e.target.value) || undefined })}
                required={!account}
              />
            </div>
            <div className="form-group">
              <label>Installation ID</label>
              <input
                type="number"
                value={formData.installation_id || ''}
                onChange={e => setFormData({ ...formData, installation_id: parseInt(e.target.value) || undefined })}
                required={!account}
              />
            </div>
            <div className="form-group">
              <label>Private Key {account?.has_private_key && '(leave blank to keep current)'}</label>
              <textarea
                value={formData.private_key}
                onChange={e => setFormData({ ...formData, private_key: e.target.value })}
                required={!account}
                rows={5}
                placeholder="-----BEGIN RSA PRIVATE KEY-----..."
              />
            </div>
          </>
        )}

        <div className="form-actions">
          <button type="button" className="cancel-btn" onClick={onCancel}>Cancel</button>
          <button type="submit" className="submit-btn" disabled={submitting}>
            {submitting ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

interface GitHubRepoFormProps {
  accountId: number
  onSubmit: (data: any) => Promise<void>
  onCancel: () => void
}

const GitHubRepoForm = ({ accountId, onSubmit, onCancel }: GitHubRepoFormProps) => {
  const [formData, setFormData] = useState({
    owner: '',
    name: '',
    track_issues: true,
    track_prs: true,
    track_comments: true,
    track_project_fields: false,
    tags: [] as string[],
    check_interval: 60,
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit(formData)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title="Add Repository" onClose={onCancel}>
      <form onSubmit={handleSubmit} className="source-form">
        {error && <div className="form-error">{error}</div>}

        <div className="form-row">
          <div className="form-group">
            <label>Owner</label>
            <input
              type="text"
              value={formData.owner}
              onChange={e => setFormData({ ...formData, owner: e.target.value })}
              required
              placeholder="organization or username"
            />
          </div>
          <div className="form-group">
            <label>Repository Name</label>
            <input
              type="text"
              value={formData.name}
              onChange={e => setFormData({ ...formData, name: e.target.value })}
              required
              placeholder="repo-name"
            />
          </div>
        </div>

        <div className="form-group checkboxes">
          <label>Track:</label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={formData.track_issues}
              onChange={e => setFormData({ ...formData, track_issues: e.target.checked })}
            />
            Issues
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={formData.track_prs}
              onChange={e => setFormData({ ...formData, track_prs: e.target.checked })}
            />
            Pull Requests
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={formData.track_comments}
              onChange={e => setFormData({ ...formData, track_comments: e.target.checked })}
            />
            Comments
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={formData.track_project_fields}
              onChange={e => setFormData({ ...formData, track_project_fields: e.target.checked })}
            />
            Project Fields
          </label>
        </div>

        <IntervalInput
          value={formData.check_interval}
          onChange={check_interval => setFormData({ ...formData, check_interval })}
          label="Check interval"
        />

        <div className="form-group">
          <label>Tags</label>
          <TagsInput
            tags={formData.tags}
            onChange={tags => setFormData({ ...formData, tags })}
          />
        </div>

        <div className="form-actions">
          <button type="button" className="cancel-btn" onClick={onCancel}>Cancel</button>
          <button type="submit" className="submit-btn" disabled={submitting}>
            {submitting ? 'Adding...' : 'Add Repository'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

// === Google Drive Panel ===

const GoogleDrivePanel = () => {
  const {
    listGoogleAccounts, getGoogleAuthUrl, deleteGoogleAccount,
    addGoogleFolder, updateGoogleFolder, deleteGoogleFolder, syncGoogleFolder,
    getGoogleOAuthConfig, uploadGoogleOAuthConfig, deleteGoogleOAuthConfig
  } = useSources()
  const [accounts, setAccounts] = useState<GoogleAccount[]>([])
  const [oauthConfig, setOauthConfig] = useState<GoogleOAuthConfig | null | undefined>(undefined)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [addingFolderTo, setAddingFolderTo] = useState<number | null>(null)
  const [browsingFoldersFor, setBrowsingFoldersFor] = useState<number | null>(null)
  const [managingExclusionsFor, setManagingExclusionsFor] = useState<{ accountId: number; folder: GoogleFolder } | null>(null)
  const [uploadingConfig, setUploadingConfig] = useState(false)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [accountsData, configData] = await Promise.all([
        listGoogleAccounts(),
        getGoogleOAuthConfig()
      ])
      setAccounts(accountsData)
      setOauthConfig(configData)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [listGoogleAccounts, getGoogleOAuthConfig])

  useEffect(() => { loadData() }, [loadData])

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

  const handleConnect = async () => {
    try {
      const { authorization_url } = await getGoogleAuthUrl()
      window.open(authorization_url, '_blank', 'width=600,height=700')
      // Poll for new accounts
      const interval = setInterval(async () => {
        const newAccounts = await listGoogleAccounts()
        if (newAccounts.length > accounts.length) {
          setAccounts(newAccounts)
          clearInterval(interval)
        }
      }, 2000)
      setTimeout(() => clearInterval(interval), 60000)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to get Google auth URL')
    }
  }

  const handleDeleteAccount = async (id: number) => {
    await deleteGoogleAccount(id)
    loadData()
  }

  const handleAddFolder = async (accountId: number, data: any) => {
    await addGoogleFolder(accountId, data)
    setAddingFolderTo(null)
    loadData()
  }

  const handleBrowseSelect = async (accountId: number, items: SelectedItem[]) => {
    // Add each selected item as a folder/file
    for (const item of items) {
      await addGoogleFolder(accountId, {
        folder_id: item.id,
        folder_name: item.name,
        recursive: item.is_folder ? item.recursive : false,
      })
    }
    setBrowsingFoldersFor(null)
    loadData()
  }

  const handleUpdateExclusions = async (accountId: number, folderId: number, excludeIds: string[]) => {
    await updateGoogleFolder(accountId, folderId, { exclude_folder_ids: excludeIds })
    setManagingExclusionsFor(null)
    loadData()
  }

  const handleDeleteFolder = async (accountId: number, folderId: number) => {
    await deleteGoogleFolder(accountId, folderId)
    loadData()
  }

  const handleToggleFolderActive = async (accountId: number, folderId: number, active: boolean) => {
    await updateGoogleFolder(accountId, folderId, { active: !active })
    loadData()
  }

  const handleSyncFolder = async (accountId: number, folderId: number) => {
    await syncGoogleFolder(accountId, folderId)
    loadData()
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  // Show OAuth config upload if not configured
  if (oauthConfig === null) {
    return (
      <div className="source-panel">
        <div className="panel-header">
          <h3>Google Drive</h3>
        </div>
        <div className="oauth-config-setup">
          <h4>OAuth Configuration Required</h4>
          <p>Upload your Google OAuth credentials JSON file to enable Google Drive integration.</p>
          <p className="form-hint">Get this from the Google Cloud Console under APIs & Services → Credentials.</p>
          <div className="config-upload">
            <label className="upload-btn">
              {uploadingConfig ? 'Uploading...' : 'Upload Credentials JSON'}
              <input
                type="file"
                accept=".json"
                onChange={handleConfigUpload}
                disabled={uploadingConfig}
                style={{ display: 'none' }}
              />
            </label>
          </div>
        </div>
      </div>
    )
  }

  // Show OAuth config info section
  const OAuthConfigSection = () => (
    <div className="oauth-config-info">
      <details>
        <summary>OAuth Configuration</summary>
        <div className="config-details">
          <p><strong>Project:</strong> {oauthConfig.project_id}</p>
          <p><strong>Client ID:</strong> {oauthConfig.client_id.substring(0, 20)}...</p>
          <p><strong>Redirect URIs:</strong></p>
          <ul>
            {oauthConfig.redirect_uris.map((uri, i) => (
              <li key={i}>{uri}</li>
            ))}
          </ul>
          <div className="config-actions">
            <label className="upload-btn small">
              {uploadingConfig ? 'Uploading...' : 'Replace Config'}
              <input
                type="file"
                accept=".json"
                onChange={handleConfigUpload}
                disabled={uploadingConfig}
                style={{ display: 'none' }}
              />
            </label>
            <button className="delete-btn small" onClick={handleDeleteConfig}>
              Delete Config
            </button>
          </div>
        </div>
      </details>
    </div>
  )

  return (
    <div className="source-panel">
      <div className="panel-header">
        <h3>Google Drive Accounts</h3>
        <button className="add-btn" onClick={handleConnect}>Connect Account</button>
      </div>

      <OAuthConfigSection />

      {accounts.length === 0 ? (
        <EmptyState
          message="No Google Drive accounts connected"
          actionLabel="Connect Google Account"
          onAction={handleConnect}
        />
      ) : (
        <div className="source-list">
          {accounts.map(account => (
            <div key={account.id} className="google-account-card">
              <div className="source-card-header">
                <div className="source-card-info">
                  <h4>{account.name}</h4>
                  <p className="source-subtitle">{account.email}</p>
                </div>
                <div className="source-card-actions-inline">
                  <StatusBadge active={account.active} />
                  <button className="delete-btn" onClick={() => handleDeleteAccount(account.id)}>Disconnect</button>
                </div>
              </div>

              {account.sync_error && (
                <div className="sync-error-banner">{account.sync_error}</div>
              )}

              <div className="folders-section">
                <div className="folders-header">
                  <h5>Synced Folders ({account.folders.length})</h5>
                  <div className="folders-actions">
                    <button className="add-btn small" onClick={() => setBrowsingFoldersFor(account.id)}>Browse & Add</button>
                    <button className="add-btn small secondary" onClick={() => setAddingFolderTo(account.id)}>Add by ID</button>
                  </div>
                </div>

                {account.folders.length === 0 ? (
                  <p className="no-folders">No folders configured for sync</p>
                ) : (
                  <div className="folders-list">
                    {account.folders.map(folder => (
                      <div key={folder.id} className={`folder-card ${folder.active ? '' : 'inactive'}`}>
                        <div className="folder-info">
                          <a
                            className="folder-name"
                            href={`https://drive.google.com/open?id=${folder.folder_id}`}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            {folder.folder_name}
                          </a>
                          {folder.folder_path && <span className="folder-path">{folder.folder_path}</span>}
                          <SyncStatus lastSyncAt={folder.last_sync_at} />
                        </div>
                        <div className="folder-settings">
                          {folder.recursive && <span className="setting-badge">Recursive</span>}
                          {folder.include_shared && <span className="setting-badge">Shared</span>}
                          {folder.exclude_folder_ids.length > 0 && (
                            <span className="setting-badge warning">
                              {folder.exclude_folder_ids.length} excluded
                            </span>
                          )}
                        </div>
                        <div className="folder-actions">
                          <SyncButton
                            onSync={() => handleSyncFolder(account.id, folder.id)}
                            disabled={!folder.active || !account.active}
                            label="Sync"
                          />
                          {folder.recursive && (
                            <button
                              className="exclusions-btn"
                              onClick={() => setManagingExclusionsFor({ accountId: account.id, folder })}
                            >
                              Exclusions
                            </button>
                          )}
                          <button
                            className="toggle-btn"
                            onClick={() => handleToggleFolderActive(account.id, folder.id, folder.active)}
                          >
                            {folder.active ? 'Disable' : 'Enable'}
                          </button>
                          <button
                            className="delete-btn small"
                            onClick={() => handleDeleteFolder(account.id, folder.id)}
                          >
                            Remove
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {addingFolderTo && (
        <GoogleFolderForm
          accountId={addingFolderTo}
          onSubmit={(data) => handleAddFolder(addingFolderTo, data)}
          onCancel={() => setAddingFolderTo(null)}
        />
      )}

      {browsingFoldersFor && (
        <FolderBrowser
          accountId={browsingFoldersFor}
          onSelect={(items) => handleBrowseSelect(browsingFoldersFor, items)}
          onCancel={() => setBrowsingFoldersFor(null)}
        />
      )}

      {managingExclusionsFor && (
        <ExclusionBrowser
          accountId={managingExclusionsFor.accountId}
          folder={managingExclusionsFor.folder}
          onSave={(excludeIds) => handleUpdateExclusions(managingExclusionsFor.accountId, managingExclusionsFor.folder.id, excludeIds)}
          onCancel={() => setManagingExclusionsFor(null)}
        />
      )}
    </div>
  )
}

// === Google Drive Folder Browser ===

interface PathItem {
  id: string
  name: string
}

interface SelectedItem {
  id: string
  name: string
  is_folder: boolean
  recursive: boolean
}

interface FolderBrowserProps {
  accountId: number
  onSelect: (items: SelectedItem[]) => void
  onCancel: () => void
}

const FolderBrowser = ({ accountId, onSelect, onCancel }: FolderBrowserProps) => {
  const { browseGoogleDrive } = useSources()
  const [path, setPath] = useState<PathItem[]>([{ id: 'root', name: 'My Drive' }])
  const [items, setItems] = useState<DriveItem[]>([])
  const [selected, setSelected] = useState<Map<string, SelectedItem>>(new Map())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const currentFolderId = path[path.length - 1].id

  const loadFolder = useCallback(async (folderId: string) => {
    setLoading(true)
    setError(null)
    try {
      const response = await browseGoogleDrive(accountId, folderId)
      setItems(response.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load folder')
    } finally {
      setLoading(false)
    }
  }, [accountId, browseGoogleDrive])

  useEffect(() => {
    loadFolder(currentFolderId)
  }, [currentFolderId, loadFolder])

  const navigateToFolder = (item: DriveItem) => {
    setPath([...path, { id: item.id, name: item.name }])
  }

  const navigateToPathIndex = (index: number) => {
    setPath(path.slice(0, index + 1))
  }

  const toggleSelect = (item: DriveItem) => {
    const newSelected = new Map(selected)
    if (newSelected.has(item.id)) {
      newSelected.delete(item.id)
    } else {
      newSelected.set(item.id, {
        id: item.id,
        name: item.name,
        is_folder: item.is_folder,
        recursive: true,
      })
    }
    setSelected(newSelected)
  }

  const toggleRecursive = (itemId: string) => {
    const newSelected = new Map(selected)
    const item = newSelected.get(itemId)
    if (item) {
      newSelected.set(itemId, { ...item, recursive: !item.recursive })
      setSelected(newSelected)
    }
  }

  const handleAdd = () => {
    onSelect(Array.from(selected.values()))
  }

  return (
    <Modal title="Browse Google Drive" onClose={onCancel}>
      <div className="folder-browser">
        {/* Breadcrumb */}
        <div className="folder-breadcrumb">
          {path.map((item, index) => (
            <span key={item.id}>
              {index > 0 && <span className="breadcrumb-sep">&gt;</span>}
              <button
                className={`breadcrumb-item ${index === path.length - 1 ? 'current' : ''}`}
                onClick={() => navigateToPathIndex(index)}
                disabled={index === path.length - 1}
              >
                {item.name}
              </button>
            </span>
          ))}
        </div>

        {/* Content */}
        {error && <div className="form-error">{error}</div>}

        {loading ? (
          <div className="folder-loading">Loading...</div>
        ) : items.length === 0 ? (
          <div className="folder-empty">This folder is empty</div>
        ) : (
          <div className="folder-list">
            {items.map(item => (
              <div key={item.id} className={`folder-item ${selected.has(item.id) ? 'selected' : ''}`}>
                <label className="folder-item-checkbox">
                  <input
                    type="checkbox"
                    checked={selected.has(item.id)}
                    onChange={() => toggleSelect(item)}
                  />
                </label>
                <span className="folder-item-icon">
                  {item.is_folder ? '📁' : '📄'}
                </span>
                {item.id === 'shared' ? (
                  <span className="folder-item-name">{item.name}</span>
                ) : (
                  <a
                    className="folder-item-name"
                    href={item.is_folder
                      ? `https://drive.google.com/drive/folders/${item.id}`
                      : `https://drive.google.com/file/d/${item.id}`
                    }
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {item.name}
                  </a>
                )}
                {item.is_folder && selected.has(item.id) && (
                  <label className="folder-item-recursive">
                    <input
                      type="checkbox"
                      checked={selected.get(item.id)?.recursive ?? true}
                      onChange={() => toggleRecursive(item.id)}
                    />
                    <span>Recursive</span>
                  </label>
                )}
                {item.is_folder && (
                  <button
                    className="folder-item-enter"
                    onClick={() => navigateToFolder(item)}
                    title="Browse folder"
                  >
                    →
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Footer */}
        <div className="folder-browser-footer">
          <span className="selected-count">
            {selected.size} item{selected.size !== 1 ? 's' : ''} selected
          </span>
          <div className="folder-browser-actions">
            <button type="button" className="cancel-btn" onClick={onCancel}>Cancel</button>
            <button
              type="button"
              className="submit-btn"
              onClick={handleAdd}
              disabled={selected.size === 0}
            >
              Add Selected
            </button>
          </div>
        </div>
      </div>
    </Modal>
  )
}

// === Exclusion Browser ===

interface ExclusionBrowserProps {
  accountId: number
  folder: GoogleFolder
  onSave: (excludeIds: string[]) => void
  onCancel: () => void
}

interface ExcludedFolder {
  id: string
  name: string
  path: string
}

const ExclusionBrowser = ({ accountId, folder, onSave, onCancel }: ExclusionBrowserProps) => {
  const { browseGoogleDrive } = useSources()
  const [path, setPath] = useState<PathItem[]>([{ id: folder.folder_id, name: folder.folder_name }])
  const [items, setItems] = useState<DriveItem[]>([])
  const [excluded, setExcluded] = useState<Map<string, ExcludedFolder>>(() => {
    // Initialize with current exclusions (we don't have names, so use ID as name)
    const map = new Map<string, ExcludedFolder>()
    for (const id of folder.exclude_folder_ids) {
      map.set(id, { id, name: id, path: '(previously excluded)' })
    }
    return map
  })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const currentFolderId = path[path.length - 1].id
  const currentPath = path.map(p => p.name).join(' > ')

  const loadFolder = useCallback(async (folderId: string) => {
    setLoading(true)
    setError(null)
    try {
      const response = await browseGoogleDrive(accountId, folderId)
      // Only show folders, not files
      setItems(response.items.filter(item => item.is_folder))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load folder')
    } finally {
      setLoading(false)
    }
  }, [accountId, browseGoogleDrive])

  useEffect(() => {
    loadFolder(currentFolderId)
  }, [currentFolderId, loadFolder])

  const navigateToFolder = (item: DriveItem) => {
    setPath([...path, { id: item.id, name: item.name }])
  }

  const navigateToPathIndex = (index: number) => {
    setPath(path.slice(0, index + 1))
  }

  const toggleExclude = (item: DriveItem) => {
    const newExcluded = new Map(excluded)
    if (newExcluded.has(item.id)) {
      newExcluded.delete(item.id)
    } else {
      newExcluded.set(item.id, {
        id: item.id,
        name: item.name,
        path: currentPath + ' > ' + item.name,
      })
    }
    setExcluded(newExcluded)
  }

  const removeExclusion = (id: string) => {
    const newExcluded = new Map(excluded)
    newExcluded.delete(id)
    setExcluded(newExcluded)
  }

  const handleSave = () => {
    onSave(Array.from(excluded.keys()))
  }

  return (
    <Modal title={`Manage Exclusions: ${folder.folder_name}`} onClose={onCancel}>
      <div className="exclusion-browser">
        {/* Current exclusions */}
        {excluded.size > 0 && (
          <div className="excluded-list">
            <h5>Excluded Folders ({excluded.size})</h5>
            <div className="excluded-items">
              {Array.from(excluded.values()).map(item => (
                <div key={item.id} className="excluded-item">
                  <span className="excluded-name" title={item.path}>
                    📁 {item.name}
                  </span>
                  <button
                    className="remove-exclusion-btn"
                    onClick={() => removeExclusion(item.id)}
                    title="Remove exclusion"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Breadcrumb */}
        <div className="folder-breadcrumb">
          {path.map((item, index) => (
            <span key={item.id}>
              {index > 0 && <span className="breadcrumb-sep">&gt;</span>}
              <button
                className={`breadcrumb-item ${index === path.length - 1 ? 'current' : ''}`}
                onClick={() => navigateToPathIndex(index)}
                disabled={index === path.length - 1}
              >
                {item.name}
              </button>
            </span>
          ))}
        </div>

        {/* Content */}
        {error && <div className="form-error">{error}</div>}

        {loading ? (
          <div className="folder-loading">Loading...</div>
        ) : items.length === 0 ? (
          <div className="folder-empty">No subfolders in this folder</div>
        ) : (
          <div className="folder-list">
            {items.map(item => (
              <div key={item.id} className={`folder-item ${excluded.has(item.id) ? 'excluded' : ''}`}>
                <label className="folder-item-checkbox">
                  <input
                    type="checkbox"
                    checked={excluded.has(item.id)}
                    onChange={() => toggleExclude(item)}
                  />
                </label>
                <span className="folder-item-icon">📁</span>
                <a
                  className="folder-item-name"
                  href={`https://drive.google.com/drive/folders/${item.id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                >
                  {item.name}
                </a>
                {excluded.has(item.id) && (
                  <span className="exclusion-badge">Excluded</span>
                )}
                <button
                  className="folder-item-enter"
                  onClick={() => navigateToFolder(item)}
                  title="Browse subfolder"
                >
                  →
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Footer */}
        <div className="folder-browser-footer">
          <span className="selected-count">
            {excluded.size} folder{excluded.size !== 1 ? 's' : ''} excluded
          </span>
          <div className="folder-browser-actions">
            <button type="button" className="cancel-btn" onClick={onCancel}>Cancel</button>
            <button
              type="button"
              className="submit-btn"
              onClick={handleSave}
            >
              Save Exclusions
            </button>
          </div>
        </div>
      </div>
    </Modal>
  )
}

// === Calendar Panel ===

import { CalendarEvent } from '@/hooks/useSources'

interface GroupedEvents {
  [calendarName: string]: CalendarEvent[]
}

const CalendarPanel = () => {
  const {
    listCalendarAccounts, createCalendarAccount, updateCalendarAccount,
    deleteCalendarAccount, syncCalendarAccount, listGoogleAccounts, getUpcomingEvents
  } = useSources()
  const [accounts, setAccounts] = useState<CalendarAccount[]>([])
  const [googleAccounts, setGoogleAccounts] = useState<GoogleAccount[]>([])
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [expandedCalendars, setExpandedCalendars] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingAccount, setEditingAccount] = useState<CalendarAccount | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [calendarData, googleData, eventsData] = await Promise.all([
        listCalendarAccounts(),
        listGoogleAccounts(),
        getUpcomingEvents({ days: 365, limit: 200 })
      ])
      setAccounts(calendarData)
      setGoogleAccounts(googleData)
      setEvents(eventsData)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load accounts')
    } finally {
      setLoading(false)
    }
  }, [listCalendarAccounts, listGoogleAccounts, getUpcomingEvents])

  useEffect(() => { loadData() }, [loadData])

  const handleCreate = async (data: any) => {
    await createCalendarAccount(data)
    setShowForm(false)
    loadData()
  }

  const handleUpdate = async (data: any) => {
    if (editingAccount) {
      await updateCalendarAccount(editingAccount.id, data)
      setEditingAccount(null)
      loadData()
    }
  }

  const handleDelete = async (id: number) => {
    await deleteCalendarAccount(id)
    loadData()
  }

  const handleToggleActive = async (account: CalendarAccount) => {
    await updateCalendarAccount(account.id, { active: !account.active })
    loadData()
  }

  const handleSync = async (id: number) => {
    await syncCalendarAccount(id)
    loadData()
  }

  const toggleCalendar = (calendarName: string) => {
    const newExpanded = new Set(expandedCalendars)
    if (newExpanded.has(calendarName)) {
      newExpanded.delete(calendarName)
    } else {
      newExpanded.add(calendarName)
    }
    setExpandedCalendars(newExpanded)
  }

  // Group events by account ID and then by calendar name
  const getEventsForAccount = (accountId: number): GroupedEvents => {
    return events
      .filter(event => event.calendar_account_id === accountId)
      .reduce((acc, event) => {
        const calName = event.calendar_name || 'Unknown'
        if (!acc[calName]) acc[calName] = []
        acc[calName].push(event)
        return acc
      }, {} as GroupedEvents)
  }

  const formatEventDate = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  }

  const formatEventTime = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  return (
    <div className="source-panel">
      <div className="panel-header">
        <h3>Calendar Accounts</h3>
        <button className="add-btn" onClick={() => setShowForm(true)}>Add Calendar</button>
      </div>

      {accounts.length === 0 ? (
        <EmptyState
          message="No calendar accounts configured"
          actionLabel="Add Calendar"
          onAction={() => setShowForm(true)}
        />
      ) : (
        <div className="source-list">
          {accounts.map(account => (
            <div key={account.id} className="calendar-account-card">
              <div className="source-card-header">
                <div className="source-card-info">
                  <h4>{account.name}</h4>
                  <p className="source-subtitle">
                    {account.calendar_type === 'google'
                      ? `Google Calendar (${account.google_account?.email || 'linked'})`
                      : `CalDAV: ${account.caldav_url}`
                    }
                  </p>
                </div>
                <div className="source-card-actions-inline">
                  <StatusBadge active={account.active} onClick={() => handleToggleActive(account)} />
                  <SyncButton onSync={() => handleSync(account.id)} disabled={!account.active} label="Sync" />
                  <button className="edit-btn" onClick={() => setEditingAccount(account)}>Edit</button>
                  <button className="delete-btn" onClick={() => handleDelete(account.id)}>Delete</button>
                </div>
              </div>

              <div className="source-details">
                <span>Type: {account.calendar_type === 'google' ? 'Google Calendar' : 'CalDAV'}</span>
                <SyncStatus lastSyncAt={account.last_sync_at} />
                {account.sync_error && (
                  <span className="sync-error">Error: {account.sync_error}</span>
                )}
              </div>

              {/* Events grouped by calendar */}
              <div className="calendar-events-section">
                <h5>Calendars & Events</h5>
                {(() => {
                  const accountEvents = getEventsForAccount(account.id)
                  return Object.keys(accountEvents).length === 0 ? (
                    <p className="no-events">No events synced yet</p>
                  ) : (
                  <div className="calendar-groups">
                    {Object.entries(accountEvents).map(([calendarName, calEvents]) => (
                      <div key={calendarName} className="calendar-group">
                        <button
                          className={`calendar-group-header ${expandedCalendars.has(calendarName) ? 'expanded' : ''}`}
                          onClick={() => toggleCalendar(calendarName)}
                        >
                          <span className="calendar-expand-icon">
                            {expandedCalendars.has(calendarName) ? '▼' : '▶'}
                          </span>
                          <span className="calendar-group-name">{calendarName}</span>
                          <span className="calendar-event-count">{calEvents.length} events</span>
                        </button>
                        {expandedCalendars.has(calendarName) && (
                          <div className="calendar-events-list">
                            {calEvents.map((event, idx) => (
                              <div key={`${event.id}-${idx}`} className={`calendar-event-item ${event.all_day ? 'all-day' : ''}`}>
                                <div className="event-date-col">
                                  <span className="event-date">{formatEventDate(event.start_time)}</span>
                                  {!event.all_day && (
                                    <span className="event-time">{formatEventTime(event.start_time)}</span>
                                  )}
                                  {event.all_day && <span className="event-time all-day-badge">All day</span>}
                                </div>
                                <div className="event-info-col">
                                  <span className="event-title">{event.event_title}</span>
                                  {event.location && <span className="event-location">{event.location}</span>}
                                  {event.recurrence_rule && <span className="event-recurring-badge">Recurring</span>}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                  )
                })()}
              </div>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <CalendarForm
          googleAccounts={googleAccounts}
          onSubmit={handleCreate}
          onCancel={() => setShowForm(false)}
        />
      )}

      {editingAccount && (
        <CalendarForm
          account={editingAccount}
          googleAccounts={googleAccounts}
          onSubmit={handleUpdate}
          onCancel={() => setEditingAccount(null)}
        />
      )}
    </div>
  )
}

interface CalendarFormProps {
  account?: CalendarAccount
  googleAccounts: GoogleAccount[]
  onSubmit: (data: any) => Promise<void>
  onCancel: () => void
}

const CalendarForm = ({ account, googleAccounts, onSubmit, onCancel }: CalendarFormProps) => {
  const [formData, setFormData] = useState({
    name: account?.name || '',
    calendar_type: account?.calendar_type || 'caldav' as 'caldav' | 'google',
    caldav_url: account?.caldav_url || '',
    caldav_username: account?.caldav_username || '',
    caldav_password: '',
    google_account_id: account?.google_account_id || undefined as number | undefined,
    tags: account?.tags || [],
    check_interval: account?.check_interval || 15,
    sync_past_days: account?.sync_past_days || 30,
    sync_future_days: account?.sync_future_days || 90,
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const data: any = {
        name: formData.name,
        calendar_type: formData.calendar_type,
        tags: formData.tags,
        check_interval: formData.check_interval,
        sync_past_days: formData.sync_past_days,
        sync_future_days: formData.sync_future_days,
      }

      if (formData.calendar_type === 'caldav') {
        data.caldav_url = formData.caldav_url
        data.caldav_username = formData.caldav_username
        if (formData.caldav_password) {
          data.caldav_password = formData.caldav_password
        }
      } else {
        data.google_account_id = formData.google_account_id
      }

      await onSubmit(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title={account ? 'Edit Calendar Account' : 'Add Calendar Account'} onClose={onCancel}>
      <form onSubmit={handleSubmit} className="source-form">
        {error && <div className="form-error">{error}</div>}

        <div className="form-group">
          <label>Name</label>
          <input
            type="text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            required
            placeholder="My Calendar"
          />
        </div>

        <div className="form-group">
          <label>Calendar Type</label>
          <select
            value={formData.calendar_type}
            onChange={e => setFormData({ ...formData, calendar_type: e.target.value as 'caldav' | 'google' })}
            disabled={!!account}
          >
            <option value="caldav">CalDAV (Radicale, Nextcloud, etc.)</option>
            <option value="google">Google Calendar</option>
          </select>
        </div>

        {formData.calendar_type === 'caldav' ? (
          <>
            <div className="form-group">
              <label>CalDAV Server URL</label>
              <input
                type="url"
                value={formData.caldav_url}
                onChange={e => setFormData({ ...formData, caldav_url: e.target.value })}
                required={!account}
                placeholder="https://caldav.example.com/user/calendar/"
              />
            </div>

            <div className="form-group">
              <label>Username</label>
              <input
                type="text"
                value={formData.caldav_username}
                onChange={e => setFormData({ ...formData, caldav_username: e.target.value })}
                required={!account}
              />
            </div>

            <div className="form-group">
              <label>Password {account && '(leave blank to keep current)'}</label>
              <input
                type="password"
                value={formData.caldav_password}
                onChange={e => setFormData({ ...formData, caldav_password: e.target.value })}
                required={!account}
              />
            </div>
          </>
        ) : (
          <div className="form-group">
            <label>Google Account</label>
            {googleAccounts.length === 0 ? (
              <p className="form-hint">
                No Google accounts connected. Connect a Google account in the Google Drive tab first.
              </p>
            ) : (
              <select
                value={formData.google_account_id || ''}
                onChange={e => setFormData({ ...formData, google_account_id: parseInt(e.target.value) || undefined })}
                required
              >
                <option value="">Select a Google account...</option>
                {googleAccounts.map(ga => (
                  <option key={ga.id} value={ga.id}>{ga.email}</option>
                ))}
              </select>
            )}
          </div>
        )}

        <div className="form-row">
          <div className="form-group">
            <label>Sync Past Days</label>
            <input
              type="number"
              value={formData.sync_past_days}
              onChange={e => setFormData({ ...formData, sync_past_days: parseInt(e.target.value) || 30 })}
              min={0}
              max={365}
            />
          </div>
          <div className="form-group">
            <label>Sync Future Days</label>
            <input
              type="number"
              value={formData.sync_future_days}
              onChange={e => setFormData({ ...formData, sync_future_days: parseInt(e.target.value) || 90 })}
              min={0}
              max={365}
            />
          </div>
        </div>

        <IntervalInput
          value={formData.check_interval}
          onChange={check_interval => setFormData({ ...formData, check_interval })}
          label="Check interval"
        />

        <div className="form-group">
          <label>Tags</label>
          <TagsInput
            tags={formData.tags}
            onChange={tags => setFormData({ ...formData, tags })}
          />
        </div>

        <div className="form-actions">
          <button type="button" className="cancel-btn" onClick={onCancel}>Cancel</button>
          <button type="submit" className="submit-btn" disabled={submitting}>
            {submitting ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

// === Books Panel ===

interface Book {
  id: number
  title: string
  author: string | null
  publisher: string | null
  published: string | null
  language: string | null
  total_pages: number | null
  tags: string[]
  section_count: number
  file_path: string | null
}

const BooksPanel = () => {
  const { apiCall } = useAuth()
  const [books, setBooks] = useState<Book[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadBooks = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await apiCall('/books')
      if (!response.ok) throw new Error('Failed to fetch books')
      const data = await response.json()
      setBooks(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load books')
    } finally {
      setLoading(false)
    }
  }, [apiCall])

  useEffect(() => { loadBooks() }, [loadBooks])

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return

    setUploading(true)
    setUploadError(null)
    setUploadSuccess(null)

    try {
      for (const file of Array.from(files)) {
        const formData = new FormData()
        formData.append('file', file)

        const response = await apiCall('/books/upload', {
          method: 'POST',
          body: formData,
        })

        if (!response.ok) {
          const data = await response.json()
          throw new Error(data.detail || 'Upload failed')
        }
      }
      setUploadSuccess(`${files.length} book(s) uploaded and queued for processing`)
      loadBooks()
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
    }
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadBooks} />

  return (
    <div className="source-panel">
      <div className="panel-header">
        <h3>Books</h3>
        <div className="panel-header-actions">
          <span className="item-count">{books.length} books</span>
          <label className="upload-btn">
            {uploading ? 'Uploading...' : 'Upload Books'}
            <input
              ref={fileInputRef}
              type="file"
              accept=".epub,.pdf,.mobi,.azw,.azw3"
              multiple
              onChange={handleUpload}
              disabled={uploading}
              style={{ display: 'none' }}
            />
          </label>
        </div>
      </div>

      {uploadError && <div className="upload-error">{uploadError}</div>}
      {uploadSuccess && <div className="upload-success">{uploadSuccess}</div>}

      {books.length === 0 ? (
        <EmptyState
          message="No books indexed yet"
          actionLabel="Upload Books"
          onAction={() => fileInputRef.current?.click()}
        />
      ) : (
        <div className="source-list">
          {books.map(book => (
            <div key={book.id} className="source-card">
              <div className="source-card-header">
                <div className="source-card-info">
                  <h4>
                    {book.file_path ? (
                      <a href={`/files/${book.file_path}?download=true`} title="Download book">
                        {book.title}
                      </a>
                    ) : (
                      book.title
                    )}
                  </h4>
                  {book.author && <p className="source-subtitle">by {book.author}</p>}
                </div>
              </div>
              <div className="source-details">
                {book.publisher && <span>Publisher: {book.publisher}</span>}
                {book.total_pages && <span>{book.total_pages} pages</span>}
                {book.section_count > 0 && <span>{book.section_count} sections</span>}
                {book.language && <span>Language: {book.language}</span>}
              </div>
              {book.tags.length > 0 && (
                <div className="tags">
                  {book.tags.map(tag => <span key={tag} className="tag">{tag}</span>)}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// === Forums Panel ===

const ForumsPanel = () => {
  const { apiCall } = useAuth()
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState<string | null>(null)
  const [syncSuccess, setSyncSuccess] = useState<string | null>(null)

  // Sync settings
  const [minKarma, setMinKarma] = useState(10)
  const [limit, setLimit] = useState(50)
  const [maxItems, setMaxItems] = useState(1000)
  const [daysBack, setDaysBack] = useState(30)
  const [af, setAf] = useState(false)

  const handleSync = async () => {
    setSyncing(true)
    setSyncError(null)
    setSyncSuccess(null)

    try {
      const sinceDate = new Date()
      sinceDate.setDate(sinceDate.getDate() - daysBack)

      const response = await apiCall('/forums/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          since: sinceDate.toISOString(),
          min_karma: minKarma,
          limit: limit,
          max_items: maxItems,
          af: af,
          tags: [],
        }),
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'Sync failed')
      }

      setSyncSuccess('LessWrong sync started. Posts will be indexed in the background.')
    } catch (e) {
      setSyncError(e instanceof Error ? e.message : 'Sync failed')
    } finally {
      setSyncing(false)
    }
  }

  return (
    <div className="source-panel">
      <div className="panel-header">
        <h3>Forums (LessWrong)</h3>
      </div>

      <div className="sync-settings-card">
        <h4>Sync Settings</h4>
        <p className="sync-description">
          Configure and trigger synchronization of posts from LessWrong. Posts matching your criteria will be indexed for search.
        </p>
        <div className="sync-settings-form">
          <div className="form-row">
            <div className="form-group">
              <label>Days Back</label>
              <input
                type="number"
                value={daysBack}
                onChange={e => setDaysBack(parseInt(e.target.value) || 30)}
                min={1}
                max={365}
              />
            </div>
            <div className="form-group">
              <label>Min Karma</label>
              <input
                type="number"
                value={minKarma}
                onChange={e => setMinKarma(parseInt(e.target.value) || 0)}
                min={0}
              />
            </div>
            <div className="form-group">
              <label>Posts per request</label>
              <input
                type="number"
                value={limit}
                onChange={e => setLimit(parseInt(e.target.value) || 50)}
                min={1}
                max={100}
              />
            </div>
            <div className="form-group">
              <label>Max Items</label>
              <input
                type="number"
                value={maxItems}
                onChange={e => setMaxItems(parseInt(e.target.value) || 1000)}
                min={1}
                max={10000}
              />
            </div>
          </div>
          <div className="form-group checkbox">
            <label>
              <input
                type="checkbox"
                checked={af}
                onChange={e => setAf(e.target.checked)}
              />
              Alignment Forum only
            </label>
          </div>
          <button
            className="sync-btn"
            onClick={handleSync}
            disabled={syncing}
          >
            {syncing ? 'Syncing...' : 'Sync LessWrong'}
          </button>
        </div>
        {syncError && <div className="sync-error">{syncError}</div>}
        {syncSuccess && <div className="sync-success">{syncSuccess}</div>}
      </div>
    </div>
  )
}

// === Photos Panel ===

interface Photo {
  id: number
  filename: string
  file_path: string | null
  exif_taken_at: string | null
  camera: string | null
  tags: string[]
  mime_type: string | null
}

const PhotosPanel = () => {
  const { apiCall } = useAuth()
  const [photos, setPhotos] = useState<Photo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadPhotos = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await apiCall('/photos')
      if (!response.ok) throw new Error('Failed to fetch photos')
      const data = await response.json()
      setPhotos(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load photos')
    } finally {
      setLoading(false)
    }
  }, [apiCall])

  useEffect(() => { loadPhotos() }, [loadPhotos])

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return

    setUploading(true)
    setUploadError(null)
    setUploadSuccess(null)

    try {
      let successCount = 0
      for (const file of Array.from(files)) {
        const formData = new FormData()
        formData.append('file', file)

        const response = await apiCall('/photos/upload', {
          method: 'POST',
          body: formData,
        })

        if (!response.ok) {
          const data = await response.json()
          throw new Error(data.detail || 'Upload failed')
        }
        successCount++
      }
      setUploadSuccess(`${successCount} photo(s) uploaded successfully`)
      loadPhotos()
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
    }
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return null
    const date = new Date(dateStr)
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadPhotos} />

  return (
    <div className="source-panel">
      <div className="panel-header">
        <h3>Photos</h3>
        <div className="panel-header-actions">
          <span className="item-count">{photos.length} photos</span>
          <label className="upload-btn">
            {uploading ? 'Uploading...' : 'Upload Photos'}
            <input
              ref={fileInputRef}
              type="file"
              accept=".jpg,.jpeg,.png,.gif,.webp,.heic,.heif"
              multiple
              onChange={handleUpload}
              disabled={uploading}
              style={{ display: 'none' }}
            />
          </label>
        </div>
      </div>

      {uploadError && <div className="upload-error">{uploadError}</div>}
      {uploadSuccess && <div className="upload-success">{uploadSuccess}</div>}

      {photos.length === 0 ? (
        <EmptyState
          message="No photos indexed yet"
          actionLabel="Upload Photos"
          onAction={() => fileInputRef.current?.click()}
        />
      ) : (
        <div className="photos-grid">
          {photos.map(photo => (
            <div key={photo.id} className="photo-card">
              {photo.file_path && (
                <div className="photo-preview">
                  <img src={`/files/${photo.file_path}`} alt={photo.filename} loading="lazy" />
                </div>
              )}
              <div className="photo-info">
                <span className="photo-filename" title={photo.filename}>
                  {photo.filename}
                </span>
                <div className="photo-meta">
                  {photo.exif_taken_at && <span>{formatDate(photo.exif_taken_at)}</span>}
                  {photo.camera && <span>{photo.camera}</span>}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

interface GoogleFolderFormProps {
  accountId: number
  onSubmit: (data: any) => Promise<void>
  onCancel: () => void
}

const GoogleFolderForm = ({ accountId, onSubmit, onCancel }: GoogleFolderFormProps) => {
  const [formData, setFormData] = useState({
    folder_id: '',
    folder_name: '',
    recursive: true,
    include_shared: false,
    tags: [] as string[],
    check_interval: 60,
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit(formData)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title="Add Google Drive Folder" onClose={onCancel}>
      <form onSubmit={handleSubmit} className="source-form">
        {error && <div className="form-error">{error}</div>}

        <div className="form-group">
          <label>Folder ID</label>
          <input
            type="text"
            value={formData.folder_id}
            onChange={e => setFormData({ ...formData, folder_id: e.target.value })}
            required
            placeholder="From Google Drive URL"
          />
          <p className="form-hint">Find this in the folder URL after /folders/</p>
        </div>

        <div className="form-group">
          <label>Folder Name</label>
          <input
            type="text"
            value={formData.folder_name}
            onChange={e => setFormData({ ...formData, folder_name: e.target.value })}
            required
            placeholder="My Documents"
          />
        </div>

        <div className="form-group checkboxes">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={formData.recursive}
              onChange={e => setFormData({ ...formData, recursive: e.target.checked })}
            />
            Include subfolders
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={formData.include_shared}
              onChange={e => setFormData({ ...formData, include_shared: e.target.checked })}
            />
            Include shared files
          </label>
        </div>

        <IntervalInput
          value={formData.check_interval}
          onChange={check_interval => setFormData({ ...formData, check_interval })}
          label="Check interval"
        />

        <div className="form-group">
          <label>Tags</label>
          <TagsInput
            tags={formData.tags}
            onChange={tags => setFormData({ ...formData, tags })}
          />
        </div>

        <div className="form-actions">
          <button type="button" className="cancel-btn" onClick={onCancel}>Cancel</button>
          <button type="submit" className="submit-btn" disabled={submitting}>
            {submitting ? 'Adding...' : 'Add Folder'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

export default Sources
