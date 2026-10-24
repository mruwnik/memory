import { Link } from 'react-router-dom'
import { DEFAULT_KEY_DAILY_CAP_USD, providerName, type AgentSpec, type Catalog, type ProviderKey } from '@/hooks/useAgents'
import { Field, Notice, Select } from '@/components/shared/ui'
import { formatCost } from '@/utils/format'

const DEFAULT = 'default'

const toChoice = (value: string): AgentSpec['provider_key'] => value === DEFAULT ? null : Number(value)

export const providerOf = (model: string) => model.split('/')[0]

/**
 * The user key that pays for runs of `spec`, and why runs would fail if there is none.
 * Mirrors provider_keys.choose_key.
 */
export function payer({ model, provider_key: choice = null }: AgentSpec, catalog: Catalog): { key: ProviderKey | null; problem: string | null } {
  const name = providerName(providerOf(model))
  const keys = catalog.provider_keys.filter(k => k.provider === providerOf(model))
  const key = typeof choice === 'number' ? keys.find(k => k.id === choice) : keys.find(k => k.is_default)
  if (typeof choice === 'number' && !key) {
    return { key: null, problem: `That key was deleted or isn't a key for ${name}; runs fail until you pick another.` }
  }
  if (!key) return { key: null, problem: `Add an LLM key for ${name} so this agent can run.` }
  if (key.status === 'invalid') return { key, problem: `${name} rejected '${key.label}'; runs fail until you replace it.` }
  return { key, problem: null }
}

export function KeyPicker({ value, catalog, onChange }: {
  value: AgentSpec
  catalog: Catalog
  onChange: (choice: AgentSpec['provider_key']) => void
}) {
  const choice = value.provider_key ?? null
  const name = providerName(providerOf(value.model))
  const keys = catalog.provider_keys.filter(k => k.provider === providerOf(value.model))
  const { key, problem } = payer(value, catalog)
  const { ceilings } = catalog
  const keyCap = key?.max_cost_usd_per_day
    ? `${formatCost(key.max_cost_usd_per_day)}/day`
    : `${formatCost(DEFAULT_KEY_DAILY_CAP_USD)}/day unless this agent sets one`
  const note = key && `Runs pay with your key '${key.label}'. This agent's caps and the key's daily cap (${keyCap}) apply. Without a max $ per run, a run stops at ${formatCost(ceilings.default_user_key_run_cost_usd)}.`

  return (
    <div className="space-y-2">
      <Field label="LLM key" hint={!problem && note}>
        <Select id="spec-key" value={choice === null ? DEFAULT : String(choice)} onChange={e => onChange(toChoice(e.target.value))}>
          <option value={DEFAULT}>Default key for {name}</option>
          {keys.map(k => <option key={k.id} value={String(k.id)}>{k.label} (••••{k.key_hint})</option>)}
          {typeof choice === 'number' && !keys.some(k => k.id === choice) && <option value={String(choice)}>Unavailable key #{choice}</option>}
        </Select>
      </Field>
      {problem && (
        <Notice tone="warning">
          {problem} <Link className="underline" to="/ui/keys">Manage LLM keys</Link>
        </Notice>
      )}
    </div>
  )
}
