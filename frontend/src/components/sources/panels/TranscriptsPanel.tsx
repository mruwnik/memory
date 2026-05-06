import { useState, useEffect, useCallback } from 'react'
import { useSources, TranscriptAccount, Project } from '@/hooks/useSources'
import {
  SourceCard,
  Modal,
  TagsInput,
  EmptyState,
  LoadingState,
  ErrorState,
} from '../shared'
import { styles } from '../styles'
import { useSourcesContext } from '../Sources'

export const TranscriptsPanel = () => {
  const {
    listTranscriptAccounts,
    createTranscriptAccount,
    updateTranscriptAccount,
    deleteTranscriptAccount,
    syncTranscriptAccount,
    rescanTranscriptAccount,
    listTranscriptProviders,
    listProjects,
  } = useSources()
  const { userId } = useSourcesContext()
  const [accounts, setAccounts] = useState<TranscriptAccount[]>([])
  const [providers, setProviders] = useState<string[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingAccount, setEditingAccount] = useState<TranscriptAccount | null>(null)

  const loadAccounts = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [accountData, providerData, projectData] = await Promise.all([
        listTranscriptAccounts(userId),
        listTranscriptProviders(),
        listProjects(),
      ])
      setAccounts(accountData)
      setProviders(providerData)
      setProjects(projectData)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load transcript accounts')
    } finally {
      setLoading(false)
    }
  }, [listTranscriptAccounts, listTranscriptProviders, listProjects, userId])

  useEffect(() => { loadAccounts() }, [loadAccounts])

  const handleCreate = async (data: any) => {
    await createTranscriptAccount(data)
    setShowForm(false)
    loadAccounts()
  }

  const handleUpdate = async (data: any) => {
    if (editingAccount) {
      await updateTranscriptAccount(editingAccount.id, data)
      setEditingAccount(null)
      loadAccounts()
    }
  }

  const handleDelete = async (id: number) => {
    await deleteTranscriptAccount(id)
    loadAccounts()
  }

  const handleToggleActive = async (account: TranscriptAccount) => {
    await updateTranscriptAccount(account.id, { active: !account.active })
    loadAccounts()
  }

  const handleSync = async (id: number) => {
    await syncTranscriptAccount(id)
    loadAccounts()
  }

  const handleRescan = async (id: number) => {
    await rescanTranscriptAccount(id)
    loadAccounts()
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadAccounts} />

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>Transcript Accounts</h3>
        <button className={styles.btnAdd} onClick={() => setShowForm(true)}>Add Account</button>
      </div>

      {accounts.length === 0 ? (
        <EmptyState
          message="No transcript accounts configured. Connect Fireflies (or another supported provider) to ingest meeting transcripts."
          actionLabel="Add Transcript Account"
          onAction={() => setShowForm(true)}
        />
      ) : (
        <div className={styles.sourceList}>
          {accounts.map(account => (
            <SourceCard
              key={account.id}
              title={account.name}
              subtitle={account.provider}
              active={account.active}
              lastSyncAt={account.last_sync_at}
              syncError={account.sync_error}
              onToggleActive={() => handleToggleActive(account)}
              onEdit={() => setEditingAccount(account)}
              onDelete={() => handleDelete(account.id)}
              onSync={() => handleSync(account.id)}
              additionalActions={
                <button
                  className="py-1.5 px-3 border border-slate-200 rounded text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                  onClick={() => handleRescan(account.id)}
                  disabled={!account.active}
                  title="Walk back the full rescan window (slower; catches drift)"
                >
                  Full rescan
                </button>
              }
            >
              <div className="flex flex-wrap gap-3 mt-2 text-xs text-slate-500">
                <span>Provider: {account.provider}</span>
                {account.tags && account.tags.length > 0 && (
                  <span>Tags: {account.tags.join(', ')}</span>
                )}
                <span>Sensitivity: {account.sensitivity}</span>
                {!account.has_api_key && (
                  <span className="text-red-600">No API key set</span>
                )}
              </div>
            </SourceCard>
          ))}
        </div>
      )}

      {showForm && (
        <TranscriptForm
          providers={providers}
          projects={projects}
          onSubmit={handleCreate}
          onCancel={() => setShowForm(false)}
        />
      )}

      {editingAccount && (
        <TranscriptForm
          account={editingAccount}
          providers={providers}
          projects={projects}
          onSubmit={handleUpdate}
          onCancel={() => setEditingAccount(null)}
        />
      )}
    </div>
  )
}

interface TranscriptFormProps {
  account?: TranscriptAccount
  providers: string[]
  projects: Project[]
  onSubmit: (data: any) => Promise<void>
  onCancel: () => void
}

const TranscriptForm = ({ account, providers, projects, onSubmit, onCancel }: TranscriptFormProps) => {
  const [formData, setFormData] = useState({
    name: account?.name || '',
    provider: account?.provider || providers[0] || 'fireflies',
    api_key: '',
    webhook_secret: '',
    clear_webhook_secret: false,
    tags: account?.tags || [],
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
        tags: formData.tags,
        project_id: formData.project_id,
        sensitivity: formData.sensitivity,
      }

      if (!account) {
        // Create: provider + api_key are required
        data.provider = formData.provider
        data.api_key = formData.api_key
        if (formData.webhook_secret) {
          data.webhook_secret = formData.webhook_secret
        }
      } else {
        // Edit: only send rotations
        if (formData.api_key) {
          data.api_key = formData.api_key
        }
        // Webhook tri-state — must check `clear_webhook_secret` BEFORE the
        // truthy check on `webhook_secret`. Both branches deliberately
        // require non-empty/checked input: if the user toggled the "Remove"
        // checkbox on then off again, `webhook_secret` is left as "" and
        // `clear_webhook_secret` is false — neither branch fires, so we
        // omit the field and the backend leaves the existing secret alone.
        // If you change `else if (formData.webhook_secret)` to a presence
        // check, that "toggle then untoggle" path will silently CLEAR the
        // secret instead. Don't.
        if (formData.clear_webhook_secret) {
          data.webhook_secret = ''
        } else if (formData.webhook_secret) {
          data.webhook_secret = formData.webhook_secret
        }
      }

      await onSubmit(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  // Defensive: a create form with no providers would silently submit an
  // unsupported provider and 400. This only happens if the worker's
  // PROVIDERS dict is empty server-side (regression / misconfiguration).
  if (!account && providers.length === 0) {
    return (
      <Modal title="Add Transcript Account" onClose={onCancel}>
        <div className={styles.formError}>
          No transcript providers are available on the server. Check the
          worker configuration (PROVIDERS dict in
          <code> memory.workers.tasks.transcripts</code>).
        </div>
        <div className={styles.formActions}>
          <button type="button" className={styles.btnCancel} onClick={onCancel}>Close</button>
        </div>
      </Modal>
    )
  }

  return (
    <Modal title={account ? 'Edit Transcript Account' : 'Add Transcript Account'} onClose={onCancel}>
      <form onSubmit={handleSubmit} className={styles.form}>
        {error && <div className={styles.formError}>{error}</div>}

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Name</label>
          <input
            type="text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            required
            placeholder="My Fireflies"
            className={styles.formInput}
          />
          <p className={styles.formHint}>Display label for this account.</p>
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Provider</label>
          <select
            value={formData.provider}
            onChange={e => setFormData({ ...formData, provider: e.target.value })}
            disabled={!!account}
            required
            className={styles.formSelect}
          >
            {providers.map(p => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          {account && <p className={styles.formHint}>Provider cannot be changed after creation.</p>}
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>
            API Key {account && <span className="text-slate-400">(leave blank to keep current)</span>}
          </label>
          <input
            type="password"
            value={formData.api_key}
            onChange={e => setFormData({ ...formData, api_key: e.target.value })}
            required={!account}
            autoComplete="new-password"
            className={styles.formInput}
          />
          <p className={styles.formHint}>
            For Fireflies, generate one at fireflies.ai → Settings → Developer Settings.
          </p>
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>
            Webhook Secret {account && <span className="text-slate-400">(optional; leave blank to keep current)</span>}
          </label>
          <input
            type="password"
            value={formData.webhook_secret}
            onChange={e => setFormData({ ...formData, webhook_secret: e.target.value, clear_webhook_secret: false })}
            autoComplete="new-password"
            disabled={formData.clear_webhook_secret}
            className={styles.formInput}
          />
          {account && account.has_webhook_secret && (
            <div className={styles.formCheckbox}>
              <input
                type="checkbox"
                id="clear-webhook-secret"
                checked={formData.clear_webhook_secret}
                onChange={e => setFormData({ ...formData, clear_webhook_secret: e.target.checked, webhook_secret: '' })}
                className="rounded border-slate-300"
              />
              <label htmlFor="clear-webhook-secret" className="text-sm text-slate-700">
                Remove existing webhook secret
              </label>
            </div>
          )}
          <p className={styles.formHint}>Optional shared secret for webhook HMAC verification.</p>
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Tags</label>
          <TagsInput
            tags={formData.tags}
            onChange={tags => setFormData({ ...formData, tags })}
          />
          <p className={styles.formHint}>Tags inherited by Meetings produced from this account.</p>
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
            <p className={styles.formHint}>Project for access control.</p>
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
            <p className={styles.formHint}>Visibility of meetings from this account.</p>
          </div>
        </div>

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

export default TranscriptsPanel
