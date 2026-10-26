import { useCallback } from 'react'
import { useMCP } from './useMCP'

export type Reasoning = 'off' | 'low' | 'medium' | 'high'
export type RunStatus = 'pending' | 'running' | 'done' | 'failed' | 'budget_exceeded' | 'cancelled'
export const TERMINAL_STATUSES: RunStatus[] = ['done', 'failed', 'budget_exceeded', 'cancelled']

export interface AgentSpec {
  system_prompt: string
  model: string
  // null: as much as each run can afford, up to 16000 (resolved when a run starts)
  max_output_tokens: number | null
  reasoning: Reasoning
  temperature: number | null
  provider_extras: Record<string, unknown>
  tools: string[]
  mcp_servers: { server_id: number; tools: string[] }[]
  finish: { mode: 'answer' | 'tool'; tools: string[] }
  result_schema: Record<string, unknown> | null
  max_turns: number
  max_cost_usd: string | null
  max_cost_usd_per_day: string | null
  max_runs_per_hour: number | null
  timeout_sec: number
  // A key id of yours, or null for your default key for the model's provider.
  provider_key?: number | null
}

export interface AgentRun {
  id: string
  definition_id: number | null
  input: string
  status: RunStatus
  result: Record<string, unknown> | null
  error: string | null
  usage: Record<string, number>
  cost_usd: string
  max_cost_usd: string
  num_turns: number
  trigger_kind: string
  starred: boolean
  model: string
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  steps_purged_at: string | null
  spec?: AgentSpec
}

export interface AgentDefinition {
  id: number
  name: string
  description: string
  spec: AgentSpec
  created_at: string | null
  updated_at: string | null
  last_run?: AgentRun | null
  triggers?: { total: number; enabled: number; tripped: number }
  // External tools whose schema changed after this definition was saved.
  changed_tools?: string[]
  // Why it can't run as configured; empty when it can.
  problems?: string[]
}

export interface SystemAgentEntry {
  slug: string
  title: string
  description: string
  use: 'ingest' | 'search' | 'sessions'
  model: string
  provider: string
  has_key: boolean
  problem: string | null
}

export type Block =
  | { type: 'text'; text: string }
  | { type: 'reasoning'; text: string }
  | { type: 'tool_call'; id: string; name: string; input: Record<string, unknown>; executed_by: 'runner' | 'provider' }
  | { type: 'tool_result'; tool_call_id: string; name: string; text: string; is_error: boolean; duration_ms: number | null }

export interface RunStep {
  seq: number
  kind: 'user' | 'assistant' | 'tool_results' | 'notice'
  content: Block[]
  usage: Record<string, number> | null
  cost_usd: string | null
  created_at: string | null
}

export interface ModelEntry {
  model: string
  capabilities: {
    tool_choice_required: boolean
    strict_tools: boolean
    reasoning_levels: Reasoning[]
    sampling_params: boolean
    native_web_search: boolean
    native_web_fetch: boolean
    max_output_tokens: number
  }
  extras_schema: Record<string, unknown>
}

export interface Catalog {
  builtins: string[]
  tools: { name: string; description: string; read_only: boolean }[]
  models: ModelEntry[]
  ceilings: {
    max_timeout_sec: number
    max_runs_per_hour: number
    default_user_key_run_cost_usd: string
  }
  default_model: string
  trigger_source_types: string[]
  mcp_servers: ExternalServer[]
  provider_keys: ProviderKey[]
}

export type KeyProvider = 'anthropic' | 'openai'
export const PROVIDER_NAMES: Record<KeyProvider, string> = { anthropic: 'Anthropic', openai: 'OpenAI' }
export const KEY_PROVIDERS = Object.keys(PROVIDER_NAMES) as KeyProvider[]
export const providerName = (provider: string) => PROVIDER_NAMES[provider as KeyProvider] ?? provider
// settings.AGENT_DEFAULT_USER_KEY_DAILY_COST_USD, which the API doesn't expose.
export const DEFAULT_KEY_DAILY_CAP_USD = '20.00'

/** A stored provider API key; the key itself is never returned, only its last 4 characters. */
export interface ProviderKey {
  id: number
  provider: KeyProvider
  label: string
  key_hint: string
  is_default: boolean
  is_system_default: boolean
  status: 'ok' | 'invalid'
  status_detail: string | null
  last_used_at: string | null
  max_cost_usd_per_day: string | null
  created_at: string | null
  updated_at: string | null
}

export interface KeyUpsert {
  keyId?: number
  provider?: KeyProvider
  label?: string
  key?: string
  isDefault?: boolean
  isSystemDefault?: boolean
  // '' removes the cap
  maxCostUsdPerDay?: string
}

export interface KeyDeleteResult {
  deleted: number
  pinned_by: string[]
  new_default: number | null
}

export type ServerStatus = 'pending_auth' | 'connected' | 'needs_reauth' | 'error'
export type ServerTransport = 'http' | 'sse'
export type ServerAuthType = 'auto' | 'oauth' | 'headers' | 'none'

export interface ServerTool {
  name: string
  description: string
  input_schema: Record<string, unknown>
  read_only: boolean
  hash: string
  changed_at?: string | null
}

export interface KeyList { providers: Record<KeyProvider, ProviderKey[]> }

export interface ExternalServer {
  id: number
  name: string
  url: string
  transport: ServerTransport
  auth_type: Exclude<ServerAuthType, 'auto'>
  status: ServerStatus
  status_detail: string | null
  header_names: string[]
  issuer: string | null
  scopes: string | null
  client_id: string | null
  client_source: 'dcr' | 'manual' | null
  has_token: boolean
  token_expires_at: string | null
  tools: ServerTool[]
  tools_refreshed_at: string | null
  tools_changed_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ServerAuth {
  type: ServerAuthType
  headers?: Record<string, string>
  client_id?: string
  client_secret?: string
  scopes?: string
}

export interface ServerUpsertResult {
  server: ExternalServer
  authorize_url: string | null
  secret_sent_to?: string | null
  error: string | null
}

export interface ToolDiff {
  added: string[]
  removed: string[]
  changed: string[]
  dropped: { name: string; reason: string }[]
}

/** How a run names an external server's tool (mirrors spec.external_tool_name). */
export const externalToolName = (serverId: number, tool: string) => `mcp_${serverId}_${tool}`

export interface IngestFilter {
  source_types: string[]
  tags_any: string[]
  channel_ids: string[]
  text_contains: string[]
  regex: string | null
  exclude_authors: string[]
}

export type Delivery = { mode: 'per_item' } | { mode: 'batch'; window_sec: number; max_items: number }

export interface TriggerConfig {
  filter: IngestFilter
  delivery: Delivery
  template: string
  on_edit?: boolean
}

export interface AgentTrigger {
  id: number
  definition_id: number
  kind: 'ingest'
  config: TriggerConfig
  enabled: boolean
  state: { tripped?: { reason: string; at: string } }
  api_key_id: number | null
  via_api_key: boolean
  created_at: string | null
  updated_at: string | null
}

export const DEFAULT_TRIGGER_TEMPLATE = 'New items matched this trigger:\n\n{items}'

export const emptyTriggerConfig = (): TriggerConfig => ({
  filter: { source_types: [], tags_any: [], channel_ids: [], text_contains: [], regex: null, exclude_authors: [] },
  delivery: { mode: 'per_item' },
  template: DEFAULT_TRIGGER_TEMPLATE,
  on_edit: false,
})

export interface AgentSchedule {
  id: string
  cron_expression: string | null
  next_scheduled_time: string | null
  enabled: boolean
  data: { definition_id: number; input: string }
}

export const defaultSpec = (model: string): AgentSpec => ({
  system_prompt: '',
  model,
  max_output_tokens: null,
  reasoning: 'medium',
  temperature: null,
  provider_extras: {},
  tools: [],
  mcp_servers: [],
  finish: { mode: 'answer', tools: [] },
  result_schema: null,
  max_turns: 20,
  max_cost_usd: null,
  max_cost_usd_per_day: null,
  max_runs_per_hour: null,
  timeout_sec: 600,
  provider_key: null,
})

export const useAgents = () => {
  const { mcpCall } = useMCP()

  const call = useCallback(
    async <T,>(tool: string, args: Record<string, unknown> = {}): Promise<T> => (await mcpCall(tool, args))[0] as T,
    [mcpCall],
  )

  const listDefinitions = useCallback(
    async () => (await call<{ definitions: AgentDefinition[] }>('agent_list_all')).definitions,
    [call],
  )
  const listSystemAgents = useCallback(
    async () => (await call<{ agents: SystemAgentEntry[] }>('agent_system_list')).agents,
    [call],
  )
  const getDefinition = useCallback((id: number) => call<AgentDefinition>('agent_fetch', { definition_id: id }), [call])
  const saveDefinition = useCallback(
    (body: { name: string; description: string; spec: AgentSpec; id?: number }) =>
      call<AgentDefinition>('agent_upsert', {
        name: body.name,
        description: body.description,
        spec: body.spec,
        ...(body.id !== undefined ? { definition_id: body.id } : {}),
      }),
    [call],
  )
  const deleteDefinition = useCallback((id: number) => call('agent_delete', { definition_id: id }), [call])
  const startRun = useCallback(
    (body: { input: string; definitionId?: number; spec?: AgentSpec }) =>
      call<{ run_id: string; status: RunStatus }>('agent_run', {
        input: body.input,
        ...(body.definitionId !== undefined ? { definition_id: body.definitionId } : { spec: body.spec }),
      }),
    [call],
  )
  const waitRun = useCallback(
    (runId: string, afterSeq = -1, wait = 0, limit = 100) =>
      call<{ run: AgentRun; steps: RunStep[] }>('agent_wait', { run_id: runId, after_seq: afterSeq, wait, limit }),
    [call],
  )
  const listRuns = useCallback(
    async (filters: { definitionId?: number; status?: RunStatus; limit?: number; before?: { createdAt: string; id: string } } = {}) =>
      (
        await call<{ runs: AgentRun[] }>('agent_list_runs', {
          ...(filters.definitionId !== undefined ? { definition_id: filters.definitionId } : {}),
          ...(filters.status ? { status: filters.status } : {}),
          ...(filters.before ? { before: filters.before.createdAt, before_id: filters.before.id } : {}),
          limit: filters.limit ?? 50,
        })
      ).runs,
    [call],
  )
  const cancelRun = useCallback((runId: string) => call<AgentRun>('agent_cancel', { run_id: runId }), [call])
  const starRun = useCallback(
    (runId: string, starred: boolean) => call<AgentRun>('agent_star', { run_id: runId, starred }),
    [call],
  )
  const catalog = useCallback(() => call<Catalog>('agent_catalog'), [call])

  const listSchedules = useCallback(
    async (definitionId: number) =>
      (await call<{ schedules: AgentSchedule[] }>('agent_schedules', { definition_id: definitionId })).schedules,
    [call],
  )
  const saveSchedule = useCallback(
    (body: { definitionId: number; input?: string; cron?: string; enabled?: boolean; taskId?: string }) =>
      call<AgentSchedule>('agent_schedule', {
        definition_id: body.definitionId,
        ...(body.input !== undefined ? { input: body.input } : {}),
        ...(body.cron !== undefined ? { cron_expression: body.cron } : {}),
        ...(body.enabled !== undefined ? { enabled: body.enabled } : {}),
        ...(body.taskId !== undefined ? { task_id: body.taskId } : {}),
      }),
    [call],
  )
  const deleteSchedule = useCallback((taskId: string) => call('agent_unschedule', { task_id: taskId }), [call])

  const listTriggers = useCallback(
    async (definitionId: number) =>
      (await call<{ triggers: AgentTrigger[] }>('agent_triggers', { definition_id: definitionId })).triggers,
    [call],
  )
  const saveTrigger = useCallback(
    (body: { definitionId: number; config?: TriggerConfig; enabled?: boolean; triggerId?: number }) =>
      call<AgentTrigger & { warnings?: string[] }>('agent_trigger_upsert', {
        definition_id: body.definitionId,
        ...(body.config !== undefined ? { config: body.config } : {}),
        ...(body.enabled !== undefined ? { enabled: body.enabled } : {}),
        ...(body.triggerId !== undefined ? { trigger_id: body.triggerId } : {}),
      }),
    [call],
  )
  const deleteTrigger = useCallback((triggerId: number) => call('agent_trigger_delete', { trigger_id: triggerId }), [call])

  const listServers = useCallback(
    async () => (await call<{ servers: ExternalServer[] }>('agent_server_list')).servers,
    [call],
  )
  const upsertServer = useCallback(
    (body: { name: string; url: string; transport: ServerTransport; auth?: ServerAuth; serverId?: number }) =>
      call<ServerUpsertResult>('agent_server_upsert', {
        name: body.name,
        url: body.url,
        transport: body.transport,
        ...(body.auth ? { auth: body.auth } : {}),
        ...(body.serverId !== undefined ? { server_id: body.serverId } : {}),
      }),
    [call],
  )
  const authorizeServer = useCallback(
    async (serverId: number) => (await call<{ authorize_url: string }>('agent_server_authorize', { server_id: serverId })).authorize_url,
    [call],
  )
  const refreshServerTools = useCallback(
    (serverId: number) => call<{ diff: ToolDiff; server: ExternalServer }>('agent_server_refresh_tools', { server_id: serverId }),
    [call],
  )
  const disconnectServer = useCallback(
    (serverId: number) => call<ExternalServer>('agent_server_disconnect', { server_id: serverId }),
    [call],
  )
  const deleteServer = useCallback((serverId: number) => call('agent_server_delete', { server_id: serverId }), [call])

  const listKeys = useCallback(
    () => call<KeyList>('agent_key_list'),
    [call],
  )
  const upsertKey = useCallback(
    (body: KeyUpsert) =>
      call<ProviderKey>('agent_key_upsert', {
        ...(body.keyId !== undefined ? { key_ref: body.keyId } : {}),
        ...(body.provider !== undefined ? { provider: body.provider } : {}),
        ...(body.label !== undefined ? { label: body.label } : {}),
        ...(body.key ? { key: body.key } : {}),
        ...(body.isDefault !== undefined ? { is_default: body.isDefault } : {}),
        ...(body.isSystemDefault !== undefined ? { is_system_default: body.isSystemDefault } : {}),
        ...(body.maxCostUsdPerDay !== undefined ? { max_cost_usd_per_day: body.maxCostUsdPerDay } : {}),
      }),
    [call],
  )
  const deleteKey = useCallback((keyId: number) => call<KeyDeleteResult>('agent_key_delete', { key_ref: keyId }), [call])

  return {
    listDefinitions, listSystemAgents, getDefinition, saveDefinition, deleteDefinition, startRun, waitRun, listRuns, cancelRun, starRun, catalog,
    listSchedules, saveSchedule, deleteSchedule, listTriggers, saveTrigger, deleteTrigger,
    listServers, upsertServer, authorizeServer, refreshServerTools, disconnectServer, deleteServer,
    listKeys, upsertKey, deleteKey,
  }
}
