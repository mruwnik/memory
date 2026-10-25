import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { ApiError } from '@/hooks/apiError'
import { useNotesSource, type NotesGitSetup, type NotesSource } from '@/hooks/useNotesSource'
import { Badge, Button, Card, ConfirmButton, ConfirmDialog, CopyButton, DetailList, ErrorState, Field, FormActions, Input, LoadingState, Notice, RefreshButton, Section, useActionNotice } from '@/components/shared/ui'
import { formatUserDateTime } from '@/utils/timezones'
import { useSourcesContext } from '../context'
import { formatRelativeTime } from '../shared'

const LastSync = ({ source }: { source: NotesSource }) => {
  const sync = source.last_sync
  if (!sync) return <>Never</>
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Badge tone={sync.ok ? 'success' : 'danger'}>{sync.ok ? 'ok' : 'failed'}</Badge>
      <time dateTime={sync.at} title={formatUserDateTime(new Date(sync.at))}>{formatRelativeTime(sync.at)}</time>
    </span>
  )
}

interface GitFormProps {
  git: NotesGitSetup
  onSaved: (source: NotesSource) => void
}

const GitForm = ({ git, onSaved }: GitFormProps) => {
  const { setUpNotesGit, clearNotesGitToken } = useNotesSource()
  const [remote, setRemote] = useState(git.remote_url ?? '')
  const [branch, setBranch] = useState(git.branch ?? 'main')
  const [confirming, setConfirming] = useState(false)
  const [name, setName] = useState(git.author_name ?? '')
  const [email, setEmail] = useState(git.author_email ?? '')
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const { run, notice } = useActionNotice()

  const https = remote.trim().startsWith('https://')

  const destinationChanged = git.has_repo && (remote.trim() !== (git.remote_url ?? '') || branch.trim() !== git.branch)
  const save = async (confirmed = false) => {
    setConfirming(false)
    setBusy(true)
    setError(null)
    const ok = await run(async () => {
      try {
        onSaved(await setUpNotesGit({
          remote_url: remote.trim(),
          branch: branch.trim(),
          confirm_destination_change: confirmed,
          author_name: name,
          author_email: email,
          token: https && token.trim() ? token.trim() : null,
        }))
      } catch (err) {
        if (err instanceof ApiError) setError(err)
        throw err
      }
    }, 'Saved. The vault syncs in the background; refresh to see the result.')
    if (ok) setToken('')
    setBusy(false)
  }

  const clearToken = () => run(async () => onSaved(await clearNotesGitToken()), 'Token cleared.')

  return (
    <Card region title="Git sync" description="Notes are committed to the vault's git repository and pushed to its remote. Only admins can change this.">
      {confirming && <ConfirmDialog title="Change notes sync destination?"
        message={`Sync this vault to ${remote.trim()} on branch ${branch.trim()}? This can upload the entire vault history to that destination. The saved access token is cleared when changing the remote unless you enter a new token.`}
        confirmLabel="Change destination" onCancel={() => setConfirming(false)} onConfirm={() => save(true)} />}
      <form onSubmit={e => { e.preventDefault(); if (destinationChanged) setConfirming(true); else void save() }} className="space-y-4">
        {notice}
        <Field label="Remote URL" hint="https://host/owner/repo.git, or git@host:owner/repo.git for ssh." error={error?.fields.remote_url}>
          <Input value={remote} onChange={e => setRemote(e.target.value)} spellCheck={false} autoComplete="off" required />
        </Field>
        <Field label="Branch" hint="The current vault branch is renamed; its history and uncommitted files are preserved." error={error?.fields.branch}>
          <Input value={branch} onChange={e => setBranch(e.target.value)} required />
        </Field>
        <Field label="Commit author name" error={error?.fields.author_name}>
          <Input value={name} onChange={e => setName(e.target.value)} required />
        </Field>
        <Field label="Commit author email" error={error?.fields.author_email}>
          <Input type="email" value={email} onChange={e => setEmail(e.target.value)} required />
        </Field>
        {https ? (
          git.token.locked ? (
            <DetailList items={[['Access token', `Set by the operator in the environment (NOTES_GIT_TOKEN), ends in ${git.token.hint}`]]} />
          ) : (
            <Field label="Access token"
              hint={git.token.set
                ? `Saved, ends in ${git.token.hint}. Enter a new one to replace it. Write-only.`
                : 'A token that can push to the repository. Write-only: once saved, only its last four characters are shown.'}
              error={error?.fields.token}>
              <Input type="password" autoComplete="new-password" spellCheck={false} value={token} onChange={e => setToken(e.target.value)} />
            </Field>
          )
        ) : git.ssh_public_key ? (
          <Field label="Deploy key" hint="Add this public key to the repository as a deploy key with write access.">
            <div className="flex items-start gap-2">
              <code className="min-w-0 flex-1 break-all text-xs">{git.ssh_public_key}</code>
              <CopyButton text={git.ssh_public_key} />
            </div>
          </Field>
        ) : (
          <Notice tone="warning">This server has no SSH key for an ssh remote. Use an https remote with an access token.</Notice>
        )}
        <FormActions>
          {git.token.set && !git.token.locked && (
            <ConfirmButton confirm="Clear the saved access token? Syncs with an https remote fail until a new one is saved." confirmLabel="Clear token" onConfirm={clearToken}>Clear token</ConfirmButton>
          )}
          <Button type="submit" variant="primary" disabled={busy}>{busy ? 'Saving...' : 'Save and sync'}</Button>
        </FormActions>
      </form>
    </Card>
  )
}

export const NotesPanel = () => {
  const { getNotesSource, syncNotes } = useNotesSource()
  const { hasScope } = useAuth()
  const { refreshCounts } = useSourcesContext()
  const [source, setSource] = useState<NotesSource | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const { run, notice } = useActionNotice()
  const isAdmin = hasScope('admin') || hasScope('*')

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      setSource(await getNotesSource())
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load notes')
    } finally {
      refreshCounts()
    }
  }, [getNotesSource, refreshCounts])

  useEffect(() => { load() }, [load])

  if (loadError) return <ErrorState message={loadError} onRetry={load} />
  if (!source) return <LoadingState />

  const sync = source.last_sync
  const saved = (next: NotesSource) => {
    setSource(next)
    refreshCounts()
  }

  return (
    <Section description="Notes written through Memory are saved as Markdown files in the notes vault.">
      {notice}
      <Card region title="Notes"
        actions={<>
          <RefreshButton onClick={load} />
          {isAdmin && source.git?.has_repo && (
            <Button size="sm" onClick={() => run(syncNotes, 'Sync queued. Refresh to see the result.')}>Sync now</Button>
          )}
        </>}>
        <DetailList items={[['Notes', source.count], ['Last git sync', <LastSync source={source} />]]} />
        {sync && !sync.ok && <Notice tone="error" className="mt-3">{sync.hint ?? `The sync failed at ${sync.failed_step}.`}</Notice>}
      </Card>
      {source.git && <GitForm key={source.git.remote_url ?? ''} git={source.git} onSaved={saved} />}
    </Section>
  )
}
