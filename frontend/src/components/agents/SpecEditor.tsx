import { useEffect, useState } from 'react'
import { externalToolName, type AgentSpec, type Catalog, type ExternalServer, type ModelEntry, type Reasoning } from '@/hooks/useAgents'
import { ServerStatusBadge } from './ServerStatusBadge'
import { KeyPicker, payer, providerOf } from './KeyPicker'
import { Link } from 'react-router-dom'
import { Checkbox, Field, Input, Label, MultiSelect, Notice, Select, Textarea } from '@/components/shared/ui'

const REASONING_ORDER: Reasoning[] = ['off', 'low', 'medium', 'high']

interface Props {
  value: AgentSpec
  catalog: Catalog
  onChange: (spec: AgentSpec) => void
  // Told whether every JSON field currently parses, so callers can block saving.
  onValidityChange?: (valid: boolean) => void
}

const toggle = (list: string[], item: string) => (list.includes(item) ? list.filter(x => x !== item) : [...list, item])

// Quiet on purpose: most memory tools don't declare readOnlyHint yet, so nearly every tool carries it.
const SideEffects = () => <span className="text-xs text-slate-500 whitespace-nowrap">· side effects</span>

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
      <legend className="block text-sm font-medium text-slate-700 mb-1">External MCP servers</legend>
      {servers.length === 0 && (
        <div className="text-sm text-slate-500">No MCP servers yet. <Link className="text-primary hover:underline" to="/ui/agents/servers">Add one</Link></div>
      )}
      <div className="space-y-2">
        {servers.map(server => {
          const selected = value.find(s => s.server_id === server.id)?.tools ?? []
          return (
            <div key={server.id} className="border border-slate-200 rounded p-2">
              <div className="flex items-center gap-2 text-sm">
                <Link className="font-medium text-primary" to={`/ui/agents/servers/${server.id}`}>{server.name}</Link>
                <ServerStatusBadge status={server.status} />
              </div>
              {selected.length > 0 && server.status !== 'connected' && (
                <div className="text-xs text-amber-700 mt-1">Runs using this server fail until it is connected again.</div>
              )}
              <div className="grid grid-cols-2 md:grid-cols-3 gap-1 mt-1">
                {server.tools.map(tool => (
                  <Checkbox key={tool.name} className="flex-wrap min-w-0" title={tool.description}
                    disabled={isDenied(tool.name)} checked={selected.includes(tool.name)}
                    onChange={() => onChange(toggleExternal(value, server.id, tool.name))}
                    label={<>
                      <span className="font-mono break-all">{tool.name}</span>
                      {isDenied(tool.name) && <span className="text-xs text-slate-500">agent_* tools are never allowed in runs</span>}
                      {!tool.read_only && <SideEffects />}
                    </>} />
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
      <Label className="mb-1" htmlFor={id}>{label}</Label>
      <Textarea id={id} className={`${className} font-mono`} aria-invalid={error !== null}
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
    // A pinned key belongs to one provider.
    provider_key:
      typeof spec.provider_key === 'number' && providerOf(spec.model) !== providerOf(model.model) ? null : spec.provider_key,
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
  const toolNames = [...catalog.builtins, ...catalog.tools.map(t => t.name)]
  const sideEffects = new Set(catalog.tools.filter(t => !t.read_only).map(t => t.name))
  const servers = catalog.mcp_servers
  const externalNames = value.mcp_servers.flatMap(s => s.tools.map(t => externalToolName(s.server_id, t)))
  // spec.validate accepts only exposed, non-builtin tools as finish tools.
  const finishCandidates = [
    ...value.tools.filter(t => !catalog.builtins.includes(t)).map(t => ({ value: t, label: t })),
    ...value.mcp_servers.flatMap(s => s.tools.map(t => ({
      value: externalToolName(s.server_id, t),
      label: `${t} (${servers.find(srv => srv.id === s.server_id)?.name ?? `server ${s.server_id}`})`,
    }))),
  ]
  // Dropping a tool from the allowlist also drops it as a finish tool, so the spec never names an unexposed one.
  const set = (patch: Partial<AgentSpec>) => {
    const next = { ...value, ...patch }
    const exposed = new Set([...next.tools, ...next.mcp_servers.flatMap(s => s.tools.map(t => externalToolName(s.server_id, t)))])
    onChange({ ...next, finish: { ...next.finish, tools: next.finish.tools.filter(t => exposed.has(t)) } })
  }
  const externalWrites = value.mcp_servers.some(s =>
    s.tools.some(t => !servers.find(srv => srv.id === s.server_id)?.tools.find(x => x.name === t)?.read_only))
  // Untrusted input + private data + a way out: injected text could exfiltrate.
  const exfiltrationRisk = externalNames.length > 0
    && value.tools.some(t => !catalog.builtins.includes(t))
    && (value.tools.includes('web_fetch') || value.tools.some(t => sideEffects.has(t)) || externalWrites)
  // The server's cost ceilings bind only runs on its key.
  const onUserKey = payer(value, catalog).key !== null

  return (
    <div className="space-y-4">
      <div>
        <Label className="mb-1" htmlFor="spec-prompt">System prompt</Label>
        <Textarea id="spec-prompt" className="h-40 font-mono" value={value.system_prompt}
          onChange={e => set({ system_prompt: e.target.value })} />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <Label className="mb-1" htmlFor="spec-model">Model</Label>
          <Select id="spec-model" value={value.model}
            onChange={e => onChange(fitToModel(value, catalog.models.find(m => m.model === e.target.value)))}>
            {catalog.models.map(m => <option key={m.model} value={m.model}>{m.model}</option>)}
          </Select>
        </div>
        <div>
          <Label className="mb-1" htmlFor="spec-reasoning">Reasoning</Label>
          <Select id="spec-reasoning" value={value.reasoning}
            onChange={e => set({ reasoning: e.target.value as Reasoning })}>
            {levels.map(l => <option key={l} value={l}>{l}</option>)}
          </Select>
        </div>
        <div>
          <Label className="mb-1" htmlFor="spec-temperature">Temperature</Label>
          <Input id="spec-temperature" type="number" step="0.1" min="0" max="2"
            disabled={!model?.capabilities.sampling_params}
            value={value.temperature ?? ''}
            onChange={e => set({ temperature: e.target.value === '' ? null : Number(e.target.value) })} />
        </div>
        <div>
          <Label className="mb-1" htmlFor="spec-max-output">Max output tokens</Label>
          <Input id="spec-max-output" type="number" min="1" max={model?.capabilities.max_output_tokens}
            placeholder="auto"
            value={value.max_output_tokens ?? ''}
            onChange={e => set({ max_output_tokens: e.target.value === '' ? null : Number(e.target.value) })} />
        </div>
      </div>

      <div className="md:w-1/2">
        <KeyPicker value={value} catalog={catalog} onChange={provider_key => set({ provider_key })} />
      </div>

      <fieldset>
        <legend className="block text-sm font-medium text-slate-700 mb-1">Tools</legend>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-1 max-h-64 overflow-y-auto border border-slate-200 rounded p-2">
          {toolNames.map(name => (
            <Checkbox key={name} className="flex-wrap min-w-0" checked={value.tools.includes(name)}
              onChange={() => set({ tools: toggle(value.tools, name) })}
              label={<><span className="font-mono break-all">{name}</span>{sideEffects.has(name) && <SideEffects />}</>} />
          ))}
        </div>
      </fieldset>

      <ExternalServers servers={servers} value={value.mcp_servers} onChange={mcp_servers => set({ mcp_servers })} />
      {exfiltrationRisk && (
        <Notice tone="warning">
          External tool output is untrusted. With your private data and a way to send it out (web_fetch or a tool with
          side effects), instructions hidden in that output could leak it. Keep the allowlist narrow.
        </Notice>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <Label className="mb-1" htmlFor="spec-finish">Finish</Label>
          <Select id="spec-finish" value={value.finish.mode}
            onChange={e => set({ finish: { mode: e.target.value as 'answer' | 'tool', tools: [] } })}>
            <option value="answer">final answer</option>
            <option value="tool">a finish tool succeeds</option>
          </Select>
        </div>
        {value.finish.mode === 'tool' && (
          <Field className="col-span-2 md:col-span-3" label="Finish tools"
            hint={finishCandidates.length
              ? 'The run ends when one of these succeeds. Only tools allowed above are offered.'
              : 'Allow some tools above first; only allowed tools can finish a run.'}>
            <MultiSelect options={finishCandidates} value={value.finish.tools} placeholder="Search allowed tools..."
              onChange={tools => set({ finish: { mode: 'tool', tools } })} />
          </Field>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <Label className="mb-1" htmlFor="spec-turns">Max turns</Label>
          <Input id="spec-turns" type="number" min="1" max="200" value={value.max_turns}
            onChange={e => set({ max_turns: Number(e.target.value) })} />
        </div>
        <div>
          <Label className="mb-1" htmlFor="spec-cost">Max $ per run{!onUserKey && ` (≤ ${catalog.ceilings.max_run_cost_usd})`}</Label>
          <Input id="spec-cost" value={value.max_cost_usd ?? ''}
            placeholder={onUserKey ? catalog.ceilings.default_user_key_run_cost_usd : catalog.ceilings.max_run_cost_usd}
            onChange={e => set({ max_cost_usd: e.target.value || null })} />
        </div>
        <div>
          <Label className="mb-1" htmlFor="spec-daily">Max $ per day{!onUserKey && ` (≤ ${catalog.ceilings.max_daily_cost_usd})`}</Label>
          <Input id="spec-daily" value={value.max_cost_usd_per_day ?? ''}
            placeholder={onUserKey ? 'optional' : catalog.ceilings.max_daily_cost_usd}
            onChange={e => set({ max_cost_usd_per_day: e.target.value || null })} />
        </div>
        <div>
          <Label className="mb-1" htmlFor="spec-rate">Max runs per hour (≤ {catalog.ceilings.max_runs_per_hour})</Label>
          <Input id="spec-rate" type="number" min="1" max={catalog.ceilings.max_runs_per_hour}
            placeholder={String(catalog.ceilings.max_runs_per_hour)} value={value.max_runs_per_hour ?? ''}
            onChange={e => set({ max_runs_per_hour: e.target.value ? Number(e.target.value) : null })} />
        </div>
        <div>
          <Label className="mb-1" htmlFor="spec-timeout">Timeout (s, ≤ {catalog.ceilings.max_timeout_sec})</Label>
          <Input id="spec-timeout" type="number" min="1" value={value.timeout_sec}
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
