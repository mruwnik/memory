import { useEffect, useState } from 'react'
import { externalToolName, type AgentSpec, type Catalog, type ExternalServer, type ModelEntry, type Reasoning } from '@/hooks/useAgents'
import { ServerStatusBadge } from './ServerStatusBadge'

const REASONING_ORDER: Reasoning[] = ['off', 'low', 'medium', 'high']
const inputClass = 'w-full px-3 py-1.5 border border-slate-200 rounded text-sm focus:outline-none focus:ring-1 focus:ring-primary'
const labelClass = 'block text-xs font-medium text-slate-500 mb-1'

interface Props {
  value: AgentSpec
  catalog: Catalog
  onChange: (spec: AgentSpec) => void
  // Told whether every JSON field currently parses, so callers can block saving.
  onValidityChange?: (valid: boolean) => void
}

const toggle = (list: string[], item: string) => (list.includes(item) ? list.filter(x => x !== item) : [...list, item])

// Mirrors spec.DENIED_TOOL_PREFIXES: never callable inside a run, on any server.
const isDenied = (tool: string) => tool.startsWith('agent_')

/** The spec's external servers with `tool` toggled on `server`; a server left with no tools is dropped. */
const toggleExternal = (servers: AgentSpec['mcp_servers'], serverId: number, tool: string): AgentSpec['mcp_servers'] => {
  const current = servers.find(s => s.server_id === serverId)?.tools ?? []
  const tools = toggle(current, tool)
  const others = servers.filter(s => s.server_id !== serverId)
  return tools.length ? [...others, { server_id: serverId, tools }] : others
}

function ExternalServers({ servers, value, onChange }: {
  servers: ExternalServer[]
  value: AgentSpec['mcp_servers']
  onChange: (servers: AgentSpec['mcp_servers']) => void
}) {
  return (
    <fieldset>
      <legend className={labelClass}>External MCP servers</legend>
      {servers.length === 0 && (
        <div className="text-sm text-slate-500">No MCP servers yet. <a className="text-primary" href="/ui/agents/servers">Add one</a></div>
      )}
      <div className="space-y-2">
        {servers.map(server => {
          const selected = value.find(s => s.server_id === server.id)?.tools ?? []
          return (
            <div key={server.id} className="border border-slate-200 rounded p-2">
              <div className="flex items-center gap-2 text-sm">
                <a className="font-medium text-primary" href={`/ui/agents/servers/${server.id}`}>{server.name}</a>
                <ServerStatusBadge status={server.status} />
              </div>
              {selected.length > 0 && server.status !== 'connected' && (
                <div className="text-xs text-amber-700 mt-1">Runs using this server fail until it is connected again.</div>
              )}
              <div className="grid grid-cols-2 md:grid-cols-3 gap-1 mt-1">
                {server.tools.map(tool => (
                  <label key={tool.name} className="flex items-center gap-2 text-sm" title={tool.description}>
                    <input type="checkbox" disabled={isDenied(tool.name)} checked={selected.includes(tool.name)}
                      onChange={() => onChange(toggleExternal(value, server.id, tool.name))} />
                    <span className="font-mono">{tool.name}</span>
                    {isDenied(tool.name) && <span className="text-xs text-slate-400">agent_* tools are never allowed in runs</span>}
                    {!tool.read_only && <span className="text-xs text-amber-600">side effects</span>}
                  </label>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </fieldset>
  )
}

function JsonField({ id, label, value, onChange, onError, className }: {
  id: string
  label: string
  value: unknown
  onChange: (parsed: unknown) => void
  onError: (invalid: boolean) => void
  className: string
}) {
  const [error, setError] = useState<string | null>(null)
  return (
    <div>
      <label className={labelClass} htmlFor={id}>{label}</label>
      <textarea id={id} className={`${inputClass} ${className} font-mono`} aria-invalid={error !== null}
        defaultValue={value === null ? '' : JSON.stringify(value, null, 2)}
        onChange={e => {
          const text = e.target.value.trim()
          try {
            const parsed = text ? JSON.parse(text) : null
            setError(null)
            onError(false)
            onChange(parsed)
          } catch {
            setError('Not valid JSON')
            onError(true)
          }
        }} />
      {error && <div className="text-xs text-red-600 mt-1">{error}</div>}
    </div>
  )
}

/** The spec adjusted to what `model` accepts, so a model switch never leaves an invalid setting. */
const fitToModel = (spec: AgentSpec, model: ModelEntry | undefined): AgentSpec => {
  if (!model) return spec
  const levels = model.capabilities.reasoning_levels
  return {
    ...spec,
    model: model.model,
    reasoning: levels.includes(spec.reasoning) ? spec.reasoning : levels.includes('medium') ? 'medium' : levels[0],
    temperature: model.capabilities.sampling_params ? spec.temperature : null,
    max_output_tokens:
      spec.max_output_tokens !== null && spec.max_output_tokens > model.capabilities.max_output_tokens
        ? null
        : spec.max_output_tokens,
  }
}

export function SpecEditor({ value, catalog, onChange, onValidityChange }: Props) {
  const [invalidJson, setInvalidJson] = useState<Record<string, boolean>>({})
  useEffect(() => {
    onValidityChange?.(!Object.values(invalidJson).some(Boolean))
  }, [invalidJson, onValidityChange])
  const jsonError = (id: string) => (invalid: boolean) => setInvalidJson(prev => ({ ...prev, [id]: invalid }))
  const model = catalog.models.find(m => m.model === value.model)
  const levels = REASONING_ORDER.filter(l => model?.capabilities.reasoning_levels.includes(l))
  const set = (patch: Partial<AgentSpec>) => onChange({ ...value, ...patch })
  const toolNames = [...catalog.builtins, ...catalog.tools.map(t => t.name)]
  const sideEffects = new Set(catalog.tools.filter(t => !t.read_only).map(t => t.name))
  const servers = catalog.mcp_servers
  const externalNames = value.mcp_servers.flatMap(s => s.tools.map(t => externalToolName(s.server_id, t)))
  const externalWrites = value.mcp_servers.some(s =>
    s.tools.some(t => !servers.find(srv => srv.id === s.server_id)?.tools.find(x => x.name === t)?.read_only))
  // Untrusted input + private data + a way out: injected text could exfiltrate.
  const exfiltrationRisk = externalNames.length > 0
    && value.tools.some(t => !catalog.builtins.includes(t))
    && (value.tools.includes('web_fetch') || value.tools.some(t => sideEffects.has(t)) || externalWrites)

  return (
    <div className="space-y-4">
      <div>
        <label className={labelClass} htmlFor="spec-prompt">System prompt</label>
        <textarea id="spec-prompt" className={`${inputClass} h-40 font-mono`} value={value.system_prompt}
          onChange={e => set({ system_prompt: e.target.value })} />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <label className={labelClass} htmlFor="spec-model">Model</label>
          <select id="spec-model" className={inputClass} value={value.model}
            onChange={e => onChange(fitToModel(value, catalog.models.find(m => m.model === e.target.value)))}>
            {catalog.models.map(m => <option key={m.model} value={m.model}>{m.model}</option>)}
          </select>
        </div>
        <div>
          <label className={labelClass} htmlFor="spec-reasoning">Reasoning</label>
          <select id="spec-reasoning" className={inputClass} value={value.reasoning}
            onChange={e => set({ reasoning: e.target.value as Reasoning })}>
            {levels.map(l => <option key={l} value={l}>{l}</option>)}
          </select>
        </div>
        <div>
          <label className={labelClass} htmlFor="spec-temperature">Temperature</label>
          <input id="spec-temperature" type="number" step="0.1" min="0" max="2" className={inputClass}
            disabled={!model?.capabilities.sampling_params}
            value={value.temperature ?? ''}
            onChange={e => set({ temperature: e.target.value === '' ? null : Number(e.target.value) })} />
        </div>
        <div>
          <label className={labelClass} htmlFor="spec-max-output">Max output tokens</label>
          <input id="spec-max-output" type="number" min="1" max={model?.capabilities.max_output_tokens} className={inputClass}
            placeholder="auto"
            value={value.max_output_tokens ?? ''}
            onChange={e => set({ max_output_tokens: e.target.value === '' ? null : Number(e.target.value) })} />
        </div>
      </div>

      <fieldset>
        <legend className={labelClass}>Tools</legend>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-1 max-h-64 overflow-y-auto border border-slate-200 rounded p-2">
          {toolNames.map(name => (
            <label key={name} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={value.tools.includes(name)} onChange={() => set({ tools: toggle(value.tools, name) })} />
              <span className="font-mono">{name}</span>
              {sideEffects.has(name) && <span className="text-xs text-amber-600">side effects</span>}
            </label>
          ))}
        </div>
      </fieldset>

      <ExternalServers servers={servers} value={value.mcp_servers} onChange={mcp_servers => set({ mcp_servers })} />
      {exfiltrationRisk && (
        <div role="alert" className="p-3 bg-amber-50 text-amber-800 rounded text-sm">
          External tool output is untrusted. With your private data and a way to send it out (web_fetch or a tool with
          side effects), instructions hidden in that output could leak it. Keep the allowlist narrow.
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <label className={labelClass} htmlFor="spec-finish">Finish</label>
          <select id="spec-finish" className={inputClass} value={value.finish.mode}
            onChange={e => set({ finish: { mode: e.target.value as 'answer' | 'tool', tools: [] } })}>
            <option value="answer">final answer</option>
            <option value="tool">a finish tool succeeds</option>
          </select>
        </div>
        {value.finish.mode === 'tool' && (
          <div className="md:col-span-3">
            <span className={labelClass}>Finish tools</span>
            <div className="flex flex-wrap gap-2">
              {[...value.tools.filter(t => !catalog.builtins.includes(t)), ...externalNames].map(t => (
                <label key={t} className="text-sm flex items-center gap-1">
                  <input type="checkbox" checked={value.finish.tools.includes(t)}
                    onChange={() => set({ finish: { mode: 'tool', tools: toggle(value.finish.tools, t) } })} />
                  <span className="font-mono">{t}</span>
                </label>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <label className={labelClass} htmlFor="spec-turns">Max turns</label>
          <input id="spec-turns" type="number" min="1" max="200" className={inputClass} value={value.max_turns}
            onChange={e => set({ max_turns: Number(e.target.value) })} />
        </div>
        <div>
          <label className={labelClass} htmlFor="spec-cost">Max $ per run (≤ {catalog.ceilings.max_run_cost_usd})</label>
          <input id="spec-cost" className={inputClass} placeholder={catalog.ceilings.max_run_cost_usd} value={value.max_cost_usd ?? ''}
            onChange={e => set({ max_cost_usd: e.target.value || null })} />
        </div>
        <div>
          <label className={labelClass} htmlFor="spec-daily">Max $ per day (≤ {catalog.ceilings.max_daily_cost_usd})</label>
          <input id="spec-daily" className={inputClass} placeholder={catalog.ceilings.max_daily_cost_usd} value={value.max_cost_usd_per_day ?? ''}
            onChange={e => set({ max_cost_usd_per_day: e.target.value || null })} />
        </div>
        <div>
          <label className={labelClass} htmlFor="spec-rate">Max runs per hour (≤ {catalog.ceilings.max_runs_per_hour})</label>
          <input id="spec-rate" type="number" min="1" max={catalog.ceilings.max_runs_per_hour} className={inputClass}
            placeholder={String(catalog.ceilings.max_runs_per_hour)} value={value.max_runs_per_hour ?? ''}
            onChange={e => set({ max_runs_per_hour: e.target.value ? Number(e.target.value) : null })} />
        </div>
        <div>
          <label className={labelClass} htmlFor="spec-timeout">Timeout (s, ≤ {catalog.ceilings.max_timeout_sec})</label>
          <input id="spec-timeout" type="number" min="1" className={inputClass} value={value.timeout_sec}
            onChange={e => set({ timeout_sec: Number(e.target.value) })} />
        </div>
      </div>

      <JsonField id="spec-schema" label="Result schema (JSON, optional)" className="h-24"
        value={value.result_schema} onError={jsonError('spec-schema')}
        onChange={parsed => set({ result_schema: parsed as AgentSpec['result_schema'] })} />
      <JsonField id="spec-extras" label="Provider extras (JSON)" className="h-16"
        value={value.provider_extras} onError={jsonError('spec-extras')}
        onChange={parsed => set({ provider_extras: (parsed ?? {}) as AgentSpec['provider_extras'] })} />
    </div>
  )
}
