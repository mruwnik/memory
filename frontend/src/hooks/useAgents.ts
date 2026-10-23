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
  // Not editable here yet: external MCP servers can't be registered in phase 1.
  mcp_servers: { server_id: number; tools: string[] }[]
  finish: { mode: 'answer' | 'tool'; tools: string[] }
  result_schema: Record<string, unknown> | null
  max_turns: number
  max_cost_usd: string | null
  max_cost_usd_per_day: string | null
  timeout_sec: number
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
  ceilings: { max_run_cost_usd: string; max_daily_cost_usd: string; max_timeout_sec: number }
  default_model: string
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
  timeout_sec: 600,
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
    async (filters: { definitionId?: number; status?: RunStatus; limit?: number } = {}) =>
      (
        await call<{ runs: AgentRun[] }>('agent_list_runs', {
          ...(filters.definitionId !== undefined ? { definition_id: filters.definitionId } : {}),
          ...(filters.status ? { status: filters.status } : {}),
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

  return { listDefinitions, getDefinition, saveDefinition, deleteDefinition, startRun, waitRun, listRuns, cancelRun, starRun, catalog }
}
