import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useSettings, type SettingRow, type SettingsState } from '@/hooks/useSettings'
import { Badge, Button, Card, ConfirmButton, Field, FormActions, Input, List, Notice, Page, Row, RowError, useActionNotice, useSubmit } from '@/components/shared/ui'

const RESTART_COMMAND = 'tools/ops/restart.sh api'
export const VOYAGE_SETTING_KEY = 'voyage_api_key'
const MCP_SETTING_KEY = 'disabled_mcp_servers'
const SEARCH_CEILING_KEYS = ['enable_bm25_search', 'enable_search_scoring', 'enable_hyde_expansion', 'enable_query_analysis', 'enable_reranking']
export const MODULES_DESCRIPTION = 'Applies once the api restarts. A module that is off hides its pages, MCP tools and scopes; its data stays.'

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

type Save = (values: Record<string, unknown>, success: string) => Promise<boolean>

interface RowProps {
  row: SettingRow
  busy: boolean
  save: Save
}

/** The page's state, shared with the setup wizard's settings steps. */
export const useSettingsState = (onRestarted?: () => Promise<unknown>) => {
  const { getSettings, updateSettings, restartApi } = useSettings()
  const [state, setState] = useState<SettingsState | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const { run, notice } = useActionNotice()
  const [saving, setSaving] = useState(false)
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

  const save: Save = async (values, success) => {
    setSaving(true)
    const ok = await run(async () => setState(await updateSettings(values)), success)
    setSaving(false)
    return ok
  }

  const restart = async () => {
    setRestarting(true)
    // The signed-in user carries the modules, which the nav and routes follow.
    if (await run(restartApi, 'The api restarted.')) await Promise.all([load(), onRestarted?.()])
    setRestarting(false)
  }

  return { state, loadError, load, notice, save, restart, restarting, busy: saving || restarting }
}

export const VoyageKey = ({ row, busy, save }: RowProps) => {
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

interface ToggleProps extends RowProps {
  title?: ReactNode
  /** Names the setting in button labels and messages. */
  label?: string
  meta?: (string | null)[]
}

const Toggle = ({ row, busy, save, title = row.description, label = row.description, meta = [row.env_var] }: ToggleProps) => {
  const on = row.value === true
  return (
    <Row title={title}
      badges={<>
        <SourceBadge row={row} />
        <Badge tone={on ? 'success' : 'neutral'}>{on ? 'on' : 'off'}</Badge>
        {row.pending_restart && <Badge tone="warning">restart pending</Badge>}
      </>}
      meta={[...meta, row.locked ? lockedNote(row) : null].filter(Boolean) as string[]}
      actions={row.locked ? undefined : <>
        <Button size="sm" aria-label={`${on ? 'Turn off' : 'Turn on'}: ${label}`} disabled={busy} onClick={() => save({ [row.key]: !on }, `${label}: turned ${on ? 'off' : 'on'}.`)}>{on ? 'Turn off' : 'Turn on'}</Button>
        {row.source === 'ui' && <Button size="sm" variant="ghost" aria-label={`Reset: ${label}`} disabled={busy} onClick={() => save({ [row.key]: null }, `${label}: reset to the default.`)}>Reset</Button>}
      </>}>
      {row.error && <RowError>{row.error}</RowError>}
    </Row>
  )
}

export const moduleRows = (state: SettingsState | null) => (state?.settings ?? []).filter(s => s.module)

export const ModuleToggles = ({ rows, busy, save }: { rows: SettingRow[]; busy: boolean; save: Save }) => (
  <List flush>
    {rows.map(row => (
      <Toggle key={row.key} row={row} busy={busy} save={save} title={<code>{row.module}</code>} label={`module ${row.module}`}
        meta={[row.description, row.mcp_servers.length ? `MCP servers: ${row.mcp_servers.join(', ')}` : null].filter(Boolean) as string[]} />
    ))}
  </List>
)

const McpServers = ({ row, busy, save }: RowProps) => {
  const disabled = new Set(Array.isArray(row.value) ? row.value : [])
  const toggle = (server: string) => {
    const next = new Set(disabled)
    const enabling = next.delete(server) || !next.add(server)
    return save({ [row.key]: [...next].sort() }, `${server}: ${enabling ? 'enabled' : 'disabled'}.`)
  }
  return (
    <>
      {row.error && <Notice tone="warning">{row.error}</Notice>}
      <List flush>
        {row.choices.map(server => (
          <Row key={server} compact title={server} badges={<Badge tone={disabled.has(server) ? 'neutral' : 'success'}>{disabled.has(server) ? 'disabled' : 'enabled'}</Badge>}
            actions={<Button size="sm" aria-label={`${disabled.has(server) ? 'Enable' : 'Disable'} ${server}`} disabled={busy} onClick={() => toggle(server)}>{disabled.has(server) ? 'Enable' : 'Disable'}</Button>} />
        ))}
      </List>
    </>
  )
}

export const RestartBanner = ({ state, onRestart, busy }: { state: SettingsState; onRestart: () => void; busy: boolean }) => {
  const pending = state.settings.filter(s => s.pending_restart).map(s => s.module ? `module ${s.module}` : s.description)
  return (
    <Notice tone="warning">
      <p>Restart the api to apply: {pending.join(', ') || 'pending changes'}.</p>
      {state.self_restart
        ? <ConfirmButton className="mt-2" variant="secondary" confirm="Restart the api now? Requests and runs in flight are interrupted for every user." confirmLabel="Restart" disabled={busy} onConfirm={onRestart}>{busy ? 'Restarting...' : 'Restart now'}</ConfirmButton>
        : <p className="mt-1">Restart it by hand, or run <code>{RESTART_COMMAND}</code>.</p>}
    </Notice>
  )
}

interface Props {
  /** Re-reads the signed-in user once the api is back, for the modules it now runs. */
  onRestarted?: () => Promise<unknown>
}

const SettingsPage = ({ onRestarted }: Props) => {
  const { state, loadError, load, notice, save, restart, restarting, busy } = useSettingsState(onRestarted)

  const rows = state?.settings ?? []
  const voyage = rows.find(s => s.kind === 'secret' && s.key === VOYAGE_SETTING_KEY)
  const mcp = rows.find(s => s.key === MCP_SETTING_KEY)
  const ceilings = rows.filter(s => SEARCH_CEILING_KEYS.includes(s.key))
  const modules = moduleRows(state)

  return (
    <Page title="Server settings" width="narrow" description="A value set by the operator in the environment wins and can't be changed here."
      loading={!state && !loadError && 'Loading settings...'} error={loadError} onRetry={load}>
      {state?.restart_pending && <RestartBanner state={state} onRestart={restart} busy={restarting} />}
      {notice}
      {voyage && (
        <Card region title="Embeddings" description="Applies live.">
          <VoyageKey row={voyage} busy={busy} save={save} />
        </Card>
      )}
      {modules.length > 0 && (
        <Card region title="Modules" description={MODULES_DESCRIPTION} flush>
          <ModuleToggles rows={modules} busy={busy} save={save} />
        </Card>
      )}
      {state && (
        <Card region title="MCP servers" description="A disabled server's tools disappear for every user and agent within about 30 seconds. A module's servers go with the module."
          actions={mcp && <SourceBadge row={mcp} />}>
          {mcp
            ? <McpServers row={mcp} busy={busy} save={save} />
            : <Notice tone="warning">This api does not report the {MCP_SETTING_KEY} setting, so MCP servers can't be managed here.</Notice>}
        </Card>
      )}
      {ceilings.length > 0 && (
        <Card region title="Search" description="Ceilings: a feature turned off here is off for every search, whatever the request asks." flush>
          <List flush>{ceilings.map(row => <Toggle key={row.key} row={row} busy={busy} save={save} />)}</List>
        </Card>
      )}
    </Page>
  )
}

export default SettingsPage
