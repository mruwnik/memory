import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAgents, type KeyProvider, type ProviderKey } from '@/hooks/useAgents'
import type { LLMKeyChoice, LLMKeyView } from '@/hooks/useSources'
import { Badge, Field, RowError, Select } from '@/components/shared/ui'

// The API never identifies another user's key, so it can't be re-selected; this value
// only stands in for it while it is the current choice.
const OTHER_USERS_KEY = 'other'

const baseLabel = (view: LLMKeyView): string => {
  if (view.choice === 'key') return view.label ?? (view.key_id === null ? 'Set by another user' : 'Your key')
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

export function LLMKeyField({ value, onChange, compact = false }: {
  value: LLMKeyView | undefined
  onChange: (choice: LLMKeyChoice) => void | Promise<unknown>
  compact?: boolean
}) {
  const { listKeys } = useAgents()
  const [keys, setKeys] = useState<ProviderKey[]>([])
  const [error, setError] = useState<string | null>(null)
  // Shows the pick until `value` catches up: forms only save it on submit, and a failed
  // row save drops it so the server's choice shows again.
  const [pending, setPending] = useState<LLMKeyChoice>()
  useEffect(() => setPending(undefined), [value])
  const provider = (value?.provider ?? 'anthropic') as KeyProvider
  useEffect(() => {
    listKeys().then(r => setKeys(r.providers[provider] ?? [])).catch(() => setKeys([]))
  }, [listKeys, provider])

  const handleChange = async (choice: LLMKeyChoice) => {
    setError(null)
    setPending(choice)
    try {
      await onChange(choice)
    } catch (e) {
      setPending(undefined)
      setError(e instanceof Error ? e.message : 'Failed to save the LLM key')
    }
  }

  const saved = currentValue(value)
  const current = pending === undefined ? saved : String(pending)
  const select = (
    <Select aria-label="LLM key" title={compact ? 'LLM key' : undefined} className={compact ? 'w-auto py-1 text-xs' : undefined}
      value={current} onChange={e => handleChange(toChoice(e.target.value))}>
      <option value="default">{value?.choice === 'default' ? llmKeyLabel(value) : 'Default'}</option>
      <option value="none">No LLM</option>
      {keys.map(k => <option key={k.id} value={String(k.id)}>{k.label} (••••{k.key_hint})</option>)}
      {value?.choice === 'key' && !keys.some(k => String(k.id) === saved) && (
        <option value={saved} disabled={saved === OTHER_USERS_KEY}>{llmKeyLabel(value)}</option>
      )}
    </Select>
  )
  const stopped = value !== undefined && value.choice !== 'none' && !value.runs
  if (compact) {
    return <>
      {select}
      {stopped && <Badge tone="warning">No LLM work runs</Badge>}
      {error && <RowError>{error}</RowError>}
    </>
  }
  return (
    <Field label="LLM key" error={error} hint={<>
      {stopped && "No LLM work runs on this source's new items: no usable key pays for it. "}
      Pays for summaries and other LLM work on this source's items. <Link className="underline" to="/ui/keys">Manage keys</Link>
    </>}>
      {select}
    </Field>
  )
}
