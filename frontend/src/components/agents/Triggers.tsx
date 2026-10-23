import { useCallback, useEffect, useState } from 'react'
import { emptyTriggerConfig, useAgents, type AgentTrigger, type TriggerConfig } from '@/hooks/useAgents'

const inputClass = 'w-full px-3 py-1.5 border rounded text-sm'
const labelClass = 'block text-xs font-medium text-slate-600 mb-1'

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
  return `${parts.join('; ')} · ${delivery}`
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

  const toggleType = (t: string) => setTypes(prev => prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t])

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
  })

  return (
    <div className="space-y-3 p-3 border rounded bg-slate-50">
      <fieldset>
        <legend className={labelClass}>Source types (none = any type; at least one filter below or here is required)</legend>
        <div className="flex flex-wrap gap-2">
          {sourceTypes.map(t => (
            <label key={t} className="text-xs flex items-center gap-1">
              <input type="checkbox" checked={types.includes(t)} onChange={() => toggleType(t)} /> {t}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelClass} htmlFor="trigger-tags">Any of these tags (comma-separated)</label>
          <input id="trigger-tags" className={inputClass} value={tags} onChange={e => setTags(e.target.value)} />
        </div>
        <div>
          <label className={labelClass} htmlFor="trigger-channels">Channel ids (Discord/Slack)</label>
          <input id="trigger-channels" className={inputClass} value={channels} onChange={e => setChannels(e.target.value)} />
        </div>
        <div>
          <label className={labelClass} htmlFor="trigger-text">Text contains any of</label>
          <input id="trigger-text" className={inputClass} value={text} onChange={e => setText(e.target.value)} />
        </div>
        <div>
          <label className={labelClass} htmlFor="trigger-regex">Regex (optional)</label>
          <input id="trigger-regex" className={`${inputClass} font-mono`} value={regex} onChange={e => setRegex(e.target.value)} />
        </div>
        <div>
          <label className={labelClass} htmlFor="trigger-excluded">Ignore authors (ids, names or emails)</label>
          <input id="trigger-excluded" className={inputClass} value={excluded} onChange={e => setExcluded(e.target.value)} />
        </div>
        <div className="flex items-end gap-2 text-sm">
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={batched} onChange={e => setBatched(e.target.checked)} /> Batch
          </label>
          {batched && (
            <>
              <input aria-label="Window seconds" type="number" min="60" max="86400" className="w-24 px-2 py-1 border rounded"
                value={windowSec} onChange={e => setWindowSec(Number(e.target.value))} />
              <span className="text-xs text-slate-500">s or</span>
              <input aria-label="Max items" type="number" min="1" max="200" className="w-20 px-2 py-1 border rounded"
                value={maxItems} onChange={e => setMaxItems(Number(e.target.value))} />
              <span className="text-xs text-slate-500">items</span>
            </>
          )}
        </div>
      </div>
      <div>
        <label className={labelClass} htmlFor="trigger-template">Run input ({'{items}'} becomes one line per item)</label>
        <textarea id="trigger-template" className={`${inputClass} h-20`} value={template} onChange={e => setTemplate(e.target.value)} />
      </div>
      <div className="flex gap-2">
        <button className="px-3 py-1.5 bg-primary text-white rounded text-sm" onClick={save}>Save trigger</button>
        <button className="px-3 py-1.5 border rounded text-sm" onClick={onCancel}>Cancel</button>
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
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">Triggers</h2>
        {editing === null && (
          <button className="px-3 py-1.5 border rounded text-sm" onClick={() => { setWarnings([]); setEditing('new') }}>New trigger</button>
        )}
      </div>
      <p className="text-xs text-slate-500">
        Runs this agent when newly stored items match, once per item. Only items you can read match. Posts by our own
        Discord bots and your Slack user never do; anything else the agent writes that comes back in (email, GitHub,
        bot-token Slack) is only stopped by the caps. A trigger that hits a cap pauses itself and notifies you.
      </p>
      {error && <div className="p-3 bg-red-50 text-red-700 rounded text-sm whitespace-pre-wrap">{error}</div>}
      {warnings.map(w => <div key={w} className="p-3 bg-amber-50 text-amber-800 rounded text-sm">{w}</div>)}
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
      {triggers === null && !error && <p className="text-sm text-slate-500">Loading triggers...</p>}
      {triggers?.length === 0 && <p className="text-sm text-slate-500">No triggers.</p>}
      {!!triggers?.length && (
        <ul className="divide-y bg-white rounded-xl shadow-sm">
          {triggers.map(t => (
            <li key={t.id} className="p-3 flex justify-between items-start gap-3 text-sm">
              <span className="min-w-0">
                <span className="text-slate-700">{describeTrigger(t.config)}</span>
                {t.state.tripped && (
                  <span className="block mt-1 text-xs text-amber-800 bg-amber-50 rounded px-2 py-1">
                    Tripped: {t.state.tripped.reason}
                  </span>
                )}
                {!t.enabled && !t.state.tripped && <span className="block text-xs text-slate-400">paused</span>}
              </span>
              <span className="flex gap-2 shrink-0">
                <button className="px-2 py-1 border rounded text-xs"
                  onClick={() => act(() => saveTrigger({ definitionId, enabled: !t.enabled, triggerId: t.id }))}>
                  {t.enabled ? 'Pause' : 'Enable'}
                </button>
                <button className="px-2 py-1 border rounded text-xs" onClick={() => { setWarnings([]); setEditing(t) }}>Edit</button>
                <button className="px-2 py-1 border border-red-300 text-red-700 rounded text-xs" onClick={() => remove(t)}>Delete</button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
