import { useSSHKeys, type SSHKey } from '@/hooks/useSSHKeys'
import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { ApiError } from '@/hooks/apiError'
import { useNotesSource, type NotesGitSetup, type NotesSource, type SSHProbe } from '@/hooks/useNotesSource'
import { Badge, Button, Card, ConfirmButton, ConfirmDialog, CopyButton, DetailList, Disclosure, ErrorState, Field, FormActions, Hint, Input, LoadingState, Notice, RefreshButton, Section, Select, Textarea, useActionNotice } from '@/components/shared/ui'
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

const SSHConfig = ({ git, remote, onSaved, onDirty }: GitFormProps & { remote: string; onDirty: (dirty: boolean) => void }) => {
  const { setUpNotesSSH, probeNotesSSH } = useNotesSource()
  const { list } = useSSHKeys()
  const [keys, setKeys] = useState<SSHKey[]>([])
  const [probe, setProbe] = useState<SSHProbe | null>(null)
  const [probing, setProbing] = useState(false)
  const [keyId, setKeyId] = useState(git.ssh?.key_id?.toString() ?? '')
  const [hostKeys, setHostKeys] = useState(git.ssh?.remote_url === remote ? git.ssh.host_keys : '')
  const [busy, setBusy] = useState(false)
  const { run, notice } = useActionNotice()
  const [loadError, setLoadError] = useState<string | null>(null)
  const load = useCallback(async () => {
    try { setKeys(await list()); setLoadError(null) }
    catch (e) { setLoadError(e instanceof Error ? e.message : 'Unable to load keys') }
  }, [list])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    onDirty(keyId !== (git.ssh?.key_id?.toString() ?? '') || (!!keyId && (hostKeys !== git.ssh?.host_keys || remote !== git.ssh?.remote_url)))
    return () => onDirty(false)
  }, [keyId, hostKeys, remote, git.ssh, onDirty])
  const publicKey = keys.find(key => key.id.toString() === keyId)?.public_key ?? (git.ssh?.key_id.toString() === keyId ? git.ssh.public_key : null)
  const configure = async () => {
    setBusy(true)
    await run(async () => onSaved(await setUpNotesSSH(remote, hostKeys, keyId ? Number(keyId) : null)), 'SSH configuration saved.')
    setBusy(false)
  }
  return <div className="space-y-4">
    {notice}
    {loadError && <ErrorState message={loadError} onRetry={load} />}
    <Field label="SSH key" hint="Select one of your personal or instance keys. Notes will use it for shared vault sync.">
      <Select value={keyId} onChange={e => setKeyId(e.target.value)}><option value="">No SSH key selected</option>{keys.map(key => <option key={key.id} value={key.id}>{key.name}</option>)}</Select>
    </Field>
    <a href="/ui/ssh-keys">Import or manage SSH keys</a>
    <Button onClick={load}>Refresh keys</Button>
    {publicKey && <Field label="Public key" hint="Register this key with your Git account or repository, with push access."><div className="flex items-start gap-2"><code className="min-w-0 flex-1 break-all text-xs">{publicKey}</code><CopyButton text={publicKey} /></div></Field>}
    {keyId ? <>
      <Button disabled={probing || busy} onClick={async () => {
        setProbing(true)
        setProbe(null)
        await run(async () => setProbe(await probeNotesSSH(remote)))
        setProbing(false)
      }}>{probing ? 'Checking server…' : 'Check server'}</Button>
      {git.ssh?.remote_url === remote && <Hint>Server trust is saved. Sync stops if the server presents an unrecognized key.</Hint>}
      {probe && probe.remote_url === remote.trim() && <Card title={`Trust ${probe.host}:${probe.port}?`}>
        <p className="text-sm">These are the keys the server presented. Confirm the fingerprints with your server administrator if this is your first connection.</p>
        {git.ssh?.remote_url === remote && probe.keys.some(key => !git.ssh?.host_keys.split('\n').includes(key.public_key)) && <Notice tone="warning">The server presented a key that differs from saved trust. Confirm the change before trusting it.</Notice>}
        <DetailList items={probe.keys.map(key => [key.algorithm, key.fingerprint])} />
        <FormActions>
          <Button onClick={() => setProbe(null)} disabled={busy}>Cancel</Button>
          <Button variant="primary" disabled={busy} onClick={async () => {
            const keys = probe.keys.map(key => key.public_key).join('\n')
            setBusy(true)
            await run(async () => {
              const saved = await setUpNotesSSH(probe.remote_url, keys, Number(keyId))
              setHostKeys(keys)
              setProbe(null)
              onSaved(saved)
            }, 'Server trusted. SSH configuration saved.')
            setBusy(false)
          }}>Trust this server</Button>
        </FormActions>
      </Card>}
      <Disclosure summary="Advanced: enter trusted host keys manually">
        <Field label="Trusted SSH host keys" hint="Paste verified server public keys, one per line: key type and base64 value, without a hostname or comment.">
          <Textarea value={hostKeys} onChange={e => setHostKeys(e.target.value)} rows={4} spellCheck={false} />
        </Field>
        <Button onClick={configure} disabled={busy || !hostKeys.trim()}>{busy ? 'Saving SSH...' : 'Save SSH configuration'}</Button>
      </Disclosure>
    </> : <>
      <Notice tone="warning">Import or generate a database SSH key using the link above, then select it here. SSH sync requires a selected key.</Notice>
      {git.ssh && <Button onClick={configure} disabled={busy}>Clear SSH configuration</Button>}
    </>}
    {git.ssh && git.ssh.remote_url !== remote && <Notice tone="warning">Save the trusted host keys for this remote before syncing.</Notice>}
  </div>
}

const GitForm = ({ git, onSaved }: GitFormProps) => {
  const { setUpNotesGit, clearNotesGitToken } = useNotesSource()
  const [remote, setRemote] = useState(git.remote_url ?? '')
  const [branch, setBranch] = useState(git.branch ?? 'main')
  const [confirming, setConfirming] = useState(false)
  const [sshDirty, setSSHDirty] = useState(false)
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
        ) : (
          <SSHConfig key={remote.trim()} git={git} remote={remote.trim()} onSaved={onSaved} onDirty={setSSHDirty} />
        )}
        <FormActions>
          {git.token.set && !git.token.locked && (
            <ConfirmButton confirm="Clear the saved access token? Syncs with an https remote fail until a new one is saved." confirmLabel="Clear token" onConfirm={clearToken}>Clear token</ConfirmButton>
          )}
          <Button type="submit" variant="primary" disabled={busy || (!https && sshDirty) || (!https && !!git.ssh && (!git.ssh.host_keys || git.ssh.remote_url !== remote.trim()))}>{busy ? 'Saving...' : 'Save and sync'}</Button>
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
