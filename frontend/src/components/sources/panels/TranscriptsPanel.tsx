import { useState, useEffect, useCallback } from 'react'
import { useSources, TranscriptAccount } from '@/hooks/useSources'
import { useProjects, Project } from '@/hooks/useProjects'
import { SourceRow, TagsInput } from '../shared'
import { RowError, Button, EmptyState, ErrorState, Field, FieldRow, FormActions, Hint, Input, Label, List, LoadingState, Modal, Notice, Section, Select, useActionNotice } from '@/components/shared/ui'
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
  } = useSources()
  const { listProjects } = useProjects()
  const { userId } = useSourcesContext()
  const [accounts, setAccounts] = useState<TranscriptAccount[]>([])
  const [providers, setProviders] = useState<string[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingAccount, setEditingAccount] = useState<TranscriptAccount | null>(null)
  const { run, notice } = useActionNotice()

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

  const handleDelete = (id: number) => run(async () => {
    await deleteTranscriptAccount(id)
    loadAccounts()
  })

  const handleToggleActive = (account: TranscriptAccount) => run(async () => {
    await updateTranscriptAccount(account.id, { active: !account.active })
    loadAccounts()
  })

  const handleSync = (id: number) => run(async () => {
    await syncTranscriptAccount(id)
    loadAccounts()
  }, 'Sync queued')

  const handleRescan = (id: number) => run(async () => {
    await rescanTranscriptAccount(id)
    loadAccounts()
  }, 'Rescan queued')

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadAccounts} />

  return (
    <Section title="Transcript Accounts" actions={<Button variant="primary" onClick={() => setShowForm(true)}>Add Account</Button>}>
      {notice}
      {accounts.length === 0 ? (
        <EmptyState
          message="No transcript accounts configured. Connect Fireflies (or another supported provider) to ingest meeting transcripts."
          actionLabel="Add Transcript Account"
          onAction={() => setShowForm(true)}
        />
      ) : (
        <List>
          {accounts.map(account => (
            <SourceRow
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
                <Button size="sm"
                  onClick={() => handleRescan(account.id)}
                  disabled={!account.active}
                  title="Walk back the full rescan window (slower; catches drift)"
                >
                  Full rescan
                </Button>
              }
              meta={[
                !!account.tags?.length && `Tags: ${account.tags.join(', ')}`,
                `Sensitivity: ${account.sensitivity}`,
              ]}
            >
              {!account.has_api_key && <RowError>No API key set</RowError>}
            </SourceRow>
          ))}
        </List>
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
    </Section>
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
        <Notice tone="error">
          No transcript providers are available on the server. Check the
          worker configuration (PROVIDERS dict in
          <code> memory.workers.tasks.transcripts</code>).
        </Notice>
        <FormActions>
          <Button type="button" onClick={onCancel}>Close</Button>
        </FormActions>
      </Modal>
    )
  }

  return (
    <Modal title={account ? 'Edit Transcript Account' : 'Add Transcript Account'} onClose={onCancel}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        <Field label="Name" hint="Display label for this account.">
          <Input
            type="text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            required
            placeholder="My Fireflies"
          />
        </Field>

        <Field>
          <Label>Provider</Label>
          <Select
            value={formData.provider}
            onChange={e => setFormData({ ...formData, provider: e.target.value })}
            disabled={!!account}
            required
          >
            {providers.map(p => (
              <option key={p} value={p}>{p}</option>
            ))}
          </Select>
          {account && <Hint>Provider cannot be changed after creation.</Hint>}
        </Field>

        <Field label={<>
            API Key {account && <span className="text-slate-500">(leave blank to keep current)</span>}</>} hint="For Fireflies, generate one at fireflies.ai → Settings → Developer Settings.">
          <Input
            type="password"
            value={formData.api_key}
            onChange={e => setFormData({ ...formData, api_key: e.target.value })}
            required={!account}
            autoComplete="new-password"
          />
        </Field>

        <Field>
          <Label>
            Webhook Secret {account && <span className="text-slate-500">(optional; leave blank to keep current)</span>}
          </Label>
          <Input
            type="password"
            value={formData.webhook_secret}
            onChange={e => setFormData({ ...formData, webhook_secret: e.target.value, clear_webhook_secret: false })}
            autoComplete="new-password"
            disabled={formData.clear_webhook_secret}
          />
          {account && account.has_webhook_secret && (
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="clear-webhook-secret"
                checked={formData.clear_webhook_secret}
                onChange={e => setFormData({ ...formData, clear_webhook_secret: e.target.checked, webhook_secret: '' })}
              />
              <label htmlFor="clear-webhook-secret" className="text-sm text-slate-700">
                Remove existing webhook secret
              </label>
            </div>
          )}
          <Hint>Optional shared secret for webhook HMAC verification.</Hint>
        </Field>

        <Field label="Tags" hint="Tags inherited by Meetings produced from this account.">
          <TagsInput
            tags={formData.tags}
            onChange={tags => setFormData({ ...formData, tags })}
          />
        </Field>

        <FieldRow>
          <Field label="Project" hint="Project for access control.">
            <Select
              value={formData.project_id || ''}
              onChange={e => setFormData({ ...formData, project_id: e.target.value ? parseInt(e.target.value) : undefined })}
            >
              <option value="">None</option>
              {projects.map(project => (
                <option key={project.id} value={project.id}>
                  {project.title} ({project.repo_path})
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Sensitivity" hint="Visibility of meetings from this account.">
            <Select
              value={formData.sensitivity}
              onChange={e => setFormData({ ...formData, sensitivity: e.target.value as 'public' | 'basic' | 'internal' | 'confidential' })}
            >
              <option value="public">Public</option>
              <option value="basic">Basic</option>
              <option value="internal">Internal</option>
              <option value="confidential">Confidential</option>
            </Select>
          </Field>
        </FieldRow>

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

export default TranscriptsPanel
