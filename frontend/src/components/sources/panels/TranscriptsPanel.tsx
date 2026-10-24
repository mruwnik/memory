import { useState, useEffect, useCallback } from 'react'
import { useSources, TranscriptAccount } from '@/hooks/useSources'
import { useProjects, Project } from '@/hooks/useProjects'
import { SourceRow, TagsInput } from '../shared'
import { RowError, Button, ConfirmButton, DetailList, EmptyState, ErrorState, Field, FieldRow, FormActions, Input, List, LoadingState, Modal, Notice, Section, Select, useActionNotice } from '@/components/shared/ui'
import { useSourcesContext } from '../Sources'

// Mirrors PUSH_ONLY_PROVIDERS in memory/common/transcripts.py: these take no API key and are never polled.
const PUSH_ONLY_PROVIDERS = ['generic']

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
  const [pushAccount, setPushAccount] = useState<TranscriptAccount | null>(null)
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
    <Section title="Transcript accounts" actions={<Button variant="primary" onClick={() => setShowForm(true)}>Add account</Button>}>
      {notice}
      {accounts.length === 0 ? (
        <EmptyState
          message="No transcript accounts configured. Connect Fireflies (or another supported provider) to ingest meeting transcripts."
          actionLabel="Add transcript account"
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
              sync={account}
              onToggleActive={() => handleToggleActive(account)}
              onEdit={() => setEditingAccount(account)}
              onDelete={() => handleDelete(account.id)}
              onSync={account.polls ? () => handleSync(account.id) : undefined}
              additionalActions={<>
                {account.polls && (
                  <Button size="sm"
                    onClick={() => handleRescan(account.id)}
                    disabled={!account.active}
                    title="Walk back the full rescan window (slower; catches drift)"
                  >
                    Full rescan
                  </Button>
                )}
                <Button size="sm" onClick={() => setPushAccount(account)} title="URL and secret for pushing transcripts to this account">
                  Push
                </Button>
              </>}
              meta={[
                !!account.tags?.length && `Tags: ${account.tags.join(', ')}`,
                `Sensitivity: ${account.sensitivity}`,
                account.webhook_secret_hint && 'Push enabled',
              ]}
            >
              {account.polls && !account.has_api_key && <RowError>No API key set</RowError>}
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

      {pushAccount && (
        <PushModal account={pushAccount} onClose={() => { setPushAccount(null); loadAccounts() }} />
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
    tags: account?.tags || [],
    project_id: account?.project_id || undefined as number | undefined,
    sensitivity: account?.sensitivity || 'basic' as 'public' | 'basic' | 'internal' | 'confidential',
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const polls = account ? account.polls : !PUSH_ONLY_PROVIDERS.includes(formData.provider)

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

      if (!account) data.provider = formData.provider
      // On edit a blank key means "keep the current one".
      if (polls && formData.api_key) data.api_key = formData.api_key

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
      <Modal title="Add transcript account" onClose={onCancel}>
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
    <Modal title={account ? 'Edit transcript account' : 'Add transcript account'} onClose={onCancel}>
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

        <Field label="Provider" hint={account
          ? 'Provider cannot be changed after creation.'
          : 'Generic accounts are push-only: transcripts arrive at the account\'s ingest URL.'}>
          <Select
            value={formData.provider}
            onChange={e => setFormData({ ...formData, provider: e.target.value })}
            disabled={!!account}
            required
          >
            {providers.map(p => (
              <option key={p} value={p}>{PUSH_ONLY_PROVIDERS.includes(p) ? `${p} (push only)` : p}</option>
            ))}
          </Select>
        </Field>

        {polls && (
          <Field label={<>
              API key {account && <span className="text-slate-500">(leave blank to keep current)</span>}</>} hint="For Fireflies, generate one at fireflies.ai → Settings → Developer Settings.">
            <Input
              type="password"
              value={formData.api_key}
              onChange={e => setFormData({ ...formData, api_key: e.target.value })}
              required={!account}
              autoComplete="new-password"
            />
          </Field>
        )}

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

const exampleCurl = (url: string, secret: string | null) => `SECRET='${secret ?? '<your secret>'}'
BODY='{"external_id":"demo-1","title":"Demo","segments":[{"speaker":"Ann","text":"Hello"}]}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')
curl -X POST '${url}' -H 'Content-Type: application/json' -H "X-Hub-Signature: sha256=$SIG" -d "$BODY"`

const CopyButton = ({ text, label = 'Copy' }: { text: string; label?: string }) => {
  const [copied, setCopied] = useState(false)
  return (
    <Button size="sm" onClick={() => navigator.clipboard.writeText(text).then(() => setCopied(true), () => setCopied(false))}>
      {copied ? 'Copied' : label}
    </Button>
  )
}

const PushModal = ({ account, onClose }: { account: TranscriptAccount; onClose: () => void }) => {
  const { generateTranscriptWebhookSecret, deleteTranscriptWebhookSecret } = useSources()
  const [hint, setHint] = useState(account.webhook_secret_hint)
  const [secret, setSecret] = useState<string | null>(null)
  const { run, notice } = useActionNotice()

  const generate = () => run(async () => {
    const result = await generateTranscriptWebhookSecret(account.id)
    setSecret(result.secret)
    setHint(result.webhook_secret_hint)
  })

  const remove = () => run(async () => {
    await deleteTranscriptWebhookSecret(account.id)
    setSecret(null)
    setHint(null)
  })

  return (
    <Modal title={`Push transcripts to ${account.name}`} onClose={onClose}>
      <div className="space-y-4">
        {notice}
        <p className="text-sm text-slate-600">
          POST transcripts as JSON to this URL, signed with the secret: an HMAC-SHA256 of the raw body in
          {' '}<code>X-Hub-Signature: sha256=&lt;hex&gt;</code>, or <code>Authorization: Bearer &lt;secret&gt;</code>.
          {' '}Each <code>external_id</code> is ingested once; re-sending it doesn't update the meeting.
          {account.provider === 'fireflies' && ' Fireflies webhooks can use the same URL and secret.'}
        </p>
        <DetailList items={[
          ['Ingest URL', <span className="flex items-center gap-2"><code className="break-all">{account.ingest_url}</code><CopyButton text={account.ingest_url} /></span>],
          ['Secret', hint ? `Set, ending in …${hint}` : 'Not set (push is disabled)'],
        ]} />

        {secret && (
          <Notice tone="success">
            <p>Copy the secret now. It won't be shown again.</p>
            <span className="flex items-center gap-2 mt-2">
              <code className="break-all">{secret}</code><CopyButton text={secret} />
            </span>
          </Notice>
        )}

        <div>
          <p className="text-sm font-medium text-slate-700 mb-1">Example</p>
          <pre className="bg-slate-100 p-3 rounded-lg text-xs overflow-x-auto"><code>{exampleCurl(account.ingest_url, secret)}</code></pre>
        </div>

        <FormActions>
          {hint && <ConfirmButton size="md" confirm="Remove the secret? Pushes to this account will be rejected." confirmLabel="Remove" onConfirm={remove}>Remove secret</ConfirmButton>}
          {hint
            ? <ConfirmButton variant="secondary" size="md" confirm="Rotate the secret? Senders using the current one will be rejected until updated." confirmLabel="Rotate" onConfirm={generate}>Rotate secret</ConfirmButton>
            : <Button variant="primary" onClick={generate}>Generate secret</Button>}
          <Button onClick={onClose}>Done</Button>
        </FormActions>
      </div>
    </Modal>
  )
}

export default TranscriptsPanel
