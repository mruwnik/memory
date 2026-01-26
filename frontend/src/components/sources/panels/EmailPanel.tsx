import { useState, useEffect, useCallback } from 'react'
import { useSources, EmailAccount, GoogleAccount, Project } from '@/hooks/useSources'
import {
  SourceCard,
  Modal,
  TagsInput,
  EmptyState,
  LoadingState,
  ErrorState,
} from '../shared'
import { styles } from '../styles'

export const EmailPanel = () => {
  const { listEmailAccounts, createEmailAccount, updateEmailAccount, deleteEmailAccount, syncEmailAccount, listGoogleAccounts, listProjects } = useSources()
  const [accounts, setAccounts] = useState<EmailAccount[]>([])
  const [googleAccounts, setGoogleAccounts] = useState<GoogleAccount[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingAccount, setEditingAccount] = useState<EmailAccount | null>(null)

  const loadAccounts = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [emailData, googleData, projectData] = await Promise.all([
        listEmailAccounts(),
        listGoogleAccounts(),
        listProjects()
      ])
      setAccounts(emailData)
      setGoogleAccounts(googleData)
      setProjects(projectData)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load accounts')
    } finally {
      setLoading(false)
    }
  }, [listEmailAccounts, listGoogleAccounts, listProjects])

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
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>Email Accounts</h3>
        <button className={styles.btnAdd} onClick={() => setShowForm(true)}>Add Account</button>
      </div>

      {accounts.length === 0 ? (
        <EmptyState
          message="No email accounts configured"
          actionLabel="Add Email Account"
          onAction={() => setShowForm(true)}
        />
      ) : (
        <div className={styles.sourceList}>
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
              <div className="flex flex-wrap gap-3 mt-2 text-xs text-slate-500">
                {account.account_type === 'gmail' ? (
                  <span>Type: Gmail (OAuth)</span>
                ) : (
                  <span>Server: {account.imap_server}:{account.imap_port}</span>
                )}
                {account.folders && account.folders.length > 0 && (
                  <span>Folders: {account.folders.join(', ')}</span>
                )}
              </div>
              {account.sync_error && (
                <div className={styles.errorBanner}>{account.sync_error}</div>
              )}
            </SourceCard>
          ))}
        </div>
      )}

      {showForm && (
        <EmailForm
          googleAccounts={googleAccounts}
          projects={projects}
          onSubmit={handleCreate}
          onCancel={() => setShowForm(false)}
        />
      )}

      {editingAccount && (
        <EmailForm
          account={editingAccount}
          googleAccounts={googleAccounts}
          projects={projects}
          onSubmit={handleUpdate}
          onCancel={() => setEditingAccount(null)}
        />
      )}
    </div>
  )
}

interface EmailFormProps {
  account?: EmailAccount
  googleAccounts: GoogleAccount[]
  projects: Project[]
  onSubmit: (data: any) => Promise<void>
  onCancel: () => void
}

const EmailForm = ({ account, googleAccounts, projects, onSubmit, onCancel }: EmailFormProps) => {
  const [formData, setFormData] = useState({
    name: account?.name || '',
    email_address: account?.email_address || '',
    account_type: account?.account_type || 'imap' as 'imap' | 'gmail',
    imap_server: account?.imap_server || '',
    imap_port: account?.imap_port || 993,
    username: account?.username || '',
    password: '',
    use_ssl: account?.use_ssl ?? true,
    smtp_server: account?.smtp_server || '',
    smtp_port: account?.smtp_port || 587,
    google_account_id: account?.google_account_id || undefined as number | undefined,
    folders: account?.folders || [],
    tags: account?.tags || [],
    send_enabled: account?.send_enabled ?? true,
    project_id: account?.project_id || undefined as number | undefined,
    sensitivity: account?.sensitivity || 'basic' as 'public' | 'basic' | 'internal' | 'confidential',
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
        email_address: formData.email_address,
        account_type: formData.account_type,
        folders: formData.folders,
        tags: formData.tags,
        send_enabled: formData.send_enabled,
        project_id: formData.project_id,
        sensitivity: formData.sensitivity,
      }

      if (formData.account_type === 'imap') {
        data.imap_server = formData.imap_server
        data.imap_port = formData.imap_port
        data.username = formData.username
        data.use_ssl = formData.use_ssl
        if (formData.password) {
          data.password = formData.password
        }
        if (formData.smtp_server) {
          data.smtp_server = formData.smtp_server
        }
        if (formData.smtp_port && formData.smtp_port !== 587) {
          data.smtp_port = formData.smtp_port
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
    <Modal title={account ? 'Edit Email Account' : 'Add Email Account'} onClose={onCancel}>
      <form onSubmit={handleSubmit} className={styles.form}>
        {error && <div className={styles.formError}>{error}</div>}

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Name</label>
          <input
            type="text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            required
            className={styles.formInput}
          />
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Account Type</label>
          <select
            value={formData.account_type}
            onChange={e => setFormData({ ...formData, account_type: e.target.value as 'imap' | 'gmail' })}
            disabled={!!account}
            className={styles.formSelect}
          >
            <option value="imap">IMAP (Standard Email)</option>
            <option value="gmail">Gmail (OAuth)</option>
          </select>
        </div>

        {formData.account_type === 'imap' ? (
          <>
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>Email Address</label>
              <input
                type="email"
                value={formData.email_address}
                onChange={e => setFormData({ ...formData, email_address: e.target.value })}
                required
                disabled={!!account}
                className={styles.formInput}
              />
            </div>

            <div className={styles.formRow}>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>IMAP Server</label>
                <input
                  type="text"
                  value={formData.imap_server}
                  onChange={e => setFormData({ ...formData, imap_server: e.target.value })}
                  required
                  placeholder="imap.example.com"
                  className={styles.formInput}
                />
              </div>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>Port</label>
                <input
                  type="number"
                  value={formData.imap_port}
                  onChange={e => setFormData({ ...formData, imap_port: parseInt(e.target.value) })}
                  required
                  className={styles.formInput}
                />
              </div>
            </div>

            <div className={styles.formGroup}>
              <label className={styles.formLabel}>Username</label>
              <input
                type="text"
                value={formData.username}
                onChange={e => setFormData({ ...formData, username: e.target.value })}
                required
                className={styles.formInput}
              />
            </div>

            <div className={styles.formGroup}>
              <label className={styles.formLabel}>
                Password {account && <span className="text-slate-400">(leave blank to keep current)</span>}
              </label>
              <input
                type="password"
                value={formData.password}
                onChange={e => setFormData({ ...formData, password: e.target.value })}
                required={!account}
                className={styles.formInput}
              />
            </div>

            <div className={styles.formCheckbox}>
              <input
                type="checkbox"
                id="use-ssl"
                checked={formData.use_ssl}
                onChange={e => setFormData({ ...formData, use_ssl: e.target.checked })}
                className="rounded border-slate-300"
              />
              <label htmlFor="use-ssl" className="text-sm text-slate-700">Use SSL</label>
            </div>

            <div className={styles.formRow}>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>
                  SMTP Server <span className="text-slate-400">(optional)</span>
                </label>
                <input
                  type="text"
                  value={formData.smtp_server}
                  onChange={e => setFormData({ ...formData, smtp_server: e.target.value })}
                  placeholder="smtp.example.com"
                  className={styles.formInput}
                />
              </div>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>SMTP Port</label>
                <input
                  type="number"
                  value={formData.smtp_port}
                  onChange={e => setFormData({ ...formData, smtp_port: parseInt(e.target.value) || 587 })}
                  className={styles.formInput}
                />
              </div>
            </div>
            <p className={styles.formHint}>SMTP settings for sending email. If not set, will be inferred from IMAP server.</p>
          </>
        ) : (
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Google Account</label>
            {googleAccounts.length === 0 ? (
              <p className={styles.formHint}>
                No Google accounts connected. Add a Google account in the Accounts tab first.
              </p>
            ) : (
              <>
                <select
                  value={formData.google_account_id || ''}
                  onChange={e => {
                    const googleAccountId = parseInt(e.target.value) || undefined
                    const googleAccount = googleAccounts.find(ga => ga.id === googleAccountId)
                    setFormData({
                      ...formData,
                      google_account_id: googleAccountId,
                      email_address: googleAccount?.email || formData.email_address,
                    })
                  }}
                  required
                  className={styles.formSelect}
                >
                  <option value="">Select a Google account...</option>
                  {googleAccounts.map(ga => (
                    <option key={ga.id} value={ga.id}>{ga.email}</option>
                  ))}
                </select>
                <p className={styles.formHint}>
                  Gmail uses OAuth for secure access. The email address will be set automatically.
                </p>
              </>
            )}
          </div>
        )}

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Tags</label>
          <TagsInput
            tags={formData.tags}
            onChange={tags => setFormData({ ...formData, tags })}
          />
        </div>

        <div className={styles.formRow}>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Project</label>
            <select
              value={formData.project_id || ''}
              onChange={e => setFormData({ ...formData, project_id: e.target.value ? parseInt(e.target.value) : undefined })}
              className={styles.formSelect}
            >
              <option value="">None</option>
              {projects.map(project => (
                <option key={project.id} value={project.id}>
                  {project.title} ({project.repo_path})
                </option>
              ))}
            </select>
            <p className={styles.formHint}>Project for access control (from GitHub milestones)</p>
          </div>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Sensitivity</label>
            <select
              value={formData.sensitivity}
              onChange={e => setFormData({ ...formData, sensitivity: e.target.value as 'public' | 'basic' | 'internal' | 'confidential' })}
              className={styles.formSelect}
            >
              <option value="public">Public</option>
              <option value="basic">Basic</option>
              <option value="internal">Internal</option>
              <option value="confidential">Confidential</option>
            </select>
            <p className={styles.formHint}>Visibility level for emails from this account</p>
          </div>
        </div>

        <div className={styles.formCheckbox}>
          <input
            type="checkbox"
            id="send-enabled"
            checked={formData.send_enabled}
            onChange={e => setFormData({ ...formData, send_enabled: e.target.checked })}
            className="rounded border-slate-300"
          />
          <label htmlFor="send-enabled" className="text-sm text-slate-700">
            Enable sending emails from this account
          </label>
        </div>

        <div className={styles.formActions}>
          <button type="button" className={styles.btnCancel} onClick={onCancel}>Cancel</button>
          <button
            type="submit"
            className={styles.btnSubmit}
            disabled={submitting || (formData.account_type === 'gmail' && googleAccounts.length === 0)}
          >
            {submitting ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

export default EmailPanel
