import { useCallback } from 'react'
import { useAuth, SERVER_URL, SESSION_COOKIE_NAME } from './useAuth'

// Get auth token from cookies for WebSocket authentication
const getAuthToken = (): string | null => {
  const value = `; ${document.cookie}`
  // Try access_token first, then session_id
  for (const name of ['access_token', SESSION_COOKIE_NAME]) {
    const parts = value.split(`; ${name}=`)
    if (parts.length === 2) {
      const token = parts.pop()?.split(';').shift()
      if (token) return token
    }
  }
  return null
}

// Build WebSocket URL for log streaming
// Note: Token in query param is necessary because WebSocket doesn't support
// custom headers during handshake. The token may be logged in browser history
// and server logs. Session tokens have limited lifetime (SESSION_VALID_FOR days).
// Build URL for the differ proxy (HTTP)
export const getDifferUrl = (sessionId: string, path: string = ''): string => {
  const baseUrl = SERVER_URL || window.location.origin
  return `${baseUrl}/claude/${sessionId}/differ/${path}`
}

export const getLogStreamUrl = (sessionId: string): string | null => {
  const token = getAuthToken()
  if (!token) return null

  // Convert http(s):// to ws(s)://
  const baseUrl = SERVER_URL || window.location.origin
  const wsUrl = baseUrl.replace(/^http/, 'ws')
  return `${wsUrl}/claude/${sessionId}/logs/stream?token=${encodeURIComponent(token)}`
}

export interface DifferInfo {
  host: string
  port: number
}

export interface ClaudeSession {
  session_id: string
  container_id: string | null
  container_name: string | null
  status: string | null
  environment_id: number | null
  differ: DifferInfo | null
}

export interface Snapshot {
  id: number
  name: string
  content_hash: string
  claude_account_email: string | null
  subscription_type: string | null
  summary: string | null
  filename: string
  size: number
  created_at: string | null
}

export interface Environment {
  id: number
  name: string
  volume_name: string
  description: string | null
  initialized_from_snapshot_id: number | null
  cloned_from_environment_id: number | null
  size_bytes: number | null
  last_used_at: string | null
  created_at: string | null
  session_count: number
}

export interface CreateEnvironmentRequest {
  name: string
  description?: string
  snapshot_id?: number // Optional: initialize from this snapshot
  source_environment_id?: number // Optional: clone from this environment
}

export interface ResetEnvironmentRequest {
  snapshot_id?: number // Optional: reinitialize from this snapshot
}

export interface AttachInfo {
  session_id: string
  container_name: string
  attach_cmd: string
  exec_cmd: string
}

export interface OrchestratorStatus {
  available: boolean
  socket_path: string | null
  containers?: { running: number; max: number } | null
  // Sampler-backed fields (added in orchestrator commit 4c45a70). `used*` is
  // the live total across running containers; `allocated*` is the static sum
  // of HostConfig limits; `max*` is the orchestrator-wide cap.
  memory?: { used_mb?: number; allocated_mb?: number; max_mb?: number } | null
  cpus?: { used?: number; allocated?: number; max?: number } | null
}

// /claude/stats response shape. Admins get the full payload; non-admins get
// only `ts` and their own `containers[]` (no `global`).
export interface FleetStats {
  ts: string
  global?: {
    running: number
    max: number
    memory_mb: { used: number; allocated: number; max: number }
    cpus: { used: number; allocated: number; max: number }
  }
  containers: ContainerStatsEntry[]
}

export interface ContainerStatsEntry {
  id: string
  status: string
  allocated: { memory_mb: number; cpus: number }
  // null for non-running containers and on the very first sample tick.
  used: { memory_mb: number; memory_pct: number; cpu_pct: number | null } | null
}

export interface StatsHistoryPoint {
  ts: string
  session_id: string
  // null on the first sample for a container (no prior baseline for the delta).
  cpu_pct: number | null
  memory_mb: number
  memory_pct: number
}

export interface StatsHistoryResponse {
  points: StatsHistoryPoint[]
  count: number
  truncated: boolean
}

export interface SpawnRequest {
  snapshot_id?: number // Static snapshot (mutually exclusive with environment_id)
  environment_id?: number // Persistent environment (mutually exclusive with snapshot_id)
  repo_url?: string
  github_token?: string
  github_token_write?: string
  enable_playwright?: boolean
  allowed_tools?: string[]
  custom_env?: Record<string, string>
  initial_prompt?: string
  run_id?: string // Custom run ID for branch naming (defaults to session_id)
}

export interface ScheduleResponse {
  task_id: string
  cron_expression: string
  next_scheduled_time: string
  topic: string
}

export interface GithubRepoBasic {
  id: number
  owner: string
  name: string
  repo_path: string
}

export interface SessionLogs {
  session_id: string
  source: 'file' | 'container'
  logs: string
}

export interface PaneInfo {
  id: string        // Stable tmux pane ID (%0, %1, etc.)
  position: string  // Positional ID (0.0, 0.1) - use for select_pane
  window_name: string
  active: boolean
  command: string
  title: string
  size?: string
}

export interface ContainerStats {
  memory: { used_mb: number; limit_mb: number; pct: number }
  cpu: { pct: number; limit_pct: number }
}

export interface PaneListResponse {
  panes: PaneInfo[]
  stats: ContainerStats | null
}

export const useClaude = () => {
  const { apiCall } = useAuth()

  // Session management
  const listSessions = useCallback(async (): Promise<ClaudeSession[]> => {
    const response = await apiCall('/claude/list')
    if (!response.ok) throw new Error('Failed to list sessions')
    return response.json()
  }, [apiCall])

  const getSession = useCallback(async (sessionId: string): Promise<ClaudeSession> => {
    const response = await apiCall(`/claude/${sessionId}`)
    if (!response.ok) throw new Error('Failed to get session')
    return response.json()
  }, [apiCall])

  const spawnSession = useCallback(async (request: SpawnRequest): Promise<ClaudeSession> => {
    const response = await apiCall('/claude/spawn', {
      method: 'POST',
      body: JSON.stringify(request),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to spawn session')
    }
    return response.json()
  }, [apiCall])

  const scheduleSession = useCallback(
    async (request: { cron_expression: string; spawn_config: SpawnRequest }): Promise<ScheduleResponse> => {
      const response = await apiCall('/claude/schedule', {
        method: 'POST',
        body: JSON.stringify(request),
      })
      if (!response.ok) {
        let detail: string | undefined
        try {
          const error = await response.json()
          detail = error.detail
        } catch {
          // Response body is not valid JSON (e.g., plain text 500 from proxy)
        }
        throw new Error(detail || `Failed to schedule session (HTTP ${response.status})`)
      }
      return response.json()
    },
    [apiCall]
  )

  const killSession = useCallback(async (sessionId: string): Promise<void> => {
    const response = await apiCall(`/claude/${sessionId}`, { method: 'DELETE' })
    if (!response.ok) throw new Error('Failed to kill session')
  }, [apiCall])

  const getAttachInfo = useCallback(async (sessionId: string): Promise<AttachInfo> => {
    const response = await apiCall(`/claude/${sessionId}/attach`)
    if (!response.ok) throw new Error('Failed to get attach info')
    return response.json()
  }, [apiCall])

  const getOrchestratorStatus = useCallback(async (): Promise<OrchestratorStatus> => {
    const response = await apiCall('/claude/status')
    if (!response.ok) throw new Error('Failed to get orchestrator status')
    return response.json()
  }, [apiCall])

  const getFleetStats = useCallback(async (): Promise<FleetStats> => {
    const response = await apiCall('/claude/stats')
    if (!response.ok) throw new Error('Failed to get fleet stats')
    return response.json()
  }, [apiCall])

  const getStatsHistory = useCallback(
    async (
      opts: { sessionId?: string; since?: string; max?: number } = {}
    ): Promise<StatsHistoryResponse> => {
      const params = new URLSearchParams()
      if (opts.sessionId) params.set('session_id', opts.sessionId)
      if (opts.since) params.set('since', opts.since)
      if (opts.max !== undefined) params.set('max', String(opts.max))
      const qs = params.toString()
      const response = await apiCall(`/claude/stats/history${qs ? `?${qs}` : ''}`)
      if (!response.ok) throw new Error('Failed to get stats history')
      return response.json()
    },
    [apiCall]
  )

  // Snapshots (for selecting config when spawning)
  const listSnapshots = useCallback(async (): Promise<Snapshot[]> => {
    const response = await apiCall('/claude/snapshots/list')
    if (!response.ok) throw new Error('Failed to list snapshots')
    return response.json()
  }, [apiCall])

  // GitHub repos (for selecting repo when spawning)
  // Admins see all repos, others see only their own
  const listUserRepos = useCallback(async (): Promise<GithubRepoBasic[]> => {
    const response = await apiCall('/github/repos')
    if (!response.ok) throw new Error('Failed to list GitHub repos')
    return response.json()
  }, [apiCall])

  // Panes
  const listPanes = useCallback(async (sessionId: string): Promise<PaneListResponse> => {
    const response = await apiCall(`/claude/${sessionId}/panes`)
    if (!response.ok) return { panes: [], stats: null }
    return response.json()
  }, [apiCall])

  // Session logs
  const getSessionLogs = useCallback(
    async (sessionId: string, tail: number = 200): Promise<SessionLogs> => {
      const response = await apiCall(`/claude/${sessionId}/logs?tail=${tail}`)
      if (!response.ok) throw new Error('Failed to get session logs')
      return response.json()
    },
    [apiCall]
  )

  // Environments (persistent Docker volumes)
  const listEnvironments = useCallback(async (): Promise<Environment[]> => {
    const response = await apiCall('/claude/environments/list')
    if (!response.ok) throw new Error('Failed to list environments')
    return response.json()
  }, [apiCall])

  const getEnvironment = useCallback(async (envId: number): Promise<Environment> => {
    const response = await apiCall(`/claude/environments/${envId}`)
    if (!response.ok) throw new Error('Failed to get environment')
    return response.json()
  }, [apiCall])

  const createEnvironment = useCallback(
    async (request: CreateEnvironmentRequest): Promise<Environment> => {
      const response = await apiCall('/claude/environments/create', {
        method: 'POST',
        body: JSON.stringify(request),
      })
      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || 'Failed to create environment')
      }
      return response.json()
    },
    [apiCall]
  )

  const deleteEnvironment = useCallback(async (envId: number): Promise<void> => {
    const response = await apiCall(`/claude/environments/${envId}`, { method: 'DELETE' })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to delete environment')
    }
  }, [apiCall])

  const resetEnvironment = useCallback(
    async (envId: number, request: ResetEnvironmentRequest = {}): Promise<Environment> => {
      const response = await apiCall(`/claude/environments/${envId}/reset`, {
        method: 'POST',
        body: JSON.stringify(request),
      })
      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || 'Failed to reset environment')
      }
      return response.json()
    },
    [apiCall]
  )

  return {
    // Sessions
    listSessions,
    getSession,
    spawnSession,
    scheduleSession,
    killSession,
    getAttachInfo,
    getOrchestratorStatus,
    getFleetStats,
    getStatsHistory,
    getSessionLogs,
    // Panes
    listPanes,
    // Snapshots
    listSnapshots,
    // Environments
    listEnvironments,
    getEnvironment,
    createEnvironment,
    deleteEnvironment,
    resetEnvironment,
    // Repos
    listUserRepos,
  }
}
