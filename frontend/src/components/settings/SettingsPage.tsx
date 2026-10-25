import { useCallback, useEffect, useRef, useState } from 'react'
import { useSettings, type SettingRow, type SettingsState } from '@/hooks/useSettings'
import { Badge, Button, Card, ConfirmButton, Field, FormActions, Input, List, Notice, Page, Row, RowError, useActionNotice, useSubmit } from '@/components/shared/ui'

const RESTART_COMMAND = 'tools/ops/restart.sh api'
const MCP_SETTING_KEY = 'disabled_mcp_servers'
const SEARCH_CEILING_KEYS = ['enable_bm25_search', 'enable_search_scoring', 'enable_hyde_expansion', 'enable_query_analysis', 'enable_reranking']

interface Verdict { ok: boolean; message: string }

const voyageVerdict = ({ ok, voyage_status }: { ok: boolean; voyage_status: number | null }): Verdict => {
  if (ok) return { ok, message: 'Voyage accepted the key.' }
  if (voyage_status === 502) return { ok, message: "Couldn't reach Voyage. Try again shortly." }
  if (voyage_status === 429) return { ok, message: 'Voyage is rate limiting requests. Try again shortly.' }
  return { ok, message: `Voyage rejected the key${voyage_status ? ` (HTTP ${voyage_status})` : ''}.` }
}

const lockedNote = (row: SettingRow) => `Set by the operator in the environment (${row.env_var})`

const SourceBadge = ({ row }: { row: SettingRow }) => (
  <Badge tone={row.source === 'env' ? 'warning' : row.source === 'ui' ? 'primary' : 'neutral'}>
    {row.source === 'env' ? 'set by the operator' : row.source === 'ui' ? 'set here' : 'default'}
  </Badge>
)

interface RowProps {
  row: SettingRow
  busy: boolean
  save: (values: Record<string, unknown>, success: string) => Promise<boolean>
}

const VoyageKey = ({ row, busy, save }: RowProps) => {
  const { checkVoyageKey } = useSettings()
  const [replacing, setReplacing] = useState(false)
  const editing = replacing || !row.set
  const [value, setValue] = useState('')
  const check = useSubmit()
  const [checked, setChecked] = useState<Verdict | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const replaceRef = useRef<HTMLButtonElement>(null)
  const wasEditing = useRef(editing)

  // The focused control unmounts when the view switches; hand focus to its replacement.
  useEffect(() => {
    if (wasEditing.current !== editing) (editing ? inputRef.current : replaceRef.current)?.focus()
    wasEditing.current = editing
  }, [editing])

  const edit = (next: string) => {
    setValue(next)
    setChecked(null)
  }

  const runCheck = async () => {
    setChecked(null)
    await check.submit(async () => {
      const result = await checkVoyageKey(value.trim())
      setChecked(voyageVerdict(result))
    })
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!await save({ [row.key]: value.trim() }, 'Voyage key saved. Items waiting for embeddings will be processed shortly.')) return
    edit('')
    setReplacing(false)
  }

  if (row.locked) {
    return (
      <List flush>
        <Row title="Voyage API key" badges={<><SourceBadge row={row} /><Badge tone="success">set</Badge></>}
          meta={[row.key_hint ? `ends in ${row.key_hint}` : null, lockedNote(row)].filter(Boolean) as string[]} />
      </List>
    )
  }

  if (!editing) {
    return (
      <List flush>
        <Row title="Voyage API key" badges={<><SourceBadge row={row} /><Badge tone="success">set</Badge></>}
          meta={[row.key_hint ? `ends in ${row.key_hint}` : 'write-only']}
          actions={<>
            <Button ref={replaceRef} size="sm" disabled={busy} onClick={() => setReplacing(true)}>Replace key</Button>
            <ConfirmButton aria-label="Clear Voyage API key" confirm="Clear the Voyage key? Embedding and reranking stop until a key is set." onConfirm={() => save({ [row.key]: null }, 'Voyage key cleared.')} confirmLabel="Clear key">Clear</ConfirmButton>
          </>} />
      </List>
    )
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      {row.error && <Notice tone="warning">{row.error}</Notice>}
      <Field label="Voyage API key" hint="Write-only: once saved, only its last four characters are shown. Embeddings and reranking use it.">
        <Input ref={inputRef} type="password" autoComplete="new-password" spellCheck={false} value={value} onChange={e => edit(e.target.value)} />
      </Field>
      {check.error && <Notice tone="error">{check.error}</Notice>}
      {checked && <Notice tone={checked.ok ? 'success' : 'error'}>{checked.message}</Notice>}
      <FormActions>
        {row.set && <Button disabled={busy} onClick={() => { edit(''); setReplacing(false) }}>Cancel</Button>}
        <Button disabled={!value.trim() || check.busy || busy} onClick={runCheck}>{check.busy ? 'Checking...' : 'Check'}</Button>
        <Button type="submit" variant="primary" disabled={!value.trim() || busy}>{busy ? 'Saving...' : 'Save key'}</Button>
      </FormActions>
    </form>
  )
}

const Toggle = ({ row, busy, save }: RowProps) => {
  const on = row.value === true
  return (
    <Row title={row.description} badges={<><SourceBadge row={row} /><Badge tone={on ? 'success' : 'neutral'}>{on ? 'on' : 'off'}</Badge></>}
      meta={[row.env_var, row.locked ? lockedNote(row) : null].filter(Boolean) as string[]}
      actions={row.locked ? undefined : <>
        <Button size="sm" aria-label={`${on ? 'Turn off' : 'Turn on'}: ${row.description}`} disabled={busy} onClick={() => save({ [row.key]: !on }, `${row.description}: turned ${on ? 'off' : 'on'}.`)}>{on ? 'Turn off' : 'Turn on'}</Button>
        {row.source === 'ui' && <Button size="sm" variant="ghost" aria-label={`Reset: ${row.description}`} disabled={busy} onClick={() => save({ [row.key]: null }, 'Reset to the default.')}>Reset</Button>}
      </>}>
      {row.error && <RowError>{row.error}</RowError>}
    </Row>
  )
}

const McpServers = ({ row, busy, save }: RowProps) => {
  const disabled = new Set(Array.isArray(row.value) ? row.value : [])
  const toggle = (server: string) => {
    const next = new Set(disabled)
    const enabling = next.delete(server) || !next.add(server)
    return save({ [row.key]: [...next].sort() }, `${server}: ${enabling ? 'enabled' : 'disabled'}.`)
  }
  return (
    <>
      {row.locked && <Notice tone="info">{lockedNote(row)}</Notice>}
      {row.error && <Notice tone="warning">{row.error}</Notice>}
      <List flush>
        {row.choices.map(server => (
          <Row key={server} title={server} badges={<Badge tone={disabled.has(server) ? 'neutral' : 'success'}>{disabled.has(server) ? 'disabled' : 'enabled'}</Badge>}
            actions={row.locked ? undefined : <Button size="sm" aria-label={`${disabled.has(server) ? 'Enable' : 'Disable'} ${server}`} disabled={busy} onClick={() => toggle(server)}>{disabled.has(server) ? 'Enable' : 'Disable'}</Button>} />
        ))}
      </List>
    </>
  )
}

const RestartBanner = ({ state, onRestart, busy }: { state: SettingsState; onRestart: () => void; busy: boolean }) => {
  const pending = state.settings.filter(s => s.pending_restart).map(s => s.description)
  return (
    <Notice tone="warning">
      <p>Restart the api to apply: {pending.join(', ') || 'pending changes'}.</p>
      {state.self_restart
        ? <ConfirmButton className="mt-2" variant="secondary" confirm="Restart the api now? Requests and runs in flight are interrupted for every user." confirmLabel="Restart" disabled={busy} onConfirm={onRestart}>{busy ? 'Restarting...' : 'Restart now'}</ConfirmButton>
        : <p className="mt-1">Restart it by hand, or run <code>{RESTART_COMMAND}</code>.</p>}
    </Notice>
  )
}

const SettingsPage = () => {
  const { getSettings, updateSettings, restartApi } = useSettings()
  const [state, setState] = useState<SettingsState | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const { run, notice } = useActionNotice()
  const [busy, setBusy] = useState(false)
  const [restarting, setRestarting] = useState(false)

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      setState(await getSettings())
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load settings')
    }
  }, [getSettings])

  useEffect(() => { load() }, [load])

  const save = async (values: Record<string, unknown>, success: string) => {
    setBusy(true)
    const ok = await run(async () => setState(await updateSettings(values)), success)
    setBusy(false)
    return ok
  }

  const restart = async () => {
    setRestarting(true)
    if (await run(restartApi, 'The api restarted.')) await load()
    setRestarting(false)
  }

  const rows = state?.settings ?? []
  const voyage = rows.find(s => s.kind === 'secret' && s.key === 'voyage_api_key')
  const mcp = rows.find(s => s.key === MCP_SETTING_KEY)
  const ceilings = rows.filter(s => SEARCH_CEILING_KEYS.includes(s.key))

  return (
    <Page title="Instance settings" width="narrow" description="A value set by the operator in the environment wins and can't be changed here."
      loading={!state && !loadError && 'Loading settings...'} error={loadError} onRetry={load}>
      {state?.restart_pending && <RestartBanner state={state} onRestart={restart} busy={restarting} />}
      {notice}
      {voyage && (
        <Card title="Embeddings" description="Applies live.">
          <VoyageKey row={voyage} busy={busy || restarting} save={save} />
        </Card>
      )}
      {state && (
        <Card title="MCP servers" description="A disabled server's tools disappear for every user and agent within about 30 seconds.">
          {mcp
            ? <McpServers row={mcp} busy={busy || restarting} save={save} />
            : <Notice tone="warning">This api does not report the {MCP_SETTING_KEY} setting, so MCP servers can't be managed here.</Notice>}
        </Card>
      )}
      {ceilings.length > 0 && (
        <Card title="Search" description="Ceilings: a feature turned off here is off for every search, whatever the request asks." flush>
          <List flush>{ceilings.map(row => <Toggle key={row.key} row={row} busy={busy || restarting} save={save} />)}</List>
        </Card>
      )}
    </Page>
  )
}

export default SettingsPage
