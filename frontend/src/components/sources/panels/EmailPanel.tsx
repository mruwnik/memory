import { useState, useEffect, useCallback } from 'react'
import { useSources, EmailAccount, GoogleAccount, ImapFolder, EmailAccountTestResult } from '@/hooks/useSources'
import { useProjects, Project } from '@/hooks/useProjects'
import { SourceRow, TagsInput, TestConnectionButton } from '../shared'
import { Button, EmptyState, ErrorState, Field, FieldRow, FormActions, Hint, Input, Label, List, LoadingState, Modal, Notice, Section, Select, useActionNotice } from '@/components/shared/ui'
import { useSourcesContext } from '../Sources'

export const EmailPanel = () => {
  const { listEmailAccounts, createEmailAccount, updateEmailAccount, deleteEmailAccount, syncEmailAccount, testEmailAccount, listGoogleAccounts } = useSources()
  const { listProjects } = useProjects()
  const { userId } = useSourcesContext()
  const [accounts, setAccounts] = useState<EmailAccount[]>([])
  const [googleAccounts, setGoogleAccounts] = useState<GoogleAccount[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingAccount, setEditingAccount] = useState<EmailAccount | null>(null)
  const { run, notice } = useActionNotice()

  const loadAccounts = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [emailData, googleData, projectData] = await Promise.all([
        listEmailAccounts(userId),
        listGoogleAccounts(userId),
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
  }, [listEmailAccounts, listGoogleAccounts, listProjects, userId])

  // Reload when userId changes (admin switching user view)
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

  const handleDelete = (id: number) => run(async () => {
    await deleteEmailAccount(id)
    loadAccounts()
  })

  const handleToggleActive = (account: EmailAccount) => run(async () => {
    await updateEmailAccount(account.id, { active: !account.active })
    loadAccounts()
  })

  const handleSync = (id: number) => run(async () => {
    await syncEmailAccount(id)
    loadAccounts()
  }, 'Sync queued')

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadAccounts} />

  return (
    <Section title="Email Accounts" actions={<Button variant="primary" onClick={() => setShowForm(true)}>Add Account</Button>}>
      {notice}
      {accounts.length === 0 ? (
        <EmptyState
          message="No email accounts configured"
          actionLabel="Add Email Account"
          onAction={() => setShowForm(true)}
        />
      ) : (
        <List>
          {accounts.map(account => (
            <SourceRow
              key={account.id}
              title={account.name}
              subtitle={account.email_address}
              active={account.active}
              lastSyncAt={account.last_sync_at}
              syncError={account.sync_error}
              meta={[
                account.account_type === 'gmail' ? 'Gmail (OAuth)' : `${account.imap_server}:${account.imap_port}`,
                !!account.folders?.length && `Folders: ${account.folders.join(', ')}`,
              ]}
              onToggleActive={() => handleToggleActive(account)}
              onEdit={() => setEditingAccount(account)}
              onDelete={() => handleDelete(account.id)}
              onSync={() => handleSync(account.id)}
            >
              {account.account_type !== 'gmail' && (
                <TestConnectionButton onTest={() => testEmailAccount({ id: account.id })} />
              )}
            </SourceRow>
          ))}
        </List>
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
    </Section>
  )
}

// Sent-folder names to fall back on when a server doesn't advertise the
// \Sent SPECIAL-USE flag (older Dovecot, Courier, many self-hosted setups).
const SENT_FOLDER_NAMES = new Set(['sent', 'sent items', 'sent mail'])

// A folder is a sensible default to sync if it's the inbox or the Sent mailbox.
// The \Sent SPECIAL-USE flag is the primary signal; a name match is the
// fallback for flag-less servers.
const isDefaultSyncFolder = (folder: ImapFolder): boolean =>
  folder.selectable &&
  (folder.name.toUpperCase() === 'INBOX' ||
    folder.flags.includes('\\Sent') ||
    SENT_FOLDER_NAMES.has(folder.name.toLowerCase()))

export const defaultSyncFolders = (folders: ImapFolder[]): string[] =>
  folders.filter(isDefaultSyncFolder).map(f => f.name)

interface FolderPickerProps {
  folders: ImapFolder[]
  selected: string[]
  onChange: (selected: string[]) => void
}

const FolderPicker = ({ folders, selected, onChange }: FolderPickerProps) => {
  const toggle = (name: string) =>
    onChange(
      selected.includes(name)
        ? selected.filter(n => n !== name)
        : [...selected, name],
    )

  return (
    <Field>
      <Label>Folders to sync</Label>
      <Hint>Leave all unchecked to sync only INBOX.</Hint>
      <div className="max-h-48 overflow-y-auto border border-slate-200 rounded p-2 space-y-1">
        {folders.map(folder => (
          <label
            key={folder.name}
            className={`flex items-center gap-2 text-sm ${folder.selectable ? 'text-slate-700' : 'text-slate-500'}`}
          >
            <input
              type="checkbox"
              checked={selected.includes(folder.name)}
              disabled={!folder.selectable}
              onChange={() => toggle(folder.name)}
            />
            <span>{folder.name}</span>
            {!folder.selectable && <span className="text-xs italic">(not selectable)</span>}
          </label>
        ))}
      </div>
    </Field>
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
    sensitivity: account?.sensitivity || 'basic' as 'public' | 'basic' | 'internal' | 'confidential' | 'hidden',
  })
  const { testEmailAccount } = useSources()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [availableFolders, setAvailableFolders] = useState<ImapFolder[]>([])

  const handleTestResult = (result: EmailAccountTestResult) => {
    if (result.status !== 'success' || !result.folders) return
    setAvailableFolders(result.folders)
    // Pre-select sensible defaults (INBOX + Sent) only when the user hasn't
    // already chosen folders, so editing an existing account is non-destructive.
    if (formData.folders.length === 0) {
      setFormData(fd => ({ ...fd, folders: defaultSyncFolders(result.folders!) }))
    }
  }

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
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        <Field label="Name">
          <Input
            type="text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            required
          />
        </Field>

        <Field label="Account Type">
          <Select
            value={formData.account_type}
            onChange={e => setFormData({ ...formData, account_type: e.target.value as 'imap' | 'gmail' })}
            disabled={!!account}
          >
            <option value="imap">IMAP (Standard Email)</option>
            <option value="gmail">Gmail (OAuth)</option>
          </Select>
        </Field>

        {formData.account_type === 'imap' ? (
          <>
            <Field label="Email Address">
              <Input
                type="email"
                value={formData.email_address}
                onChange={e => setFormData({ ...formData, email_address: e.target.value })}
                required
                disabled={!!account}
              />
            </Field>

            <FieldRow>
              <Field label="IMAP Server">
                <Input
                  type="text"
                  value={formData.imap_server}
                  onChange={e => setFormData({ ...formData, imap_server: e.target.value })}
                  required
                  placeholder="imap.example.com"
                />
              </Field>
              <Field label="Port">
                <Input
                  type="number"
                  value={formData.imap_port}
                  onChange={e => setFormData({ ...formData, imap_port: parseInt(e.target.value) })}
                  required
                />
              </Field>
            </FieldRow>

            <Field label="Username">
              <Input
                type="text"
                value={formData.username}
                onChange={e => setFormData({ ...formData, username: e.target.value })}
                required
              />
            </Field>

            <Field label={<>
                Password {account && <span className="text-slate-500">(leave blank to keep current)</span>}</>}>
              <Input
                type="password"
                value={formData.password}
                onChange={e => setFormData({ ...formData, password: e.target.value })}
                required={!account}
              />
            </Field>

            <TestConnectionButton
              onTest={() => testEmailAccount({
                id: account?.id,
                imap_server: formData.imap_server,
                imap_port: formData.imap_port,
                username: formData.username,
                password: formData.password || undefined,
                use_ssl: formData.use_ssl,
              })}
              onResult={handleTestResult}
            />

            {availableFolders.length > 0 && (
              <FolderPicker
                folders={availableFolders}
                selected={formData.folders}
                onChange={folders => setFormData({ ...formData, folders })}
              />
            )}

            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="use-ssl"
                checked={formData.use_ssl}
                onChange={e => setFormData({ ...formData, use_ssl: e.target.checked })}
              />
              <label htmlFor="use-ssl" className="text-sm text-slate-700">Use SSL</label>
            </div>

            <FieldRow>
              <Field label={<>
                  SMTP Server <span className="text-slate-500">(optional)</span></>}>
                <Input
                  type="text"
                  value={formData.smtp_server}
                  onChange={e => setFormData({ ...formData, smtp_server: e.target.value })}
                  placeholder="smtp.example.com"
                />
              </Field>
              <Field label="SMTP Port">
                <Input
                  type="number"
                  value={formData.smtp_port}
                  onChange={e => setFormData({ ...formData, smtp_port: parseInt(e.target.value) || 587 })}
                />
              </Field>
            </FieldRow>
            <Hint>SMTP settings for sending email. If not set, will be inferred from IMAP server.</Hint>
          </>
        ) : (
          <Field>
            <Label>Google Account</Label>
            {googleAccounts.length === 0 ? (
              <Hint>
                No Google accounts connected. Add a Google account in the Accounts tab first.
              </Hint>
            ) : (
              <>
                <Select
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
                >
                  <option value="">Select a Google account...</option>
                  {googleAccounts.map(ga => (
                    <option key={ga.id} value={ga.id}>{ga.email}</option>
                  ))}
                </Select>
                <Hint>
                  Gmail uses OAuth for secure access. The email address will be set automatically.
                </Hint>
              </>
            )}
          </Field>
        )}

        <Field label="Tags">
          <TagsInput
            tags={formData.tags}
            onChange={tags => setFormData({ ...formData, tags })}
          />
        </Field>

        <FieldRow>
          <Field label="Project" hint="Project for access control (from GitHub milestones)">
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
          <Field label="Sensitivity" hint="Visibility level for emails from this account">
            <Select
              value={formData.sensitivity}
              onChange={e => setFormData({ ...formData, sensitivity: e.target.value as 'public' | 'basic' | 'internal' | 'confidential' | 'hidden' })}
            >
              <option value="public">Public</option>
              <option value="basic">Basic</option>
              <option value="internal">Internal</option>
              <option value="confidential">Confidential</option>
              <option value="hidden">Hidden (excluded from search)</option>
            </Select>
          </Field>
        </FieldRow>

        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="send-enabled"
            checked={formData.send_enabled}
            onChange={e => setFormData({ ...formData, send_enabled: e.target.checked })}
          />
          <label htmlFor="send-enabled" className="text-sm text-slate-700">
            Enable sending emails from this account
          </label>
        </div>

        <FormActions>
          <Button type="button" onClick={onCancel}>Cancel</Button>
          <Button variant="primary"
            type="submit"
            disabled={submitting || (formData.account_type === 'gmail' && googleAccounts.length === 0)}
          >
            {submitting ? 'Saving...' : 'Save'}
          </Button>
        </FormActions>
      </form>
    </Modal>
  )
}

export default EmailPanel
