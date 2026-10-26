import { useCallback, useEffect, useState } from 'react'
import { useSettings, type SettingRow } from '@/hooks/useSettings'
import { Badge, Button, Card, ErrorState, Field, FormActions, Input, List, LoadingState, Notice, Row, useActionNotice } from '@/components/shared/ui'

type Save = (values: Record<string, unknown>, success: string) => Promise<boolean>
interface RowProps { row: SettingRow; busy: boolean; save: Save }
const lockedNote = (row: SettingRow) => `Set by the operator in the environment (${row.env_var})`
const SourceBadge = ({ row }: { row: SettingRow }) => (
  <Badge tone={row.source === 'env' ? 'warning' : row.source === 'ui' ? 'primary' : 'neutral'}>
    {row.source === 'env' ? 'set by the operator' : row.source === 'ui' ? 'set here' : 'default'}
  </Badge>
)

const minutesText = (seconds: number) => `Every ${seconds / 60} minute${seconds === 60 ? '' : 's'}`

const NotesInterval = ({ row, busy, save }: RowProps) => {
  const seconds = typeof row.value === 'number' ? row.value : 0
  const [minutes, setMinutes] = useState<number | ''>(seconds / 60)

  if (row.locked) {
    return (
      <List flush>
        <Row title={minutesText(seconds)} badges={<SourceBadge row={row} />} meta={[lockedNote(row)]} />
      </List>
    )
  }

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (minutes !== '') save({ [row.key]: Math.round(minutes * 60) }, 'Notes sync interval saved.')
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      {row.error && <Notice tone="warning">{row.error}</Notice>}
      <div className="flex flex-wrap items-center gap-2"><SourceBadge row={row} /><span className="text-sm text-slate-600">{minutesText(seconds)}</span></div>
      <Field label="Minutes between syncs" hint="How often the notes vault pulls from and pushes to its git remote: 1 minute to 1 day.">
        <Input type="number" min={1} max={1440} step={1} className="w-32" value={minutes} onChange={e => setMinutes(e.target.value === '' ? '' : Number(e.target.value))} required />
      </Field>
      <FormActions>
        {row.source === 'ui' && <Button disabled={busy} onClick={() => save({ [row.key]: null }, 'Notes sync interval reset to the default.')}>Reset</Button>}
        <Button type="submit" variant="primary" disabled={busy || minutes === '' || minutes * 60 === seconds}>{busy ? 'Saving...' : 'Save interval'}</Button>
      </FormActions>
    </form>
  )
}

export const NotesSyncSettings = () => {
  const { getSettings, updateSettings } = useSettings()
  const [row, setRow] = useState<SettingRow | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { run, notice } = useActionNotice()
  const load = useCallback(async () => {
    setError(null)
    setLoading(true)
    try {
      const state = await getSettings()
      setRow(state.settings.find(s => s.key === 'notes_sync_interval') ?? null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load notes sync settings')
    } finally {
      setLoading(false)
    }
  }, [getSettings])
  useEffect(() => { load() }, [load])

  const save: Save = async (values, success) => {
    setBusy(true)
    const ok = await run(async () => {
      const state = await updateSettings(values)
      setRow(state.settings.find(s => s.key === 'notes_sync_interval') ?? null)
    }, success)
    setBusy(false)
    return ok
  }

  return (
    <Card region title="Notes sync" description="Applies within a minute, without a restart.">
      {notice}
      {loading ? <LoadingState /> : error ? <ErrorState message={error} onRetry={load} /> : row ? (
        <NotesInterval key={String(row.value)} row={row} busy={busy} save={save} />
      ) : <Notice tone="warning">This api does not report the notes sync interval.</Notice>}
    </Card>
  )
}
