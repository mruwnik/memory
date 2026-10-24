import { useCallback, useEffect, useState } from 'react'
import { emptyTriggerConfig, useAgents, type AgentTrigger, type TriggerConfig } from '@/hooks/useAgents'
import { Badge, Button, Checkbox, ConfirmButton, EmptyState, Field, FieldRow, FormActions, Input, List, LoadingState, Modal, MultiSelect, Notice, Row, Section, Textarea, useActionNotice, useSubmit } from '@/components/shared/ui'

const splitList = (text: string) => text.split(',').map(s => s.trim()).filter(Boolean)

export const describeTrigger = (config: TriggerConfig): string => {
  const f = config.filter
  const parts = [
    f.source_types.length && `types: ${f.source_types.join(', ')}`,
    f.tags_any.length && `tags: ${f.tags_any.join(', ')}`,
    f.channel_ids.length && `channels: ${f.channel_ids.join(', ')}`,
    f.text_contains.length && `text: ${f.text_contains.map(t => `"${t}"`).join(' or ')}`,
    f.regex && `regex: /${f.regex}/`,
    f.exclude_authors.length && `not by: ${f.exclude_authors.join(', ')}`,
  ].filter(Boolean)
  const delivery = config.delivery.mode === 'batch'
    ? `batched (${config.delivery.max_items} items or ${config.delivery.window_sec}s)`
    : 'one run per item'
  return `${parts.join('; ')} · ${delivery}${config.on_edit ? ' · also on edits' : ''}`
}

function TriggerForm({ initial, sourceTypes, onSave, onCancel }: {
  initial: TriggerConfig
  sourceTypes: string[]
  onSave: (config: TriggerConfig) => Promise<void>
  onCancel: () => void
}) {
  const [types, setTypes] = useState<string[]>(initial.filter.source_types)
  const [tags, setTags] = useState(initial.filter.tags_any.join(', '))
  const [channels, setChannels] = useState(initial.filter.channel_ids.join(', '))
  const [text, setText] = useState(initial.filter.text_contains.join(', '))
  const [regex, setRegex] = useState(initial.filter.regex ?? '')
  const [excluded, setExcluded] = useState(initial.filter.exclude_authors.join(', '))
  const [batched, setBatched] = useState(initial.delivery.mode === 'batch')
  const [windowSec, setWindowSec] = useState(initial.delivery.mode === 'batch' ? initial.delivery.window_sec : 3600)
  const [maxItems, setMaxItems] = useState(initial.delivery.mode === 'batch' ? initial.delivery.max_items : 50)
  const [template, setTemplate] = useState(initial.template)
  const [onEdit, setOnEdit] = useState(initial.on_edit ?? false)
  const { busy, error, submit } = useSubmit()

  const save = () => submit(() => onSave({
    filter: {
      source_types: types,
      tags_any: splitList(tags),
      channel_ids: splitList(channels),
      text_contains: splitList(text),
      regex: regex.trim() || null,
      exclude_authors: splitList(excluded),
    },
    delivery: batched ? { mode: 'batch', window_sec: windowSec, max_items: maxItems } : { mode: 'per_item' },
    template,
    on_edit: onEdit,
  }))

  return (
    <div className="space-y-4">
      {error && <Notice tone="error">{error}</Notice>}
      <Field label="Source types" hint="None means any type. At least one filter, here or below, is required.">
        <MultiSelect options={sourceTypes} value={types} onChange={setTypes} placeholder="Any type" />
      </Field>
      <FieldRow>
        <Field label="Any of these tags (comma-separated)"><Input value={tags} onChange={e => setTags(e.target.value)} /></Field>
        <Field label="Channel ids (Discord/Slack)"><Input value={channels} onChange={e => setChannels(e.target.value)} /></Field>
        <Field label="Text contains any of"><Input value={text} onChange={e => setText(e.target.value)} /></Field>
        <Field label="Regex (optional)"><Input className="font-mono" value={regex} onChange={e => setRegex(e.target.value)} /></Field>
        <Field label="Ignore authors (ids, names or emails)"><Input value={excluded} onChange={e => setExcluded(e.target.value)} /></Field>
        <div className="flex flex-wrap items-end gap-2 text-sm">
          <Checkbox label="Batch" checked={batched} onChange={e => setBatched(e.target.checked)} />
          {batched && (
            <>
              <Input aria-label="Window seconds" type="number" min="60" max="86400" className="w-24"
                value={windowSec} onChange={e => setWindowSec(Number(e.target.value))} />
              <span className="text-xs text-slate-600">s or</span>
              <Input aria-label="Max items" type="number" min="1" max="200" className="w-20"
                value={maxItems} onChange={e => setMaxItems(Number(e.target.value))} />
              <span className="text-xs text-slate-600">items</span>
            </>
          )}
        </div>
      </FieldRow>
      <Field label={<>Run input ({'{items}'} becomes one line per item)</>}>
        <Textarea className="h-20" value={template} onChange={e => setTemplate(e.target.value)} />
      </Field>
      <Checkbox label="Also fire when a matching item is edited" checked={onEdit} onChange={e => setOnEdit(e.target.checked)} />
      <FormActions>
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant="primary" disabled={busy} onClick={save}>{busy ? 'Saving...' : 'Save trigger'}</Button>
      </FormActions>
    </div>
  )
}

export function Triggers({ definitionId, sourceTypes }: { definitionId: number; sourceTypes: string[] }) {
  const { listTriggers, saveTrigger, deleteTrigger } = useAgents()
  const [triggers, setTriggers] = useState<AgentTrigger[] | null>(null)
  const [editing, setEditing] = useState<AgentTrigger | 'new' | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [busyId, setBusyId] = useState<number | null>(null)
  const { run, notice } = useActionNotice()

  const reload = useCallback(() => {
    setLoadError(null)
    return listTriggers(definitionId).then(setTriggers).catch(e => setLoadError(e.message))
  }, [listTriggers, definitionId])
  useEffect(() => { reload() }, [reload])

  const act = async (id: number, action: () => Promise<unknown>) => {
    setBusyId(id)
    await run(async () => { await action(); await reload() })
    setBusyId(null)
  }

  const save = async (config: TriggerConfig) => {
    const saved = await saveTrigger({ definitionId, config, ...(editing && editing !== 'new' && { triggerId: editing.id }) })
    setWarnings(saved.warnings ?? [])
    setEditing(null)
    await reload()
  }

  return (
    <Section
      title="Triggers"
      actions={editing === null && <Button onClick={() => { setWarnings([]); setEditing('new') }}>New trigger</Button>}
      description={<>
        Runs this agent when newly stored items match, once per item. Only items you can read match. Posts by our own
        Discord bots and your Slack user never do; anything else the agent writes that comes back in (email, GitHub,
        bot-token Slack) is only stopped by the caps. A trigger that hits a cap pauses itself and notifies you.
      </>}
    >
      {loadError && <Notice tone="error" onRetry={reload}>{loadError}</Notice>}
      {notice}
      {warnings.map(w => <Notice key={w} tone="warning">{w}</Notice>)}
      {editing !== null && (
        <Modal title={editing === 'new' ? 'New trigger' : 'Edit trigger'} onClose={() => setEditing(null)} size="xl">
          <TriggerForm
            initial={editing === 'new' ? emptyTriggerConfig() : editing.config}
            sourceTypes={sourceTypes}
            onCancel={() => setEditing(null)}
            onSave={save}
          />
        </Modal>
      )}
      {triggers === null && !loadError && <LoadingState message="Loading triggers..." />}
      {triggers?.length === 0 && <EmptyState message="No triggers yet." />}
      {!!triggers?.length && (
        <List>
          {triggers.map(t => (
            <Row
              key={t.id}
              title={<span className="font-normal">{describeTrigger(t.config)}</span>}
              muted={!t.enabled}
              badges={<>
                {t.state.tripped && <Badge tone="warning" className="whitespace-normal">Tripped: {t.state.tripped.reason}</Badge>}
                {!t.enabled && !t.state.tripped && <Badge>Paused</Badge>}
              </>}
              actions={<>
                <Button size="sm" disabled={busyId !== null}
                  onClick={() => act(t.id, () => saveTrigger({ definitionId, enabled: !t.enabled, triggerId: t.id }))}>
                  {busyId === t.id ? 'Saving...' : t.enabled ? 'Pause' : 'Resume'}
                </Button>
                <Button size="sm" onClick={() => { setWarnings([]); setEditing(t) }}>Edit</Button>
                <ConfirmButton confirm="Delete this trigger?" onConfirm={() => act(t.id, () => deleteTrigger(t.id))} />
              </>}
            />
          ))}
        </List>
      )}
    </Section>
  )
}
