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
export const getLogStreamUrl = (sessionId: string): string | null => {
  const token = getAuthToken()
  if (!token) return null

  // Convert http(s):// to ws(s)://
  const baseUrl = SERVER_URL || window.location.origin
  const wsUrl = baseUrl.replace(/^http/, 'ws')
  return `${wsUrl}/claude/${sessionId}/logs/stream?token=${encodeURIComponent(token)}`
}

export interface ClaudeSession {
  session_id: string
  container_id: string | null
  container_name: string | null
  status: string | null
  environment_id: number | null
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
  size_bytes: number | null
  last_used_at: string | null
  created_at: string | null
  session_count: number
}

export interface CreateEnvironmentRequest {
  name: string
  description?: string
  snapshot_id?: number // Optional: initialize from this snapshot
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
}

export interface SpawnRequest {
  snapshot_id?: number // Static snapshot (mutually exclusive with environment_id)
  environment_id?: number // Persistent environment (mutually exclusive with snapshot_id)
  repo_url?: string
  github_token?: string
  github_token_write?: string
  use_happy?: boolean
  allowed_tools?: string[]
  custom_env?: Record<string, string>
  initial_prompt?: string
  run_id?: string // Custom run ID for branch naming (defaults to session_id)
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
    killSession,
    getAttachInfo,
    getOrchestratorStatus,
    getSessionLogs,
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
