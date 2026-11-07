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
  // A prebuilt or system agent's slug; null with definition_id for an inline spec.
  agent: string | null
  executor: string
  parent_run_id: string | null
  depth: number
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
  agents: { slug: string; title: string; description: string }[]
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
  exclude_channel_ids: string[]
  text_contains: string[]
  regex: string | null
  authors_any: string[]
  exclude_authors: string[]
  known_people_only: boolean
  changed_by_any: string[]
  exclude_changed_by: string[]
  // null: on, except for queue handlers
  exclude_own: boolean | null
  repos: string[]
  mentions_me: boolean
  threads_im_in: boolean
  dms_to_me: boolean
}

export type Delivery = { mode: 'per_item' } | { mode: 'batch'; window_sec: number; max_items: number }

export const CHANGE_TAGS = [
  'edited', 'commits', 'comments', 'review', 'checks', 'checks_complete', 'state',
  'labels', 'assignees', 'draft', 'title', 'due', 'priority',
] as const

export interface TriggerConfig {
  filter: IngestFilter
  delivery: Delivery
  template: string
  on_edit: boolean
  on_changes: string[]
}

export interface CronConfig { cron: string; input: string }

export const NOTIFY_CHANNELS = ['email', 'discord', 'slack'] as const
export type NotifyChannel = typeof NOTIFY_CHANNELS[number]
export interface NotifyHandler { channel: NotifyChannel; target: string; message: string; subject?: string | null }

export type Handler = { agent: number | string } | { queue: string } | { notify: NotifyHandler }

export interface LastDelivery { status: string | null; at: string | null; run_id: string | null; run_status: RunStatus | null }

interface SubscriptionBase {
  id: number
  name: string
  definition_id: number | null
  handler: Handler
  enabled: boolean
  state: {
    tripped?: { reason: string; at: string }
    throttled?: { reason: string; since: string; last: string }
  }
  api_key_id: number | null
  via_api_key: boolean
  api_key_name: string | null
  max_per_hour: number | null
  backlog_max: number | null
  coalesce_mode: 'none' | 'latest'
  order_by: 'none' | 'item' | 'thread'
  active_since: string | null
  next_at: string | null
  created_at: string | null
  updated_at: string | null
  activity: { pending: number; oldest_pending_at: string | null; last: LastDelivery | null }
}

export type Subscription =
  | (SubscriptionBase & { kind: 'ingest'; config: TriggerConfig })
  | (SubscriptionBase & { kind: 'cron'; config: CronConfig })

export interface Timer {
  id: number
  kind: 'timer'
  fire_at: string | null
  handler: Handler
  input: string | null
  result: { status: string; [key: string]: unknown } | null
  created_at: string | null
}

export interface ItemDelivery {
  item_id: number
  status: string
  changes: string[]
  coalesced: number[]
  created_at: string | null
  not_before: string | null
  error: string | null
  run_id: string | null
  run_status: RunStatus | null
}

export interface TickDelivery {
  event_id: number
  time: string | null
  missed: number
  result: { status: string; run_id?: string; error?: string } | null
}

export interface SubscriptionUpsert {
  subscription_id?: number
  name?: string
  handler?: Handler
  source?: Partial<TriggerConfig> | Partial<CronConfig> | { at: string; input?: string }
  enabled?: boolean
  resume?: boolean
  max_per_hour?: number
  backlog_max?: number
  coalesce?: 'none' | 'latest'
  order_by?: 'none' | 'item' | 'thread'
  // Refuse rather than update a subscription already so named.
  create_only?: boolean
}

export const DEFAULT_TRIGGER_TEMPLATE = 'New items matched this trigger:\n\n{items}'

export const emptyFilter = (): IngestFilter => ({
  source_types: [], tags_any: [], channel_ids: [], exclude_channel_ids: [], text_contains: [], regex: null,
  authors_any: [], exclude_authors: [], known_people_only: false, changed_by_any: [], exclude_changed_by: [],
  exclude_own: null, repos: [], mentions_me: false, threads_im_in: false, dms_to_me: false,
})

export const emptyTriggerConfig = (): TriggerConfig => ({
  filter: emptyFilter(),
  delivery: { mode: 'per_item' },
  template: DEFAULT_TRIGGER_TEMPLATE,
  on_edit: false,
  on_changes: [],
})

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
        agent: body.definitionId ?? body.spec,
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

  const listSubscriptions = useCallback(
    (filters: { definitionId?: number; includeFired?: boolean } = {}) =>
      call<{ subscriptions: Subscription[]; timers: Timer[]; fired?: Timer[] }>('subscription_list_all', {
        ...(filters.definitionId !== undefined ? { definition_id: filters.definitionId } : {}),
        ...(filters.includeFired ? { include_fired: true } : {}),
      }),
    [call],
  )
  const fetchSubscription = useCallback(
    (subscriptionId: number, options: { pending?: boolean; limit?: number } = {}) =>
      call<{ subscription: Subscription; deliveries: (ItemDelivery | TickDelivery)[]; runs: AgentRun[] }>('subscription_fetch', {
        subscription_id: subscriptionId,
        ...(options.pending !== undefined ? { pending: options.pending } : {}),
        ...(options.limit !== undefined ? { limit: options.limit } : {}),
      }),
    [call],
  )
  const upsertSubscription = useCallback(
    (args: SubscriptionUpsert) => call<(Subscription | Timer) & { warnings?: string[] }>('subscription_upsert', { ...args }),
    [call],
  )
  const deleteSubscription = useCallback(
    (subscriptionId: number) => call('subscription_delete', { subscription_id: subscriptionId }),
    [call],
  )
  const cancelTimer = useCallback((timerId: number) => call('subscription_delete', { timer_id: timerId }), [call])

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
    listSubscriptions, fetchSubscription, upsertSubscription, deleteSubscription, cancelTimer,
    listServers, upsertServer, authorizeServer, refreshServerTools, disconnectServer, deleteServer,
    listKeys, upsertKey, deleteKey,
  }
}
