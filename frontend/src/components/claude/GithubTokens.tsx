import { useCallback, useEffect, useState } from 'react'
import { useClaude, type GithubToken, type GithubTokenKind } from '../../hooks/useClaude'
import { Button, ConfirmButton, Field, Input, Notice } from '@/components/shared/ui'

// Earlier versions cached tokens here; they now live only on the server.
const LEGACY_STORAGE_KEYS = ['claude_session_github_token', 'claude_session_github_token_write']

const KINDS: { kind: GithubTokenKind; label: string; hint: string }[] = [
  { kind: 'clone', label: 'GitHub token', hint: 'PAT for HTTPS clone.' },
  { kind: 'write', label: 'GitHub write token', hint: 'For differ (push, PR creation).' },
]

const TokenField = ({ kind, label, hint, saved, onChange }: {
  kind: GithubTokenKind
  label: string
  hint: string
  saved: GithubToken | undefined
  onChange: () => Promise<void>
}) => {
  const { saveGithubToken, deleteGithubToken } = useClaude()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
      setValue('')
      setEditing(false)
      await onChange()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update token')
    } finally {
      setBusy(false)
    }
  }
  const save = () => run(() => saveGithubToken(kind, value.trim()))

  const fullHint = `${hint} Saved on the server and used by sessions you start here. Scheduled and API sessions get the clone token; the write token only if they opt in with use_saved_write_token.`

  if (saved && !editing) {
    return (
      <Field label={label} hint={fullHint} error={error}>
        <>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm text-slate-600">Saved ••••{saved.token_hint}</span>
            <Button type="button" size="sm" onClick={() => setEditing(true)}>Replace</Button>
            <ConfirmButton type="button" disabled={busy} confirm={`Remove the saved ${label.toLowerCase()}? New sessions will start without it.`}
              onConfirm={() => run(() => deleteGithubToken(kind))}>Remove</ConfirmButton>
          </div>
        </>
      </Field>
    )
  }

  return (
    <div className="space-y-2">
      <Field label={`${label} (optional)`} hint={fullHint} error={error}>
        <Input
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          // The token fields sit inside the new-session form: Enter saves the token instead of starting a session.
          onKeyDown={(e) => {
            if (e.key !== 'Enter') return
            e.preventDefault()
            if (value.trim()) save()
          }}
          placeholder="ghp_..."
          className="font-mono"
        />
      </Field>
      <div className="flex gap-2">
        <Button type="button" size="sm" variant="primary" disabled={busy || !value.trim()} onClick={save}>Save {label.toLowerCase()}</Button>
        {editing && <Button type="button" size="sm" onClick={() => { setEditing(false); setValue('') }}>Cancel</Button>}
      </div>
    </div>
  )
}

const GithubTokens = () => {
  const { listGithubTokens } = useClaude()
  const [tokens, setTokens] = useState<GithubToken[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setTokens(await listGithubTokens())
      setLoadError(null)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load GitHub tokens')
    }
  }, [listGithubTokens])

  useEffect(() => {
    LEGACY_STORAGE_KEYS.forEach(key => localStorage.removeItem(key))
    load()
  }, [load])

  if (loadError) {
    return <Notice tone="error" onRetry={load}>Couldn't load your GitHub tokens: {loadError}</Notice>
  }
  return (
    <>
      {KINDS.map(({ kind, label, hint }) => (
        <TokenField key={kind} kind={kind} label={label} hint={hint}
          saved={tokens.find(t => t.kind === kind)} onChange={load} />
      ))}
    </>
  )
}

export default GithubTokens
