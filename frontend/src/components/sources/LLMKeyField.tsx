import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAgents, type KeyList, type KeyProvider, type ProviderKey } from '@/hooks/useAgents'
import type { LLMKeyChoice, LLMKeyView } from '@/hooks/useSources'
import { Badge, Field, Select } from '@/components/shared/ui'

// The API never identifies another user's key, so it can't be re-selected; this value
// only stands in for it while it is the current choice.
const OTHER_USERS_KEY = 'other'

const baseLabel = (view: LLMKeyView): string => {
  if (view.choice === 'key') return view.label ?? (view.key_id === null ? "Another user's key" : 'Your key')
  if (view.label) return `Default (${view.label})`
  return view.runs ? "Default (another user's key)" : 'Default'
}

export function llmKeyLabel(view: LLMKeyView): string {
  if (view.choice === 'none') return 'No LLM'
  return view.runs ? baseLabel(view) : `${baseLabel(view)}: no LLM work runs`
}

const toChoice = (value: string): LLMKeyChoice => (value === 'default' || value === 'none' ? value : Number(value))

const currentValue = (view: LLMKeyView | undefined): string => {
  if (view?.choice !== 'key') return view?.choice ?? 'default'
  return view.key_id === null ? OTHER_USERS_KEY : String(view.key_id)
}

// A panel mounts one field per row at once; they share one request.
let inflight: Promise<KeyList> | null = null
const loadKeys = (listKeys: () => Promise<KeyList>) => (inflight ??= listKeys().finally(() => { inflight = null }))

/**
 * Picks which key pays for a source's LLM work. Compact mode saves on change: `onChange`
 * resolves to false when the save failed, and the caller shows why.
 */
export function LLMKeyField({ value, onChange, compact = false }: {
  value: LLMKeyView | undefined
  onChange: (choice: LLMKeyChoice) => unknown
  compact?: boolean
}) {
  const { listKeys } = useAgents()
  const [keys, setKeys] = useState<ProviderKey[]>([])
  const [keysFailed, setKeysFailed] = useState(false)
  const [saving, setSaving] = useState(false)
  // Shows the pick until `value` catches up: forms only save it on submit, and a failed
  // row save drops it so the server's choice shows again.
  const [pending, setPending] = useState<LLMKeyChoice>()
  useEffect(() => setPending(undefined), [value])
  const provider = (value?.provider ?? 'anthropic') as KeyProvider
  useEffect(() => {
    loadKeys(listKeys)
      .then(r => { setKeys(r.providers[provider] ?? []); setKeysFailed(false) })
      .catch(() => setKeysFailed(true))
  }, [listKeys, provider])

  const handleChange = async (choice: LLMKeyChoice) => {
    setPending(choice)
    setSaving(true)
    const saved = await onChange(choice)
    setSaving(false)
    if (saved === false) setPending(undefined)
  }

  const saved = currentValue(value)
  const current = pending === undefined ? saved : String(pending)
  const prefix = compact ? 'LLM: ' : ''
  const defaultLocked = value !== undefined && value.choice !== 'default' && !value.can_choose_default
  const defaultLabel = value?.choice === 'default'
    ? llmKeyLabel(value)
    : defaultLocked ? 'Default (only the owner or an admin can choose it)' : 'Default'
  const select = (
    <Select aria-label="LLM key" className={compact ? 'w-auto py-1 text-xs' : undefined}
      value={current} disabled={saving} onChange={e => handleChange(toChoice(e.target.value))}>
      <option value="default" disabled={defaultLocked}>{prefix}{defaultLabel}</option>
      <option value="none">{prefix}No LLM</option>
      {keys.map(k => <option key={k.id} value={String(k.id)}>{prefix}{k.label} (••••{k.key_hint})</option>)}
      {value?.choice === 'key' && !keys.some(k => String(k.id) === saved) && (
        <option value={saved} disabled={saved === OTHER_USERS_KEY}>{prefix}{llmKeyLabel(value)}</option>
      )}
      {compact && keysFailed && <option disabled>Couldn't load your keys</option>}
    </Select>
  )
  const stopped = value !== undefined && value.choice !== 'none' && !value.runs
  if (compact) {
    return <>
      {select}
      {stopped && <Badge tone="warning">No LLM work runs</Badge>}
    </>
  }
  return (
    <Field label="LLM key" error={keysFailed ? "Couldn't load your keys." : null} hint={<>
      {stopped && "No LLM work runs on this source's new items: no usable key pays for it. "}
      Pays for summaries and other LLM work on this source's items. <Link className="underline" to="/ui/keys">Manage LLM keys</Link>
    </>}>
      {select}
    </Field>
  )
}
