import { useCallback, useEffect, useState } from 'react'
import { emptyTriggerConfig, useAgents, type AgentTrigger, type TriggerConfig } from '@/hooks/useAgents'
import { Badge, Button, Checkbox, Field, FieldRow, Input, List, ListItem, MultiSelect, Notice, Section, Textarea } from '@/components/shared/ui'

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


  const save = () => onSave({
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
  })

  return (
    <div className="bg-slate-50 border border-slate-200 rounded-lg p-4 space-y-4">
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
      <div className="flex gap-2">
        <Button variant="primary" onClick={save}>Save trigger</Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

export function Triggers({ definitionId, sourceTypes }: { definitionId: number; sourceTypes: string[] }) {
  const { listTriggers, saveTrigger, deleteTrigger } = useAgents()
  const [triggers, setTriggers] = useState<AgentTrigger[] | null>(null)
  const [editing, setEditing] = useState<AgentTrigger | 'new' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])

  const reload = useCallback(
    () => listTriggers(definitionId).then(setTriggers).catch(e => setError(e.message)),
    [listTriggers, definitionId],
  )
  useEffect(() => { reload() }, [reload])

  const act = async (action: () => Promise<unknown>) => {
    setError(null)
    try {
      await action()
      setEditing(null)
      await reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const remove = (t: AgentTrigger) => {
    if (!window.confirm('Delete this trigger?')) return
    act(() => deleteTrigger(t.id))
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
      {error && <Notice tone="error">{error}</Notice>}
      {warnings.map(w => <Notice key={w} tone="warning">{w}</Notice>)}
      {editing !== null && (
        <TriggerForm
          initial={editing === 'new' ? emptyTriggerConfig() : editing.config}
          sourceTypes={sourceTypes}
          onCancel={() => setEditing(null)}
          onSave={config => act(async () => {
            const saved = await saveTrigger({
              definitionId,
              config,
              ...(editing === 'new' ? {} : { triggerId: editing.id }),
            })
            setWarnings(saved.warnings ?? [])
          })}
        />
      )}
      {triggers === null && !error && <p className="text-sm text-slate-600">Loading triggers...</p>}
      {triggers?.length === 0 && <p className="text-sm text-slate-600">No triggers.</p>}
      {!!triggers?.length && (
        <List>
          {triggers.map(t => (
            <ListItem key={t.id} className="items-start">
              <span className="min-w-0">
                <span className="text-slate-700">{describeTrigger(t.config)}</span>
                {t.state.tripped && (
                  <Badge tone="warning" className="mt-1 whitespace-normal">Tripped: {t.state.tripped.reason}</Badge>
                )}
                {!t.enabled && !t.state.tripped && <span className="block text-xs text-slate-600">paused</span>}
              </span>
              <span className="flex gap-2 shrink-0">
                <Button size="sm" onClick={() => act(() => saveTrigger({ definitionId, enabled: !t.enabled, triggerId: t.id }))}>
                  {t.enabled ? 'Pause' : 'Enable'}
                </Button>
                <Button size="sm" onClick={() => { setWarnings([]); setEditing(t) }}>Edit</Button>
                <Button variant="danger" size="sm" onClick={() => remove(t)}>Delete</Button>
              </span>
            </ListItem>
          ))}
        </List>
      )}
    </Section>
  )
}
